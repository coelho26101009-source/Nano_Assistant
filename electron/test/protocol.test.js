/** The control-channel wire format, and the reader that separates it from logs. */
'use strict';

const { assert, suite, test } = require('./harness');
const { LineReader, PROTOCOL_TAG, decode, encode } = require('../lib/ipc-protocol');

suite('ipc-protocol');

test('a message survives a round trip', () => {
  const original = { id: '3', op: 'start_voice_turn', args: { source: 'hotkey' } };
  const line = encode(original);
  assert.ok(line.startsWith(PROTOCOL_TAG), 'the line must be tagged');
  assert.ok(line.endsWith('\n'), 'the line must be terminated');
  assert.deepStrictEqual(decode(line), original);
});

test('ordinary log output is not mistaken for a message', () => {
  assert.strictEqual(decode('  [NANO] Voice/STT ... READY'), null);
  assert.strictEqual(decode('Traceback (most recent call last):'), null);
  assert.strictEqual(decode(''), null);
  assert.strictEqual(decode('{"op":"start_voice_turn"}'), null,
    'untagged JSON must NOT be accepted: only the framed protocol counts');
});

test('a malformed protocol line is dropped, not thrown', () => {
  assert.strictEqual(decode(`${PROTOCOL_TAG}{not json`), null);
  assert.strictEqual(decode(`${PROTOCOL_TAG}[1,2,3]`), null, 'only objects are messages');
  assert.strictEqual(decode(`${PROTOCOL_TAG}"a string"`), null);
});

test('a tag appearing after log text is still parsed', () => {
  // Python writes whole lines under a lock, but another thread can print
  // immediately before one; the reader must not lose the message.
  const line = `[NANO] something${encode({ event: 'voice_phase', payload: { phase: 'IDLE' } })}`;
  assert.deepStrictEqual(decode(line), { event: 'voice_phase', payload: { phase: 'IDLE' } });
});

suite('LineReader');

test('messages and log lines are routed separately', () => {
  const messages = [];
  const logs = [];
  const reader = new LineReader({ onMessage: (m) => messages.push(m), onLog: (l) => logs.push(l) });

  reader.push('  [NANO] Backend ...... READY\n');
  reader.push(encode({ id: '1', ok: true }));
  reader.push('  [NANO] Voice ........ READY\r\n');

  assert.deepStrictEqual(messages, [{ id: '1', ok: true }]);
  assert.deepStrictEqual(logs, ['  [NANO] Backend ...... READY', '  [NANO] Voice ........ READY']);
});

test('a message split across chunk boundaries is reassembled', () => {
  const messages = [];
  const reader = new LineReader({ onMessage: (m) => messages.push(m) });
  const line = encode({ event: 'voice_phase', payload: { phase: 'SPEAKING' } });

  // Stream chunking is arbitrary; this is the case that silently loses events
  // if the reader parses per-chunk instead of per-line.
  for (let i = 0; i < line.length; i += 7) reader.push(line.slice(i, i + 7));

  assert.strictEqual(messages.length, 1);
  assert.strictEqual(messages[0].payload.phase, 'SPEAKING');
});

test('several messages in one chunk all arrive, in order', () => {
  const messages = [];
  const reader = new LineReader({ onMessage: (m) => messages.push(m) });
  reader.push(encode({ id: 'a' }) + encode({ id: 'b' }) + encode({ id: 'c' }));
  assert.deepStrictEqual(messages.map((m) => m.id), ['a', 'b', 'c']);
});

test('an unterminated line cannot grow without bound', () => {
  const reader = new LineReader({});
  for (let i = 0; i < 200; i += 1) reader.push('x'.repeat(4096));
  assert.ok(reader._buffer.length <= 64 * 1024 * 4,
    'the buffer must be capped so a broken stream cannot exhaust memory');
});
