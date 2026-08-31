#!/usr/bin/env node
/**
 * PROVE the Second Brain "create node" modal keeps keyboard focus in the
 * title input while the user types.
 *
 *     npx electron test/focus-trap-render.js
 *
 * WHY THIS EXISTS
 * `useFocusTrap` (frontend/components/ui.tsx) used to key its
 * listener-registration effect off `[open, onKeyDown]`, where `onKeyDown`
 * was a `useCallback` depending on `onClose`. Every `Modal` caller passes an
 * inline `onClose={() => setX(false)}` arrow, so its identity changes on
 * every render -- including the render each keystroke causes while typing
 * into the create-node title field. That re-ran the effect, which re-armed
 * a 30ms "focus the first focusable element in the dialog" timer. Because
 * the modal header (with the X/close button) sits before the modal body
 * (with the title input) in DOM order, that timer moved focus onto the
 * close button after every single character.
 *
 * There is no jsdom/React-Testing-Library harness anywhere in this repo
 * (see frontend/package.json — no jest, no @testing-library/*), and a test
 * that greps the source for "useCallback" or a dependency array would not
 * survive a differently-shaped reintroduction of the same bug. So this
 * drives the real production bundle inside real Chromium, exactly the way
 * electron/test/memory-render.js already does, and asserts on
 * `document.activeElement` after real keystrokes with real timing.
 */
'use strict';

const { app, BrowserWindow, ipcMain } = require('electron');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..');
const OUT_DIR = path.join(ROOT, 'frontend', 'out');
const PRELOAD = path.join(__dirname, '..', 'preload.js');

/* ── The stub bridge ─────────────────────────────────────────────────────
   Same shape as electron/test/memory-render.js: a test double of the
   transport (window.eel), not of the components under test. */
const BRIDGE = `
(() => {
  const NODES = [
    { id: 'n1', slug: 'gtx-1660-ti', title: 'GTX 1660 Ti', type: 'device',
      summary: 'A minha placa gráfica é uma GTX 1660 Ti com 6 GB.', body: '',
      tags: ['pc'], pinned: false, mentionCount: 4, origin: 'derived',
      createdAt: '2026-08-01T10:00:00Z', updatedAt: '2026-08-20T10:00:00Z' },
  ];
  const OVERVIEW = {
    profile: { name: { value: 'Simão', source: 'user' } },
    facts: [],
    memories: [],
    kinds: ['preference','fact','hardware','software','project','goal','decision','person','other'],
    stats: { total: 0, active: 0, candidates: 0, archived: 0, byKind: {} },
    knowledge: { nodes: 1, edges: 0, byType: { device: 1 } },
    retrieval: { mode: 'fts5', engine: 'SQLite FTS5 (BM25)', entries: 1, byKind: { node: 1 } },
    conversationCount: 0, messageCount: 0, ready: true,
    migration: { from: 0, to: 2, ok: true, error: null },
    longTermEnabled: true, captureEnabled: true,
    documentsSupported: true, documentsNote: 'Índice local FTS5.',
  };
  const ANSWERS = {
    get_memory_overview: () => OVERVIEW,
    list_conversations: () => ({ ok: true, conversations: [], activeId: null }),
    list_knowledge_nodes: () => ({
      ok: true, nodes: NODES, stats: { nodes: NODES.length, edges: 0 },
      types: ['person','project','topic','game','software','device','goal','preference','decision','note'],
      relations: ['related_to','part_of','uses','prefers','works_on','decided','mentioned_in','depends_on'],
    }),
    get_system_readiness: () => ({
      agent: { state: 'READY', pending_permissions: 0 },
      voice: { state: 'READY', blockers: [], enabled: true },
      wakeWord: { state: 'DISABLED', modelStatus: 'NOT_INSTALLED' },
      wakePhrase: { state: 'DISABLED' },
      model: { state: 'READY', local: { model: 'qwen3:8b', online: true, modelReady: true, enabled: true },
               cloud: { model: 'openai/gpt-oss-20b', configured: true }, provider: 'groq' },
      worker: { state: 'READY', running: true, queue_size: 0, poll_interval: 2 },
      providers: {}, emergencyStop: false, autonomyMode: 'SAFE',
      browser: { state: 'READY' }, vision: { state: 'READY' },
    }),
    get_providers: () => ({
      mode: 'AUTO', modes: ['AUTO','CLOUD','LOCAL'],
      groq: { id: 'groq', name: 'Groq', kind: 'cloud', role: 'primary', state: 'READY',
              model: 'openai/gpt-oss-20b', models: [], detail: '',
              secret: { configured: true, masked: '****', source: 'encrypted_store', encrypted: true } },
      route: { provider: 'groq', model: 'openai/gpt-oss-20b', usable: true,
               fallback: false, mode: 'AUTO', reason: '' },
    }),
    get_command_center_state: () => ({
      worker: { running: true, queue_size: 0, poll_interval: 2 }, system: {},
      task_summary: {}, current_task: null, tasks: [], activities: [],
      permissions: [], agents: { agents: [], selected: [] }, health: {},
      emergency_stop: false, autonomy_mode: 'SAFE',
    }),
    get_task_counts: () => ({ active: 0, attention: 0, badge: 0, total: 0, byStatus: {} }),
    get_loaded_plugins: () => ({}),
    list_permission_policies: () => [],
  };
  const eel = new Proxy({}, {
    get(_target, name) {
      if (typeof name !== 'string') return undefined;
      return (...args) => (callback) => {
        const answer = ANSWERS[name];
        const value = answer ? answer(...args) : null;
        setTimeout(() => callback && callback(value), 0);
      };
    },
  });
  window.eel = eel;
  window.__nanoHandlers = window.__nanoHandlers || {};
})();
`;

/** Click a top-bar section, then a stage sub-tab, and let it settle. */
const OPEN = (section, tab) => `(() => {
  for (const el of document.querySelectorAll('.topnav-item')) {
    if ((el.textContent || '').trim().startsWith(${JSON.stringify(section)})) { el.click(); break; }
  }
  if (${JSON.stringify(tab || '')}) {
    setTimeout(() => {
      for (const el of document.querySelectorAll('.subtab')) {
        if ((el.textContent || '').trim().startsWith(${JSON.stringify(tab || '')})) { el.click(); break; }
      }
    }, 120);
  }
  return true;
})()`;

/** Click the first element matching `selector` whose text starts with `text`. */
const CLICK_BY_TEXT = (selector, text) => `(() => {
  for (const el of document.querySelectorAll(${JSON.stringify(selector)})) {
    if ((el.textContent || '').trim().startsWith(${JSON.stringify(text)})) { el.click(); return true; }
  }
  return false;
})()`;

/** Bounding rect of the first element matching `selector` whose text starts with `text`. */
const RECT_BY_TEXT = (selector, text) => `(() => {
  for (const el of document.querySelectorAll(${JSON.stringify(selector)})) {
    if ((el.textContent || '').trim().startsWith(${JSON.stringify(text)})) {
      const r = el.getBoundingClientRect();
      return { x: r.left, y: r.top, width: r.width, height: r.height };
    }
  }
  return null;
})()`;

/** What has focus right now, plus the title input's current value. */
const PROBE_FOCUS = `(() => {
  const el = document.activeElement;
  const input = document.querySelector('.modal input.input');
  return {
    activeIsTitleInput: !!(el && input && el === input),
    activeTag: el ? el.tagName.toLowerCase() : null,
    activeAriaLabel: el ? el.getAttribute('aria-label') : null,
    activeText: el ? (el.textContent || '').trim().slice(0, 40) : null,
    inputValue: input ? input.value : null,
    modalOpen: !!document.querySelector('.modal-backdrop'),
  };
})()`;

function serve() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const rel = decodeURIComponent(req.url.split('?')[0]).replace(/^\/+/, '') || 'index.html';
      const file = path.join(OUT_DIR, rel);
      if (!file.startsWith(OUT_DIR) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
        res.writeHead(404); res.end('not found'); return;
      }
      const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
                      '.png': 'image/png', '.json': 'application/json' };
      res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' });
      fs.createReadStream(file).pipe(res);
    });
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

/** Send one real keystroke through Chromium's input pipeline, like a user. */
function typeChar(window, ch) {
  window.webContents.sendInputEvent({ type: 'keyDown', keyCode: ch });
  window.webContents.sendInputEvent({ type: 'char', keyCode: ch });
  window.webContents.sendInputEvent({ type: 'keyUp', keyCode: ch });
}

/**
 * A real mouse click at the element's on-screen position, routed through
 * Chromium's input pipeline -- unlike a scripted `el.click()`, this is what
 * actually gives the clicked element focus, which the focus-restoration
 * assertion below depends on.
 */
async function realClick(window, rectScript) {
  const rect = await window.webContents.executeJavaScript(rectScript);
  if (!rect) return false;
  const x = Math.round(rect.x + rect.width / 2);
  const y = Math.round(rect.y + rect.height / 2);
  window.webContents.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount: 1 });
  window.webContents.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount: 1 });
  return true;
}

async function main() {
  if (!fs.existsSync(path.join(OUT_DIR, 'index.html'))) {
    console.log(JSON.stringify({ ok: false, error: 'frontend/out is not built' }));
    app.exit(2);
    return;
  }
  const server = await serve();
  const url = `http://127.0.0.1:${server.address().port}/index.html`;
  const report = { ok: true, steps: [] };
  const fail = (step, detail) => { report.ok = false; report.steps.push({ step, ok: false, detail }); };
  const pass = (step, detail) => { report.steps.push({ step, ok: true, detail }); };

  ipcMain.handle('nano:window-state', () => (
    { maximized: false, fullScreen: false, focused: true, platform: process.platform }));

  let window = null;
  try {
    window = new BrowserWindow({
      show: true, frame: false, width: 1366, height: 768,
      webPreferences: { preload: PRELOAD, contextIsolation: true,
                        nodeIntegration: false, sandbox: true },
    });

    await window.loadURL(url);
    await window.webContents.executeJavaScript(BRIDGE);
    await wait(2500);

    await window.webContents.executeJavaScript(OPEN('Mem', 'Second Brain'));
    await wait(900);

    const opened = await realClick(window, RECT_BY_TEXT('button', 'Novo nó'));
    if (!opened) { fail('open-modal', 'could not find the "Novo nó" button'); throw new Error('setup failed'); }
    await wait(150);

    let state = await window.webContents.executeJavaScript(PROBE_FOCUS);
    if (!state.modalOpen) { fail('modal-open', state); throw new Error('setup failed'); }
    pass('modal-open', state);

    // Click the title input explicitly, the way a real user would, rather
    // than relying on the dialog's own initial-focus choice.
    await window.webContents.executeJavaScript(
      `document.querySelector('.modal input.input').focus(); true;`
    );
    await wait(60);

    const target = 'Nano Project';
    let typedSoFar = '';
    for (const ch of target) {
      typeChar(window, ch);
      typedSoFar += ch;
      // Longer than the 30ms timer the bug used to arm, on purpose: this is
      // exactly the window in which focus used to jump to the close button.
      await wait(80);
      state = await window.webContents.executeJavaScript(PROBE_FOCUS);
      if (!state.activeIsTitleInput) {
        fail('focus-stays-in-input', { afterChar: ch, typedSoFar, ...state });
        break;
      }
      if (state.inputValue !== typedSoFar) {
        fail('input-value-tracks-typing', { afterChar: ch, typedSoFar, ...state });
        break;
      }
    }
    if (report.ok) pass('typed-continuously', { target, final: state.inputValue });

    // Tab / Shift+Tab still move focus (trap did not disable navigation).
    window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Tab' });
    window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Tab' });
    await wait(60);
    state = await window.webContents.executeJavaScript(PROBE_FOCUS);
    if (state.activeIsTitleInput) fail('tab-moves-focus', state);
    else pass('tab-moves-focus', state);

    // Escape closes the dialog.
    window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Escape' });
    window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Escape' });
    await wait(150);
    state = await window.webContents.executeJavaScript(PROBE_FOCUS);
    if (state.modalOpen) fail('escape-closes-modal', state);
    else pass('escape-closes-modal', state);

    // Focus returns to the invoking control ("Novo nó") after close.
    if (!(state.activeText || '').startsWith('Novo n')) {
      fail('focus-restored-to-trigger', state);
    } else {
      pass('focus-restored-to-trigger', state);
    }

    // Reopen and close via the X button.
    await window.webContents.executeJavaScript(CLICK_BY_TEXT('button', 'Novo nó'));
    await wait(150);
    await window.webContents.executeJavaScript(
      `document.querySelector('.modal-header [aria-label="Fechar"]').click(); true;`
    );
    await wait(150);
    state = await window.webContents.executeJavaScript(PROBE_FOCUS);
    if (state.modalOpen) fail('close-button-closes-modal', state);
    else pass('close-button-closes-modal', state);
  } catch (error) {
    report.ok = false;
    report.error = String(error && error.stack ? error.stack : error);
  } finally {
    if (window) window.destroy();
    server.close();
  }

  console.log(JSON.stringify(report, null, 2));
  app.exit(report.ok ? 0 : 1);
}

app.disableHardwareAcceleration();
app.whenReady().then(main);
