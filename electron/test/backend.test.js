/**
 * The Python backend's lifecycle, driven against a fake child process.
 *
 * No Python and no Electron: the point is to exercise the real NanoBackend
 * against a stand-in that speaks the real protocol, so the behaviours that
 * matter for the hotkey -- readiness gating, exactly one request per press,
 * honest busy answers, clean shutdown -- are tested rather than assumed.
 */
'use strict';

const EventEmitter = require('events');
const { assert, suite, test } = require('./harness');
const { NanoBackend } = require('../lib/backend');
const { LineReader, encode } = require('../lib/ipc-protocol');

/**
 * A stand-in for `core/main.py --desktop-control`.
 *
 * `operations` is the allow-list, exactly as DESKTOP_OPERATIONS is on the
 * Python side: anything not in it is answered with `unknown_operation`, so the
 * fail-closed behaviour is part of what these tests exercise.
 */
function fakePython(operations = {}) {
  const child = new EventEmitter();
  child.pid = 4242;
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.received = [];
  child.killed = false;

  const reader = new LineReader({
    onMessage: (request) => {
      child.received.push(request);
      const handler = operations[request.op];
      const reply = handler
        ? { id: request.id, ok: true, op: request.op, result: handler(request.args || {}) }
        : { id: request.id, ok: false, error: 'unknown_operation', op: request.op };
      // Asynchronous, like a real process.
      setImmediate(() => child.stdout.emit('data', Buffer.from(encode(reply))));
    },
  });

  child.stdin = {
    destroyed: false,
    write: (chunk) => { reader.push(chunk); return true; },
  };
  child.kill = () => { child.killed = true; child.emit('exit', 0, null); };
  child.emitLog = (line) => child.stdout.emit('data', Buffer.from(`${line}\n`));
  child.emitEvent = (event, payload) =>
    child.stdout.emit('data', Buffer.from(encode({ event, payload })));

  return child;
}

function makeBackend(child) {
  const backend = new NanoBackend({ spawnFn: () => child });
  backend.start({ command: 'python', args: [], options: {} });
  return backend;
}

suite('backend control channel');

test('a control request gets its answer back', async () => {
  const backend = makeBackend(fakePython({ ping: () => ({ pong: true, pid: 7 }) }));
  const reply = await backend.request('ping');
  assert.strictEqual(reply.ok, true);
  assert.strictEqual(reply.result.pong, true);
});

test('an operation the backend does not implement fails closed', async () => {
  const backend = makeBackend(fakePython({ ping: () => ({}) }));
  const reply = await backend.request('run_shell_command', { cmd: 'whoami' });
  assert.strictEqual(reply.ok, false);
  assert.strictEqual(reply.error, 'unknown_operation');
});

test('replies are matched to their own request, not to arrival order', async () => {
  const child = fakePython({ ping: () => ({ tag: 'ping' }), voice_status: () => ({ tag: 'status' }) });
  const backend = makeBackend(child);
  const [a, b] = await Promise.all([backend.request('ping'), backend.request('voice_status')]);
  assert.strictEqual(a.result.tag, 'ping');
  assert.strictEqual(b.result.tag, 'status');
});

test('a request with no answer rejects instead of hanging', async () => {
  const child = fakePython();
  child.stdin.write = () => true;   // swallow it: the backend never replies
  const backend = makeBackend(child);
  await assert.rejects(() => backend.request('ping', {}, { timeoutMs: 60 }), /Sem resposta/);
});

test('the backend exiting rejects everything in flight', async () => {
  const child = fakePython();
  child.stdin.write = () => true;
  const backend = makeBackend(child);
  const pending = backend.request('ping', {}, { timeoutMs: 5000 });
  child.emit('exit', 1, null);
  await assert.rejects(() => pending, /terminou/);
});

suite('backend readiness');

test('waitUntilAlive resolves only once the backend answers', async () => {
  const child = fakePython({ ping: () => ({ pong: true }) });
  const backend = makeBackend(child);
  assert.strictEqual(await backend.waitUntilAlive({ timeoutMs: 2000, intervalMs: 20 }), true);
});

test('waitUntilAlive fails fast when the backend dies during startup', async () => {
  const child = fakePython();
  child.stdin.write = () => true;
  const backend = makeBackend(child);
  setTimeout(() => child.emit('exit', 9, null), 30);
  await assert.rejects(
    () => backend.waitUntilAlive({ timeoutMs: 4000, intervalMs: 20 }),
    /terminou/,
    'a dead backend must be reported, not waited on until the timeout',
  );
});

test('waitUntilAlive gives up rather than retrying forever', async () => {
  const child = fakePython();
  child.stdin.write = () => true;
  const backend = makeBackend(child);
  await assert.rejects(
    () => backend.waitUntilAlive({ timeoutMs: 200, intervalMs: 40 }),
    /não ficou pronto/,
  );
});

suite('hotkey behaviour');

test('one activation sends exactly one start_voice_turn', async () => {
  const child = fakePython({ start_voice_turn: () => ({ ok: true, accepted: true, source: 'hotkey' }) });
  const backend = makeBackend(child);
  const reply = await backend.request('start_voice_turn', { source: 'hotkey' });

  const starts = child.received.filter((r) => r.op === 'start_voice_turn');
  assert.strictEqual(starts.length, 1, 'one press must not become two turns');
  assert.strictEqual(starts[0].args.source, 'hotkey');
  assert.strictEqual(reply.result.accepted, true);
});

test('a second activation during a turn is answered busy, not queued', async () => {
  let active = false;
  const child = fakePython({
    start_voice_turn: () => {
      if (active) return { ok: false, accepted: false, busy: true, active_source: 'hotkey' };
      active = true;
      return { ok: true, accepted: true, source: 'hotkey' };
    },
  });
  const backend = makeBackend(child);

  const first = await backend.request('start_voice_turn', { source: 'hotkey' });
  const second = await backend.request('start_voice_turn', { source: 'hotkey' });

  assert.strictEqual(first.result.accepted, true);
  assert.strictEqual(second.result.busy, true);
  assert.strictEqual(second.result.accepted, false,
    'the second press must be refused, so no second microphone reader opens');
});

test('the hotkey path can only ask for a voice turn', async () => {
  // The operation name is the entire vocabulary. There is no argument that
  // turns start_voice_turn into "run this": args carry a source, nothing else.
  const child = fakePython({ start_voice_turn: (args) => ({ echoed: args }) });
  const backend = makeBackend(child);
  await backend.request('start_voice_turn', { source: 'hotkey' });
  assert.deepStrictEqual(Object.keys(child.received[0].args), ['source']);
});

suite('backend events');

test('voice phase events reach the shell', async () => {
  const child = fakePython();
  const backend = makeBackend(child);
  const seen = [];
  backend.on('event', (name, payload) => seen.push([name, payload.phase]));

  child.emitEvent('voice_phase', { phase: 'COMMAND_LISTENING', detail: 'A ouvir comando…' });
  child.emitEvent('voice_phase', { phase: 'IDLE', detail: '' });
  await new Promise((r) => setImmediate(r));

  assert.deepStrictEqual(seen, [['voice_phase', 'COMMAND_LISTENING'], ['voice_phase', 'IDLE']]);
});

test('the announced HTTP port is recorded', async () => {
  const child = fakePython();
  const backend = makeBackend(child);
  child.emitEvent('backend_started', { port: 51234, mode: 'electron' });
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(backend.port, 51234);
});

test('backend log output does not become an event', async () => {
  const child = fakePython();
  const backend = makeBackend(child);
  const events = [];
  const logs = [];
  backend.on('event', (name) => events.push(name));
  backend.on('log', (line) => logs.push(line));

  child.emitLog('  [NANO] Ollama ....... READY  (llama3.1:8b)');
  await new Promise((r) => setImmediate(r));

  assert.strictEqual(events.length, 0);
  assert.strictEqual(logs.length, 1);
});

suite('backend shutdown');

test('quitting asks politely, then confirms the process is gone', async () => {
  const child = fakePython({
    shutdown: () => { setTimeout(() => child.emit('exit', 0, null), 10); return { stopping: true }; },
  });
  const backend = makeBackend(child);
  await backend.stop({ graceMs: 500 });

  assert.ok(child.received.some((r) => r.op === 'shutdown'), 'a clean shutdown is requested first');
  assert.strictEqual(backend.running, false);
});

test('a wedged backend is killed rather than left orphaned', async () => {
  const child = fakePython();          // never answers, never exits
  child.stdin.write = () => true;
  const backend = makeBackend(child);
  const killed = [];

  await backend.stop({ graceMs: 60, killTree: (pid) => { killed.push(pid); child.emit('exit', null, 'SIGKILL'); } });

  assert.deepStrictEqual(killed, [4242], 'the process tree must be killed if it will not stop');
});
