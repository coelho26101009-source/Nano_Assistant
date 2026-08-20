const { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage, shell, dialog } = require('electron');
const path = require('path');
const { spawn, exec } = require('child_process');
const fs = require('fs');
const net = require('net');

let mainWindow = null, tray = null, pythonProcess = null, isQuitting = false;
const GOT_LOCK = app.requestSingleInstanceLock();
if (!GOT_LOCK) app.quit();
let startedHidden = process.argv.includes('--hidden');
const IS_DEV = !app.isPackaged;
const APP_ROOT = IS_DEV ? path.join(__dirname, '..') : path.join(process.resourcesPath, 'app');
const ICON_PATH = path.join(__dirname, 'assets', 'icon.ico');
const MAIN_PY = path.join(APP_ROOT, 'core', 'main.py');

function findPython() {
  const candidates = [
    path.join(APP_ROOT, 'runtime', 'python', 'python.exe'),
    path.join(APP_ROOT, 'runtime', 'python', 'Scripts', 'python.exe'),
    path.join(APP_ROOT, '.venv', 'Scripts', 'python.exe'),
  ];
  for (const candidate of candidates) if (fs.existsSync(candidate)) return candidate;
  if (IS_DEV) return 'py';
  throw new Error('Runtime Python do Nano não encontrado. Reinstala a aplicação.');
}

function loadEnv() {
  const env = { ...process.env };
  const userEnv = path.join(app.getPath('userData'), '.env');
  const devEnv = path.join(APP_ROOT, '.env');
  const envFile = fs.existsSync(userEnv) ? userEnv : (IS_DEV && fs.existsSync(devEnv) ? devEnv : null);
  if (!envFile) return env;
  for (const line of fs.readFileSync(envFile, 'utf8').split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith('#') || !t.includes('=')) continue;
    const idx = t.indexOf('=');
    env[t.slice(0, idx).trim()] = t.slice(idx + 1).trim();
  }
  return env;
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const p = srv.address().port;
      srv.close(() => resolve(p));
    });
  });
}

function waitForServer(port, maxMs = 45000) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + maxMs;
    const tryConnect = () => {
      if (Date.now() > deadline) return reject(new Error('Servidor do Nano não ficou pronto a tempo.'));
      const socket = new net.Socket();
      socket.setTimeout(500);
      socket.once('connect', () => { socket.destroy(); setTimeout(resolve, 300); });
      socket.once('error', () => { socket.destroy(); setTimeout(tryConnect, 300); });
      socket.once('timeout', () => { socket.destroy(); setTimeout(tryConnect, 300); });
      socket.connect(port, '127.0.0.1');
    };
    tryConnect();
  });
}

function launchPython(port) {
  return new Promise((resolve, reject) => {
    let pythonCmd;
    try { pythonCmd = findPython(); } catch (err) { reject(err); return; }
    if (!fs.existsSync(MAIN_PY)) { reject(new Error('Core do Nano não encontrado.')); return; }
    const env = {
      ...loadEnv(),
      // Current names. The HELIOS_* duplicates are kept as a compatibility
      // fallback (core/app_paths.py reads NANO_* first, HELIOS_* second).
      NANO_MODE: 'electron',
      NANO_APP_ROOT: APP_ROOT,
      NANO_DATA_DIR: path.join(app.getPath('userData'), 'data'),
      HELIOS_MODE: 'electron',
      HELIOS_APP_ROOT: APP_ROOT,
      HELIOS_DATA_DIR: path.join(app.getPath('userData'), 'data'),
      PYTHONPATH: [APP_ROOT, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
      PYTHONUNBUFFERED: '1',
    };
    pythonProcess = spawn(pythonCmd, [MAIN_PY, '--mode', 'electron', '--port', String(port)], {
      cwd: APP_ROOT, env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true,
    });
    let resolved = false;
    const onData = data => {
      for (const line of data.toString().split('\n').filter(Boolean)) {
        console.log('[NANO]', line.trim());
        if ((line.includes('NANO_PORT=') || line.includes('HELIOS_PORT=')) && !resolved) { resolved = true; resolve(); }
      }
    };
    pythonProcess.stdout.on('data', onData);
    pythonProcess.stderr.on('data', onData);
    pythonProcess.on('error', err => { if (!resolved) { resolved = true; reject(err); } });
    pythonProcess.on('exit', code => {
      if (!resolved) { resolved = true; reject(new Error(`Motor do Nano terminou (${code})`)); }
      else if (!isQuitting) showOffline(code);
    });
    setTimeout(() => { if (!resolved) { resolved = true; resolve(); } }, 20000);
  });
}

function showOffline(code) {
  try {
    mainWindow?.webContents.executeJavaScript(`document.body.innerHTML='<h1>NANO OFFLINE</h1><p>O motor terminou (código ${code}). Reinicia a aplicação.</p>';`);
  } catch (_) {}
}
function isAutoLaunchEnabled() { try { return app.getLoginItemSettings().openAtLogin; } catch (_) { return false; } }
function setAutoLaunch(enabled) { try { app.setLoginItemSettings({ openAtLogin: enabled, openAsHidden: true, args: ['--hidden'] }); } catch (e) { console.warn(e.message); } }

function createWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1440, height: 900, minWidth: 1100, minHeight: 700, frame: false,
    backgroundColor: '#030508', icon: fs.existsSync(ICON_PATH) ? ICON_PATH : undefined, show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: true,
    },
  });
  const url = `http://127.0.0.1:${port}`;
  mainWindow.loadURL(url);
  mainWindow.webContents.on('will-navigate', (event, navigationUrl) => {
    if (!navigationUrl.startsWith(url)) event.preventDefault();
  });
  mainWindow.webContents.setWindowOpenHandler(({ url: externalUrl }) => {
    try { shell.openExternal(externalUrl); } catch (_) {}
    return { action: 'deny' };
  });
  mainWindow.once('ready-to-show', () => { if (!startedHidden) mainWindow.show(); });
  mainWindow.on('close', e => { if (!isQuitting) { e.preventDefault(); mainWindow.hide(); } });
  mainWindow.webContents.on('did-fail-load', () => { if (!isQuitting) setTimeout(() => mainWindow?.loadURL(url), 2000); });
}

function createTray() {
  try {
    const icon = fs.existsSync(ICON_PATH) ? nativeImage.createFromPath(ICON_PATH).resize({ width: 16, height: 16 }) : nativeImage.createEmpty();
    tray = new Tray(icon); tray.setToolTip('H.E.L.I.O.S.'); refreshTrayMenu();
    tray.on('click', () => { mainWindow?.show(); mainWindow?.focus(); });
  } catch (e) { console.warn('[NANO] Tray:', e.message); }
}
function refreshTrayMenu() {
  if (!tray) return;
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '⚡ Abrir Nano', click: () => { mainWindow?.show(); mainWindow?.focus(); } },
    { type: 'separator' },
    { label: '🪟 Iniciar com o Windows', type: 'checkbox', checked: isAutoLaunchEnabled(), click: item => { setAutoLaunch(item.checked); refreshTrayMenu(); } },
    { type: 'separator' },
    { label: '🔄 Reiniciar', click: () => { app.relaunch(); app.exit(0); } },
    { type: 'separator' },
    { label: '✕ Sair', click: () => { isQuitting = true; app.quit(); } },
  ]));
}

ipcMain.on('window-minimize', () => mainWindow?.minimize());
ipcMain.on('window-maximize', () => mainWindow?.isMaximized() ? mainWindow.unmaximize() : mainWindow?.maximize());
ipcMain.on('window-hide', () => mainWindow?.hide());
ipcMain.on('window-close', () => { isQuitting = true; app.quit(); });
ipcMain.handle('autolaunch-get', () => isAutoLaunchEnabled());
ipcMain.handle('autolaunch-set', (_e, enabled) => { setAutoLaunch(!!enabled); refreshTrayMenu(); return isAutoLaunchEnabled(); });
app.on('second-instance', () => { mainWindow?.show(); mainWindow?.focus(); });

if (GOT_LOCK) app.whenReady().then(async () => {
  try {
    if (!startedHidden && app.getLoginItemSettings().wasOpenedAtLogin) startedHidden = true;
    const port = await getFreePort();
    await launchPython(port);
    await waitForServer(port);
    createWindow(port);
    createTray();
  } catch (err) {
    console.error('[NANO] Erro fatal:', err.message);
    dialog.showErrorBox('Nano — Erro ao iniciar', `Não foi possível iniciar.\n\n${err.message}`);
    app.quit();
  }
});
app.on('activate', () => mainWindow?.show());
app.on('before-quit', () => { isQuitting = true; });
app.on('window-all-closed', e => { if (!isQuitting) e.preventDefault?.(); });
app.on('will-quit', () => {
  if (pythonProcess && !pythonProcess.killed) { try { exec(`taskkill /pid ${pythonProcess.pid} /f /t`); } catch (_) {} }
  tray?.destroy();
});
