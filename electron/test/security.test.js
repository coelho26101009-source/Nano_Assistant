/**
 * The security boundary between the page and the machine.
 *
 * These tests LOAD the real preload and the real main process with a stubbed
 * `electron` module and then inspect what they actually did -- which functions
 * were exposed, which IPC channels were really registered. That matters: a test
 * that searches the source text for "nodeIntegration: false" passes when the
 * line moves into a comment, and passes when a second BrowserWindow is added
 * without it.
 */
'use strict';

const path = require('path');
const { assert, loadFresh, stubElectron, suite, test } = require('./harness');

/** Load a module under the electron stub and hand back what it did. */
function loadUnderStub(relativePath, overrides) {
  const stub = stubElectron(overrides);
  try {
    const exports = loadFresh(path.join(__dirname, '..', relativePath));
    return { exports, record: stub.record };
  } finally {
    stub.restore();
  }
}

/* ── preload.js ─────────────────────────────────────────────────────────── */

suite('preload surface');

/** Exactly the API the UI is allowed to have. Adding to this is a decision. */
const ALLOWED_PRELOAD_KEYS = [
  'isDesktop',
  'minimize', 'toggleMaximize', 'hide', 'quit',
  'getWindowState', 'onWindowState',
  'getDesktopStatus', 'retryShortcut',
  'setOverlayEnabled', 'setAutoLaunch',
];

test('the preload exposes one namespace and nothing else', () => {
  const { record } = loadUnderStub('preload.js');
  assert.deepStrictEqual(Object.keys(record.exposed), ['nanoApp']);
});

test('the exposed API is exactly the allow-list', () => {
  const { record } = loadUnderStub('preload.js');
  const keys = Object.keys(record.exposed.nanoApp).sort();
  assert.deepStrictEqual(keys, [...ALLOWED_PRELOAD_KEYS].sort(),
    'a new entry on window.nanoApp is a widening of the security boundary');
});

test('no Node primitive or escape hatch is reachable from the page', () => {
  const { record } = loadUnderStub('preload.js');
  const api = record.exposed.nanoApp;
  // The names that would each, on their own, end the sandbox.
  for (const forbidden of [
    'invoke', 'send', 'on', 'once', 'emit', 'ipcRenderer', 'ipc',
    'exec', 'execSync', 'spawn', 'shell', 'openExternal',
    'fs', 'readFile', 'writeFile', 'path', 'process', 'require',
    'eval', 'runTool', 'execute',
  ]) {
    assert.ok(!(forbidden in api), `window.nanoApp must not expose "${forbidden}"`);
  }
});

test('the exposed object is frozen, so the page cannot extend it', () => {
  const { record } = loadUnderStub('preload.js');
  assert.ok(Object.isFrozen(record.exposed.nanoApp));
});

test('every exposed operation targets a fixed channel it chooses itself', () => {
  const stub = stubElectron();
  let api;
  try {
    loadFresh(path.join(__dirname, '..', 'preload.js'));
    api = stub.record.exposed.nanoApp;

    // Call everything with a hostile argument: a caller must never be able to
    // choose the channel, no matter what it passes.
    api.minimize('nano:quit');
    api.toggleMaximize('../../etc/passwd');
    api.hide({ channel: 'anything' });
    api.getWindowState('nano:quit');
    api.getDesktopStatus('nano:quit');

    const channels = [
      ...stub.record.rendererSend.map((c) => c.channel),
      ...stub.record.rendererInvoke.map((c) => c.channel),
    ];
    for (const channel of channels) {
      assert.ok(channel.startsWith('nano:'),
        `"${channel}" is outside the nano: namespace — the caller chose it`);
    }
    assert.deepStrictEqual(stub.record.rendererSend.map((c) => c.channel),
      ['nano:minimize', 'nano:toggle-maximize', 'nano:hide']);
  } finally {
    stub.restore();
  }
});

test('a non-function subscriber is ignored rather than crashing the page', () => {
  const stub = stubElectron();
  try {
    loadFresh(path.join(__dirname, '..', 'preload.js'));
    const api = stub.record.exposed.nanoApp;
    const off = api.onWindowState(undefined);
    assert.strictEqual(typeof off, 'function');
    off();
  } finally {
    stub.restore();
  }
});

/* ── overlay preload ────────────────────────────────────────────────────── */

suite('overlay preload surface');

test('the overlay can only receive a state', () => {
  const { record } = loadUnderStub(path.join('overlay', 'preload.js'));
  assert.deepStrictEqual(Object.keys(record.exposed), ['nanoOverlay']);
  assert.deepStrictEqual(Object.keys(record.exposed.nanoOverlay), ['onState'],
    'the overlay is a status light: it must have no way to send anything back');
});

/* ── main.js ────────────────────────────────────────────────────────────── */

suite('main process IPC surface');

/** Exactly the channels the renderer may reach. */
const ALLOWED_IPC = [
  'nano:minimize', 'nano:toggle-maximize', 'nano:hide',
  'nano:window-state', 'nano:desktop-status', 'nano:set-overlay-enabled',
  'nano:set-auto-launch', 'nano:retry-shortcut', 'nano:quit',
];

test('the main process registers only the allow-listed channels', () => {
  const { record } = loadUnderStub('main.js');
  const registered = [...record.ipcOn, ...record.ipcHandle].sort();
  assert.deepStrictEqual(registered, [...ALLOWED_IPC].sort(),
    'an unexpected ipcMain channel is a new path from the page into the machine');
});

test('no registered channel hints at execution', () => {
  const { record } = loadUnderStub('main.js');
  for (const channel of [...record.ipcOn, ...record.ipcHandle]) {
    assert.ok(!/exec|spawn|shell|command|run|eval|file|read|write/i.test(channel),
      `"${channel}" sounds like an execution or filesystem path`);
  }
});

test('loading the main process starts nothing', () => {
  // whenReady never resolves in the stub, so a module-load side effect that
  // spawned Python or opened a window would show up here as a window.
  const { record } = loadUnderStub('main.js');
  assert.strictEqual(record.browserWindows.length, 0);
  assert.strictEqual(record.quit, 0);
});

test('a second instance quits immediately instead of starting a second Nano', () => {
  const { record } = loadUnderStub('main.js', {
    app: { requestSingleInstanceLock: () => false },
  });
  assert.strictEqual(record.quit, 1, 'the second launch must quit');
  assert.strictEqual(record.browserWindows.length, 0, 'and create no window');
  assert.ok(record.appEvents.includes('activate'),
    'the surviving instance still wires its lifecycle events');
});

test('the first instance listens for later launches so it can focus itself', () => {
  const { record } = loadUnderStub('main.js');
  assert.ok(record.appEvents.includes('second-instance'),
    'without this, a second launch would do nothing visible at all');
});

test('the activation accelerator is the documented one', () => {
  const { exports } = loadUnderStub('main.js');
  assert.strictEqual(exports.ACTIVATION_ACCELERATOR, 'CommandOrControl+Shift+Space');
});

/* ── Content Security Policy ────────────────────────────────────────────── */

suite('content security policy');

test('the main window installs a CSP on every response', () => {
  const { exports, record } = loadUnderStub('main.js');
  exports.__test.reset();
  exports.__test.createMainWindow(4321);

  const [window] = record.windows.filter((w) => !w.options.alwaysOnTop);
  assert.ok(window, 'the main window was not created');

  // Behavioural, not textual: invoke the handler main.js actually registered
  // and inspect the headers it produces.
  const handler = window._headersReceived;
  assert.ok(typeof handler === 'function',
    'main.js registered no onHeadersReceived handler, so no CSP is applied');

  let result = null;
  handler({ responseHeaders: { 'content-type': ['text/html'] } }, (r) => { result = r; });
  const policy = (result.responseHeaders['Content-Security-Policy'] || [])[0];
  assert.ok(policy, 'the response carried no Content-Security-Policy header');
  assert.ok(policy.includes("default-src 'none'"), 'the policy does not deny by default');
  assert.ok(policy.includes("script-src 'self'"), 'script-src is missing');
  assert.ok(policy.includes('ws://127.0.0.1:4321'),
    'the policy does not permit the local control-plane socket, so the UI would not connect');
});

test('a policy sent by the backend cannot widen or break the one we set', () => {
  const { exports, record } = loadUnderStub('main.js');
  exports.__test.reset();
  exports.__test.createMainWindow(4321);
  const window = record.windows.filter((w) => !w.options.alwaysOnTop)[0];

  let result = null;
  window._headersReceived(
    { responseHeaders: { 'Content-Security-Policy': ["default-src *"] } },
    (r) => { result = r; },
  );

  const values = Object.entries(result.responseHeaders)
    .filter(([key]) => key.toLowerCase() === 'content-security-policy')
    .map(([, value]) => value[0]);
  assert.strictEqual(values.length, 1, 'two CSP headers would intersect unpredictably');
  assert.ok(!values[0].includes('default-src *'), 'the backend policy survived');
});

test('script-src never permits inline or eval', () => {
  const { exports } = loadUnderStub('main.js');
  const policy = exports.contentSecurityPolicy(1234);
  const scriptSrc = policy.split(';').find((part) => part.trim().startsWith('script-src'));
  assert.ok(scriptSrc, 'script-src disappeared');
  assert.ok(!scriptSrc.includes('unsafe-inline'), "script-src allows 'unsafe-inline'");
  assert.ok(!scriptSrc.includes('unsafe-eval'), "script-src allows 'unsafe-eval'");
});
