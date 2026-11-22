/**
 * The NANO island: what it shows, in what order, and what it can never do.
 *
 * The first half tests lib/island.js on its own (pure). The second loads the
 * REAL main.js under a stubbed Electron and drives backend events through
 * onBackendEvent, the way the Python backend does.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { assert, loadFresh, stubElectron, suite, test } = require('./harness');
const island = require('../lib/island');
const overlayState = require('../lib/overlay-state');

class Clock {
  constructor() { this.t = 1_000_000; }
  now() { return this.t; }
}

function stackWithClock() {
  const clock = new Clock();
  return { clock, stack: new island.IslandStack({ now: () => clock.now() }) };
}

suite('island views');

test('a chat turn goes thinking -> working -> done, with friendly tool names', () => {
  assert.strictEqual(island.fromChatActivity({ phase: 'thinking' }).view.label, 'A pensar…');
  const working = island.fromChatActivity({ phase: 'working', tools: ['web_search'] }).view;
  assert.strictEqual(working.label, 'A pesquisar na web…');
  assert.strictEqual(working.size, 'wide');
  const unknown = island.fromChatActivity({ phase: 'working', tools: ['pc_window_close'] }).view;
  assert.strictEqual(unknown.label, 'A executar…');
  assert.strictEqual(unknown.detail, 'pc window close', 'an unknown tool is shown readably, never as a code');
  assert.strictEqual(island.fromChatActivity({ phase: 'done' }).view.state, 'done');
  assert.strictEqual(island.fromChatActivity({ phase: 'nonsense' }), null);
});

test('an approval says what and where to answer, and has no way to answer', () => {
  const view = island.fromApproval({ action: 'FECHAR JANELA', target: 'Discord' });
  assert.strictEqual(view.state, 'approval');
  assert.strictEqual(view.detail, 'FECHAR JANELA — Discord');
  assert.strictEqual(view.hint, 'Clica para decidir no NANO');
  for (const key of Object.keys(view)) {
    assert.ok(!/approve|allow|accept|confirm|deny/i.test(key), `"${key}" looks like a decision`);
  }
});

test('backend text is bounded and stripped of control characters', () => {
  const reminder = island.fromReminder({ text: 'Ligar\u0007 à avó\n' + 'x'.repeat(500), due_label: '18:30' }).view;
  assert.ok(!/[\u0000-\u001f]/.test(reminder.detail));
  assert.ok(reminder.detail.length <= 140);
  assert.strictEqual(island.fromReminder({ text: '   ' }), null);
});

test('each PC alert has its own icon and tone', () => {
  assert.strictEqual(island.fromNotice({ kind: 'battery', title: 'Bateria fraca', text: '10%' }).view.icon, 'battery');
  assert.strictEqual(island.fromNotice({ kind: 'download', title: 'Download terminado', text: 'a.zip' }).view.state, 'download');
  assert.strictEqual(island.fromNotice({ kind: 'network', title: 'Rede', text: 'A ligação à rede voltou.' }).view.tone, 'success');
  assert.strictEqual(island.fromNotice({ kind: 'network', title: 'Sem rede', text: 'caiu' }).view.tone, 'info');
  assert.strictEqual(island.fromNotice({ kind: 'briefing', title: 'Bom dia', text: 'x' }).view.icon, 'spark');
});

test('music: a song becomes a pill, a hostile cover is dropped', () => {
  const song = { status: 'playing', title: 'Entropy', artist: 'Beach Bunny', position: 10, duration: 180,
    position_at: 5, art: 'data:image/png;base64,AAAA' };
  const view = island.fromMedia(song).view;
  assert.strictEqual(view.state, 'media');
  assert.strictEqual(view.media.art, 'data:image/png;base64,AAAA');
  assert.strictEqual(island.fromMedia({ ...song, art: 'javascript:alert(1)' }).view.media.art, '');
  assert.strictEqual(island.fromMedia({ ...song, art: 'data:image/svg+xml;base64,AAAA' }).view.media.art, '');
  assert.strictEqual(island.fromMedia({ status: 'none' }), null);
  assert.strictEqual(island.fromMedia({ ...song, status: 'paused' }).ttl, island.TTL.paused);
  assert.strictEqual(island.trackCard({ ...song, status: 'paused' }), null, 'a paused song does not pop open');
});

test('volume: a level bar, muted shows zero', () => {
  assert.strictEqual(island.fromVolume({ level: 47.6, muted: false }).view.level, 48);
  const muted = island.fromVolume({ level: 60, muted: true }).view;
  assert.strictEqual(muted.level, 0);
  assert.strictEqual(muted.icon, 'mute');
  assert.strictEqual(island.fromVolume({ level: 'loud' }), null);
});

suite('island stack');

test('the most important layer wins, and the others come back after it', () => {
  const { stack, clock } = stackWithClock();
  stack.set('media', island.fromMedia({ status: 'playing', title: 'Song' }).view);
  stack.set('chat', island.fromChatActivity({ phase: 'thinking' }).view, 10000);
  assert.strictEqual(stack.current().layer, 'chat');
  stack.approvalRequested('r1', { action: 'X' });
  assert.strictEqual(stack.current().layer, 'approval');
  stack.approvalResolved('r1');
  assert.strictEqual(stack.current().layer, 'chat');
  clock.t += 10001;
  assert.strictEqual(stack.current(), null, 'thinking expired; a playing song alone keeps the island hidden');
  assert.strictEqual(stack.current({ peek: true }).layer, 'media', 'called: the song is still playing');
});

test('two approvals are counted, and one answered leaves the other', () => {
  const { stack } = stackWithClock();
  stack.approvalRequested('a', { action: 'A' });
  stack.approvalRequested('b', { action: 'B' });
  const view = island.translate(stack.current(), 'pt');
  assert.strictEqual(view.label, 'O NANO precisa de 2 aprovações');
  stack.approvalResolved('b');
  assert.strictEqual(island.translate(stack.current(), 'pt').label, 'O NANO precisa da tua aprovação');
});

test('at rest: hidden, unless set to stay on screen or called', () => {
  const { stack } = stackWithClock();
  assert.strictEqual(stack.current({ alwaysVisible: true }).layer, 'idle');
  assert.strictEqual(stack.current({ alwaysVisible: false }), null);
  assert.strictEqual(stack.current({ peek: true }).layer, 'idle', 'called: the resting pill comes out');
  assert.strictEqual(stack.current({ peek: true, mainFocused: true }), null, 'NANO in front: it stays aside');
});

test('a song that keeps playing waits until the island is called', () => {
  const { stack } = stackWithClock();
  const playing = { status: 'playing', title: 'Entropy', artist: 'Beach Bunny', key: 'k' };
  stack.set('media', island.fromMedia(playing).view, 0);
  assert.strictEqual(stack.current(), null, 'hidden at rest, even with music on');
  assert.strictEqual(stack.current({ peek: true }).state, 'media', 'called: the music pill');
  assert.strictEqual(stack.current({ alwaysVisible: true }).state, 'media');
  // Something that matters still comes out on its own.
  stack.set('hud', island.fromVolume({ level: 40 }).view, 1800);
  assert.strictEqual(stack.current().state, 'volume');
});

test('pausing or playing again shows the music pill for a moment', () => {
  const blip = island.mediaBlip({ status: 'paused', title: 'Entropy', key: 'k' });
  assert.strictEqual(blip.view.state, 'media');
  assert.strictEqual(blip.view.label, 'Em pausa');
  assert.strictEqual(blip.ttl, island.TTL.mediaBlip);
  assert.strictEqual(island.mediaBlip({ status: 'none' }), null);
});

test('the pointer calls the island only at the top edge, near the centre', () => {
  const screenBounds = { x: 0, y: 0, width: 1920, height: 1080 };
  assert.strictEqual(island.inPeekZone({ x: 960, y: 0 }, screenBounds), true);
  assert.strictEqual(island.inPeekZone({ x: 960 + 250, y: 1 }, screenBounds), true);
  assert.strictEqual(island.inPeekZone({ x: 960, y: 40 }, screenBounds), false, 'a tab, not the edge');
  assert.strictEqual(island.inPeekZone({ x: 100, y: 0 }, screenBounds), false, 'the corner is not the island');
  // A second screen to the right, a little higher.
  const second = { x: 1920, y: -200, width: 2560, height: 1440 };
  assert.strictEqual(island.inPeekZone({ x: 1920 + 1280, y: -200 }, second), true);
  assert.strictEqual(island.inPeekZone({ x: 1920 + 1280, y: 0 }, second), false);
  assert.strictEqual(island.inPeekZone(null, screenBounds), false);
  assert.strictEqual(island.inPeekZone({ x: NaN, y: 0 }, screenBounds), false);
});

test('with NANO in front, only a voice turn stays on the island', () => {
  const { stack } = stackWithClock();
  stack.set('chat', island.fromChatActivity({ phase: 'thinking' }).view);
  stack.approvalRequested('r', {});
  assert.strictEqual(stack.current({ mainFocused: true, alwaysVisible: true }), null);
  stack.set('voice', island.fromVoiceView(overlayState.fromPhase('COMMAND_LISTENING')).view);
  assert.strictEqual(stack.current({ mainFocused: true }).layer, 'voice');
});

test('the listening clock counts from the start of the turn', () => {
  const { stack, clock } = stackWithClock();
  const listening = island.fromVoiceView(overlayState.fromPhase('COMMAND_LISTENING')).view;
  stack.set('voice', listening);
  const started = clock.t;
  clock.t += 3000;
  stack.set('voice', listening);
  assert.strictEqual(stack.current().since, started);
});

suite('island language');

test('every fixed island and voice string has an English entry', () => {
  const sources = [
    ...Object.values(overlayState.ERROR_LABEL), overlayState.GENERIC_ERROR,
    ...Object.values(overlayState.PHASE_VIEW).filter(Boolean).map((v) => v.label),
    'Pronto', 'Qual delas?', 'Sem comando', 'O Nano já está ocupado',
    ...Object.values(island.TOOL_LABELS),
  ];
  const text = fs.readFileSync(path.join(__dirname, '..', 'lib', 'island.js'), 'utf8');
  for (const literal of text.matchAll(/(?:label|hint): '([^']+)'/g)) sources.push(literal[1]);
  for (const source of new Set(sources)) {
    assert.ok(Object.prototype.hasOwnProperty.call(island.EN, source), `no English for "${source}"`);
  }
});

test('English and Portuguese both render; data is never translated', () => {
  const { stack } = stackWithClock();
  stack.set('notice', island.fromNotice({ kind: 'download', title: 'A pensar…', text: 'Pronto' }).view, 1000);
  const view = island.translate(stack.current(), 'en');
  assert.strictEqual(view.label, 'A pensar…', 'a notice title is backend text, already in the right language');
  assert.strictEqual(view.detail, 'Pronto');
  stack.clear('notice');
  stack.set('chat', island.fromChatActivity({ phase: 'thinking' }).view);
  assert.strictEqual(island.translate(stack.current(), 'en').label, 'Thinking…');
  assert.strictEqual(island.translate(stack.current(), 'pt').label, 'A pensar…');
  assert.strictEqual(island.languageOf('pt-BR'), 'pt');
  assert.strictEqual(island.languageOf('de-DE'), 'en');
});

/* ── The real shell ─────────────────────────────────────────────────────── */

function bootedShell({ ready = true } = {}) {
  const stub = stubElectron();
  const shell = loadFresh(path.join(__dirname, '..', 'main.js'));
  stub.restore();
  const api = shell.__test;
  api.reset();
  api.createMainWindow(51234);
  api.createOverlayWindow();
  const overlay = api.state().overlayWindow;
  overlay.finishLoad();
  if (ready) { api.setIslandReady(true); api.renderIsland(); }
  return { api, overlay, main: api.state().mainWindow, record: stub.record, stub };
}

const lastState = (overlay) => overlay.sent.filter((m) => m.channel === 'nano:overlay-state').at(-1).payload;

suite('island in the shell');

const waitForExit = () => new Promise((resolve) => setTimeout(resolve, 420));

test('by default the island is hidden at rest and appears for activity', async () => {
  const { api, overlay, main } = bootedShell();
  main.hide();
  api.renderIsland();
  await waitForExit();
  assert.strictEqual(overlay.isVisible(), false);
  api.onBackendEvent('chat_activity', { phase: 'thinking' });
  assert.strictEqual(overlay.isVisible(), true);
  assert.strictEqual(lastState(overlay).state, 'thinking');
});

test('"Ilha sempre no ecrã": the resting pill stays on screen', () => {
  const { api, overlay, main } = bootedShell();
  main.hide();
  api.setIslandAlwaysVisible(true);
  assert.strictEqual(overlay.isVisible(), true);
  assert.strictEqual(lastState(overlay).size, 'idle');
  assert.strictEqual(api.state().shellState.islandMode, 'always');
});

test('the pointer held at the top calls the island, and leaving sends it back', async () => {
  const { api, overlay, main, stub } = bootedShell();
  main.hide();
  let cursor = { x: 960, y: 0 };
  stub.electron.screen.getCursorScreenPoint = () => cursor;
  stub.electron.screen.getDisplayNearestPoint = () => ({ bounds: { x: 0, y: 0, width: 1920, height: 1080 },
    workArea: { x: 0, y: 0, width: 1920, height: 1040 } });
  api.peekTick(1000);
  assert.strictEqual(api.state().peekActive, false, 'a pass across the edge is not a call');
  api.peekTick(1300);
  assert.strictEqual(api.state().peekActive, true);
  assert.strictEqual(overlay.isVisible(), true);
  assert.strictEqual(lastState(overlay).size, 'idle');
  cursor = { x: 960, y: 500 };
  api.peekTick(1400);
  assert.strictEqual(api.state().peekActive, true, 'it lingers a moment');
  api.peekTick(2700);
  assert.strictEqual(api.state().peekActive, false);
  assert.strictEqual(lastState(overlay).visible, false, 'it slides away on its own');
  api.syncPeekWatcher();
});

test('a pointer resting on the called island keeps it out', () => {
  const { api, overlay, main, stub } = bootedShell();
  main.hide();
  let cursor = { x: 960, y: 0 };
  stub.electron.screen.getCursorScreenPoint = () => cursor;
  stub.electron.screen.getDisplayNearestPoint = () => ({ bounds: { x: 0, y: 0, width: 1920, height: 1080 },
    workArea: { x: 0, y: 0, width: 1920, height: 1040 } });
  api.peekTick(1000);
  api.peekTick(1300);
  api.onIslandPointer({ sender: overlay.webContents }, true);
  cursor = { x: 960, y: 30 };
  api.peekTick(1500);
  api.peekTick(5000);
  assert.strictEqual(api.state().peekActive, true);
  api.onIslandPointer({ sender: overlay.webContents }, false);
  api.peekTick(5100);
  api.peekTick(6300);
  assert.strictEqual(api.state().peekActive, false);
});

test('listening for its name: the tray can switch it, the resting pill says it', () => {
  const { api, overlay, main } = bootedShell();
  main.hide();
  const requests = [];
  api.setBackend({ running: true, request: (op, args) => { requests.push([op, args]); return Promise.resolve({}); } });
  api.onBackendEvent('ui_language', { lang: 'pt-PT' });
  api.onBackendEvent('wake_state', { enabled: true, listening: true, phrase: 'ei nano' });
  assert.deepStrictEqual(api.state().wakeState, { enabled: true, listening: true, phrase: 'ei nano' });
  api.setIslandAlwaysVisible(true);
  assert.strictEqual(lastState(overlay).wake, 'Diz "Ei Nano" ou');
  api.onBackendEvent('ui_language', { lang: 'en' });
  assert.strictEqual(lastState(overlay).wake, 'Say "Ei Nano" or');
  api.onBackendEvent('wake_state', { enabled: false, listening: false, phrase: 'ei nano' });
  assert.strictEqual(lastState(overlay).wake, '');
  api.setWakePhrase(true);
  assert.deepStrictEqual(requests.at(-1), ['set_wake_phrase', { enabled: true }]);
});

test('with the island set to stay on screen, the pointer is not watched', () => {
  const { api, main } = bootedShell();
  main.hide();
  api.setIslandAlwaysVisible(true);
  api.peekTick(1000);
  api.peekTick(2000);
  assert.strictEqual(api.state().peekActive, false);
});

test('the island steps aside while NANO is in front, and comes back', () => {
  const { api, overlay, main } = bootedShell();
  api.onBackendEvent('chat_activity', { phase: 'thinking' });
  main.show();                         // the stub focuses it
  api.renderIsland();
  assert.strictEqual(lastState(overlay).visible, false, 'NANO already shows all of it');
  main.hide();
  main.emit('hide');
  assert.strictEqual(lastState(overlay).state, 'thinking');
});

test('an approval shows until it is resolved, and never restores NANO', () => {
  const { api, overlay, main } = bootedShell();
  main.hide();
  api.onBackendEvent('approval', { phase: 'request', id: 'r1', action: 'APAGAR FICHEIRO', target: 'a.txt' });
  const view = lastState(overlay);
  assert.strictEqual(view.state, 'approval');
  assert.strictEqual(view.detail, 'APAGAR FICHEIRO — a.txt');
  assert.strictEqual(main.isVisible(), false);
  api.onBackendEvent('approval', { phase: 'resolved', id: 'r1' });
  assert.strictEqual(lastState(overlay).visible, false, 'answered: the island hides again');
});

test('a reminder, a PC alert and a download each show on the island', () => {
  const { api, overlay, main } = bootedShell();
  main.hide();
  api.onBackendEvent('ui_language', { lang: 'pt-PT' });
  api.onBackendEvent('reminder_due', { id: 1, text: 'Treino', due_label: '18:30' });
  assert.strictEqual(lastState(overlay).label, 'Lembrete · 18:30');
  api.onBackendEvent('nano_notice', { kind: 'battery', title: 'Bateria fraca', text: '10%' });
  assert.strictEqual(lastState(overlay).icon, 'battery');
  api.onBackendEvent('nano_notice', { kind: 'download', title: 'Download terminado', text: 'jogo.zip' });
  assert.strictEqual(lastState(overlay).state, 'download');
});

test('a new song opens the player briefly; volume shows its level', () => {
  const { api, overlay, main } = bootedShell();
  main.hide();
  api.onBackendEvent('media', { status: 'playing', title: 'Entropy', artist: 'Beach Bunny', changed: 'track', key: 'k' });
  const card = lastState(overlay);
  assert.strictEqual(card.state, 'media');
  assert.strictEqual(card.expanded, true);
  api.onBackendEvent('volume', { level: 35, muted: false });
  assert.strictEqual(lastState(overlay).state, 'volume');
  assert.strictEqual(lastState(overlay).level, 35);
  api.onBackendEvent('media', { status: 'none' });
  api.state().islandStack.clear('hud');
  api.renderIsland();
  assert.strictEqual(lastState(overlay).visible, false, 'the song stopped: hidden again');
});

test('the island\'s signals are only accepted from the island window', () => {
  const { api, overlay, main } = bootedShell();
  main.hide();
  const requests = [];
  api.setBackend({ running: true, request: (op, args) => { requests.push([op, args]); return Promise.resolve({}); } });

  const stranger = { sender: main.webContents };
  api.onIslandOpen(stranger);
  api.onIslandMedia(stranger, 'next');
  assert.strictEqual(main.isVisible(), false, 'the main window cannot open itself through the island channel');
  assert.deepStrictEqual(requests, []);

  const fromIsland = { sender: overlay.webContents };
  api.onIslandMedia(fromIsland, 'shutdown');
  api.onIslandMedia(fromIsland, 'next');
  assert.deepStrictEqual(requests, [['media_control', { action: 'next' }]]);
  api.onIslandOpen(fromIsland);
  assert.strictEqual(main.isVisible(), true, 'a click on the island opens NANO');
});

test('the backend\'s language reaches the island', () => {
  const { api, overlay, main } = bootedShell();
  main.hide();
  api.onBackendEvent('ui_language', { lang: 'en' });
  api.onBackendEvent('chat_activity', { phase: 'thinking' });
  assert.strictEqual(lastState(overlay).label, 'Thinking…');
  api.onBackendEvent('ui_language', { lang: 'pt-PT' });
  assert.strictEqual(lastState(overlay).label, 'A pensar…');
});

test('switching the island off hides even an approval, and says so in the log', () => {
  const { api, overlay, main } = bootedShell();
  main.hide();
  api.setOverlayEnabled(false);
  api.onBackendEvent('approval', { phase: 'request', id: 'r', action: 'X' });
  assert.strictEqual(overlay.isVisible(), false);
});
