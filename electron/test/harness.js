/**
 * A tiny test harness for the Electron shell, with two jobs.
 *
 * 1. RUN TESTS WITHOUT A TEST FRAMEWORK. Adding jest or mocha to ship a desktop
 *    shell is a lot of dependency for a handful of assertions, and `node
 *    test/run.js` is something pytest can shell out to, so the whole suite
 *    stays one command.
 *
 * 2. LOAD REAL ELECTRON CODE WITHOUT ELECTRON. `stubElectron()` intercepts
 *    `require('electron')` so main.js and preload.js can be loaded and then
 *    INSPECTED -- which channels they actually register, which functions they
 *    actually expose. That is the difference between asserting on the source
 *    text and asserting on behaviour: a test that greps for a string passes if
 *    the string moves into a comment.
 */
'use strict';

const assert = require('assert');
const Module = require('module');

const tests = [];
let currentSuite = '';

function suite(name) { currentSuite = name; }

function test(name, fn) {
  tests.push({ name: currentSuite ? `${currentSuite} — ${name}` : name, fn });
}

async function run() {
  let passed = 0;
  const failures = [];
  for (const entry of tests) {
    try {
      await entry.fn();
      passed += 1;
      process.stdout.write(`  PASS  ${entry.name}\n`);
    } catch (err) {
      failures.push({ name: entry.name, err });
      process.stdout.write(`  FAIL  ${entry.name}\n        ${err.message}\n`);
    }
  }
  process.stdout.write(`\n${passed} passed, ${failures.length} failed\n`);
  if (failures.length) {
    for (const failure of failures) {
      process.stdout.write(`\n--- ${failure.name} ---\n${failure.err.stack}\n`);
    }
    process.exitCode = 1;
  }
}

/* ── Electron stub ──────────────────────────────────────────────────────── */

/**
 * Replace `require('electron')` for the duration of one load, recording every
 * interaction so the caller can assert on what the module really did.
 *
 * Returns `{ record, restore }`. Always call restore().
 */
function stubElectron(overrides = {}) {
  const record = {
    exposed: {},              // contextBridge.exposeInMainWorld
    ipcOn: [],                // ipcMain.on channels
    ipcHandle: [],            // ipcMain.handle channels
    rendererSend: [],         // ipcRenderer.send calls
    rendererInvoke: [],       // ipcRenderer.invoke calls
    rendererOn: [],           // ipcRenderer.on subscriptions
    appEvents: [],
    browserWindows: [],       // constructor options, in creation order
    windows: [],              // the window objects themselves
    quit: 0,
  };

  const electron = {
    app: {
      isPackaged: false,
      getVersion: () => '0.0.0-test',
      getPath: () => require('os').tmpdir(),
      getLoginItemSettings: () => ({ openAtLogin: false, wasOpenedAtLogin: false }),
      setLoginItemSettings: () => {},
      requestSingleInstanceLock: () => true,
      // Never resolves: loading the module must not start a backend.
      whenReady: () => new Promise(() => {}),
      on: (event) => { record.appEvents.push(event); },
      quit: () => { record.quit += 1; },
      relaunch: () => {},
      exit: () => {},
      ...overrides.app,
    },
    /**
     * A BrowserWindow that remembers what was done to it.
     *
     * Enough fidelity to answer the question the overlay tests ask: was it
     * shown, was it shown WITHOUT focus, did anything restore the main window,
     * and is it a top-level window or a child of another one.
     */
    BrowserWindow: class {
      constructor(options = {}) {
        record.browserWindows.push(options);
        this.options = options;
        this.visible = Boolean(options.show);
        this.minimized = false;
        this.focused = false;
        this.destroyed = false;
        this.alwaysOnTop = Boolean(options.alwaysOnTop);
        this.sent = [];
        this.bounds = { x: 0, y: 0, width: options.width || 0, height: options.height || 0 };
        this._handlers = {};
        const self = this;
        this.webContents = {
          on(event, handler) { self._handlers['wc:' + event] = handler; },
          once(event, handler) { self._handlers['wc-once:' + event] = handler; },
          send(channel, payload) { self.sent.push({ channel, payload }); },
          setWindowOpenHandler() {},
          setZoomFactor() {},
          setVisualZoomLevelLimits() { return Promise.resolve(); },
          // The stub models exactly the Electron surface main.js touches. The
          // CSP is installed through session.webRequest.onHeadersReceived, so
          // the stub has to offer it -- and it RECORDS the registered callback
          // rather than swallowing it, so a test can invoke the real header
          // rewriting and assert on the policy that comes out.
          session: {
            setPermissionRequestHandler() {},
            webRequest: {
              onHeadersReceived(handler) { self._headersReceived = handler; },
            },
          },
        };
        record.windows.push(this);
      }

      /** Pretend the renderer finished loading, firing did-finish-load. */
      finishLoad() {
        const handler = this._handlers['wc-once:did-finish-load'];
        if (handler) handler();
      }

      loadURL() {} loadFile() {}
      on(event, handler) { this._handlers[event] = handler; return this; }
      once(event, handler) { this._handlers['once:' + event] = handler; return this; }
      emit(event, ...args) {
        if (this._handlers[event]) this._handlers[event](...args);
        if (this._handlers['once:' + event]) this._handlers['once:' + event](...args);
      }

      show() { this.visible = true; this.minimized = false; this.focused = true; }
      showInactive() { this.visible = true; this.minimized = false; }
      hide() { this.visible = false; this.focused = false; }
      minimize() { this.minimized = true; this.visible = false; }
      restore() { this.minimized = false; this.visible = true; }
      focus() { this.focused = true; }
      maximize() {} unmaximize() {}
      destroy() { this.destroyed = true; this.visible = false; }
      close() {}

      isVisible() { return this.visible && !this.destroyed; }
      isMinimized() { return this.minimized; }
      isFocused() { return this.focused; }
      isMaximized() { return false; }
      isFullScreen() { return false; }
      isDestroyed() { return this.destroyed; }
      isAlwaysOnTop() { return this.alwaysOnTop; }

      setBounds(bounds) { this.bounds = { ...this.bounds, ...bounds }; }
      getBounds() { return this.bounds; }
      getNormalBounds() { return this.bounds; }
      setAlwaysOnTop(value) { this.alwaysOnTop = Boolean(value); }
      setVisibleOnAllWorkspaces() {}
      setIgnoreMouseEvents() {}
    },
    Menu: { buildFromTemplate: (t) => t },
    Tray: class { setToolTip() {} setContextMenu() {} on() {} destroy() {} isDestroyed() { return false; } },
    dialog: { showErrorBox: () => {}, showMessageBox: () => Promise.resolve({}) },
    globalShortcut: {
      register: () => true, unregister: () => {}, unregisterAll: () => {},
      isRegistered: () => true, ...overrides.globalShortcut,
    },
    ipcMain: {
      on: (channel) => { record.ipcOn.push(channel); },
      handle: (channel) => { record.ipcHandle.push(channel); },
    },
    ipcRenderer: {
      send: (channel, ...args) => { record.rendererSend.push({ channel, args }); },
      invoke: (channel, ...args) => {
        record.rendererInvoke.push({ channel, args });
        return Promise.resolve(null);
      },
      on: (channel, listener) => { record.rendererOn.push({ channel, listener }); },
      removeListener: () => {},
    },
    contextBridge: {
      exposeInMainWorld: (key, value) => { record.exposed[key] = value; },
    },
    nativeImage: {
      createEmpty: () => ({ isEmpty: () => true, resize: () => ({}) }),
      createFromPath: () => ({ isEmpty: () => false, resize: () => ({}) }),
    },
    screen: {
      getAllDisplays: () => [{ workArea: { x: 0, y: 0, width: 1920, height: 1040 } }],
      getCursorScreenPoint: () => ({ x: 0, y: 0 }),
      getDisplayNearestPoint: () => ({ workArea: { x: 0, y: 0, width: 1920, height: 1040 } }),
    },
    shell: { openExternal: () => Promise.resolve() },
  };

  const originalLoad = Module._load;
  Module._load = function patched(request, parent, isMain) {
    if (request === 'electron') return electron;
    return originalLoad.call(this, request, parent, isMain);
  };

  return {
    record,
    electron,
    restore() { Module._load = originalLoad; },
  };
}

/** Load a module with a fresh cache entry, so repeated loads are independent. */
function loadFresh(modulePath) {
  const resolved = require.resolve(modulePath);
  delete require.cache[resolved];
  return require(resolved);
}

module.exports = { assert, loadFresh, run, stubElectron, suite, test };
