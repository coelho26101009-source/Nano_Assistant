#!/usr/bin/env node
/**
 * REPRODUCE the overlay-while-main-window-hidden bug, in real Electron.
 *
 *     npx electron test/overlay-live.js
 *
 * The human report was that the voice overlay appears while the Nano window is
 * visible and does not appear once it is minimised or hidden to the tray. The
 * overlay has no `parent:` option and the show path contains no check on the
 * main window, so reading the code does not explain it. This runs it.
 *
 * It builds a main window and the overlay with the EXACT options main.js uses,
 * then drives three states and reports, for each, what Electron thinks and what
 * is actually on the screen. Screen capture is the ground truth: a window can
 * report isVisible() === true and still paint nothing.
 */
'use strict';

const { app, BrowserWindow, screen } = require('electron');
const path = require('path');

const OVERLAY_SIZE = { width: 320, height: 96 };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

let mainWindow = null;
let overlayWindow = null;
const report = [];

/** Exactly the options main.js uses today. */
function createOverlay(extraWebPreferences = {}) {
  const window = new BrowserWindow({
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
    focusable: false,
    acceptFirstMouse: false,
    hasShadow: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, '..', 'overlay', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
      ...extraWebPreferences,
    },
  });
  window.loadFile(path.join(__dirname, '..', 'overlay', 'overlay.html'));
  window.setAlwaysOnTop(true, 'screen-saver');
  window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  window.setIgnoreMouseEvents(true, { forward: true });
  return window;
}

function positionOverlay() {
  const cursor = screen.getCursorScreenPoint();
  const display = screen.getDisplayNearestPoint(cursor);
  const area = display.workArea;
  overlayWindow.setBounds({
    ...OVERLAY_SIZE,
    x: Math.round(area.x + (area.width - OVERLAY_SIZE.width) / 2),
    y: Math.round(area.y + area.height - OVERLAY_SIZE.height - 72),
  });
}

/** Show the overlay exactly as applyOverlayView does. */
function showOverlay(label) {
  positionOverlay();
  overlayWindow.webContents.send('nano:overlay-state', {
    visible: true, state: 'listening', label, hideAfterMs: 0,
  });
  if (!overlayWindow.isVisible()) overlayWindow.showInactive();
}

function hideOverlay() {
  overlayWindow.hide();
  overlayWindow.webContents.send('nano:overlay-state', { visible: false, state: 'idle', label: '' });
}

/**
 * Is the overlay ACTUALLY painting? A transparent window that reports visible
 * but renders nothing looks identical to a window that never appeared, and
 * that distinction is the whole investigation.
 */
async function capturePixels() {
  try {
    const image = await overlayWindow.webContents.capturePage();
    const size = image.getSize();
    const bitmap = image.toBitmap();          // BGRA
    let opaque = 0;
    for (let i = 3; i < bitmap.length; i += 4) if (bitmap[i] > 16) opaque += 1;
    return { width: size.width, height: size.height, opaquePixels: opaque };
  } catch (err) {
    return { error: err.message };
  }
}

async function probe(scenario) {
  await wait(900);
  const pixels = await capturePixels();
  const row = {
    scenario,
    overlayIsVisible: overlayWindow.isVisible(),
    overlayBounds: overlayWindow.getBounds(),
    overlayOpacity: overlayWindow.getOpacity(),
    overlayAlwaysOnTop: overlayWindow.isAlwaysOnTop(),
    mainIsVisible: mainWindow.isVisible(),
    mainIsMinimized: mainWindow.isMinimized(),
    mainIsFocused: mainWindow.isFocused(),
    paintedPixels: pixels.opaquePixels,
    captureError: pixels.error || null,
  };
  report.push(row);
  console.log(`--- ${scenario}`);
  console.log(`    overlay.isVisible : ${row.overlayIsVisible}`);
  console.log(`    overlay painted   : ${row.paintedPixels} opaque px${row.captureError ? ' (' + row.captureError + ')' : ''}`);
  console.log(`    overlay bounds    : ${JSON.stringify(row.overlayBounds)}`);
  console.log(`    main visible/min  : ${row.mainIsVisible} / ${row.mainIsMinimized}`);
  console.log(`    main focused      : ${row.mainIsFocused}`);
  return row;
}

async function main() {
  mainWindow = new BrowserWindow({
    width: 900, height: 600, show: true, backgroundColor: '#05070A',
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  mainWindow.loadURL('data:text/html,<body style="background:%2305070A"></body>');

  overlayWindow = createOverlay();
  await new Promise((r) => overlayWindow.webContents.once('did-finish-load', r));
  await wait(700);

  // A: the case that works for the user.
  showOverlay('A ouvir…  (main visible)');
  await probe('main VISIBLE');
  hideOverlay();
  await wait(500);

  // B: minimised.
  mainWindow.minimize();
  await wait(700);
  showOverlay('A ouvir…  (main minimised)');
  await probe('main MINIMIZED');
  hideOverlay();
  await wait(500);

  // C: hidden to tray.
  mainWindow.restore();
  await wait(400);
  mainWindow.hide();
  await wait(700);
  showOverlay('A ouvir…  (main hidden)');
  await probe('main HIDDEN to tray');
  hideOverlay();
  await wait(400);

  // D: a second turn while still hidden.
  showOverlay('A ouvir…  (second turn, main hidden)');
  await probe('main HIDDEN, second turn');
  hideOverlay();

  console.log('\nJSON ' + JSON.stringify(report));
  app.exit(0);
}

app.whenReady().then(main).catch((err) => {
  console.log('FATAL ' + err.message);
  app.exit(1);
});
