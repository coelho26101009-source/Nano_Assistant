#!/usr/bin/env node
/** Capture real Electron overlay rendering for visual review.
 *
 *   npx electron test/overlay-visual.js <output-directory>
 *
 * Voice views come from the production mapper. The two layout stress cases use
 * synthetic text and are identified as such in the output report.
 */
'use strict';

const { app, BrowserWindow, screen } = require('electron');
const fs = require('fs');
const path = require('path');
const overlayState = require('../lib/overlay-state');

const OUT = process.argv[2] && path.resolve(process.argv[2]);
const SIZE = { width: 548, height: 84 };
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function run() {
  if (!OUT) throw new Error('Pass an output directory');
  fs.mkdirSync(OUT, { recursive: true });

  const window = new BrowserWindow({
    ...SIZE, show: false, frame: false, transparent: true,
    resizable: false, movable: false, minimizable: false,
    maximizable: false, fullscreenable: false, skipTaskbar: true,
    alwaysOnTop: true, focusable: false, acceptFirstMouse: false,
    hasShadow: false, backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, '..', 'overlay', 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
      webSecurity: true, webviewTag: false,
    },
  });
  window.setAlwaysOnTop(true, 'screen-saver');
  window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  window.setIgnoreMouseEvents(true, { forward: true });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event) => event.preventDefault());
  const loaded = new Promise((resolve) => window.webContents.once('did-finish-load', resolve));
  window.loadFile(path.join(__dirname, '..', 'overlay', 'overlay.html'));
  await loaded;

  const samples = [
    ['activated', overlayState.fromPhase('WAKE_DETECTED')],
    ['listening', overlayState.fromPhase('COMMAND_LISTENING')],
    ['transcribing', overlayState.fromPhase('TRANSCRIBING')],
    ['processing', overlayState.fromPhase('PROCESSING')],
    ['speaking', overlayState.fromPhase('SPEAKING')],
    ['busy', overlayState.busyView({ active_source: 'hotkey' })],
    ['done', overlayState.fromTurnEnd({ ok: true })],
    ['quiet', overlayState.fromTurnEnd({ cancelled: true, error: 'no_speech' })],
    ['error', overlayState.fromTurnEnd({ error: 'microphone_failed' })],
    ['narrow-synthetic', { visible: true, state: 'transcribing', label: 'A transcrever…' }],
    ['long-synthetic', {
      visible: true, state: 'busy',
      label: 'Este é um estado muito longo para verificar o corte com reticências no painel',
      detail: 'Este detalhe também é deliberadamente longo para verificar o espaço do texto',
    }],
  ];
  const reports = [];
  for (const [name, view] of samples) {
    const width = name === 'narrow-synthetic' ? 320 : SIZE.width;
    const workArea = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea;
    const actualWidth = Math.min(width, Math.max(1, workArea.width - 24));
    window.setBounds({
      width: actualWidth, height: SIZE.height,
      x: Math.round(workArea.x + (workArea.width - actualWidth) / 2),
      y: Math.round(workArea.y + workArea.height - SIZE.height - 72),
    });
    window.webContents.send('nano:overlay-state', view);
    if (!window.isVisible()) window.showInactive();
    await wait(260);
    const metrics = await window.webContents.executeJavaScript(`(() => {
      const panel = document.getElementById('panel');
      const label = document.getElementById('label');
      const detail = document.getElementById('detail');
      const mark = document.getElementById('mark');
      return {
        panel: panel.getBoundingClientRect().toJSON(),
        label: { clientWidth: label.clientWidth, scrollWidth: label.scrollWidth },
        detail: { clientWidth: detail.clientWidth, scrollWidth: detail.scrollWidth },
        markLoaded: mark.complete && mark.naturalWidth > 0,
        state: panel.dataset.state,
        opacity: getComputedStyle(panel).opacity,
      };
    })()`);
    const file = path.join(OUT, `${name}.png`);
    fs.writeFileSync(file, (await window.webContents.capturePage()).toPNG());
    reports.push({ name, view, bounds: window.getBounds(), metrics, file });
  }
  window.webContents.send('nano:overlay-state', overlayState.HIDDEN);
  await wait(80);
  reports.push({ name: 'closing', file: path.join(OUT, 'closing.png') });
  fs.writeFileSync(reports.at(-1).file, (await window.webContents.capturePage()).toPNG());
  fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(reports, null, 2));
  window.destroy();
  console.log(JSON.stringify(reports.map(({ name, bounds, metrics }) => ({ name, bounds, metrics }))));
  app.exit(0);
}

app.whenReady().then(run).catch((error) => {
  console.error(error);
  app.exit(1);
});
