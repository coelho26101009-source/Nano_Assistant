/**
 * NANO DESKTOP — the Electron main process.
 *
 * WHAT THIS PROCESS IS
 * The owner of the desktop experience: the Python backend child, the main
 * window, the tray, the global activation shortcut and the voice overlay. It
 * decides when Nano starts, when it is ready to be seen, and when it stops.
 *
 * WHAT THIS PROCESS IS NOT
 * An execution authority. Electron never runs a user-requested action, never
 * resolves a capability and never pre-approves anything. The global shortcut
 * does exactly one thing -- ask the backend to begin a voice turn -- and
 * everything the user then says is understood by the Brain and travels the
 * existing pipeline:
 *
 *     MODEL -> REQUEST -> POLICY -> PERMISSION -> EXECUTION
 *
 * The renderer is given a small, named, typed API and no Node primitives: no
 * exec, no spawn, no fs, no shell, and no generic invoke(channel) that could be
 * pointed at something else later. See preload.js.
 *
 * STARTUP ORDER (deliberate; do not race it)
 *   single-instance lock -> conflicting-backend check -> show the window with
 *   the local startup screen -> pick a free port -> spawn Python -> wait for a
 *   control-channel ping -> wait for real HTTP -> load the real UI into the
 *   window -> tray -> register the shortcut -> create the overlay
 *
 * The window shows startup/startup.html (a local file) until the backend
 * answers, and goes back to it with an error message if the real UI fails to
 * load or never renders, so it can never sit there blank. The shortcut is never
 * registered before the backend answers, so a keypress cannot reach a backend
 * that is not listening yet.
 */
'use strict';

const {
  app, BrowserWindow, Menu, Notification, Tray, dialog, globalShortcut, ipcMain,
  nativeImage, screen, shell,
} = require('electron');
const path = require('path');
const fs = require('fs');
const net = require('net');
const { execFile } = require('child_process');

const { NanoBackend } = require('./lib/backend');
const overlayState = require('./lib/overlay-state');
const island = require('./lib/island');
const notifications = require('./lib/notifications');
const windowState = require('./lib/window-state');
const { canonicalDataDir, shellStateFile } = require('./lib/paths');
const { diagnostics, waitForHttp, loadRecovery } = require('./lib/startup-health');
const { parseEnvText } = require('./lib/env-file');
const { Updater, RELEASES_URL } = require('./lib/updater');

/* ── Constants ──────────────────────────────────────────────────────────── */

/** The V1 activation. Windows reads CommandOrControl as Ctrl. */
const ACTIVATION_ACCELERATOR = 'CommandOrControl+Shift+Space';

/**
 * Python's own single-instance mutex port (core/main.py _INSTANCE_LOCK_PORT).
 * Probed before spawning so a backend already running outside this shell is
 * reported honestly instead of producing a second microphone owner. A test
 * asserts this constant still matches the Python one.
 */
const PY_INSTANCE_LOCK_PORT = 47615;

const IS_DEV = !app.isPackaged;
const APP_ROOT = IS_DEV ? path.join(__dirname, '..') : path.join(process.resourcesPath, 'app');
const ASSETS = path.join(__dirname, 'assets');
const ICON_PATH = path.join(ASSETS, 'icon.ico');
const TRAY_ICON_PATH = path.join(ASSETS, 'tray.png');
const MAIN_PY = path.join(APP_ROOT, 'core', 'main.py');
const STARTUP_PAGE = path.join(__dirname, 'startup', 'startup.html');

/**
 * How long a loaded page may take to put the React app on screen. The built
 * index.html has an empty <div id="__next">: everything visible is drawn by
 * JavaScript, so a script that is blocked (CSP) or throws leaves only the
 * window background -- the "black screen". Measured: the app mounts in well
 * under a second on a normal PC; 15 s means it is not going to.
 */
const RENDER_DEADLINE_MS = 15000;

/*
 * Escape hatch for graphics drivers that paint Chromium windows black.
 * Chromium normally falls back to software on its own; this forces it.
 */
if ((process.env.NANO_DISABLE_GPU === '1' || process.argv.includes('--disable-gpu'))
    && typeof app.disableHardwareAcceleration === 'function') {
  app.disableHardwareAcceleration();
}

// The island's window: room for its largest shape (the 440 x 176 music
// player), a slight grow on hover and its shadow. Transparent and
// click-through outside the pill, so the empty corners never get in the way.
const OVERLAY_SIZE = { width: 540, height: 210 };
/** How long the island takes to slide back up before its window hides. */
const ISLAND_EXIT_MS = 380;

/* ── Process state ──────────────────────────────────────────────────────── */

let mainWindow = null;
let overlayWindow = null;
let tray = null;
let backend = null;
let isQuitting = false;
let startedHidden = process.argv.includes('--hidden');
let overlayHideTimer = null;
let overlayExitTimer = null;
let saveBoundsTimer = null;
/** The overlay renderer has loaded and can be sent a state. */
let overlayReady = false;
/** The last view that arrived before the renderer was ready. */
let pendingOverlayView = null;
/** What the island is showing, by layer (lib/island.js). */
let islandStack = new island.IslandStack();
let islandExpiryTimer = null;
/** The last view sent to the island, so an unchanged view is not re-sent. */
let islandLastSent = '';
/** The resting pill only shows once NANO is really up. */
let islandReady = false;
/** 'en' or 'pt': the interface language the backend resolved. */
let islandLanguage = null;
let frontendReady = false;
let lastErrorCode = null;
/** Automatic updates (lib/updater.js). Created once the app is ready. */
let updater = null;
/** Windows is logging off or shutting down: never start an installer then. */
let sessionEnding = false;
/** The person pressed "Restart and update": reopen NANO after installing. */
let restartForUpdate = false;
/** The version a "ready to install" notification was already shown for. */
let updateNotifiedFor = null;

function desktopDiagnostics() {
  return diagnostics({ version: app.getVersion(), packaged: !IS_DEV, backend,
    frontendReady, lastErrorCode });
}

function recordFailure(code) {
  lastErrorCode = code;
  // Useful even when startup failed before a renderer existed. No raw error,
  // environment, path, credential or conversation is included in this report.
  try {
    const directory = path.join(canonicalDataDir(process.env), 'logs');
    fs.mkdirSync(directory, { recursive: true });
    fs.writeFileSync(path.join(directory, 'startup-diagnostics.json'),
      JSON.stringify(desktopDiagnostics(), null, 2), 'utf8');
  } catch (_) { /* An unwritable profile must still produce the dialog. */ }
}

/** Real, measured shortcut state. Never assumed to have worked. */
const shortcutState = { accelerator: ACTIVATION_ACCELERATOR, registered: false, error: null };

/** Persisted desktop-shell preferences. Not user data — that lives in DATA_DIR. */
let shellState = { bounds: null, overlayEnabled: true, islandMode: 'hidden', autoUpdate: true };

/**
 * Calling the hidden island with the mouse: push the pointer against the top
 * edge of a screen, near the centre, and hold it there for a moment. The
 * position is read a few times a second (one cheap call, no window catching
 * the mouse); nothing is polled while the island is set to stay on screen.
 */
const PEEK = Object.freeze({ pollMs: 110, holdMs: 220, lingerMs: 1100 });
let peekTimer = null;
let peekActive = false;
let peekZoneSince = 0;
let peekAwaySince = 0;
/** The pointer is over the pill (the island's own signal). */
let islandPointerInside = false;

/**
 * Whether NANO is listening for "Ei Nano" / "Hey Nano" (the backend's
 * wake_state event). Shown in the tray, where it can also be switched off,
 * and on the island's resting pill. Never anything that was heard.
 */
let wakeState = { enabled: false, listening: false, phrase: '' };

function log(...parts) {
  console.log('[NANO]', ...parts);
}

/* ── Shell state persistence ────────────────────────────────────────────── */

function shellStatePath() {
  return shellStateFile(app.getPath('userData'));
}

function loadShellState() {
  try {
    const raw = fs.readFileSync(shellStatePath(), 'utf8');
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      shellState = {
        bounds: parsed.bounds && typeof parsed.bounds === 'object' ? parsed.bounds : null,
        overlayEnabled: parsed.overlayEnabled !== false,
        // The island at rest: 'hidden' (the default, also for anyone who had
        // the old "Modo ambiente" switched on -- Simão asked for the island to
        // stay out of the way) or 'always'. The old islandAlwaysVisible key is
        // deliberately not read: it was saved as true for everybody.
        islandMode: parsed.islandMode === 'always' ? 'always' : 'hidden',
        // On unless the person switched it off in Settings.
        autoUpdate: parsed.autoUpdate !== false,
      };
    }
  } catch (_) {
    // No file yet, or it is unreadable. Defaults are correct and safe.
  }
}

function saveShellState() {
  try {
    fs.mkdirSync(app.getPath('userData'), { recursive: true });
    fs.writeFileSync(shellStatePath(), JSON.stringify(shellState, null, 2), 'utf8');
  } catch (err) {
    log('Não foi possível guardar o estado da janela:', err.message);
  }
}

/* ── Python discovery and environment ───────────────────────────────────── */

function findPython() {
  const candidates = [
    path.join(APP_ROOT, 'runtime', 'python', 'python.exe'),
    path.join(APP_ROOT, 'runtime', 'python', 'Scripts', 'python.exe'),
    path.join(APP_ROOT, '.venv', 'Scripts', 'python.exe'),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  if (IS_DEV) return process.platform === 'win32' ? 'python' : 'python3';
  // A packaged build must not silently fall back to whatever Python happens to
  // be on PATH: that is not a self-contained application, and pretending
  // otherwise is how a broken install reaches a user.
  throw new Error(
    'O runtime Python do Nano não foi encontrado em runtime\\python. ' +
    'Esta instalação está incompleta.'
  );
}

function loadEnvFile() {
  const env = { ...process.env };
  // An explicit profile is an isolation boundary for tests and portable use.
  if (env.NANO_DATA_DIR) return env;
  const userEnv = path.join(app.getPath('userData'), '.env');
  const devEnv = path.join(APP_ROOT, '.env');
  const envFile = fs.existsSync(userEnv) ? userEnv : (IS_DEV && fs.existsSync(devEnv) ? devEnv : null);
  if (!envFile) return env;
  // Quotes, `export` and inline comments are read as python-dotenv reads
  // them; see lib/env-file.js for what went wrong before.
  return { ...env, ...parseEnvText(fs.readFileSync(envFile, 'utf8')) };
}

function backendEnv() {
  // ONE data directory, and it is Python's own definition mirrored here. See
  // lib/paths.js for the two-locations bug this closes.
  const dataDir = canonicalDataDir(process.env);
  return {
    ...loadEnvFile(),
    ...(process.env.NANO_DATA_DIR ? { NANO_SKIP_DOTENV: '1' } : {}),
    NANO_MODE: 'electron',
    NANO_APP_ROOT: APP_ROOT,
    NANO_DATA_DIR: dataDir,
    PYTHONPATH: [APP_ROOT, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
    PYTHONUNBUFFERED: '1',
    PYTHONIOENCODING: 'utf-8',
  };
}

/* ── Ports and readiness ────────────────────────────────────────────────── */

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

/** True when something already holds Python's single-instance mutex. */
function backendAlreadyRunning(port = PY_INSTANCE_LOCK_PORT) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    const done = (result) => { socket.destroy(); resolve(result); };
    socket.setTimeout(700);
    socket.once('connect', () => done(true));
    socket.once('error', () => done(false));
    socket.once('timeout', () => done(false));
    socket.connect(port, '127.0.0.1');
  });
}

/**
 * Wait for the UI to be genuinely servable.
 *
 * A TCP connect only proves something is listening; it succeeds before eel has
 * a route for index.html, which is how a desktop window ends up briefly blank.
 * This asks for the actual page and waits for a 200.
 */
/* ── Backend lifecycle ──────────────────────────────────────────────────── */

function killTree(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return;
  if (process.platform === 'win32') {
    try { execFile('taskkill.exe', ['/pid', String(pid), '/f', '/t'], { windowsHide: true }, () => {}); } catch (_) { /* already gone */ }
  } else {
    try { process.kill(pid, 'SIGKILL'); } catch (_) { /* already gone */ }
  }
}

async function startBackend() {
  frontendReady = false;
  const python = findPython();
  if (!fs.existsSync(MAIN_PY)) {
    throw new Error(`O core do Nano não foi encontrado em ${MAIN_PY}.`);
  }
  const port = await getFreePort();

  backend = new NanoBackend({ log: (line) => log(line) });
  backend.on('event', onBackendEvent);
  backend.on('exit', ({ code, expected }) => {
    if (expected || isQuitting) return;
    recordFailure('backend_exited');
    log(`O motor terminou inesperadamente (${code}).`);
    unregisterShortcut();
    islandReady = false;
    hideOverlay(true);
    if (mainWindow && !mainWindow.isDestroyed()) {
      showStartupScreen(mainWindow, 'error', 'backend_exited');
      mainWindow.show();
      dialog.showMessageBox(mainWindow, {
        type: 'error',
        title: 'Nano — o motor parou',
        message: 'O motor do Nano terminou inesperadamente.',
        detail: `Código ${code}. Usa "Reiniciar o Nano" no menu do tabuleiro para o voltar a arrancar.`,
        buttons: ['OK'],
      }).catch(() => {});
    }
  });

  backend.start({
    command: python,
    args: [MAIN_PY, '--mode', 'electron', '--port', String(port), '--desktop-control'],
    options: {
      cwd: APP_ROOT,
      env: backendEnv(),
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    },
  });

  await backend.waitUntilAlive({ timeoutMs: 90000 });
  await waitForHttp(port, 45000, { alive: () => Boolean(backend && backend.running) });
  return port;
}

async function stopBackend() {
  if (!backend) return;
  const current = backend;
  backend = null;
  try {
    await current.stop({ killTree });
  } catch (err) {
    log('Falha ao encerrar o motor:', err.message);
  }
}

/* ── Backend events -> overlay ──────────────────────────────────────────── */

function onBackendEvent(event, payload) {
  if (event === 'voice_phase') {
    const view = overlayState.fromPhase(payload && payload.phase);
    if (view) applyOverlayView(view);
    return;
  }
  if (event === 'voice_turn_ended') {
    applyOverlayView(overlayState.fromTurnEnd(payload));
    return;
  }
  if (event === 'backend_started' || event === 'ui_language') {
    if (payload && typeof payload.lang === 'string') {
      islandLanguage = island.languageOf(payload.lang);
      islandLastSent = '';
      renderIsland();
      refreshTrayMenu();
    }
    return;
  }
  if (event === 'wake_state') {
    const data = payload && typeof payload === 'object' ? payload : {};
    wakeState = {
      enabled: data.enabled === true,
      listening: data.listening === true,
      phrase: typeof data.phrase === 'string' ? data.phrase.slice(0, 40) : '',
    };
    islandLastSent = '';
    refreshTrayMenu();
    renderIsland();
    return;
  }
  if (event === 'chat_activity') {
    const next = island.fromChatActivity(payload);
    if (next) { islandStack.set('chat', next.view, next.ttl); renderIsland(); }
    return;
  }
  if (event === 'approval') {
    const data = payload || {};
    if (data.phase === 'request') islandStack.approvalRequested(String(data.id || ''), data);
    else if (data.phase === 'resolved') islandStack.approvalResolved(String(data.id || ''));
    renderIsland();
    return;
  }
  if (event === 'media') {
    // What is playing on this PC (core/now_playing.py).
    const next = island.fromMedia(payload);
    if (next) islandStack.set('media', next.view, next.ttl);
    else islandStack.clear('media');
    // A new song opens the player for a moment -- unless a reminder or an
    // alert is on screen, which matter more than a track change.
    const changed = payload && payload.changed;
    const card = changed === 'track' ? island.trackCard(payload)
      // Paused or played again: the small music pill for a moment, so the
      // hidden island still shows that it took.
      : changed === 'status' ? island.mediaBlip(payload) : null;
    // The music card already on screen is replaced, not kept: skipping songs
    // quickly (Fn+F3) used to leave the OLD song's card up for its 5 s, over
    // the new song. Only a reminder or an alert keeps its place.
    const showingMusic = islandStack.has('notice') && islandStack.peek('notice').state === 'media';
    if (changed === 'art' && showingMusic && islandStack.peek('notice').expanded) {
      // The app swapped in the new song's cover: refresh the open card.
      const refreshed = island.trackCard(payload);
      if (refreshed) islandStack.set('notice', refreshed.view, refreshed.ttl);
    } else if (card && (!islandStack.has('notice') || showingMusic)) islandStack.set('notice', card.view, card.ttl);
    else if (!next && islandStack.peek('notice') && islandStack.peek('notice').state === 'media') islandStack.clear('notice');
    renderIsland();
    return;
  }
  if (event === 'volume') {
    const next = island.fromVolume(payload);
    if (next) { islandStack.set('hud', next.view, next.ttl); renderIsland(); }
    return;
  }
  if (event === 'reminder_due' || event === 'nano_notice') {
    const next = event === 'reminder_due' ? island.fromReminder(payload) : island.fromNotice(payload);
    if (next) { islandStack.set('notice', next.view, next.ttl); renderIsland(); }
  }
  const toast = notifications.fromBackendEvent(event, payload);
  if (toast) showNativeNotification(toast);
}

/**
 * Show a reminder as a Windows notification.
 *
 * Shown from the main process, so it works with the window hidden in the tray
 * and needs no renderer permission (the renderer is still denied
 * notifications; see hardenWebContents). Clicking it brings Nano forward.
 */
function showNativeNotification(toast) {
  try {
    if (!Notification || (typeof Notification.isSupported === 'function' && !Notification.isSupported())) {
      log('Notificações nativas não suportadas neste sistema.');
      return false;
    }
    const notification = new Notification({
      title: toast.title,
      body: toast.body,
      silent: toast.silent,
      icon: appIcon(),
    });
    notification.on('click', () => showMainWindow());
    /* Windows can refuse the toast outright: with notifications turned off in
       Settings → System → Notifications (or Do not disturb on) it fires
       'failed' with "Settings prevent the notification type from being
       delivered" -- seen on a real Windows 11 PC. A reminder that reaches
       nobody is the worst outcome, so bring the window up without stealing
       focus (the page shows the reminder itself) and flash the taskbar. */
    notification.on('failed', (_event, error) => {
      log('Notificação do Windows recusada:', String(error || ''));
      revealForMissedNotification();
    });
    notification.show();
    return true;
  } catch (err) {
    log('Falha ao mostrar a notificação:', err.message);
    return false;
  }
}

/* ── Main window ────────────────────────────────────────────────────────── */

function appIcon() {
  return fs.existsSync(ICON_PATH) ? ICON_PATH : undefined;
}

/** Show the local startup screen: loading, or an error with a fixed reason code. */
function showStartupScreen(window, state = 'loading', reason = '') {
  if (!window || window.isDestroyed()) return;
  const query = state === 'error' ? { state: 'error', reason: String(reason) } : { state: 'loading' };
  Promise.resolve(window.loadFile(STARTUP_PAGE, { query })).catch(() => {});
}

/**
 * Create the main window. Without a port it only shows the startup screen
 * (used at boot, before the backend answers); with one it also loads the UI.
 */
function createMainWindow(port) {
  const workAreas = screen.getAllDisplays().map((d) => d.workArea);
  const bounds = windowState.sanitizeBounds(shellState.bounds, workAreas);

  mainWindow = new BrowserWindow({
    width: bounds.width,
    height: bounds.height,
    x: bounds.x,
    y: bounds.y,
    minWidth: windowState.MIN_WIDTH,
    minHeight: windowState.MIN_HEIGHT,
    frame: false,
    // Matches the dark --bg, so the first paint is Nano-coloured rather than white.
    backgroundColor: '#181818',
    icon: appIcon(),
    show: false,
    title: 'Nano',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
      spellcheck: true,
    },
  });

  mainWindow.once('ready-to-show', () => {
    if (bounds.maximized) mainWindow.maximize();
    if (!startedHidden) mainWindow.show();
  });

  // The renderer's title bar needs to know which glyph to draw.
  const reportWindowState = () => sendWindowState();
  mainWindow.on('maximize', reportWindowState);
  mainWindow.on('unmaximize', reportWindowState);
  mainWindow.on('enter-full-screen', reportWindowState);
  mainWindow.on('leave-full-screen', reportWindowState);
  mainWindow.on('focus', () => { reportWindowState(); renderIsland(); });
  mainWindow.on('blur', () => { reportWindowState(); renderIsland(); });
  mainWindow.on('hide', () => renderIsland());

  mainWindow.on('resize', rememberBoundsSoon);
  mainWindow.on('move', rememberBoundsSoon);

  // Windows only: the session is ending (log off, restart, shut down). An
  // installer started now could be killed half-way, so a ready update waits
  // for the next normal exit instead.
  mainWindow.on('session-end', () => { sessionEnding = true; });

  /**
   * Closing hides. THIS IS THE POINT OF THE TRAY: the global shortcut has to
   * keep working when the window is gone, and it cannot if the app has quit.
   * Quitting is a separate, explicit action (tray -> Sair do Nano), and the
   * title bar's close button says so in its tooltip so this is never a
   * surprise.
   */
  mainWindow.on('close', (event) => {
    if (isQuitting) return;
    event.preventDefault();
    rememberBounds();
    mainWindow.hide();
    notifyHiddenToTrayOnce();
  });

  if (port === undefined || port === null) {
    showStartupScreen(mainWindow, 'loading');
  } else {
    attachMainWindow(port);
  }
  return mainWindow;
}

/** Point the existing main window at the backend on `port` and load the UI. */
function attachMainWindow(port) {
  const window = mainWindow;
  if (!window || window.isDestroyed()) return;
  const origin = `http://127.0.0.1:${port}`;
  const isAppUrl = (url) => typeof url === 'string' && (url === origin || url.startsWith(`${origin}/`));
  let renderTimer = null;
  let consoleErrors = [];
  const clearRenderTimer = () => { if (renderTimer) clearTimeout(renderTimer); renderTimer = null; };

  const loadPage = () => {
    if (window.isDestroyed() || isQuitting) return;
    clearRenderTimer();
    consoleErrors = [];
    // did-fail-load owns recovery; consume loadURL's rejection as well.
    Promise.resolve(window.loadURL(`${origin}/index.html`)).catch(() => {});
  };
  const reportLoadFailure = async (code) => {
    clearRenderTimer();
    frontendReady = false;
    recordFailure(code);
    // Never leave the user looking at an empty window: say what happened.
    showStartupScreen(window, 'error', code);
    if (!startedHidden && !window.isDestroyed()) window.show();
    const answer = await dialog.showMessageBox(window, {
      type: 'error', title: 'Nano — interface indisponível',
      message: 'Não foi possível abrir a interface do Nano.',
      detail: `Código: ${code}. Podes tentar novamente ou sair e reabrir o Nano. ` +
        'Se continuar, reinstala a mesma versão. Os teus dados são preservados. ' +
        'O relatório local logs/startup-diagnostics.json contém apenas dados técnicos.',
      buttons: ['Tentar novamente', 'Sair'], defaultId: 0, cancelId: 1,
    }).catch(() => ({ response: 1 }));
    if (isQuitting || window.isDestroyed()) return;
    if (answer.response === 0) { recovery.ready(); loadPage(); }
    else quitNano();
  };
  const recovery = loadRecovery({ reload: loadPage,
    report: () => { reportLoadFailure('frontend_load_failed'); },
    closed: () => isQuitting || window.isDestroyed() });

  /*
   * A page that loads is not yet a page that works. Confirm React actually
   * mounted something into #__next; if not, show why instead of the bare
   * window background, and print the renderer's errors (a CSP block names the
   * script) to the console window so the cause is visible.
   */
  const confirmRendered = () => {
    clearRenderTimer();
    const started = Date.now();
    const probe = () => {
      renderTimer = null;
      if (window.isDestroyed() || isQuitting) return;
      Promise.resolve(window.webContents.executeJavaScript(
        "(function(){var r=document.getElementById('__next');return r?r.childElementCount:-1;})()", true))
        .catch(() => 0)
        .then((children) => {
          if (window.isDestroyed() || isQuitting) return;
          if (children !== 0) {
            recovery.ready(); frontendReady = true; lastErrorCode = null;
            return;
          }
          if (Date.now() - started < RENDER_DEADLINE_MS) {
            renderTimer = setTimeout(probe, 500);
            return;
          }
          log('A interface carregou mas não arrancou (ecrã vazio).');
          for (const line of consoleErrors.slice(0, 5)) log('  renderer:', line);
          reportLoadFailure('frontend_not_rendered');
        });
    };
    renderTimer = setTimeout(probe, 300);
  };

  // Before the first load, so the very first response is already covered.
  applyContentSecurityPolicy(mainWindow.webContents.session, port);
  hardenWebContents(window.webContents, origin);
  window.webContents.on('console-message', (...args) => {
    // Electron 35+ passes one event object; older versions passed positional args.
    const [first, level, message] = args;
    const lvl = first && typeof first === 'object' && 'level' in first ? first.level : level;
    const text = first && typeof first === 'object' && 'message' in first ? first.message : message;
    if (lvl === 'error' || lvl === 3) {
      if (consoleErrors.length < 20) consoleErrors.push(String(text).slice(0, 300));
    }
  });
  window.webContents.on('did-finish-load', () => {
    if (!isAppUrl(window.webContents.getURL())) return; // the startup screen
    confirmRendered();
  });
  window.webContents.on('render-process-gone', () => { reportLoadFailure('renderer_gone'); });
  window.on('closed', () => { clearRenderTimer(); recovery.dispose(); });

  window.webContents.on('did-fail-load', (_e, code, description, url, isMainFrame) => {
    if (!isMainFrame || isQuitting || code === -3) return;
    if (url && !isAppUrl(url)) return;
    frontendReady = false;
    recovery.failed();
  });
  loadPage();
}

/**
 * Navigation, popups, permissions and zoom, locked down in one place.
 *
 * Nano's window shows Nano. It may not be navigated to another origin by a
 * link, a redirect or a script, and it may not open child windows: an external
 * URL is handed to the OS browser after validation, or refused.
 */
/**
 * The Content Security Policy for Nano's own window.
 *
 * WHY IT IS INJECTED AS A HEADER RATHER THAN A <meta> TAG. The page is built by
 * Next.js and served by Python's Bottle; neither is a good place to own a
 * security header for the desktop shell, and a <meta> CSP cannot express
 * `frame-ancestors`. Setting it here means the policy holds for every response
 * the window loads, whatever the backend chose to send.
 *
 * EVERY DIRECTIVE IS DERIVED FROM WHAT THE BUILT APP ACTUALLY LOADS, which was
 * measured rather than assumed: the production bundle references no external
 * origin at all -- no CDN, no web font, no analytics. Scripts are same-origin
 * `<script src>` only (the one inline `<script id="__NEXT_DATA__">` is
 * `type="application/json"`, which is data and never executed), so `script-src`
 * needs no 'unsafe-inline' and no 'unsafe-eval'.
 *
 * The single concession is `style-src 'unsafe-inline'`: Next.js injects
 * `<style>` elements and the components use React's `style={{...}}` prop
 * throughout, which lands as an element style attribute. Removing it would mean
 * rewriting the styling of the entire UI, and inline STYLE is a far weaker
 * primitive than inline SCRIPT -- it cannot execute anything.
 *
 * `connect-src` names the loopback WebSocket explicitly. eel's channel is
 * ws://, and while 'self' covers same-origin ws in current Chromium, saying it
 * out loud costs nothing and does not depend on that behaviour holding.
 */
function contentSecurityPolicy(port) {
  return [
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    `connect-src 'self' ws://127.0.0.1:${port} ws://localhost:${port}`,
    "media-src 'self'",
    "worker-src 'self'",
    // Nothing may embed Nano, and Nano embeds nothing.
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    // A <base> tag could re-point every relative URL on the page.
    "base-uri 'none'",
    // There is no form in Nano that should ever submit anywhere.
    "form-action 'none'",
  ].join('; ');
}

/**
 * Apply the CSP to every response in a session.
 *
 * Deliberately REPLACES any policy the backend may have sent rather than
 * appending: two CSP headers intersect, so a laxer one cannot widen this, but a
 * stricter or malformed one from elsewhere could silently break the UI and
 * leave the cause invisible. One owner, one policy.
 */
function applyContentSecurityPolicy(session, port) {
  const policy = contentSecurityPolicy(port);
  session.webRequest.onHeadersReceived((details, callback) => {
    const headers = { ...details.responseHeaders };
    for (const key of Object.keys(headers)) {
      if (key.toLowerCase() === 'content-security-policy') delete headers[key];
    }
    headers['Content-Security-Policy'] = [policy];
    callback({ responseHeaders: headers });
  });
}

function hardenWebContents(contents, origin) {
  contents.on('will-navigate', (event, url) => {
    if (!url.startsWith(`${origin}/`) && url !== origin) {
      event.preventDefault();
      log('Navigation blocked:', url);
    }
  });

  contents.setWindowOpenHandler(({ url }) => {
    // Only real web links, and only in the user's own browser. Anything else
    // (file:, javascript:, custom schemes) is refused outright.
    try {
      const parsed = new URL(url);
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
        shell.openExternal(url).catch(() => {});
      } else {
        log('External link refused:', parsed.protocol);
      }
    } catch (_) {
      log('Invalid external link refused.');
    }
    return { action: 'deny' };
  });

  contents.on('will-attach-webview', (event) => event.preventDefault());

  // The renderer needs no device permissions: Python owns the microphone, and
  // nothing here should be able to ask for the camera, the clipboard or
  // notifications behind the user's back.
  contents.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  contents.session.setPermissionCheckHandler(() => false);

  // 100% ZOOM IS THE TARGET, NOT A WORKAROUND. The layout is fixed to work at
  // devicePixelRatio-independent 100%; pinning the factor here means a stray
  // Ctrl+wheel cannot leave the user in a half-zoomed state and conclude the
  // UI is broken.
  contents.on('did-finish-load', () => {
    contents.setZoomFactor(1);
    contents.setVisualZoomLevelLimits(1, 1).catch(() => {});
  });
}

function rememberBounds() {
  const captured = windowState.captureBounds(mainWindow);
  if (captured) {
    shellState.bounds = captured;
    saveShellState();
  }
}

function rememberBoundsSoon() {
  clearTimeout(saveBoundsTimer);
  saveBoundsTimer = setTimeout(rememberBounds, 600);
}

let hiddenNoticeShown = false;
function notifyHiddenToTrayOnce() {
  if (hiddenNoticeShown || !tray) return;
  hiddenNoticeShown = true;
  try {
    tray.displayBalloon({
      title: 'O Nano continua a correr',
      content: `A janela foi fechada, mas o Nano continua no tabuleiro. Usa ${humanAccelerator()} para falar com ele.`,
      icon: fs.existsSync(ICON_PATH) ? ICON_PATH : undefined,
    });
  } catch (_) {
    // Balloons are best-effort; the tray icon itself is the real signal.
  }
}

function showMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

/** A reminder Windows would not show: put Nano on screen, unfocused, and flash it. */
function revealForMissedNotification() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  if (!mainWindow.isVisible()) mainWindow.showInactive();
  if (typeof mainWindow.flashFrame === 'function') mainWindow.flashFrame(true);
}

function sendWindowState() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send('nano:window-state', currentWindowState());
}

function currentWindowState() {
  const window = mainWindow && !mainWindow.isDestroyed() ? mainWindow : null;
  return {
    maximized: window ? window.isMaximized() : false,
    fullScreen: window ? window.isFullScreen() : false,
    focused: window ? window.isFocused() : false,
    platform: process.platform,
  };
}

/* ── The NANO island (the floating pill; it replaced the voice capsule) ─── */

function createOverlayWindow() {
  overlayWindow = new BrowserWindow({
    width: OVERLAY_SIZE.width,
    height: OVERLAY_SIZE.height,
    show: false,
    frame: false,
    transparent: true,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    // Never takes focus. Pressing the shortcut inside a game or an editor must
    // not pull the user out of what they are doing. A click on the pill still
    // arrives (it opens NANO) without the island ever becoming focused.
    focusable: false,
    acceptFirstMouse: true,
    hasShadow: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'overlay', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
    },
  });

  // A state sent before the page has loaded is dropped on the floor, and the
  // window then shows with its default markup -- which renders nothing,
  // because the island starts at data-visible="false". That is a real race on
  // the FIRST voice turn after launch. Remember the last view and replay it
  // the moment the renderer is ready.
  overlayReady = false;
  islandLastSent = '';
  overlayWindow.webContents.once('did-finish-load', () => {
    overlayReady = true;
    // Whatever arrived meanwhile is in the stack already: draw it now.
    renderIsland();
  });

  overlayWindow.loadFile(path.join(__dirname, 'overlay', 'overlay.html'));
  // Above full-screen apps too, otherwise the one moment it matters -- a game
  // in the foreground -- is the one moment it is invisible.
  overlayWindow.setAlwaysOnTop(true, 'screen-saver');
  overlayWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  // Click-through, but mouse moves are still forwarded (Windows, macOS) so
  // the page can tell when the pointer is over the pill itself.
  overlayWindow.setIgnoreMouseEvents(true, { forward: true });

  overlayWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  overlayWindow.webContents.on('will-navigate', (event) => event.preventDefault());
  overlayWindow.on('closed', () => { overlayWindow = null; });
}

function positionOverlay() {
  if (!overlayWindow || overlayWindow.isDestroyed()) return;
  // The display under the cursor is the one the user is looking at.
  const cursor = screen.getCursorScreenPoint();
  const display = screen.getDisplayNearestPoint(cursor);
  // Electron uses display work-area DIPs here, including on scaled monitors.
  // Keep the island reachable on unusually narrow secondary displays.
  const size = {
    width: Math.min(OVERLAY_SIZE.width, Math.max(1, display.workArea.width - 24)),
    height: OVERLAY_SIZE.height,
  };
  const { x, y } = windowState.islandPosition(display.workArea, size);
  overlayWindow.setBounds({ ...size, x, y });
}

/**
 * A voice view (lib/overlay-state.js) goes on the island's voice layer.
 *
 * Kept under its old name: the voice turn, the hotkey's busy answer and the
 * "not ready" answer all arrive here, exactly as they did for the capsule.
 */
function applyOverlayView(view) {
  if (!view) return;
  const next = island.fromVoiceView(view);
  if (next) islandStack.set('voice', next.view, next.ttl);
  else islandStack.clear('voice');
  renderIsland();
}

/** What the island should show right now, translated, or null. */
function currentIslandView() {
  const mainFocused = Boolean(mainWindow && !mainWindow.isDestroyed()
    && mainWindow.isVisible() && mainWindow.isFocused());
  const view = islandStack.current({
    mainFocused,
    alwaysVisible: shellState.islandMode === 'always' && islandReady && !isQuitting,
    peek: peekActive && islandReady && !isQuitting,
  });
  if (view && view.layer === 'idle' && wakeState.listening) view.wake = wakeDisplayPhrase();
  return view ? island.translate(view, islandLanguage || systemLanguage()) : null;
}

function systemLanguage() {
  try { return island.languageOf(app.getLocale()); } catch (_) { return 'en'; }
}

/**
 * Show the island for the current state of the stack.
 *
 * INDEPENDENCE IS THE WHOLE POINT. Nothing in here restores or focuses the
 * main window: the island is a separate top-level BrowserWindow with no
 * `parent:` option, so Windows never minimises or hides it alongside the main
 * window. The main window is only READ, to step aside while NANO itself is in
 * front (see IslandStack.current).
 */
function renderIsland() {
  clearTimeout(overlayHideTimer);
  clearTimeout(islandExpiryTimer);
  islandExpiryTimer = null;

  const view = currentIslandView();
  // Wake up when the next moment expires (a reminder's 9 s, "Pronto"...).
  const expiry = islandStack.nextExpiry();
  if (expiry) {
    islandExpiryTimer = setTimeout(renderIsland, Math.max(0, expiry - Date.now()) + 5);
    // The island never keeps NANO (or a test run) alive on its own.
    if (islandExpiryTimer && typeof islandExpiryTimer.unref === 'function') islandExpiryTimer.unref();
  }

  if (!view) { hideOverlay(); return; }
  if (!shellState.overlayEnabled) {
    // NEVER SILENT. This flag is persisted in desktop-state.json and is
    // switchable from two places (the tray menu and Settings), so a stale
    // "off" is easy to acquire and was previously invisible: the island
    // simply never appeared and nothing anywhere said why.
    if (view.layer !== 'idle') {
      log('island: SUPPRESSED - "Mostrar a ilha do NANO" is off '
        + '(tray menu, or Settings > Geral > Modo desktop)');
    }
    hideOverlay();
    return;
  }
  // A new real state can arrive during the short exit. Keep that window alive.
  clearTimeout(overlayExitTimer);
  overlayExitTimer = null;
  if (!overlayWindow || overlayWindow.isDestroyed()) createOverlayWindow();

  if (!overlayReady) {
    // The renderer is still loading; replay this once it is up.
    pendingOverlayView = view;
    log(`island: deferred until the renderer loads (state=${view.state})`);
    return;
  }
  pendingOverlayView = null;

  const wasVisible = overlayWindow.isVisible();
  // Only placed when it appears: a pill that hopped between monitors while
  // it changed shape would be impossible to follow.
  if (!wasVisible) positionOverlay();
  const serialised = JSON.stringify(view);
  if (serialised !== islandLastSent || !wasVisible) {
    islandLastSent = serialised;
    overlayWindow.webContents.send('nano:overlay-state', view);
  }
  // showInactive: appear without stealing focus from whatever the user is in,
  // and WITHOUT restoring the main window.
  if (!wasVisible) overlayWindow.showInactive();

  if (view.layer !== 'idle') {
    log(`island: state=${view.state} shown=${overlayWindow.isVisible()} `
      + `mainVisible=${mainWindow ? mainWindow.isVisible() : 'none'} `
      + `mainMinimized=${mainWindow ? mainWindow.isMinimized() : 'none'}`);
  }
}

function hideOverlay(destroy = false) {
  clearTimeout(overlayHideTimer);
  clearTimeout(overlayExitTimer);
  overlayExitTimer = null;
  pendingOverlayView = null;
  if (!overlayWindow || overlayWindow.isDestroyed()) return;
  if (destroy) {
    clearTimeout(islandExpiryTimer);
    islandExpiryTimer = null;
    overlayWindow.destroy(); overlayWindow = null; overlayReady = false;
    return;
  }
  if (!overlayWindow.isVisible()) return;
  // Shrink and fade in the renderer before hiding the transparent window.
  // The renderer also stops its animations as soon as visible is false.
  overlayWindow.webContents.send('nano:overlay-state', overlayState.HIDDEN);
  islandLastSent = '';
  overlayWindow.setIgnoreMouseEvents(true, { forward: true });
  const exitingWindow = overlayWindow;
  overlayExitTimer = setTimeout(() => {
    if (!exitingWindow.isDestroyed()) exitingWindow.hide();
    overlayExitTimer = null;
  }, ISLAND_EXIT_MS);
}

/** The pointer entered or left the pill: catch clicks only over the pill. */
function onIslandPointer(event, inside) {
  if (!overlayWindow || overlayWindow.isDestroyed() || event.sender !== overlayWindow.webContents) return;
  islandPointerInside = inside === true;
  overlayWindow.setIgnoreMouseEvents(inside !== true, { forward: true });
}

/* ── Calling the hidden island with the mouse ───────────────────────────── */

function peekWanted() {
  return shellState.overlayEnabled && shellState.islandMode !== 'always' && islandReady && !isQuitting;
}

/** Start or stop watching the pointer, to match the settings. */
function syncPeekWatcher() {
  if (peekWanted()) {
    if (!peekTimer) {
      peekTimer = setInterval(peekTick, PEEK.pollMs);
      if (typeof peekTimer.unref === 'function') peekTimer.unref();
    }
    return;
  }
  clearInterval(peekTimer);
  peekTimer = null;
  peekZoneSince = 0;
  peekAwaySince = 0;
  if (peekActive) { peekActive = false; renderIsland(); }
}

/**
 * One look at the pointer. In the zone long enough: the island comes out.
 * Away from both the zone and the pill for a little while: it goes back.
 */
function peekTick(now = Date.now()) {
  if (!peekWanted()) { syncPeekWatcher(); return; }
  let cursor = null;
  let bounds = null;
  try {
    cursor = screen.getCursorScreenPoint();
    const display = screen.getDisplayNearestPoint(cursor);
    bounds = display.bounds || display.workArea;
  } catch (_) {
    return;
  }
  const inZone = island.inPeekZone(cursor, bounds);
  if (!peekActive) {
    if (!inZone) { peekZoneSince = 0; return; }
    if (!peekZoneSince) { peekZoneSince = now; return; }
    if (now - peekZoneSince < PEEK.holdMs) return;
    peekActive = true;
    peekAwaySince = 0;
    renderIsland();
    return;
  }
  if (inZone || islandPointerInside) { peekAwaySince = 0; return; }
  if (!peekAwaySince) { peekAwaySince = now; return; }
  if (now - peekAwaySince < PEEK.lingerMs) return;
  peekActive = false;
  peekZoneSince = 0;
  peekAwaySince = 0;
  renderIsland();
}

/**
 * A music player button. Only the three transport commands, which Windows
 * already lets any keyboard's media keys send; the backend checks the action
 * again (core/now_playing.py) and talks to the app that is playing.
 */
function onIslandMedia(event, action) {
  if (!overlayWindow || overlayWindow.isDestroyed() || event.sender !== overlayWindow.webContents) return;
  if (!island.MEDIA_ACTIONS.includes(action)) return;
  if (!backend || !backend.running) return;
  backend.request('media_control', { action }).catch((err) => log('media_control failed:', err.message));
}

/** The pill was clicked: bring NANO forward. Nothing else, ever. */
function onIslandOpen(event) {
  if (!overlayWindow || overlayWindow.isDestroyed() || event.sender !== overlayWindow.webContents) return;
  overlayWindow.setIgnoreMouseEvents(true, { forward: true });
  showMainWindow();
}

/**
 * Keep the resting pill on screen ('always'), or keep the island hidden until
 * something happens or it is called ('hidden', the default).
 */
function setIslandAlwaysVisible(enabled) {
  shellState.islandMode = enabled ? 'always' : 'hidden';
  saveShellState();
  syncPeekWatcher();
  renderIsland();
  refreshTrayMenu();
  return shellState.islandMode === 'always';
}

/* ── Global activation shortcut ─────────────────────────────────────────── */

function humanAccelerator() {
  return ACTIVATION_ACCELERATOR.replace('CommandOrControl', 'Ctrl').replace(/\+/g, ' + ');
}

/**
 * The one thing the hotkey does: ask the backend to start a voice turn.
 *
 * It carries no command, no capability and no permission. A second press
 * during a turn is answered with the backend's real busy state -- the guard
 * lives in VoiceRuntime.run_voice_turn, so no second microphone reader can be
 * opened even if this fired twice.
 */
async function triggerVoiceTurn(source = 'hotkey') {
  if (!backend || !backend.running) {
    applyOverlayView(overlayState.unavailableView('not_ready'));
    return;
  }
  try {
    const reply = await backend.request('start_voice_turn', { source });
    const result = (reply && reply.result) || {};
    if (result.busy) {
      applyOverlayView(overlayState.busyView(result));
    } else if (!result.accepted) {
      log('Turno de voz recusado:', result.error || 'motivo desconhecido');
      applyOverlayView(overlayState.unavailableView(result.error));
    }
    // The accepted case says nothing here on purpose: the overlay is driven by
    // the real phase events that follow, not by this acknowledgement.
  } catch (err) {
    log('Falha ao pedir um turno de voz:', err.message);
    applyOverlayView(overlayState.unavailableView('failed'));
  }
}

function registerShortcut() {
  unregisterShortcut();
  let ok = false;
  try {
    ok = globalShortcut.register(ACTIVATION_ACCELERATOR, () => { triggerVoiceTurn('hotkey'); });
  } catch (err) {
    shortcutState.error = err.message;
  }
  // register() can return true and still not be ours; isRegistered is the
  // truthful check, and honesty here is the whole requirement.
  shortcutState.registered = ok && globalShortcut.isRegistered(ACTIVATION_ACCELERATOR);
  if (!shortcutState.registered) {
    shortcutState.error = shortcutState.error
      || 'Outra aplicação já usa este atalho.';
    log(`Atalho global ${humanAccelerator()} NÃO registado: ${shortcutState.error}`);
  } else {
    shortcutState.error = null;
    log(`Atalho global ${humanAccelerator()} registado.`);
  }
  reportShortcutToBackend();
  refreshTrayMenu();
  return shortcutState.registered;
}

function unregisterShortcut() {
  try { globalShortcut.unregister(ACTIVATION_ACCELERATOR); } catch (_) { /* not held */ }
  shortcutState.registered = false;
}

/** Tell Python what really happened, so Settings can show it. */
function reportShortcutToBackend() {
  if (!backend || !backend.running) return;
  backend.request('report_shortcut', {
    shortcut: humanAccelerator(),
    registered: shortcutState.registered,
    error: shortcutState.error,
    overlay: shellState.overlayEnabled,
    autoLaunch: isAutoLaunchEnabled(),
    version: app.getVersion(),
  }).catch(() => { /* the UI falls back to "unknown", which is honest */ });
}

/* ── Automatic updates ──────────────────────────────────────────────────── */
/*
 * The flow and its safety rules live in lib/updater.js. This part only wires it
 * to the app: when to start, what to tell the person, and installing on exit.
 * Updates install only in the packaged Windows app; from the project folder
 * NANO only says that a new version exists.
 */

const UPDATE_TEXT = {
  en: {
    readyTitle: 'NANO update ready',
    readyBody: (v) => `NANO ${v} installs when you close NANO. To install now, use "Restart and update" in Settings or in the tray menu.`,
    doneTitle: 'NANO is up to date',
    doneBody: (v) => `NANO was updated to ${v}.`,
    failedTitle: 'NANO could not update',
    failedBody: (v) => `The update to ${v} did not install. NANO will try again; your data is safe. Settings → About shows what to do.`,
    trayItem: (v) => `Restart and update to ${v}`,
  },
  pt: {
    readyTitle: 'Atualização do NANO pronta',
    readyBody: (v) => `O NANO ${v} fica instalado quando fechares o NANO. Para instalar já, usa "Reiniciar e atualizar" nas Definições ou no menu do tabuleiro.`,
    doneTitle: 'O NANO está atualizado',
    doneBody: (v) => `O NANO foi atualizado para a versão ${v}.`,
    failedTitle: 'O NANO não conseguiu atualizar',
    failedBody: (v) => `A atualização para ${v} não ficou instalada. O NANO volta a tentar; os teus dados estão seguros. Em Definições → Sobre vês o que fazer.`,
    trayItem: (v) => `Reiniciar e atualizar para ${v}`,
  },
};

function updateText() {
  let language = 'en';
  try { language = String(app.getLocale() || '').toLowerCase().startsWith('pt') ? 'pt' : 'en'; } catch (_) { /* en */ }
  return UPDATE_TEXT[language];
}

function onUpdateState(state) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    try { mainWindow.webContents.send('nano:update-state', state); } catch (_) { /* window going away */ }
  }
  refreshTrayMenu();
  const text = updateText();
  if (state.status === 'ready' && state.available && updateNotifiedFor !== state.available.version) {
    updateNotifiedFor = state.available.version;
    showNativeNotification({ title: text.readyTitle, body: text.readyBody(state.available.version), silent: true });
  }
}

function fetchForUpdates() {
  // Chromium's network stack: the system proxy and the Windows certificate
  // store, like the browser. Node's fetch is the fallback (and the tests').
  const electron = require('electron');
  if (electron.net && typeof electron.net.fetch === 'function') return (...args) => electron.net.fetch(...args);
  return (...args) => fetch(...args);
}

function startUpdater() {
  if (updater) return;
  updater = new Updater({
    currentVersion: app.getVersion(),
    canInstall: !IS_DEV && process.platform === 'win32',
    dataDir: canonicalDataDir(process.env),
    fetch: fetchForUpdates(),
    spawn: require('child_process').spawn,
    fs,
    log,
    onChange: onUpdateState,
  });
  updater.start({ enabled: shellState.autoUpdate });
  const result = updater.publicState().lastResult;
  if (result && (result.ok || !result.reported)) {
    const text = updateText();
    showNativeNotification(result.ok
      ? { title: text.doneTitle, body: text.doneBody(result.version), silent: true }
      : { title: text.failedTitle, body: text.failedBody(result.version), silent: false });
  }
}

/** "Restart and update": quit, install, and open NANO again. */
function restartToUpdate() {
  if (!updater || !updater.isReady()) return false;
  restartForUpdate = true;
  quitNano();
  return true;
}

/**
 * Called while quitting, after the backend has stopped (the embedded Python
 * holds files the installer must replace). Starts the installer only for a
 * verified, ready update and never while Windows is shutting down.
 */
async function installUpdateOnExit() {
  if (!updater || sessionEnding || !updater.isReady()) return false;
  try {
    return await updater.install({ relaunch: restartForUpdate });
  } catch (err) {
    log('Não foi possível instalar a atualização:', err.message);
    return false;
  }
}

/* ── Start with Windows ─────────────────────────────────────────────────── */

function isAutoLaunchEnabled() {
  try { return app.getLoginItemSettings().openAtLogin; } catch (_) { return false; }
}

function setAutoLaunch(enabled) {
  try {
    // NO `openAsHidden`. It was macOS-only, already deprecated and inert on
    // macOS 13+, and Electron 44 removed it outright -- from both the
    // setLoginItemSettings options and the getLoginItemSettings result. On
    // Windows, the only platform Nano ships on, a hidden start has always
    // been the `--hidden` argument below, which main() reads into
    // startedHidden. Dropping it is therefore the same behaviour with a
    // removed option gone, not a change of behaviour.
    app.setLoginItemSettings({
      openAtLogin: Boolean(enabled),
      args: ['--hidden'],
    });
  } catch (err) {
    log('Não foi possível alterar o arranque automático:', err.message);
  }
  return isAutoLaunchEnabled();
}

function autoLaunchInfo() {
  return {
    enabled: isAutoLaunchEnabled(),
    // In development the login item would point at electron.exe, not at Nano,
    // so offering it as working would be a lie.
    supported: !IS_DEV,
    reason: IS_DEV ? 'Disponível apenas na aplicação instalada.' : null,
  };
}

/* ── Tray ───────────────────────────────────────────────────────────────── */

function trayImage() {
  // A 32 px PNG with no tile: Windows composites it against the taskbar, and a
  // second dark square inside the tray reads as a rendering bug.
  const source = fs.existsSync(TRAY_ICON_PATH) ? TRAY_ICON_PATH : ICON_PATH;
  if (!fs.existsSync(source)) return nativeImage.createEmpty();
  const image = nativeImage.createFromPath(source);
  return image.isEmpty() ? nativeImage.createEmpty() : image.resize({ width: 16, height: 16 });
}

function createTray() {
  try {
    tray = new Tray(trayImage());
    tray.setToolTip('Nano');
    tray.on('click', () => {
      if (mainWindow && mainWindow.isVisible() && mainWindow.isFocused()) mainWindow.hide();
      else showMainWindow();
    });
    tray.on('double-click', showMainWindow);
    refreshTrayMenu();
  } catch (err) {
    log('Tabuleiro indisponível:', err.message);
  }
}

/** The tray menu follows the interface language, like the island. */
const TRAY_TEXT = {
  en: {
    talk: (keys) => `Talk to NANO   (${keys})`,
    talkUnavailable: 'Talk to NANO   (shortcut unavailable)',
    open: 'Open NANO',
    hide: 'Hide NANO',
    island: 'NANO island',
    islandOff: 'NANO island  (off)',
    ambient: 'Keep the island on screen',
    wake: (phrase) => `Listen for "${phrase}"`,
    tipWake: (phrase) => `\nListening for "${phrase}" (on this PC only)`,
    autoLaunch: 'Start with Windows',
    restart: 'Restart NANO',
    quit: 'Quit NANO',
    tip: (keys) => `NANO — ${keys} to talk`,
    tipUnavailable: 'NANO — global shortcut unavailable',
    tipIslandOff: '\n(island off)',
  },
  pt: {
    talk: (keys) => `Falar com o NANO   (${keys})`,
    talkUnavailable: 'Falar com o NANO   (atalho indisponível)',
    open: 'Abrir o NANO',
    hide: 'Ocultar o NANO',
    island: 'Ilha do NANO',
    islandOff: 'Ilha do NANO  (desligada)',
    ambient: 'Ilha sempre no ecrã',
    wake: (phrase) => `Ouvir "${phrase}"`,
    tipWake: (phrase) => `\nA ouvir "${phrase}" (só neste PC)`,
    autoLaunch: 'Iniciar com o Windows',
    restart: 'Reiniciar o NANO',
    quit: 'Sair do NANO',
    tip: (keys) => `NANO — ${keys} para falar`,
    tipUnavailable: 'NANO — atalho global indisponível',
    tipIslandOff: '\n(ilha desligada)',
  },
};

function refreshTrayMenu() {
  if (!tray || tray.isDestroyed()) return;
  const text = TRAY_TEXT[islandLanguage || systemLanguage()] || TRAY_TEXT.en;
  const auto = autoLaunchInfo();
  const shortcutLabel = shortcutState.registered
    ? text.talk(humanAccelerator())
    : text.talkUnavailable;

  const updateItems = updater && updater.isReady()
    ? [{ label: updateText().trayItem(updater.publicState().available.version), click: restartToUpdate }, { type: 'separator' }]
    : [];

  tray.setContextMenu(Menu.buildFromTemplate([
    ...updateItems,
    { label: text.open, click: showMainWindow },
    {
      label: text.hide,
      enabled: Boolean(mainWindow && mainWindow.isVisible()),
      click: () => { rememberBounds(); mainWindow && mainWindow.hide(); },
    },
    { type: 'separator' },
    { label: shortcutLabel, click: () => triggerVoiceTurn('hotkey') },
    {
      // Named as a state, not just a verb, so an unticked box reads as "this
      // is currently off" rather than "click to show it once".
      label: shellState.overlayEnabled ? text.island : text.islandOff,
      type: 'checkbox',
      checked: shellState.overlayEnabled,
      click: (item) => {
        shellState.overlayEnabled = item.checked;
        saveShellState();
        syncPeekWatcher();
        renderIsland();
        refreshTrayMenu();
        reportShortcutToBackend();
      },
    },
    {
      label: text.ambient,
      type: 'checkbox',
      checked: shellState.islandMode === 'always',
      enabled: shellState.overlayEnabled,
      click: (item) => { setIslandAlwaysVisible(item.checked); },
    },
    {
      // On/off for listening by name. Off is one click away, always.
      label: text.wake(wakeDisplayPhrase()),
      type: 'checkbox',
      checked: wakeState.enabled,
      enabled: Boolean(backend && backend.running),
      click: (item) => { setWakePhrase(item.checked); },
    },
    { type: 'separator' },
    {
      label: text.autoLaunch,
      type: 'checkbox',
      checked: auto.enabled,
      enabled: auto.supported,
      // Never enabled on the user's behalf: this is opt-in, and it only ever
      // changes because someone clicked it here or in Settings.
      click: (item) => { setAutoLaunch(item.checked); refreshTrayMenu(); reportShortcutToBackend(); },
    },
    { type: 'separator' },
    { label: text.restart, click: () => { restartNano().catch((err) => log(err.message)); } },
    { label: text.quit, click: () => { quitNano(); } },
  ]));

  // The island being off is easy to acquire and was previously invisible:
  // it simply never appeared. Say so where it was switched off.
  const panelNote = shellState.overlayEnabled ? '' : text.tipIslandOff;
  const wakeNote = wakeState.listening ? text.tipWake(wakeDisplayPhrase()) : '';
  tray.setToolTip((shortcutState.registered
    ? text.tip(humanAccelerator())
    : text.tipUnavailable) + panelNote + wakeNote);
}

/** "ei nano" -> "Ei Nano", for the tray and the island. */
function wakeDisplayPhrase() {
  const phrase = String(wakeState.phrase || '').trim()
    || ((islandLanguage || systemLanguage()) === 'pt' ? 'ei nano' : 'hey nano');
  return phrase.replace(/\b\p{L}/gu, (letter) => letter.toUpperCase()).slice(0, 40);
}

/** The tray switch: ask the backend to turn listening by name on or off. */
function setWakePhrase(enabled) {
  if (!backend || !backend.running) return;
  backend.request('set_wake_phrase', { enabled: enabled === true })
    .catch((err) => log('set_wake_phrase failed:', err.message));
}

/* ── Restart and quit ───────────────────────────────────────────────────── */

let restarting = false;

function replaceMainWindow(port) {
  rememberBounds();
  const previous = mainWindow;
  if (previous && !previous.isDestroyed()) previous.destroy();
  frontendReady = false;
  // Rebind navigation, recovery and CSP to the new backend's ephemeral origin.
  createMainWindow(port);
}

async function restartNano() {
  if (restarting) return;
  restarting = true;
  unregisterShortcut();
  hideOverlay();
  try {
    await stopBackend();
    const port = await startBackend();
    replaceMainWindow(port);
    registerShortcut();
    log('O motor do Nano foi reiniciado.');
  } catch (err) {
    recordFailure('backend_start_failed');
    await stopBackend();
    dialog.showErrorBox('Nano — falha ao reiniciar',
      'Código: backend_start_failed. Sai do Nano e volta a abrir. Se continuar, verifica o espaço livre e a pasta de dados indicada em Sobre, ou reinstala a mesma versão.');
  } finally {
    restarting = false;
  }
}

/**
 * The only way Nano actually exits.
 *
 * Order matters: release the shortcut before the windows go, so the accelerator
 * is free even if a later step throws; stop the backend before the app quits,
 * so it is never orphaned.
 */
function quitNano() {
  if (isQuitting) return;
  isQuitting = true;
  unregisterShortcut();
  globalShortcut.unregisterAll();
  clearTimeout(overlayHideTimer);
  clearTimeout(saveBoundsTimer);
  rememberBounds();
  hideOverlay(true);

  if (updater) updater.stop();

  stopBackend()
    .catch((err) => log('Falha ao encerrar o motor:', err.message))
    // A ready update installs now, with the backend already stopped.
    .then(() => installUpdateOnExit())
    .catch(() => false)
    .finally(() => {
      if (tray && !tray.isDestroyed()) { tray.destroy(); tray = null; }
      // Ollama is NOT stopped: Nano did not start it exclusively and other
      // tools may be using it. Nano only tears down what it owns.
      app.quit();
    });
}

/* ── Renderer IPC: named operations only ────────────────────────────────── */
/*
 * Every channel below is a specific window/desktop action with a fixed shape.
 * There is no pass-through, no channel taken from the renderer, and nothing
 * that touches the filesystem, the shell or the tool pipeline. Adding a
 * generic invoke() here would hand the page an execution path it must never
 * have -- the preload's whole job is to make that impossible.
 */
/**
 * Registered at module load, deliberately: it makes the exact set of channels
 * the renderer can reach an inspectable property of this file, which is what
 * the security test asserts. Handlers are inert until a window exists.
 */
function registerRendererIpc() {
  ipcMain.on('nano:minimize', () => mainWindow && mainWindow.minimize());
  ipcMain.on('nano:toggle-maximize', () => {
    if (!mainWindow) return;
    if (mainWindow.isMaximized()) mainWindow.unmaximize();
    else mainWindow.maximize();
  });
  ipcMain.on('nano:hide', () => {
    if (!mainWindow) return;
    rememberBounds();
    mainWindow.hide();
    notifyHiddenToTrayOnce();
  });

  ipcMain.handle('nano:window-state', () => currentWindowState());

  ipcMain.handle('nano:desktop-status', () => ({
    ...desktopDiagnostics(),
    isDesktop: true,
    version: app.getVersion(),
    shortcut: humanAccelerator(),
    shortcutRegistered: shortcutState.registered,
    shortcutError: shortcutState.error,
    overlayEnabled: shellState.overlayEnabled,
    islandAlwaysVisible: shellState.islandMode === 'always',
    autoLaunch: autoLaunchInfo(),
    dataDir: canonicalDataDir(process.env),
    packaged: !IS_DEV,
  }));

  ipcMain.handle('nano:set-overlay-enabled', (_event, enabled) => {
    shellState.overlayEnabled = Boolean(enabled);
    saveShellState();
    syncPeekWatcher();
    renderIsland();
    refreshTrayMenu();
    reportShortcutToBackend();
    return shellState.overlayEnabled;
  });

  ipcMain.handle('nano:set-island-always-visible', (_event, enabled) => setIslandAlwaysVisible(enabled));

  // The island's own two signals (overlay/preload.js). Both check that they
  // really come from the island window, and neither carries data that could
  // choose what happens: one toggles click-through, the other opens NANO.
  ipcMain.on('nano:island-pointer', onIslandPointer);
  ipcMain.on('nano:island-open', onIslandOpen);
  ipcMain.on('nano:island-media', onIslandMedia);

  ipcMain.handle('nano:set-auto-launch', (_event, enabled) => {
    const state = setAutoLaunch(Boolean(enabled));
    refreshTrayMenu();
    reportShortcutToBackend();
    return { ...autoLaunchInfo(), enabled: state };
  });

  // Re-attempting registration is useful after the user closes whatever was
  // holding the accelerator. It is idempotent and reports the real outcome.
  ipcMain.handle('nano:retry-shortcut', () => {
    registerShortcut();
    return { registered: shortcutState.registered, error: shortcutState.error };
  });

  ipcMain.handle('nano:quit', () => { quitNano(); return true; });

  // Automatic updates. Fixed verbs only: the page can ask for the state, a
  // check, the switch, and "restart and update" -- never a URL, a file or an
  // installer of its choosing. What gets installed is decided by the signed
  // feed alone (lib/updater.js).
  ipcMain.handle('nano:update-status', () => (updater ? updater.publicState() : null));
  ipcMain.handle('nano:update-check', () => (updater ? updater.check({ manual: true }) : null));
  ipcMain.handle('nano:update-install', () => restartToUpdate());
  ipcMain.handle('nano:set-auto-update', (_event, enabled) => {
    shellState.autoUpdate = Boolean(enabled);
    saveShellState();
    if (updater) updater.setEnabled(shellState.autoUpdate);
    return shellState.autoUpdate;
  });
  // A fixed address, so the page can never choose what is opened.
  ipcMain.handle('nano:open-update-page', () => { shell.openExternal(RELEASES_URL).catch(() => {}); return true; });
}

/* ── Boot ───────────────────────────────────────────────────────────────── */

registerRendererIpc();

const gotLock = app.requestSingleInstanceLock();

if (!gotLock) {
  // A second launch is not an error and must not look like one: this process
  // exits quietly and the running Nano brings itself forward below.
  app.quit();
} else {
  app.on('second-instance', () => {
    log('Segunda instância pedida; a focar a janela existente.');
    showMainWindow();
  });

  // Windows only shows toasts from an app with an identity; this must match
  // build.appId so the notification carries Nano's name and icon.
  if (process.platform === 'win32' && typeof app.setAppUserModelId === 'function') {
    app.setAppUserModelId('com.nano.assistant');
  }

  app.whenReady().then(async () => {
    loadShellState();

    if (!startedHidden) {
      try {
        if (app.getLoginItemSettings().wasOpenedAtLogin) startedHidden = true;
      } catch (_) { /* not a login launch */ }
    }

    try {
      if (await backendAlreadyRunning()) {
        // Not the "launched twice" case -- Electron's own lock handles that.
        // This is a backend started outside this shell (typically NANO.bat).
        // Two backends would mean two microphone owners, so we refuse rather
        // than produce a broken second Nano.
        dialog.showErrorBox(
          'Nano já está a correr',
          'Já existe um motor do Nano em execução (provavelmente iniciado pelo NANO.bat).\n\n' +
          'Fecha essa janela e volta a abrir o Nano Desktop.',
        );
        isQuitting = true;
        app.quit();
        return;
      }

      // The window appears straight away with the startup screen, so a slow
      // backend reads as "starting" rather than as nothing happening at all.
      createMainWindow();
      const port = await startBackend();
      attachMainWindow(port);
      createTray();
      createOverlayWindow();
      registerShortcut();
      // The island (and calling it with the mouse) starts once NANO is up.
      islandReady = true;
      syncPeekWatcher();
      renderIsland();
      startUpdater();
      log(`Nano Desktop pronto em http://127.0.0.1:${port}`);
    } catch (err) {
      recordFailure('backend_start_failed');
      log('Erro fatal no arranque: backend_start_failed', err && err.message ? `(${err.message})` : '');
      showStartupScreen(mainWindow, 'error', 'backend_start_failed');
      dialog.showErrorBox(
        'Nano — não foi possível arrancar',
        'Código: backend_start_failed. O motor ou a interface não ficaram prontos.\n\n' +
        'Fecha outras instâncias do Nano e tenta novamente. Verifica o espaço livre e as permissões ' +
        'da pasta %LOCALAPPDATA%\\NanoAssistant. Se continuar, reinstala a mesma versão; os dados são preservados.\n\n' +
        'Podes partilhar logs/startup-diagnostics.json dessa pasta: contém apenas dados técnicos, sem conversas nem chaves.',
      );
      isQuitting = true;
      await stopBackend();
      app.quit();
    }
  });
}

app.on('activate', () => { if (mainWindow) showMainWindow(); });

// A crashing GPU process is the usual cause of a window that stays black even
// though the page loaded. Chromium retries and then falls back to software;
// say so in the console so NANO_DISABLE_GPU=1 can be suggested.
app.on('child-process-gone', (_event, details) => {
  if (details && details.type === 'GPU') {
    log(`O processo gráfico (GPU) parou (${details.reason}). Se a janela ficar preta, arranca com NANO_DISABLE_GPU=1.`);
  }
});

// Hiding the window must not quit the app: the tray and the global shortcut
// are the point of keeping it alive.
app.on('window-all-closed', () => { if (isQuitting) app.quit(); });

app.on('before-quit', (event) => {
  if (isQuitting) return;
  event.preventDefault();
  quitNano();
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  if (tray && !tray.isDestroyed()) tray.destroy();
});

// Exported for the test suite, which asserts the desktop shell and the Python
// backend still agree on the accelerator, the instance-lock port and -- most
// importantly -- the one data directory.
module.exports = {
  ACTIVATION_ACCELERATOR, OVERLAY_SIZE, PY_INSTANCE_LOCK_PORT, backendEnv,
  // The production Content Security Policy. Exported so electron/test/csp-check.js
  // can verify the REAL policy against the REAL bundle rather than a copy of it
  // that could drift from what ships.
  contentSecurityPolicy,
  // Exercised by electron/test/overlay.test.js against a stubbed Electron, so
  // the overlay's independence from the main window is a tested property of
  // this file rather than a claim in a comment. Not part of any runtime API.
  __test: {
    applyOverlayView,
    renderIsland,
    hideOverlay,
    onIslandPointer,
    onIslandOpen,
    onIslandMedia,
    setIslandAlwaysVisible,
    syncPeekWatcher,
    peekTick,
    setBackend: (value) => { backend = value; },
    setIslandReady: (value) => { islandReady = Boolean(value); },
    createOverlayWindow,
    createMainWindow,
    attachMainWindow,
    showStartupScreen,
    replaceMainWindow,
    onBackendEvent,
    quitNano,
    desktopDiagnostics,
    state: () => ({ overlayWindow, mainWindow, overlayReady, shellState, islandStack, islandLanguage, peekActive,
      wakeState }),
    setWakePhrase,
    setOverlayEnabled: (value) => { shellState.overlayEnabled = value; },
    reset: () => {
      clearTimeout(overlayHideTimer); clearTimeout(overlayExitTimer); clearTimeout(islandExpiryTimer);
      overlayHideTimer = null; overlayExitTimer = null; islandExpiryTimer = null;
      islandStack = new island.IslandStack(); islandLastSent = ''; islandReady = false; islandLanguage = null;
      overlayWindow = null; mainWindow = null; overlayReady = false;
      pendingOverlayView = null; isQuitting = false;
      frontendReady = false; lastErrorCode = null;
      shellState = { bounds: null, overlayEnabled: true, islandMode: 'hidden', autoUpdate: true };
      clearInterval(peekTimer); peekTimer = null; peekActive = false; peekZoneSince = 0; peekAwaySince = 0;
      islandPointerInside = false;
      wakeState = { enabled: false, listening: false, phrase: '' };
      updater = null; sessionEnding = false; restartForUpdate = false; updateNotifiedFor = null;
    },
  },
};
