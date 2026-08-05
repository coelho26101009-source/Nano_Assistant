/**
 * H.E.L.I.O.S. — Electron Main Process v4
 * Python imprime HELIOS_PORT, Electron aguarda /eel.js responder
 */

const { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage, shell, dialog } = require('electron');
const path  = require('path');
const { spawn, exec } = require('child_process');
const fs    = require('fs');
const http  = require('http');
const net   = require('net');

let mainWindow    = null;
let tray          = null;
let pythonProcess = null;
let isQuitting    = false;

// Instância única: arrancar de novo traz a janela existente para a frente
const GOT_LOCK = app.requestSingleInstanceLock();
if (!GOT_LOCK) app.quit();

// Arranque silencioso: iniciado pelo Windows no login ou com --hidden
let startedHidden = process.argv.includes('--hidden');

const IS_DEV   = !app.isPackaged;
const APP_ROOT = IS_DEV
  ? path.join(__dirname, '..')
  : path.join(process.resourcesPath, 'app');

const ICON_PATH = path.join(__dirname, 'assets', 'icon.ico');
const MAIN_PY   = path.join(APP_ROOT, 'core', 'main.py');
const ENV_FILE  = path.join(APP_ROOT, '.env');

console.log('[HELIOS] APP_ROOT:', APP_ROOT);

// ── Encontra Python ───────────────────────────────────────────────────────────
function findPython() {
  const venvPy = path.join(APP_ROOT, '.venv', 'Scripts', 'python.exe');
  if (fs.existsSync(venvPy)) {
    console.log('[HELIOS] Python (venv):', venvPy);
    return venvPy;
  }
  console.log('[HELIOS] Python: py (sistema)');
  return 'py';
}

// ── Carrega .env ──────────────────────────────────────────────────────────────
function loadEnv() {
  const env = { ...process.env };
  if (!fs.existsSync(ENV_FILE)) return env;
  for (const line of fs.readFileSync(ENV_FILE, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#') || !t.includes('=')) continue;
    const idx = t.indexOf('=');
    env[t.slice(0, idx).trim()] = t.slice(idx + 1).trim();
  }
  return env;
}

// ── Porta livre ───────────────────────────────────────────────────────────────
function getFreePort() {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.listen(0, () => { const p = srv.address().port; srv.close(() => resolve(p)); });
  });
}

// ── Aguarda HTTP responder ─────────────────────────────────────────────────────
function waitForServer(port, maxMs = 45000) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + maxMs;
    let attempts = 0;

    const tryConnect = () => {
      attempts++;
      if (Date.now() > deadline) {
        return reject(new Error(`Servidor não ficou pronto após ${attempts} tentativas (${maxMs/1000}s)`));
      }

      // Tenta conectar via TCP primeiro (mais rápido que HTTP)
      const socket = new net.Socket();
      socket.setTimeout(500);
      socket.connect(port, 'localhost', () => {
        socket.destroy();
        console.log(`[HELIOS] ✅ Porta ${port} a responder após ${attempts} tentativas`);
        // Aguarda mais 1.5s para o Eel inicializar completamente
        setTimeout(resolve, 1500);
      });
      socket.on('error', () => {
        socket.destroy();
        setTimeout(tryConnect, 300);
      });
      socket.on('timeout', () => {
        socket.destroy();
        setTimeout(tryConnect, 300);
      });
    };

    tryConnect();
  });
}

// ── Lança Python e obtém porta ────────────────────────────────────────────────
function launchPython(port) {
  return new Promise((resolve, reject) => {
    const pythonCmd = findPython();
    const env = { ...loadEnv(), HELIOS_MODE: 'electron', PYTHONUNBUFFERED: '1' };

    console.log(`[HELIOS] A lançar Python na porta ${port}...`);

    pythonProcess = spawn(
      pythonCmd,
      [MAIN_PY, '--mode', 'electron', '--port', String(port)],
      { cwd: APP_ROOT, env, stdio: ['pipe', 'pipe', 'pipe'] }
    );

    let resolved = false;

    const onData = (data) => {
      const text = data.toString();
      const lines = text.split('\n').filter(l => l.trim());
      for (const line of lines) {
        console.log('[Python]', line.trim());
        // Quando Python imprime HELIOS_PORT, sabemos que o servidor está a iniciar
        if (line.includes('HELIOS_PORT=') && !resolved) {
          resolved = true;
          console.log('[HELIOS] Python confirmou porta. A aguardar servidor HTTP...');
          resolve();
        }
      }
    };

    pythonProcess.stdout.on('data', onData);
    pythonProcess.stderr.on('data', onData);

    pythonProcess.on('error', (err) => {
      console.error('[HELIOS] Erro Python:', err.message);
      if (!resolved) { resolved = true; reject(err); }
    });

    pythonProcess.on('exit', (code) => {
      console.log('[HELIOS] Python saiu com código:', code);
      if (!resolved) { resolved = true; reject(new Error(`Python saiu (${code})`)); }
      else if (!isQuitting) showOffline(code);
    });

    // Timeout de segurança: se em 20s não imprimiu porta, tenta avançar
    setTimeout(() => {
      if (!resolved) {
        resolved = true;
        console.warn('[HELIOS] Timeout na espera de HELIOS_PORT, a avançar...');
        resolve();
      }
    }, 20000);
  });
}

function showOffline(code) {
  try {
    mainWindow?.webContents.executeJavaScript(`
      document.body.style.cssText='background:#030508;color:#FFB800;font-family:monospace;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column;gap:16px';
      document.body.innerHTML='<h1 style="letter-spacing:.2em;font-size:20px">HELIOS OFFLINE</h1><p style="opacity:.5;font-size:13px">Motor Python terminou (código ${code})</p>';
    `);
  } catch(_) {}
}

// ── Arranque com o sistema ────────────────────────────────────────────────────
function isAutoLaunchEnabled() {
  try { return app.getLoginItemSettings().openAtLogin; }
  catch (_) { return false; }
}

function setAutoLaunch(enabled) {
  try {
    app.setLoginItemSettings({
      openAtLogin:  enabled,
      openAsHidden: true,           // macOS
      args:         ['--hidden'],   // Windows: arranca só no tray
    });
    console.log('[HELIOS] Iniciar com o sistema:', enabled);
  } catch (e) {
    console.warn('[HELIOS] setLoginItemSettings falhou:', e.message);
  }
}

// ── Cria janela ───────────────────────────────────────────────────────────────
function createWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1440, height: 900,
    minWidth: 1100, minHeight: 700,
    frame: false,
    backgroundColor: '#030508',
    icon: fs.existsSync(ICON_PATH) ? ICON_PATH : undefined,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: false,
    },
    show: false,
  });

  const url = `http://localhost:${port}`;
  console.log('[HELIOS] A carregar:', url);
  mainWindow.loadURL(url);

  mainWindow.once('ready-to-show', () => {
    if (startedHidden) {
      console.log('[HELIOS] Arranque silencioso — a correr apenas no tray.');
      return;
    }
    mainWindow.show();
    console.log('[HELIOS] ✅ Janela visível!');
  });

  // Força mostrar após 12s mesmo que não emita ready-to-show
  setTimeout(() => {
    if (!startedHidden && mainWindow && !mainWindow.isVisible()) mainWindow.show();
  }, 12000);

  mainWindow.on('close', (e) => { if (!isQuitting) { e.preventDefault(); mainWindow.hide(); } });

  // Reload automático se a página falhar a carregar
  mainWindow.webContents.on('did-fail-load', (e, errCode, errDesc, failedUrl) => {
    console.warn('[HELIOS] Falha ao carregar:', errDesc);
    if (!isQuitting) setTimeout(() => mainWindow?.loadURL(failedUrl || url), 2000);
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

// ── Tray ─────────────────────────────────────────────────────────────────────
function createTray() {
  try {
    const icon = fs.existsSync(ICON_PATH)
      ? nativeImage.createFromPath(ICON_PATH).resize({ width: 16, height: 16 })
      : nativeImage.createEmpty();
    tray = new Tray(icon);
    tray.setToolTip('H.E.L.I.O.S.');
    refreshTrayMenu();
    tray.on('click',        () => { mainWindow?.show(); mainWindow?.focus(); });
    tray.on('double-click', () => { mainWindow?.show(); mainWindow?.focus(); });
    console.log('[HELIOS] Tray criado.');
  } catch(e) { console.warn('[HELIOS] Tray:', e.message); }
}

function refreshTrayMenu() {
  if (!tray) return;
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '⚡ Abrir HELIOS', click: () => { mainWindow?.show(); mainWindow?.focus(); } },
    { type: 'separator' },
    {
      label:   '🪟 Iniciar com o Windows',
      type:    'checkbox',
      checked: isAutoLaunchEnabled(),
      click:   (item) => { setAutoLaunch(item.checked); refreshTrayMenu(); },
    },
    { type: 'separator' },
    { label: '🔄 Reiniciar',    click: () => { app.relaunch(); app.exit(0); } },
    { type: 'separator' },
    { label: '✕ Sair',          click: () => { isQuitting = true; app.quit(); } },
  ]));
}

// ── IPC ───────────────────────────────────────────────────────────────────────
ipcMain.on('window-minimize', () => mainWindow?.minimize());
ipcMain.on('window-maximize', () => mainWindow?.isMaximized() ? mainWindow.unmaximize() : mainWindow?.maximize());
ipcMain.on('window-hide',     () => mainWindow?.hide());
ipcMain.on('window-close',    () => { isQuitting = true; app.quit(); });
ipcMain.handle('autolaunch-get', () => isAutoLaunchEnabled());
ipcMain.handle('autolaunch-set', (_e, enabled) => {
  setAutoLaunch(!!enabled);
  refreshTrayMenu();
  return isAutoLaunchEnabled();
});

app.on('second-instance', () => {
  mainWindow?.show();
  mainWindow?.focus();
});

// ── Boot ──────────────────────────────────────────────────────────────────────
if (GOT_LOCK) app.whenReady().then(async () => {
  console.log('[HELIOS] Electron pronto.');
  try {
    if (!startedHidden && app.getLoginItemSettings().wasOpenedAtLogin) {
      startedHidden = true;
      console.log('[HELIOS] Arrancado pelo login do Windows.');
    }

    // 1. Porta livre
    const port = await getFreePort();
    console.log('[HELIOS] Porta escolhida:', port);

    // 2. Lança Python (aguarda HELIOS_PORT no stdout)
    await launchPython(port);

    // 3. Aguarda TCP na porta (servidor HTTP/WebSocket pronto)
    console.log('[HELIOS] A aguardar servidor TCP na porta', port, '...');
    await waitForServer(port, 45000);

    // 4. Abre janela e tray
    createWindow(port);
    createTray();

  } catch(err) {
    console.error('[HELIOS] Erro fatal:', err.message);
    dialog.showErrorBox('HELIOS — Erro ao iniciar',
      `Não foi possível iniciar.\n\nErro: ${err.message}\n\nVerifica que o Python e as dependências estão instaladas.`
    );
    app.quit();
  }
});

app.on('activate',          () => mainWindow?.show());
app.on('before-quit',       () => { isQuitting = true; });
app.on('window-all-closed', (e) => { if (!isQuitting) e.preventDefault?.(); });
app.on('will-quit', () => {
  if (pythonProcess && !pythonProcess.killed) {
    try { exec(`taskkill /pid ${pythonProcess.pid} /f /t`); } catch(_) {}
  }
  tray?.destroy();
});
