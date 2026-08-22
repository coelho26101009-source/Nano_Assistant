/**
 * The overlay shows what the backend is really doing.
 *
 * These tests exist to stop the overlay ever growing a timeline of its own:
 * every state must be reachable only from a real backend event, and the states
 * a user can see must never contain a traceback or an internal error code.
 */
'use strict';

const { assert, suite, test } = require('./harness');
const overlay = require('../lib/overlay-state');

suite('overlay states');

test('every phase of a real voice turn maps to a visible state', () => {
  // Exactly the phases VoiceRuntime.run_voice_turn emits, in order.
  const expected = [
    ['WAKE_DETECTED', 'activated', 'A ouvir…'],
    ['COMMAND_LISTENING', 'listening', 'A ouvir…'],
    ['TRANSCRIBING', 'transcribing', 'A transcrever…'],
    ['PROCESSING', 'processing', 'A processar…'],
    ['SPEAKING', 'speaking', 'A falar…'],
  ];
  for (const [phase, state, label] of expected) {
    const view = overlay.fromPhase(phase);
    assert.ok(view, `${phase} must map to a view`);
    assert.strictEqual(view.visible, true, `${phase} must be visible`);
    assert.strictEqual(view.state, state);
    assert.strictEqual(view.label, label);
  }
});

test('the two resting phases hide the overlay', () => {
  for (const phase of ['IDLE', 'WAKE_LISTENING']) {
    assert.strictEqual(overlay.fromPhase(phase).visible, false,
      `${phase} means the turn is over; the overlay must go away`);
  }
});

test('an unknown phase changes nothing', () => {
  // Returning HIDDEN here would make a future backend phase flash the overlay
  // away mid-turn; null means "leave the last real state alone".
  assert.strictEqual(overlay.fromPhase('SOMETHING_NEW'), null);
  assert.strictEqual(overlay.fromPhase(undefined), null);
});

suite('overlay turn outcomes');

test('a successful turn shows a brief confirmation, then hides itself', () => {
  const view = overlay.fromTurnEnd({ ok: true, spoken: true });
  assert.strictEqual(view.state, 'done');
  assert.ok(view.hideAfterMs > 0, 'the overlay must hide itself after a turn');
});

test('silence is a quiet outcome, not an error', () => {
  const view = overlay.fromTurnEnd({ ok: false, cancelled: true, error: 'no_speech' });
  assert.strictEqual(view.state, 'quiet');
  assert.strictEqual(view.label, 'Não ouvi nada');
  assert.ok(view.hideAfterMs > 0);
});

test('a failure shows a short human sentence, never an internal code', () => {
  const view = overlay.fromTurnEnd({ ok: false, error: 'microphone_failed' });
  assert.strictEqual(view.state, 'error');
  assert.strictEqual(view.label, 'Sem acesso ao microfone');
});

test('an unrecognised error still produces a human sentence', () => {
  const view = overlay.fromTurnEnd({ ok: false, error: 'groq.APIStatusError: 500' });
  assert.strictEqual(view.label, overlay.GENERIC_ERROR);
  assert.ok(!view.label.includes('groq'), 'provider internals must not reach the user');
  assert.ok(!view.label.includes('500'));
});

test('no user-visible label leaks an identifier or a traceback', () => {
  const labels = [...Object.values(overlay.ERROR_LABEL), overlay.GENERIC_ERROR];
  for (const label of labels) {
    assert.ok(!/_/.test(label), `"${label}" looks like a code, not a sentence`);
    assert.ok(!/Traceback|Error:|Exception/i.test(label), `"${label}" leaks internals`);
    assert.ok(label.length <= 40, `"${label}" is too long for a 320 px panel`);
  }
});

suite('overlay busy state');

test('a refused second activation reports busy, honestly', () => {
  const view = overlay.busyView({ busy: true, active_source: 'hotkey' });
  assert.strictEqual(view.state, 'busy');
  assert.strictEqual(view.label, 'O Nano já está ocupado');
  assert.ok(view.hideAfterMs > 0, 'busy must not stick on screen');
});

test('a backend that is not ready says so instead of failing silently', () => {
  const view = overlay.unavailableView('not_ready');
  assert.strictEqual(view.visible, true);
  assert.strictEqual(view.state, 'error');
  assert.ok(view.label.length > 0);
});
