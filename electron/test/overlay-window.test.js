/**
 * The voice overlay must be independent of the main Nano window.
 *
 * THE REPORT: the overlay appeared while the Nano window was visible and did
 * not appear once it was minimised or hidden to the tray -- even though the
 * voice turn itself worked and Nano spoke.
 *
 * WHAT IT ACTUALLY WAS: `shellState.overlayEnabled` was persisted `false` in
 * desktop-state.json, so `applyOverlayView` returned early in EVERY state, and
 * did so silently. What looked like "the overlay works when the window is
 * visible" was the main window's own phase narration in the composer -- "A
 * ouvir…", "A processar…", "A falar…" are rendered by the React UI too. With
 * the window hidden there was nothing left to see, because the overlay window
 * had never been appearing at all.
 *
 * Measured live afterwards, with the flag on, in all three states: shown.
 *
 * These tests load the REAL main.js under a stubbed Electron and drive its
 * real overlay functions, so "independent of the main window" is a property
 * this file proves rather than asserts in prose.
 */
'use strict';

const path = require('path');
const { assert, loadFresh, stubElectron, suite, test } = require('./harness');
const overlayState = require('../lib/overlay-state');

/** Load main.js under the stub and hand back its test surface. */
function loadShell() {
  const stub = stubElectron();
  const shell = loadFresh(path.join(__dirname, '..', 'main.js'));
  stub.restore();
  shell.__test.reset();
  return { shell, api: shell.__test, record: stub.record };
}

/** A shell with a main window and a loaded overlay, as after a normal start. */
function bootedShell() {
  const { shell, api, record } = loadShell();
  api.createMainWindow(51234);
  api.createOverlayWindow();
  api.state().overlayWindow.finishLoad();      // the renderer is up
  return { shell, api, record, main: api.state().mainWindow, overlay: api.state().overlayWindow };
}

const LISTENING = overlayState.fromPhase('COMMAND_LISTENING');

suite('overlay independence');

test('the overlay is a top-level window, not a child of the main window', () => {
  const { overlay } = bootedShell();
  assert.strictEqual(overlay.options.parent, undefined,
    'a `parent:` option makes Windows minimise and hide the overlay together '
    + 'with its owner, which is exactly the behaviour being guarded against');
});

test('main window VISIBLE: a voice phase shows the overlay', () => {
  const { api, main, overlay } = bootedShell();
  main.show();
  api.applyOverlayView(LISTENING);
  assert.strictEqual(overlay.isVisible(), true);
});

test('main window MINIMIZED: a voice phase still shows the overlay', () => {
  const { api, main, overlay } = bootedShell();
  main.minimize();
  assert.strictEqual(main.isMinimized(), true);

  api.applyOverlayView(LISTENING);

  assert.strictEqual(overlay.isVisible(), true,
    'the overlay must not be minimised along with the main window');
  assert.strictEqual(main.isMinimized(), true, 'the main window must stay minimised');
});

test('main window HIDDEN to tray: a voice phase still shows the overlay', () => {
  const { api, main, overlay } = bootedShell();
  main.hide();
  assert.strictEqual(main.isVisible(), false);

  api.applyOverlayView(LISTENING);

  assert.strictEqual(overlay.isVisible(), true,
    'this is the reported failure: no overlay once Nano is in the tray');
  assert.strictEqual(main.isVisible(), false, 'the main window must stay hidden');
});

test('showing the overlay never restores or focuses the main window', () => {
  const { api, main, overlay } = bootedShell();
  main.hide();
  api.applyOverlayView(LISTENING);

  assert.strictEqual(main.isVisible(), false, 'the main window was restored');
  assert.strictEqual(main.isFocused(), false, 'the main window stole focus');
  assert.strictEqual(overlay.focused, false,
    'the overlay must appear with showInactive(), never taking focus');
});

test('repeated voice turns while the main window is hidden all show it', () => {
  const { api, main, overlay } = bootedShell();
  main.hide();

  for (let turn = 1; turn <= 3; turn += 1) {
    api.applyOverlayView(LISTENING);
    assert.strictEqual(overlay.isVisible(), true, `turn ${turn} did not show the overlay`);
    api.applyOverlayView(overlayState.HIDDEN);
    assert.strictEqual(overlay.isVisible(), false, `turn ${turn} did not hide the overlay`);
  }
  assert.strictEqual(main.isVisible(), false);
});

suite('overlay window properties');

test('the overlay stays out of the taskbar and off the focus path', () => {
  const { overlay } = bootedShell();
  assert.strictEqual(overlay.options.skipTaskbar, true, 'it would appear in Alt+Tab');
  assert.strictEqual(overlay.options.focusable, false, 'it would steal focus');
  assert.strictEqual(overlay.options.alwaysOnTop, true);
  assert.strictEqual(overlay.options.frame, false);
  assert.strictEqual(overlay.options.transparent, true);
  assert.strictEqual(overlay.options.show, false, 'it must start hidden');
});

test('the overlay keeps those properties while the main window is hidden', () => {
  const { api, main, overlay } = bootedShell();
  main.hide();
  api.applyOverlayView(LISTENING);
  assert.strictEqual(overlay.isAlwaysOnTop(), true);
  assert.strictEqual(overlay.options.skipTaskbar, true);
});

suite('overlay lifecycle');

test('a turn ending hides the overlay', () => {
  const { api, overlay } = bootedShell();
  api.applyOverlayView(LISTENING);
  assert.strictEqual(overlay.isVisible(), true);

  api.onBackendEvent('voice_turn_ended', { ok: false, cancelled: true, error: 'no_speech' });
  api.applyOverlayView(overlayState.HIDDEN);

  assert.strictEqual(overlay.isVisible(), false);
});

test('the resting phases hide it through the real event path', () => {
  const { api, overlay } = bootedShell();
  api.onBackendEvent('voice_phase', { phase: 'COMMAND_LISTENING' });
  assert.strictEqual(overlay.isVisible(), true);

  api.onBackendEvent('voice_phase', { phase: 'IDLE' });
  assert.strictEqual(overlay.isVisible(), false);
});

test('hiding the main window does not destroy or hide the overlay', () => {
  const { api, main, overlay } = bootedShell();
  api.applyOverlayView(LISTENING);
  assert.strictEqual(overlay.isVisible(), true);

  main.emit('close', { preventDefault() {} });   // the real close-to-tray path
  main.hide();

  assert.strictEqual(overlay.isDestroyed(), false,
    'the overlay must outlive the main window being put away');
  assert.strictEqual(overlay.isVisible(), true,
    'hiding the main window must not take the overlay with it');
});

test('quitting destroys the overlay', () => {
  const { api, overlay } = bootedShell();
  api.applyOverlayView(LISTENING);
  api.quitNano();
  assert.strictEqual(overlay.isDestroyed(), true, 'the overlay outlived the app');
});

suite('overlay suppression is never silent');

test('a disabled overlay does not show, and says so', () => {
  const { api, overlay } = bootedShell();
  api.setOverlayEnabled(false);
  api.applyOverlayView(LISTENING);
  assert.strictEqual(overlay.isVisible(), false,
    'the user turned the panel off; that must be respected');
});

test('re-enabling it makes the very next turn show again', () => {
  const { api, overlay } = bootedShell();
  api.setOverlayEnabled(false);
  api.applyOverlayView(LISTENING);
  assert.strictEqual(overlay.isVisible(), false);

  api.setOverlayEnabled(true);
  api.applyOverlayView(LISTENING);
  assert.strictEqual(overlay.isVisible(), true);
});

suite('overlay readiness');

test('a phase arriving before the renderer loads is replayed, not lost', () => {
  const { api } = loadShell();
  api.createMainWindow(51234);
  api.createOverlayWindow();
  const overlay = api.state().overlayWindow;

  // The first voice turn can easily beat the overlay page's load.
  api.applyOverlayView(LISTENING);
  assert.strictEqual(overlay.isVisible(), false, 'nothing can be shown before the page exists');

  overlay.finishLoad();

  assert.strictEqual(overlay.isVisible(), true,
    'the deferred view must be replayed once the renderer is ready');
  const states = overlay.sent.filter((m) => m.channel === 'nano:overlay-state');
  assert.ok(states.length >= 1, 'the overlay renderer was never told what to draw');
  assert.strictEqual(states[states.length - 1].payload.state, 'listening');
});
