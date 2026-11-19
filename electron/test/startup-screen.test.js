'use strict';

const fs = require('fs');
const path = require('path');
const { assert, suite, test, stubElectron, loadFresh } = require('./harness');

suite('startup screen (no black window)');

const STARTUP = path.join(__dirname, '..', 'startup', 'startup.html');

test('the window opens on the local startup screen before the backend is up', () => {
  const stub = stubElectron();
  try {
    const main = loadFresh(path.join(__dirname, '..', 'main.js'));
    main.__test.reset();
    main.__test.createMainWindow();
    const [window] = stub.record.windows;
    assert.deepStrictEqual(window.loads, [{ file: STARTUP, options: { query: { state: 'loading' } } }]);
    main.__test.attachMainWindow(4321);
    assert.deepStrictEqual(window.loads[1], { url: 'http://127.0.0.1:4321/index.html' });
  } finally { stub.restore(); }
});

test('the error screen only shows a fixed reason code', () => {
  const stub = stubElectron();
  try {
    const main = loadFresh(path.join(__dirname, '..', 'main.js'));
    main.__test.reset();
    main.__test.createMainWindow();
    const [window] = stub.record.windows;
    main.__test.showStartupScreen(window, 'error', 'frontend_not_rendered');
    assert.deepStrictEqual(window.loads.pop().options.query,
      { state: 'error', reason: 'frontend_not_rendered' });
  } finally { stub.restore(); }
});

test('the startup page ships, has a strict CSP and renders text, never markup', () => {
  const html = fs.readFileSync(STARTUP, 'utf8');
  assert.ok(html.includes("default-src 'none'") && html.includes("script-src 'self'"));
  assert.ok(!/<script>(?!\s*<\/script>)/.test(html), 'no inline script');
  const js = fs.readFileSync(path.join(__dirname, '..', 'startup', 'startup.js'), 'utf8');
  assert.ok(!js.includes('innerHTML'), 'the page must set textContent only');
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'));
  assert.ok(pkg.build.files.includes('startup/**/*'), 'the startup screen is packaged');
});
