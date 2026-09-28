#!/usr/bin/env node
/**
 * MEASURE the three Memória views, with data, in real Chromium.
 *
 *     npx electron test/memory-render.js        # JSON to stdout
 *
 * WHY THIS EXISTS SEPARATELY FROM render-check.js
 * render-check.js measures the SHELL with no backend, so every page renders its
 * empty state. That is the right test for layout overflow and the wrong test for
 * these three views: the interesting questions here are whether a memory card
 * lays out with its provenance chips, whether the node grid reflows, and above
 * all whether the knowledge graph actually DRAWS — an SVG whose layout is
 * computed in JavaScript cannot be verified by reading CSS, and a graph that
 * renders zero circles looks exactly like a graph with no data.
 *
 * So this harness installs a stub `window.eel` before the bundle loads,
 * answering the real bridge functions with realistic payloads, then navigates to
 * Memórias, Second Brain and Grafo and measures what came out.
 *
 * The stub is a TEST DOUBLE OF THE TRANSPORT, not of the components: the pages
 * under measurement are the shipped production bundle, unmodified, and the
 * payload shapes are copied from what core/main.py really returns.
 */
'use strict';

const { app, BrowserWindow, ipcMain } = require('electron');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..');
const OUT_DIR = path.join(ROOT, 'frontend', 'out');
/* Every request URL is mapped to a file by ONE shared, contained resolver;
   see lib/static-root.js for the three barriers it enforces. */
const { resolveStaticPath } = require('./lib/static-root');
const PRELOAD = path.join(__dirname, '..', 'preload.js');
const { watchdog } = require('./lib/watchdog');

/* Readiness predicates, injected into the page rather than required here.
   See lib/page-ready.js: it is what settles the opacity read below on the
   correct side of the CSS transition instead of on a guessed number of
   milliseconds. */
const PAGE_READY = fs.readFileSync(path.join(__dirname, 'lib', 'page-ready.js'), 'utf8');

/* A deadline, not a tuning knob: a healthy run of this harness was
   measured at well under 35s, so crossing two minutes means wedged.
   The guard reports in the normal JSON shape and exits, so the caller
   gets a named failing step instead of an empty pipe. */
const guard = watchdog({ label: 'memory-render', ms: 120000 });

const VIEWPORTS = [
  { name: '1920x1080', width: 1920, height: 1080 },
  { name: '1366x768', width: 1366, height: 768 },
  { name: '940x620-min', width: 940, height: 620 },
];

/* ── The stub bridge ─────────────────────────────────────────────────────
   Injected before any page script runs, so `useBridgeReady` finds it on its
   first check and the shell never enters its offline state. Every function
   returns the shape core/main.py returns; a name that is not here answers null,
   which is exactly what `call()` does when the backend is down. */
const BRIDGE = `
(() => {
  const NODES = [
    { id: 'n1', slug: 'gtx-1660-ti', title: 'GTX 1660 Ti', type: 'device',
      summary: 'A minha placa gráfica é uma GTX 1660 Ti com 6 GB.', body: '',
      tags: ['pc'], pinned: false, mentionCount: 4, origin: 'derived',
      createdAt: '2026-08-01T10:00:00Z', updatedAt: '2026-08-20T10:00:00Z' },
    { id: 'n2', slug: 'ollama', title: 'Ollama', type: 'software',
      summary: 'Uso o Ollama para correr modelos localmente.', body: '',
      tags: [], pinned: false, mentionCount: 6, origin: 'derived',
      createdAt: '2026-08-01T10:00:00Z', updatedAt: '2026-08-21T10:00:00Z' },
    { id: 'n3', slug: 'nano-assistant', title: 'Nano Assistant', type: 'project',
      summary: 'Assistente de ambiente de trabalho para Windows.', body: '',
      tags: ['projeto'], pinned: true, mentionCount: 9, origin: 'manual',
      createdAt: '2026-08-01T10:00:00Z', updatedAt: '2026-08-25T10:00:00Z' },
    { id: 'n4', slug: 'groq', title: 'Groq', type: 'software',
      summary: 'Provedor cloud usado em modo automático.', body: '',
      tags: [], pinned: false, mentionCount: 3, origin: 'derived',
      createdAt: '2026-08-01T10:00:00Z', updatedAt: '2026-08-22T10:00:00Z' },
    { id: 'n5', slug: 'visual-studio-code', title: 'Visual Studio Code',
      type: 'software', summary: 'O editor que uso todos os dias.', body: '',
      tags: [], pinned: false, mentionCount: 2, origin: 'derived',
      createdAt: '2026-08-01T10:00:00Z', updatedAt: '2026-08-19T10:00:00Z' },
    { id: 'n6', slug: 'simao', title: 'Simão', type: 'person',
      summary: 'O utilizador.', body: '', tags: [], pinned: false,
      mentionCount: 5, origin: 'derived',
      createdAt: '2026-08-01T10:00:00Z', updatedAt: '2026-08-24T10:00:00Z' },
  ];
  const EDGES = [
    { id: 'e1', source: 'n3', target: 'n2', relation: 'uses', weight: 2 },
    { id: 'e2', source: 'n3', target: 'n4', relation: 'uses', weight: 1.5 },
    { id: 'e3', source: 'n3', target: 'n1', relation: 'depends_on', weight: 1 },
    { id: 'e4', source: 'n6', target: 'n3', relation: 'works_on', weight: 3 },
    { id: 'e5', source: 'n6', target: 'n5', relation: 'prefers', weight: 1 },
  ];
  const MEMORIES = [
    { id: 'm1', text: 'A minha placa gráfica é uma GTX 1660 Ti.', kind: 'hardware',
      origin: 'explicit', trust: 'USER', status: 'active', confidence: 0.95,
      importance: 4, pinned: true, legacyKey: null, tags: ['pc'],
      sourceConversationId: 'c1', sourceMessageId: 12,
      createdAt: '2026-08-01T10:00:00Z', updatedAt: '2026-08-20T10:00:00Z',
      lastUsedAt: '2026-08-30T10:00:00Z', useCount: 7 },
    { id: 'm2', text: 'Prefiro respostas curtas e diretas, em português de Portugal.',
      kind: 'preference', origin: 'manual', trust: 'USER', status: 'active',
      confidence: 0.92, importance: 5, pinned: false, legacyKey: null, tags: [],
      sourceConversationId: null, sourceMessageId: null,
      createdAt: '2026-08-02T10:00:00Z', updatedAt: '2026-08-21T10:00:00Z',
      lastUsedAt: null, useCount: 0 },
    { id: 'm3', text: 'Uso o Ollama com o qwen3:8b para trabalho local.',
      kind: 'software', origin: 'explicit', trust: 'USER', status: 'active',
      confidence: 0.9, importance: 3, pinned: false, legacyKey: null,
      tags: ['ia'], sourceConversationId: 'c2', sourceMessageId: 40,
      createdAt: '2026-08-03T10:00:00Z', updatedAt: '2026-08-22T10:00:00Z',
      lastUsedAt: null, useCount: 2 },
    { id: 'm4', text: 'O meu monitor principal é um Dell de 24 polegadas.',
      kind: 'hardware', origin: 'inferred', trust: 'USER', status: 'candidate',
      confidence: 0.55, importance: 3, pinned: false, legacyKey: null, tags: [],
      sourceConversationId: 'c2', sourceMessageId: 55,
      createdAt: '2026-08-04T10:00:00Z', updatedAt: '2026-08-23T10:00:00Z',
      lastUsedAt: null, useCount: 0 },
  ];
  const OVERVIEW = {
    profile: { name: { value: 'Simão', source: 'user' } },
    facts: [{ key: 'cidade', value: 'Porto' }],
    memories: MEMORIES,
    kinds: ['preference','fact','hardware','software','project','goal','decision','person','other'],
    stats: { total: 4, active: 3, candidates: 1, archived: 0,
             byKind: { hardware: 1, preference: 1, software: 1 } },
    knowledge: { nodes: 6, edges: 5, byType: { device: 1, software: 3, project: 1, person: 1 } },
    retrieval: { mode: 'fts5', engine: 'SQLite FTS5 (BM25)', entries: 214,
                 byKind: { message: 205, memory: 4, node: 5 } },
    conversationCount: 17, messageCount: 205, ready: true,
    migration: { from: 0, to: 2, ok: true, error: null },
    longTermEnabled: true, captureEnabled: true,
    documentsSupported: true, documentsNote: 'Índice local FTS5.',
  };
  const THREADS = [
    { id: 'c1', title: 'Configurar o Ollama neste PC', titleSource: 'auto',
      createdAt: '2026-08-30T09:00:00Z', updatedAt: '2026-08-31T09:00:00Z',
      lastMessageAt: '2026-08-31T09:00:00Z', messageCount: 28, archived: false },
    { id: 'c2', title: 'Placa gráfica e jogos', titleSource: 'user',
      createdAt: '2026-08-29T09:00:00Z', updatedAt: '2026-08-30T09:00:00Z',
      lastMessageAt: '2026-08-30T09:00:00Z', messageCount: 15, archived: false },
    { id: 'c3', title: 'Uma conversa muito antiga sobre outra coisa qualquer',
      titleSource: 'auto', createdAt: '2026-07-01T09:00:00Z',
      updatedAt: '2026-07-01T09:30:00Z', lastMessageAt: '2026-07-01T09:30:00Z',
      messageCount: 6, archived: false },
  ];

  const ANSWERS = {
    get_memory_overview: () => OVERVIEW,
    list_conversations: () => ({ ok: true, conversations: THREADS, activeId: 'c1' }),
    get_conversation_history: () => [
      { id: 1, role: 'user', content: 'A minha placa gráfica é uma GTX 1660 Ti.',
        timestamp: '2026-08-31T09:00:00Z', trust: 'USER' },
      { id: 2, role: 'assistant', content: 'Anotado.',
        timestamp: '2026-08-31T09:00:05Z', trust: 'USER' },
    ],
    list_knowledge_nodes: () => ({
      ok: true, nodes: NODES, stats: { nodes: NODES.length, edges: EDGES.length },
      types: ['person','project','topic','game','software','device','goal','preference','decision','note'],
      relations: ['related_to','part_of','uses','prefers','works_on','decided','mentioned_in','depends_on'],
    }),
    get_knowledge_graph: () => ({
      ok: true, nodes: NODES, edges: EDGES, truncated: false,
      total: NODES.length, totalEdges: EDGES.length, focus: null,
      types: ['person','project','topic','game','software','device','goal','preference','decision','note'],
    }),
    get_knowledge_node: () => ({
      ok: true, node: NODES[2], edges: EDGES.map((e) => ({
        ...e, sourceTitle: 'Nano Assistant', targetTitle: 'Ollama',
        sourceType: 'project', targetType: 'software' })),
      memories: MEMORIES.slice(0, 2), conversations: THREADS.slice(0, 2),
      types: [], relations: [],
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
      ollama: { id: 'ollama', name: 'Ollama', kind: 'local', role: 'fallback', state: 'READY',
                model: 'qwen3:8b', models: [], detail: '',
                secret: { configured: false, masked: '', source: 'none', encrypted: false } },
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
        // Asynchronous, like the real websocket, so nothing depends on a
        // synchronous answer the real bridge would never give.
        setTimeout(() => callback && callback(value), 0);
      };
    },
  });
  window.eel = eel;
  window.__nanoHandlers = window.__nanoHandlers || {};
})();
`;

/** Click a sidebar section, then a stage sub-tab, and let it settle. */
const OPEN = (section, tab) => `(() => {
  for (const el of document.querySelectorAll('.rail-nav__item')) {
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

/** What actually came out. Counts of REAL rendered elements, plus overflow. */
const PROBE = `(() => {
  const doc = document.documentElement;
  const count = (selector) => document.querySelectorAll(selector).length;
  const offenders = [];
  for (const el of document.querySelectorAll('.page__inner *')) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > doc.clientWidth + 1) {
      offenders.push(el.tagName.toLowerCase() + '.' + (el.className || '').toString().split(' ')[0]);
    }
  }
  const svg = document.querySelector('.graph-svg');
  const labels = Array.from(document.querySelectorAll('.graph-node__label'))
    .map((el) => (el.textContent || '').trim());
  return {
    horizontalOverflow: doc.scrollWidth > doc.clientWidth,
    offenders: offenders.slice(0, 6),
    memoryCards: count('.memory-card'),
    originChips: count('.origin-chip'),
    statChips: count('.stat-chip'),
    tabs: count('.tabs .tab'),
    nodeCards: count('.node-card'),
    graphSvg: Boolean(svg),
    graphNodes: count('.graph-node'),
    graphDots: count('.graph-node__dot'),
    graphEdges: count('.graph-edge'),
    graphLabels: labels,
    legendItems: count('.graph-legend__item'),
    emptyStates: count('.empty-state'),
    railRows: count('.chat-item'),
    retrievalFooter: (document.querySelector('.memory-footnote') || {}).textContent || '',
    // Every circle must have a real coordinate. A NaN transform renders
    // nothing and silently looks like "no data".
    graphPositionsFinite: Array.from(document.querySelectorAll('.graph-node'))
      .every((g) => /translate\\(-?[\\d.]+ -?[\\d.]+\\)/.test(g.getAttribute('transform') || '')),
  };
})()`;

/**
 * DRIVE the graph, then measure. Separate from PROBE because these are claims
 * about what the view DOES, and the only way to check "selecting a node
 * highlights its edges and fades the rest" is to select one and look.
 *
 * Returns nulls rather than throwing when the graph is absent, so the caller
 * can report "not measured" instead of a crash that reads like a failure.
 */
const GRAPH_INTERACT = `(async () => {
  const { sleep, settledStyle } = window.__ready;
  const qa = (s) => Array.from(document.querySelectorAll(s));
  const svg = document.querySelector('.graph-svg');
  const nodes = qa('.graph-node');
  if (!svg || nodes.length < 2) return { measured: false, nodeCount: nodes.length };

  const positionOf = (g) => {
    const m = /translate\\((-?[\\d.]+) (-?[\\d.]+)\\)/.exec(g.getAttribute('transform') || '');
    return m ? { x: parseFloat(m[1]), y: parseFloat(m[2]) } : null;
  };
  const edgeLength = (line) => Math.hypot(
    Number(line.getAttribute('x2')) - Number(line.getAttribute('x1')),
    Number(line.getAttribute('y2')) - Number(line.getAttribute('y1')));

  /* Related nodes should sit nearer than unrelated ones. The fixture's most
     connected node is the project hub; measuring the mean edge length against
     the mean distance between ALL pairs is the honest version of "the layout
     is graph-aware" -- a ring layout scores 1.0 here. */
  const points = nodes.map(positionOf).filter(Boolean);
  let pairTotal = 0, pairCount = 0;
  for (let i = 0; i < points.length; i += 1) {
    for (let j = i + 1; j < points.length; j += 1) {
      pairTotal += Math.hypot(points[i].x - points[j].x, points[i].y - points[j].y);
      pairCount += 1;
    }
  }
  const edges = qa('.graph-edge');
  const meanEdge = edges.length
    ? edges.reduce((total, line) => total + edgeLength(line), 0) / edges.length : 0;
  const meanPair = pairCount ? pairTotal / pairCount : 0;

  /* ---- selecting a node focuses it -------------------------------------- */
  const target = nodes.reduce((best, g) => {
    const id = g.getAttribute('aria-label') || '';
    return id.length > (best.getAttribute('aria-label') || '').length ? g : best;
  }, nodes[0]);
  target.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  await sleep(250);

  const selected = qa('.graph-node.is-selected').length;
  const dimmedNodes = qa('.graph-node.is-dim').length;
  const litEdges = qa('.graph-edge.is-lit').length;
  const dimmedEdges = qa('.graph-edge.is-dim').length;
  const detailPanel = !!document.querySelector('.graph-selected');
  /* Faded, not hidden: the shape of the rest of the graph is context.

     READ THE OPACITY ONLY ONCE IT HAS STOPPED MOVING, via settledStyle rather
     than an immediate read -- .graph-node carries transition: opacity
     var(--transition), so a same-tick getComputedStyle can return the value
     being transitioned AWAY FROM (1) rather than the target (0.26). This
     alone is necessary but was NOT sufficient: it settled at the wrong value
     (1) on Linux CI even so, because the window itself was never composited.
     See the showInactive() comment above main()'s BrowserWindow for that
     half of the story -- a polling loop cannot tell "the value is correct
     and holding steady" apart from "the value is frozen because no real
     frame is ticking" from inside the page alone. */
  const dimEl = qa('.graph-node.is-dim')[0] || null;
  const dimOpacity = dimEl ? Number(await settledStyle(dimEl, 'opacity')) : null;

  /* ---- dragging a node moves THAT node and nothing else ------------------ */
  const before = positionOf(target);
  const others = nodes.filter((g) => g !== target).map(positionOf);
  const rect = svg.getBoundingClientRect();
  const at = (x, y, type) => new PointerEvent(type, {
    bubbles: true, clientX: x, clientY: y, pointerId: 1, pointerType: 'mouse' });
  target.dispatchEvent(at(rect.left + 100, rect.top + 100, 'pointerdown'));
  svg.dispatchEvent(at(rect.left + 220, rect.top + 190, 'pointermove'));
  svg.dispatchEvent(at(rect.left + 220, rect.top + 190, 'pointerup'));
  await sleep(250);
  const after = positionOf(target);
  const othersAfter = qa('.graph-node').filter((g) => g !== target).map(positionOf);
  const draggedBy = (before && after) ? Math.hypot(after.x - before.x, after.y - before.y) : 0;
  const othersMoved = others.some((point, index) => {
    const now = othersAfter[index];
    return point && now && Math.hypot(now.x - point.x, now.y - point.y) > 1;
  });

  /* ---- the layout is deterministic across renders ------------------------ */
  return {
    measured: true,
    nodeCount: nodes.length,
    edgeCount: edges.length,
    selectedCount: selected,
    dimmedNodes, litEdges, dimmedEdges, detailPanel, dimOpacity,
    meanEdge: Math.round(meanEdge), meanPair: Math.round(meanPair),
    draggedBy: Math.round(draggedBy),
    othersMoved,
  };
})()`;

/**
 * TYPE IN THE SEARCH BOX and read what the view says back.
 *
 * Matching DIMS every node that is not a hit, so a query with no hits fades the
 * entire graph — which looks exactly like a view that failed to load. The only
 * thing separating "nothing here is called that" from "something broke" is the
 * text the footer prints, and text that is only asserted in the source can move
 * into a comment and keep passing. So it is typed and read here.
 *
 * The value is set through React's own native setter: assigning `.value` on a
 * controlled input updates the DOM and never reaches React, so the component
 * would re-render the old value straight back over it.
 */
const SEARCH_PROBE = `(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const input = document.querySelector('#graph-search');
  const foot = () => (document.querySelector('.graph-foot')?.textContent || '').trim();
  if (!input) return { measured: false };

  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  const type = async (text) => {
    setter.call(input, text);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await sleep(300);
  };

  const idle = foot();

  await type('zzzzznaoexiste');
  const noMatch = foot();
  const dimmedOnNoMatch = document.querySelectorAll('.graph-node.is-dim').length;
  const totalNodes = document.querySelectorAll('.graph-node').length;

  const label = (document.querySelector('.graph-node')?.getAttribute('aria-label') || '');
  const name = label.split(' — ')[0];
  await type(name);
  const oneMatch = foot();

  await type('');
  await sleep(200);
  return {
    measured: true,
    idle, noMatch, oneMatch,
    restored: foot() === idle,
    dimmedOnNoMatch, totalNodes,
  };
})()`;

function serve() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const file = resolveStaticPath(OUT_DIR, req.url);
      if (file === null || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
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

async function main() {
  if (!fs.existsSync(path.join(OUT_DIR, 'index.html'))) {
    console.log(JSON.stringify({ ok: false, error: 'frontend/out is not built' }));
    guard.disarm();
    app.exit(2);
    return;
  }
  const server = await serve();
  const url = `http://127.0.0.1:${server.address().port}/index.html`;
  const report = { ok: true, views: [], consoleErrors: [] };

  ipcMain.handle('nano:window-state', () => (
    { maximized: false, fullScreen: false, focused: true, platform: process.platform }));

  let window = null;
  try {
    window = new BrowserWindow({
      show: false, frame: false, width: 1366, height: 768,
      /* backgroundThrottling: false: belt AND braces alongside showInactive()
       below. Even a shown-but-inactive window can still be treated as a
       background page for timer purposes, and this harness awaits several
       timers per view; the flag costs nothing and was already proven load-
       bearing in chat-drive.js. */
      webPreferences: { preload: PRELOAD, contextIsolation: true,
                        backgroundThrottling: false,
                        nodeIntegration: false, sandbox: true },
    });

    /* THIS WINDOW MUST ACTUALLY BE PRESENTED, not merely constructed.

       A BrowserWindow created with show:false and never shown at all has no
       surface for the OS compositor to draw into. Measured directly on this
       machine (temporary diagnostics, since removed): a 500ms window of
       requestAnimationFrame got exactly ONE tick before showInactive() and
       141 after it -- proof, not theory, that an unshown window is starved
       of real rendering opportunities regardless of platform. A CSS
       Transition is scheduled to start on exactly such an opportunity, so on
       a window that never gets one, the transitioned property can be read
       forever at its BEFORE value -- which is indistinguishable, to a
       polling loop, from a value that settled there. That is consistent with
       what test_unrelated_elements_fade_rather_than_disappear hit on Linux
       CI: dimOpacity read 1 (the resting, pre-transition value) no matter how
       long settledStyle() waited, because the frames it was "settling"
       across were never real compositor frames. (The Linux runner itself
       could not be reached directly to confirm the rAF count there; the fix
       is applied on the mechanism proven here, which does not depend on the
       OS.)

       This machine did not surface the bug locally for an unrelated, second
       reason: it independently reports prefers-reduced-motion: reduce, which
       matches the @media rule in globals.css that sets `transition: none` on
       these elements -- so there was no transition to race in the first
       place here, on top of the window never being composited. Two masks,
       not one, which is why 8 clean local runs before this fix proved
       nothing about this path.

       showInactive() is the fix, not a longer wait: it is the same call
       chat-drive.js already relies on, gives the window a real (if
       unfocused, off-screen-owned) surface, and needs no .focus() here since
       nothing in this harness reads :focus. */
    window.showInactive();

    window.webContents.on('console-message', (_e, level, message) => {
      if (level >= 2 && !message.includes('Electron Security Warning')) {
        report.consoleErrors.push(message);
      }
    });

    for (const viewport of VIEWPORTS) {
      window.setSize(viewport.width, viewport.height);
      await window.loadURL(url);
      // Installed right after load, not before: `useBridgeReady` polls for
      // window.eel every 100 ms for twelve seconds, so arriving a moment late
      // is exactly the timing the real websocket has. No reload is needed, and
      // a reload here is what made this harness hang.
      await window.webContents.executeJavaScript(BRIDGE);
      await wait(2500);
      window.webContents.setZoomFactor(1);

      for (const [section, tab, label] of [
        ['Mem', 'Memórias', 'memorias'],
        ['Mem', 'Second Brain', 'second-brain'],
        ['Mem', 'Grafo', 'grafo'],
        ['Chat', '', 'chat'],
      ]) {
        await window.webContents.executeJavaScript(OPEN(section, tab));
        await wait(900);
        const measured = await window.webContents.executeJavaScript(PROBE);
        // The graph is the one view whose value is in what it DOES, so it is
        // driven as well as measured. Selecting and dragging is deliberately
        // the LAST thing done in this view: it leaves the graph in a moved
        // state, and the next iteration reloads the page anyway.
        if (label === 'grafo') {
          // Search FIRST: it clears its own query, while the drag below
          // deliberately leaves the graph rearranged.
          measured.search =
            await window.webContents.executeJavaScript(SEARCH_PROBE, true);
          measured.interaction =
            await window.webContents.executeJavaScript(PAGE_READY + GRAPH_INTERACT, true);
        }
        report.views.push({ viewport: viewport.name, view: label, ...measured });
        if (measured.horizontalOverflow || measured.offenders.length) report.ok = false;
      }
    }
  } catch (error) {
    report.ok = false;
    report.error = String(error && error.stack ? error.stack : error);
  } finally {
    if (window) window.destroy();
    server.close();
  }

  console.log(JSON.stringify(report, null, 2));
  guard.disarm();
  app.exit(report.ok ? 0 : 1);
}

app.disableHardwareAcceleration();
app.whenReady().then(main).catch((err) => {
  guard.disarm();
  console.error(err);
  app.exit(1);
});
