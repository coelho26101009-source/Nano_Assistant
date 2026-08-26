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
 *   single-instance lock -> conflicting-backend check -> pick a free port
 *   -> spawn Python -> wait for a control-channel ping -> wait for real HTTP
 *   -> create the window -> tray -> register the shortcut -> create the overlay
 *
 * The window is never created before the backend answers, so it can never open
 * blank; the shortcut is never registered before that either, so a keypress
 * cannot reach a backend that is not listening yet.
 */
'use strict';

const {
  app, BrowserWindow, Menu, Tray, dialog, globalShortcut, ipcMain,
  nativeImage, screen, shell,
} = require('electron');
const path = require('path');
const fs = require('fs');
const net = require('net');
const http = require('http');
const { exec } = require('child_process');

const { NanoBackend } = require('./lib/backend');
const overlayState = require('./lib/overlay-state');
const windowState = require('./lib/window-state');
const { canonicalDataDir, shellStateFile } = require('./lib/paths');

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

// Wide enough for the longest real label plus the visualiser, and no wider:
// the panel is inset 14 px inside this window for its shadow, so the visible
// card is 384x84.
const OVERLAY_SIZE = { width: 412, height: 112 };

/* ── Process state ──────────────────────────────────────────────────────── */

let mainWindow = null;
let overlayWindow = null;
let tray = null;
let backend = null;
let isQuitting = false;
let startedHidden = process.argv.includes('--hidden');
let overlayHideTimer = null;
let saveBoundsTimer = null;
/** The overlay renderer has loaded and can be sent a state. */
let overlayReady = false;
/** The last view that arrived before the renderer was ready. */
let pendingOverlayView = null;

/** Real, measured shortcut state. Never assumed to have worked. */
const shortcutState = { accelerator: ACTIVATION_ACCELERATOR, registered: false, error: null };

/** Persisted desktop-shell preferences. Not user data — that lives in DATA_DIR. */
let shellState = { bounds: null, overlayEnabled: true };

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
  const userEnv = path.join(app.getPath('userData'), '.env');
  const devEnv = path.join(APP_ROOT, '.env');
  const envFile = fs.existsSync(userEnv) ? userEnv : (IS_DEV && fs.existsSync(devEnv) ? devEnv : null);
  if (!envFile) return env;
  for (const line of fs.readFileSync(envFile, 'utf8').split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) continue;
    const index = trimmed.indexOf('=');
    env[trimmed.slice(0, index).trim()] = trimmed.slice(index + 1).trim();
  }
  return env;
}

function backendEnv() {
  // ONE data directory, and it is Python's own definition mirrored here. See
  // lib/paths.js for the two-locations bug this closes.
  const dataDir = canonicalDataDir(process.env);
  return {
    ...loadEnvFile(),
    NANO_MODE: 'electron',
    NANO_APP_ROOT: APP_ROOT,
    NANO_DATA_DIR: dataDir,
    // Compatibility aliases; core/app_paths.py reads NANO_* first.
    HELIOS_MODE: 'electron',
    HELIOS_APP_ROOT: APP_ROOT,
    HELIOS_DATA_DIR: dataDir,
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
function waitForHttp(port, timeoutMs = 45000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      if (Date.now() > deadline) {
        reject(new Error('A interface do Nano não ficou disponível a tempo.'));
        return;
      }
      const req = http.get(
        { host: '127.0.0.1', port, path: '/index.html', timeout: 2000 },
        (res) => {
          res.resume();
          if (res.statusCode === 200) resolve();
          else setTimeout(attempt, 300);
        },
      );
      req.on('error', () => setTimeout(attempt, 300));
      req.on('timeout', () => { req.destroy(); setTimeout(attempt, 300); });
    };
    attempt();
  });
}

/* ── Backend lifecycle ──────────────────────────────────────────────────── */

function killTree(pid) {
  if (process.platform === 'win32') {
    try { exec(`taskkill /pid ${pid} /f /t`); } catch (_) { /* already gone */ }
  } else {
    try { process.kill(pid, 'SIGKILL'); } catch (_) { /* already gone */ }
  }
}

async function startBackend() {
  const python = findPython();
  if (!fs.existsSync(MAIN_PY)) {
    throw new Error(`O core do Nano não foi encontrado em ${MAIN_PY}.`);
  }
  const port = await getFreePort();

  backend = new NanoBackend({ log: (line) => log(line) });
  backend.on('event', onBackendEvent);
  backend.on('exit', ({ code, expected }) => {
    if (expected || isQuitting) return;
    log(`O motor terminou inesperadamente (${code}).`);
    unregisterShortcut();
    hideOverlay(true);
    if (mainWindow && !mainWindow.isDestroyed()) {
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
  await waitForHttp(port);
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
  }
}

/* ── Main window ────────────────────────────────────────────────────────── */

function appIcon() {
  return fs.existsSync(ICON_PATH) ? ICON_PATH : undefined;
}

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
    // Matches --bg-base, so the first paint is Nano-coloured rather than white.
    backgroundColor: '#05070A',
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

  const origin = `http://127.0.0.1:${port}`;
  mainWindow.loadURL(`${origin}/index.html`);
  hardenWebContents(mainWindow.webContents, origin);

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
  mainWindow.on('focus', reportWindowState);
  mainWindow.on('blur', reportWindowState);

  mainWindow.on('resize', rememberBoundsSoon);
  mainWindow.on('move', rememberBoundsSoon);

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

  mainWindow.webContents.on('did-fail-load', (_e, code, description, url, isMainFrame) => {
    if (!isMainFrame || isQuitting) return;
    log(`Falha a carregar a UI (${code} ${description}); nova tentativa.`);
    setTimeout(() => {
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.loadURL(`${origin}/index.html`);
    }, 1500);
  });
}

/**
 * Navigation, popups, permissions and zoom, locked down in one place.
 *
 * Nano's window shows Nano. It may not be navigated to another origin by a
 * link, a redirect or a script, and it may not open child windows: an external
 * URL is handed to the OS browser after validation, or refused.
 */
function hardenWebContents(contents, origin) {
  contents.on('will-navigate', (event, url) => {
    if (!url.startsWith(`${origin}/`) && url !== origin) {
      event.preventDefault();
      log('Navegação bloqueada:', url);
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
        log('Ligação externa recusada:', parsed.protocol);
      }
    } catch (_) {
      log('Ligação externa inválida recusada.');
    }
    return { action: 'deny' };
  });

  contents.on('will-attach-webview', (event) => event.preventDefault());

  // The renderer needs no device permissions: Python owns the microphone, and
  // nothing here should be able to ask for the camera, the clipboard or
  // notifications behind the user's back.
  contents.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));

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

/* ── Voice overlay ──────────────────────────────────────────────────────── */

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
    // not pull the user out of what they are doing.
    focusable: false,
    acceptFirstMouse: false,
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
  // because the panel starts at data-visible="false". That is a real race on
  // the FIRST voice turn after launch. Remember the last view and replay it
  // the moment the renderer is ready.
  overlayReady = false;
  overlayWindow.webContents.once('did-finish-load', () => {
    overlayReady = true;
    if (pendingOverlayView) applyOverlayView(pendingOverlayView);
  });

  overlayWindow.loadFile(path.join(__dirname, 'overlay', 'overlay.html'));
  // Above full-screen apps too, otherwise the one moment it matters -- a game
  // in the foreground -- is the one moment it is invisible.
  overlayWindow.setAlwaysOnTop(true, 'screen-saver');
  overlayWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
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
  const { x, y } = windowState.overlayPosition(display.workArea, OVERLAY_SIZE);
  overlayWindow.setBounds({ ...OVERLAY_SIZE, x, y });
}

/**
 * Show the overlay for one view.
 *
 * INDEPENDENCE IS THE WHOLE POINT. Nothing in here reads mainWindow, and
 * nothing in here restores or focuses it: the overlay is a separate top-level
 * BrowserWindow with no `parent:` option, so Windows never minimises or hides
 * it alongside the main window. Measured in all three states -- main visible,
 * minimised, and hidden to the tray -- the overlay shows identically.
 *
 * The main window's state IS logged next to every show, though. Not because
 * the code consults it, but because a future correlation between the two is
 * exactly the bug this function must never grow, and a log line is the only
 * thing that turns that from an opinion into evidence.
 */
function applyOverlayView(view) {
  if (!view) return;
  clearTimeout(overlayHideTimer);

  if (!view.visible) { hideOverlay(); return; }
  if (!shellState.overlayEnabled) {
    // NEVER SILENT. This flag is persisted in desktop-state.json and is
    // switchable from two places (the tray menu and Settings), so a stale
    // "off" is easy to acquire and was previously invisible: the overlay
    // simply never appeared and nothing anywhere said why.
    log('overlay: SUPPRESSED - "Mostrar o painel de voz" is off '
      + '(tray menu, or Settings > Geral > Modo desktop)');
    return;
  }
  if (!overlayWindow || overlayWindow.isDestroyed()) createOverlayWindow();

  if (!overlayReady) {
    // The renderer is still loading; replay this once it is up.
    pendingOverlayView = view;
    log(`overlay: deferred until the renderer loads (state=${view.state})`);
    return;
  }
  pendingOverlayView = null;

  positionOverlay();
  overlayWindow.webContents.send('nano:overlay-state', view);
  // showInactive: appear without stealing focus from whatever the user is in,
  // and WITHOUT restoring the main window.
  if (!overlayWindow.isVisible()) overlayWindow.showInactive();

  log(`overlay: state=${view.state} shown=${overlayWindow.isVisible()} `
    + `mainVisible=${mainWindow ? mainWindow.isVisible() : 'none'} `
    + `mainMinimized=${mainWindow ? mainWindow.isMinimized() : 'none'}`);

  if (view.hideAfterMs > 0) {
    overlayHideTimer = setTimeout(() => hideOverlay(), view.hideAfterMs);
  }
}

function hideOverlay(destroy = false) {
  clearTimeout(overlayHideTimer);
  pendingOverlayView = null;
  if (!overlayWindow || overlayWindow.isDestroyed()) return;
  overlayWindow.hide();
  // Stop the renderer animating a window nobody can see.
  overlayWindow.webContents.send('nano:overlay-state', overlayState.HIDDEN);
  if (destroy) { overlayWindow.destroy(); overlayWindow = null; overlayReady = false; }
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

/* ── Start with Windows ─────────────────────────────────────────────────── */

function isAutoLaunchEnabled() {
  try { return app.getLoginItemSettings().openAtLogin; } catch (_) { return false; }
}

function setAutoLaunch(enabled) {
  try {
    app.setLoginItemSettings({
      openAtLogin: Boolean(enabled),
      openAsHidden: true,
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

function refreshTrayMenu() {
  if (!tray || tray.isDestroyed()) return;
  const auto = autoLaunchInfo();
  const shortcutLabel = shortcutState.registered
    ? `Falar com o Nano   (${humanAccelerator()})`
    : `Falar com o Nano   (atalho indisponível)`;

  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Abrir o Nano', click: showMainWindow },
    {
      label: 'Ocultar o Nano',
      enabled: Boolean(mainWindow && mainWindow.isVisible()),
      click: () => { rememberBounds(); mainWindow && mainWindow.hide(); },
    },
    { type: 'separator' },
    { label: shortcutLabel, click: () => triggerVoiceTurn('hotkey') },
    {
      // Named as a state, not just a verb, so an unticked box reads as "this
      // is currently off" rather than "click to show it once".
      label: shellState.overlayEnabled
        ? 'Painel de voz durante a fala'
        : 'Painel de voz durante a fala  (desligado)',
      type: 'checkbox',
      checked: shellState.overlayEnabled,
      click: (item) => {
        shellState.overlayEnabled = item.checked;
        saveShellState();
        if (!item.checked) hideOverlay();
        reportShortcutToBackend();
      },
    },
    { type: 'separator' },
    {
      label: 'Iniciar com o Windows',
      type: 'checkbox',
      checked: auto.enabled,
      enabled: auto.supported,
      // Never enabled on the user's behalf: this is opt-in, and it only ever
      // changes because someone clicked it here or in Settings.
      click: (item) => { setAutoLaunch(item.checked); refreshTrayMenu(); reportShortcutToBackend(); },
    },
    { type: 'separator' },
    { label: 'Reiniciar o Nano', click: () => { restartNano().catch((err) => log(err.message)); } },
    { label: 'Sair do Nano', click: () => { quitNano(); } },
  ]));

  // The voice panel being off is easy to acquire and was previously invisible:
  // the overlay simply never appeared. Say so where it was switched off.
  const panelNote = shellState.overlayEnabled ? '' : '\n(painel de voz desligado)';
  tray.setToolTip((shortcutState.registered
    ? `Nano — ${humanAccelerator()} para falar`
    : 'Nano — atalho global indisponível') + panelNote);
}

/* ── Restart and quit ───────────────────────────────────────────────────── */

let restarting = false;

async function restartNano() {
  if (restarting) return;
  restarting = true;
  unregisterShortcut();
  hideOverlay();
  try {
    await stopBackend();
    const port = await startBackend();
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL(`http://127.0.0.1:${port}/index.html`);
    }
    registerShortcut();
    log('O motor do Nano foi reiniciado.');
  } catch (err) {
    dialog.showErrorBox('Nano — falha ao reiniciar', err.message);
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

  stopBackend()
    .catch((err) => log('Falha ao encerrar o motor:', err.message))
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
    isDesktop: true,
    version: app.getVersion(),
    shortcut: humanAccelerator(),
    shortcutRegistered: shortcutState.registered,
    shortcutError: shortcutState.error,
    overlayEnabled: shellState.overlayEnabled,
    autoLaunch: autoLaunchInfo(),
    dataDir: canonicalDataDir(process.env),
    packaged: !IS_DEV,
  }));

  ipcMain.handle('nano:set-overlay-enabled', (_event, enabled) => {
    shellState.overlayEnabled = Boolean(enabled);
    saveShellState();
    if (!shellState.overlayEnabled) hideOverlay();
    refreshTrayMenu();
    reportShortcutToBackend();
    return shellState.overlayEnabled;
  });

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

      const port = await startBackend();
      createMainWindow(port);
      createTray();
      createOverlayWindow();
      registerShortcut();
      log(`Nano Desktop pronto em http://127.0.0.1:${port}`);
    } catch (err) {
      log('Erro fatal no arranque:', err.message);
      dialog.showErrorBox(
        'Nano — não foi possível arrancar',
        `${err.message}\n\nO Nano não abriu nenhuma janela porque o motor não ficou pronto.`,
      );
      isQuitting = true;
      await stopBackend();
      app.quit();
    }
  });
}

app.on('activate', () => { if (mainWindow) showMainWindow(); });

// Hiding the window must not quit the app: the tray and the global shortcut
// are the point of keeping it alive.
app.on('window-all-closed', () => { if (isQuitting) app.quit(); });

app.on('before-quit', () => { isQuitting = true; });

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  if (tray && !tray.isDestroyed()) tray.destroy();
});

// Exported for the test suite, which asserts the desktop shell and the Python
// backend still agree on the accelerator, the instance-lock port and -- most
// importantly -- the one data directory.
module.exports = {
  ACTIVATION_ACCELERATOR, OVERLAY_SIZE, PY_INSTANCE_LOCK_PORT, backendEnv,
  // Exercised by electron/test/overlay.test.js against a stubbed Electron, so
  // the overlay's independence from the main window is a tested property of
  // this file rather than a claim in a comment. Not part of any runtime API.
  __test: {
    applyOverlayView,
    hideOverlay,
    createOverlayWindow,
    createMainWindow,
    onBackendEvent,
    quitNano,
    state: () => ({ overlayWindow, mainWindow, overlayReady, shellState }),
    setOverlayEnabled: (value) => { shellState.overlayEnabled = value; },
    reset: () => {
      overlayWindow = null; mainWindow = null; overlayReady = false;
      pendingOverlayView = null; isQuitting = false;
      shellState = { bounds: null, overlayEnabled: true };
    },
  },
};
