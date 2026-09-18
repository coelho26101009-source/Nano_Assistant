'use strict';

const http = require('http');
const path = require('path');
const { assert, suite, test, stubElectron, loadFresh } = require('./harness');
const { diagnostics, waitForHttp, loadRecovery } = require('../lib/startup-health');

suite('Beta startup recovery');

test('backend restart replaces stale navigation and websocket CSP origins', () => {
  const stub = stubElectron();
  try {
    const main = loadFresh(path.join(__dirname, '..', 'main.js'));
    main.__test.reset(); main.__test.createMainWindow(4321);
    const previous = stub.record.windows[0];
    main.__test.replaceMainWindow(5432);
    assert.strictEqual(previous.isDestroyed(), true);
    const current = stub.record.windows[1];
    let blocked = 0;
    const event = { preventDefault: () => blocked++ };
    current._handlers['wc:will-navigate'](event, 'http://127.0.0.1:5432/index.html');
    assert.strictEqual(blocked, 0);
    current._handlers['wc:will-navigate'](event, 'http://127.0.0.1:4321/index.html');
    assert.strictEqual(blocked, 1);
    current._headersReceived({ responseHeaders: {} }, ({ responseHeaders }) => {
      const policy = responseHeaders['Content-Security-Policy'][0];
      assert.ok(policy.includes('ws://127.0.0.1:5432'));
      assert.ok(!policy.includes('4321'));
    });
  } finally { stub.restore(); }
});

test('diagnostics excludes secrets, paths, conversations and arbitrary errors', () => {
  const report = diagnostics({ version: '0.1.0-beta.1', packaged: true,
    backend: { running: true, lastExitCode: 1, secret: 'test-credential', messages: ['private'] },
    frontendReady: true, lastErrorCode: 'Authorization: test-credential',
    env: { key: 'test-credential' }, dataDir: 'C:/private-profile',
    versions: { electron: '44.2.0', chrome: '152', secret: 'test-credential' } });
  assert.strictEqual(report.backend.running, true);
  assert.strictEqual(report.lastErrorCode, null);
  for (const excluded of ['test-credential', 'private', 'Authorization']) {
    assert.ok(!JSON.stringify(report).includes(excluded));
  }
  assert.deepStrictEqual(Object.keys(report.backend), ['running', 'lastExitCode']);
  assert.deepStrictEqual(Object.keys(report.versions), ['electron', 'chrome']);
});

test('permanent page failure stops after two retries and reports once', () => {
  const queue = [];
  let reloads = 0;
  let reports = 0;
  const recovery = loadRecovery({ reload: () => reloads++, report: () => reports++,
    schedule: (fn) => { queue.push(fn); return queue.length; }, cancel: () => {} });
  recovery.failed(); recovery.failed(); // duplicate events cannot multiply retries
  assert.strictEqual(queue.length, 1);
  queue.shift()(); recovery.failed(); queue.shift()();
  recovery.failed(); recovery.failed();
  assert.strictEqual(reloads, 2);
  assert.strictEqual(reports, 1);
  assert.strictEqual(queue.length, 0);
  recovery.ready(); recovery.failed();
  assert.strictEqual(queue.length, 1, 'a successful load resets the retry budget');
});

test('a hung HTTP response hits the overall startup deadline', async () => {
  const sockets = new Set();
  const server = http.createServer(() => {});
  server.on('connection', (socket) => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    await assert.rejects(waitForHttp(server.address().port, 70), /não ficou disponível/);
  } finally {
    for (const socket of sockets) socket.destroy();
    await new Promise((resolve) => server.close(resolve));
  }
});

test('HTTP readiness requires an actual successful page', async () => {
  let calls = 0;
  const server = http.createServer((_req, response) => {
    response.writeHead(++calls === 1 ? 503 : 200); response.end('test page');
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    await waitForHttp(server.address().port, 1000, { intervalMs: 5 });
    assert.strictEqual(calls, 2);
  } finally { await new Promise((resolve) => server.close(resolve)); }
});

test('HTTP readiness fails immediately if its backend has died', async () => {
  await assert.rejects(waitForHttp(1, 1000, { alive: () => false }), /motor do Nano parou/);
});

test('Electron quit is intercepted so the backend gets its shutdown path', async () => {
  const stub = stubElectron();
  try {
    const main = loadFresh(path.join(__dirname, '..', 'main.js'));
    main.__test.reset();
    let prevented = 0;
    stub.record.appHandlers['before-quit']({ preventDefault: () => prevented++ });
    assert.strictEqual(prevented, 1);
    await new Promise((resolve) => setImmediate(resolve));
    assert.strictEqual(stub.record.quit, 1);
    stub.record.appHandlers['before-quit']({ preventDefault: () => prevented++ });
    assert.strictEqual(prevented, 1, 'the final app.quit must be allowed through');
  } finally { stub.restore(); }
});

test('Chromium permission checks deny by default as well as permission requests', () => {
  const stub = stubElectron();
  try {
    const main = loadFresh(path.join(__dirname, '..', 'main.js'));
    main.__test.reset(); main.__test.createMainWindow(4321);
    assert.strictEqual(stub.record.windows[0]._permissionCheck(null, 'media'), false);
    assert.strictEqual(stub.record.windows[0]._permissionCheck(null, 'clipboard-read'), false);
  } finally { stub.restore(); }
});
