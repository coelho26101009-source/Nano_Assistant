/**
 * Nubi, NANO's mascot (2026-11-26, chosen by Simão from three proposals).
 *
 * She is a small cloud with a face. On the island she takes the place of the
 * N mark and her face follows the island's real state: thinking while NANO
 * thinks, smiling while it speaks. The listening state keeps the microphone,
 * because an open microphone is a privacy sign and must stay unmistakable.
 *
 * One switch (Settings > Geral > Modo desktop, "Mascote") turns her off
 * everywhere; the shell remembers it in desktop-state.json.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { assert, loadFresh, stubElectron, suite, test } = require('./harness');

function bootedShell() {
  const stub = stubElectron();
  const shell = loadFresh(path.join(__dirname, '..', 'main.js'));
  stub.restore();
  const api = shell.__test;
  api.reset();
  api.createMainWindow(51234);
  api.createOverlayWindow();
  const overlay = api.state().overlayWindow;
  overlay.finishLoad();
  api.setIslandReady(true);
  api.renderIsland();
  return { api, overlay, main: api.state().mainWindow };
}

const lastState = (overlay) => overlay.sent.filter((m) => m.channel === 'nano:overlay-state').at(-1).payload;

suite('mascot in the shell');

test('Nubi is on the island by default', () => {
  const { api, overlay, main } = bootedShell();
  main.hide();
  api.onBackendEvent('chat_activity', { phase: 'thinking' });
  assert.strictEqual(lastState(overlay).mascot, true);
  assert.strictEqual(api.state().shellState.mascot, true);
  assert.strictEqual(api.desktopStatus().mascot, true, 'the page learns the switch from the desktop status');
});

test('switching Nubi off takes her off the island at once', () => {
  const { api, overlay, main } = bootedShell();
  main.hide();
  api.onBackendEvent('chat_activity', { phase: 'thinking' });
  assert.strictEqual(api.setMascot(false), false);
  assert.strictEqual(lastState(overlay).mascot, false, 'the visible island is redrawn without her');
  assert.strictEqual(api.state().shellState.mascot, false);
  assert.strictEqual(api.desktopStatus().mascot, false);
  assert.strictEqual(api.setMascot('yes'), true, 'the value is reduced to a boolean');
  assert.strictEqual(lastState(overlay).mascot, true);
});

test('a saved "mascot": false is read back; anything else means on', () => {
  const { api } = bootedShell();
  assert.strictEqual(api.parseShellState({ mascot: false }).mascot, false);
  assert.strictEqual(api.parseShellState({}).mascot, true, 'older files have no key: she appears');
  assert.strictEqual(api.parseShellState({ mascot: 'no' }).mascot, true, 'only a real false turns her off');
  // The other keys are still read exactly as before.
  const state = api.parseShellState({ overlayEnabled: false, islandMode: 'always', autoUpdate: false });
  assert.strictEqual(state.overlayEnabled, false);
  assert.strictEqual(state.islandMode, 'always');
  assert.strictEqual(state.autoUpdate, false);
});

/* ── The island page draws her ─────────────────────────────────────────── */

suite('mascot on the island page');

const OVERLAY_DIR = path.join(__dirname, '..', 'overlay');
const html = fs.readFileSync(path.join(OVERLAY_DIR, 'overlay.html'), 'utf8');

/** A tiny stand-in for the DOM overlay.js touches, enough to run its render(). */
function fakeElement() {
  const attrs = {};
  return {
    dataset: {}, style: {}, textContent: '', disabled: false, title: '', offsetWidth: 0,
    classList: { add() {}, remove() {} },
    setAttribute(name, value) { attrs[name] = String(value); },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null; },
    removeAttribute(name) { delete attrs[name]; },
    append() {},
    addEventListener() {},
  };
}

function runOverlay() {
  const nodes = {};
  let render = null;
  const document = {
    documentElement: { lang: 'pt' },
    getElementById: (id) => (nodes[id] = nodes[id] || fakeElement()),
    createElement: () => fakeElement(),
    createTextNode: () => ({}),
  };
  const window = { nanoOverlay: { onState: (fn) => { render = fn; } } };
  const code = fs.readFileSync(path.join(OVERLAY_DIR, 'overlay.js'), 'utf8');
  vm.runInNewContext(code, {
    window, document, Element: function Element() {}, setInterval: () => 0, clearInterval: () => {}, Date,
  });
  return { island: nodes.island, render: (view) => render(view) };
}

test('the island page marks whether Nubi is shown', () => {
  const { island, render } = runOverlay();
  render({ visible: true, state: 'thinking', size: 'compact', icon: 'mark', label: 'A pensar…', mascot: true });
  assert.strictEqual(island.dataset.mascot, 'true');
  render({ visible: true, state: 'thinking', size: 'compact', icon: 'mark', label: 'A pensar…', mascot: false });
  assert.strictEqual(island.dataset.mascot, 'false');
  render({ visible: true, state: 'thinking', size: 'compact', icon: 'mark', label: 'A pensar…' });
  assert.strictEqual(island.dataset.mascot, 'false', 'a view that does not say so shows the plain N');
});

test('Nubi sits where the N mark sits, at rest and in the pill', () => {
  for (const layout of ['l-idle', 'l-pill']) {
    const start = html.indexOf(`id="${layout}"`);
    const end = html.indexOf('class="layout"', start + 1);
    const block = html.slice(start, end === -1 ? undefined : end);
    assert.ok(/<svg[^>]*class="[^"]*\bnubi\b/.test(block), `${layout} must hold Nubi`);
  }
});

test('Nubi has a face for each state she shows', () => {
  for (const face of ['nubi-calm', 'nubi-think', 'nubi-happy']) {
    assert.ok(html.includes(`class="${face}"`), `missing face ${face}`);
  }
  // Thinking and speaking pick their faces from the state, never from data.
  for (const state of ['thinking', 'processing', 'transcribing']) {
    assert.ok(html.includes(`#island[data-state="${state}"] .nubi .nubi-think`), `${state} must show the thinking face`);
  }
  assert.ok(html.includes('#island[data-state="speaking"] .nubi .nubi-happy'), 'speaking must smile');
  // Switched off, the original N is back and Nubi is gone.
  assert.ok(html.includes('#island:not([data-mascot="true"]) .nubi'), 'off must hide Nubi');
  assert.ok(html.includes('#island[data-mascot="true"] img.mark'), 'on must hide the N image');
});

test('the microphone keeps its place while listening', () => {
  // The listening states use the "mic" icon; Nubi only replaces the "mark".
  const island = require('../lib/island');
  for (const phase of ['COMMAND_LISTENING', 'WAKE_DETECTED']) {
    const next = island.fromVoiceView(require('../lib/overlay-state').fromPhase(phase));
    assert.strictEqual(next.view.icon, 'mic', `${phase} must keep the microphone`);
  }
});
