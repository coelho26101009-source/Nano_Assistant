#!/usr/bin/env node
/**
 * Drive the REAL chat view and conversation rail in Electron's own Chromium.
 *
 *     npx electron test/chat-drive.js       # JSON on stdout, log on stderr
 *
 * WHY A DRIVEN TEST AND NOT SOURCE ASSERTIONS
 * -------------------------------------------
 * Every claim this pass makes is a claim about what the user SEES at a moment
 * in a sequence, and none of them can be checked by reading a file:
 *
 *   "there is exactly ONE thinking indicator"   is a count of live DOM nodes
 *                                               DURING a stream, and a grep for
 *                                               the second one would pass the
 *                                               day it moved into a helper
 *   "the disclosure is discoverable"            is a computed opacity and a hit
 *                                               box, not a class name
 *   "the selected row has no left stripe"       is a ::before that a stylesheet
 *                                               can reintroduce from anywhere
 *   "the panel shows THIS message's provider"   is what survived the round trip
 *                                               through the store
 *
 * The eel bridge is a scripted stub speaking the REAL payload shapes, including
 * the streaming callbacks core/main.py invokes (`on_stream_start`,
 * `on_stream_status`, `on_stream_chunk`, `on_stream_end`). The components under
 * measurement are the shipped production bundle, unmodified.
 */
'use strict';

const { app, BrowserWindow } = require('electron');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..');
const OUT_DIR = path.join(ROOT, 'frontend', 'out');
/* Every request URL is mapped to a file by ONE shared, contained resolver;
   see lib/static-root.js for the three barriers it enforces. */
const { resolveStaticPath } = require('./lib/static-root');

/* Readiness predicates, injected into the page rather than required here.
   See lib/page-ready.js for why every wait in this file names a condition
   instead of a number of milliseconds. */
const PAGE_READY = fs.readFileSync(path.join(__dirname, 'lib', 'page-ready.js'), 'utf8');
const { watchdog } = require('./lib/watchdog');

/* 120s against a run measured at 29.2s (5 runs, spread 0.27s). Crossing it
   means wedged, not busy. */
const guard = watchdog({ label: 'chat-drive', ms: 120000 });

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.ico': 'image/x-icon',
  '.png': 'image/png',
};

function serve() {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      let file = resolveStaticPath(OUT_DIR, req.url);
      if (file === null) { res.writeHead(404); res.end('not found'); return; }
      if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) {
        const html = file + '.html';
        file = fs.existsSync(html) ? html : path.join(OUT_DIR, 'index.html');
      }
      try {
        const body = fs.readFileSync(file);
        res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' });
        res.end(body);
      } catch (err) { res.writeHead(404); res.end('not found'); }
    });
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

app.disableHardwareAcceleration();

app.whenReady().then(async () => {
  guard.phase('starting the static server');
  const server = await serve();
  const port = server.address().port;

  const win = new BrowserWindow({
    width: 1440, height: 900, show: false,
    /* backgroundThrottling: false IS LOAD-BEARING, not a tuning knob.

       This window is never composited -- it is created with show:false and
       only ever showInactive()d -- so Chromium classifies it as a background
       page and throttles its timers: setTimeout is clamped to once a second,
       and after five minutes of that, "intensive throttling" clamps it to once
       a MINUTE. This harness awaits roughly fifty timers, so the clamp is the
       difference between a 30-second run and one that never finishes. Measured
       on this machine before the flag: five runs took 37s, 74s, 158s, 29s, and
       one that was still alive after 467 seconds having burned 0.9 seconds of
       CPU -- a hang, not slow work. */
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true,
                      backgroundThrottling: false },
  });

  guard.phase('loading the production bundle');
  await win.loadURL(`http://127.0.0.1:${port}/`);
  /* THE WINDOW HAS TO BE FOCUSED, or `:focus` never matches and every focus
     assertion below silently measures the unfocused state. `document.
     activeElement` is set on a hidden window; the CSS pseudo-class is not.
     `showInactive` keeps the run from stealing the foreground. */
  win.showInactive();
  win.focus();
  win.webContents.focus();

  /* EMULATE A FOCUSED PAGE, so the answer does not depend on the desktop.

     Everything below re-asserts OS focus, and on a Windows desktop that is not
     enough: a window shown with showInactive() cannot take the foreground while
     another application owns it (Windows' foreground lock), so on a developer's
     machine with the editor in front, `document.hasFocus()` went false part-way
     through and the focused disclosure measured as transparent on every run --
     a stylesheet defect that does not exist, while CI's Linux runner passed.
     This is DevTools' "Emulate a focused page": the document behaves as focused
     and `:focus` matches, which is the state these assertions are about. It
     changes nothing about which element is focused, so a missing focus style
     still fails. If the protocol is unavailable the run falls back to the OS
     focus handling below and says so. */
  let focusEmulated = false;
  try {
    win.webContents.debugger.attach('1.3');
    await win.webContents.debugger.sendCommand('Emulation.setFocusEmulationEnabled', { enabled: true });
    focusEmulated = true;
  } catch (err) {
    console.error('WARNING: focus emulation unavailable (' + err.message
      + '); falling back to OS focus, which another window can take.');
  }

  /* WAIT FOR THE FOCUS TO ACTUALLY ARRIVE, and record whether it did.

     `:focus` does not match in a document that is not focused, so without this
     every focus assertion below measures the unfocused state and blames the
     stylesheet. The old code slept 1200ms and hoped. Ask the page instead, and
     keep re-asserting focus while we wait: on Windows another application can
     own the foreground when the run starts, and one focus() call at t=0 is not
     enough to win it back. */
  guard.phase('waiting for the window to take keyboard focus');
  const focused = await (async () => {
    for (let i = 0; i < 60; i += 1) {
      if (await win.webContents.executeJavaScript('document.hasFocus()')) return true;
      win.focus();
      win.webContents.focus();
      await new Promise((r) => setTimeout(r, 100));
    }
    return false;
  })();
  if (!focused) {
    console.error('WARNING: the window never took keyboard focus; '
      + 'every :focus assertion below is measuring the unfocused state.');
  }

  /* AND KEEP IT. Taking focus once at t=0 is not enough: this window is shown
     with showInactive() so it never owns the foreground, and on a developer's
     desktop something else reclaims it part-way through the 22-second drive.
     When that happens :focus stops matching, and the disclosure -- whose
     focused state is a background colour set by .msg__meta-toggle:focus --
     measures as transparent. That failed one run in three, reporting a
     stylesheet defect that does not exist. Re-assert focus for as long as the
     drive runs; the interval is cleared before the report is written. */
  const focusKeeper = setInterval(() => {
    if (!win.isDestroyed() && !win.webContents.isFocused()) {
      win.focus();
      win.webContents.focus();
    }
  }, 200);

  guard.phase('driving the chat view and the conversation rail');
  const result = await win.webContents.executeJavaScript(PAGE_READY + String.raw`
(async () => {
  const report = { steps: [], calls: [], views: [] };
  const ok = (label, pass, detail) => report.steps.push({ label, pass: !!pass, detail: String(detail ?? '') });
  const { sleep, waitFor, waitGone, waitStable, settledStyle } = window.__ready;

  /* Focus is a precondition for a whole family of assertions below, so it is
     reported as its own step. A focus failure then names itself instead of
     surfacing as "the focused control has no background". */
  ok('the harness window owns keyboard focus',
     document.hasFocus(), 'hasFocus=' + document.hasFocus() + ' emulated=${focusEmulated}');
  const q  = (s) => document.querySelector(s);
  const qa = (s) => Array.from(document.querySelectorAll(s));
  const byText = (sel, text) =>
    qa(sel).find((el) => (el.textContent || '').trim().toLowerCase().includes(text.toLowerCase()));

  /* ---- stored threads, and the metadata one of them recorded ------------ */

  /* The exact shape core/response_meta.for_message produces for the turn human
     testing reported: preferred Gemini, answered by Groq after a 503. */
  const FALLBACK_META = {
    provider: 'groq', model: 'openai/gpt-oss-20b', mode: 'AUTO', tier: 'FAST',
    task: 'SMALL_TALK', fallback_used: true, fallback_from: 'google',
    fallback_reason: 'provider_error',
    provider_attempts: [
      { provider: 'google', model: 'gemini-3.7-flash', outcome: 'provider_error' },
      { provider: 'groq', model: 'openai/gpt-oss-20b', outcome: 'ok' },
    ],
    tools_offered: 0, tools_available: 84,
    prompt_tokens: 120, completion_tokens: 30,
    time_to_first_token_ms: 410, total_latency_ms: 980,
  };

  let THREADS = [
    { id: 'c1', title: 'Placa gráfica', titleSource: 'auto', createdAt: '2026-08-30T10:00:00Z',
      updatedAt: '2026-08-31T10:00:00Z', lastMessageAt: '2026-08-31T10:00:00Z',
      messageCount: 4, archived: false },
    { id: 'c2', title: 'Configurar o Ollama', titleSource: 'auto', createdAt: '2026-08-30T09:00:00Z',
      updatedAt: '2026-08-31T09:00:00Z', lastMessageAt: '2026-08-31T09:00:00Z',
      messageCount: 6, archived: false },
    { id: 'c3', title: 'Ideias para o projeto', titleSource: 'user', createdAt: '2026-08-29T09:00:00Z',
      updatedAt: '2026-08-31T08:00:00Z', lastMessageAt: '2026-08-31T08:00:00Z',
      messageCount: 2, archived: false },
    { id: 'c4', title: 'Notas soltas', titleSource: 'auto', createdAt: '2026-08-28T09:00:00Z',
      updatedAt: '2026-08-31T07:00:00Z', lastMessageAt: '2026-08-31T07:00:00Z',
      messageCount: 9, archived: false },
  ];
  let ACTIVE = 'c1';

  const HISTORY = [
    { id: 1, role: 'user', content: 'a minha placa gráfica chega?', timestamp: '2026-08-31T10:00:00Z' },
    { id: 2, role: 'assistant', content: 'Chega para a maior parte dos jogos a 1080p.',
      timestamp: '2026-08-31T10:00:01Z', meta: FALLBACK_META },
  ];

  const providersPayload = () => ({
    mode: 'AUTO', modes: ['AUTO', 'CLOUD', 'LOCAL'],
    preferredCloud: 'google', cloudProviders: ['google', 'groq'],
    google: { id: 'google', name: 'Google', kind: 'cloud', role: 'cloud', state: 'READY',
      model: 'gemini-stub-fast', models: ['gemini-stub-fast'], records: [],
      secret: { configured: true, masked: 'AIz…wxyz', source: 'encrypted_store', encrypted: true },
      tiers: { fast: 'gemini-stub-fast', complex: 'gemini-stub-fast' }, detail: 'pronto' },
    groq: { id: 'groq', name: 'Groq', kind: 'cloud', role: 'primary', state: 'READY',
      model: 'openai/gpt-oss-20b', models: ['openai/gpt-oss-20b'],
      secret: { configured: true, masked: 'gsk_…abcd', source: 'encrypted_store', encrypted: true },
      tiers: { fast: 'openai/gpt-oss-20b', complex: 'openai/gpt-oss-120b' }, detail: 'pronto' },
    ollama: { id: 'ollama', name: 'Ollama', kind: 'local', role: 'fallback', state: 'READY',
      model: 'qwen3:8b', models: ['qwen3:8b'],
      secret: { configured: true, masked: '', source: 'none', encrypted: false },
      detail: 'disponível', url: 'http://127.0.0.1:11434' },
    cooldowns: {
      groq: { provider: 'groq', temporarily_limited: false, retry_in_seconds: null, consecutive_failures: 0 },
      google: { provider: 'google', temporarily_limited: false, retry_in_seconds: null, consecutive_failures: 0 },
    },
    /* The pill's live route says GOOGLE while the stored message says GROQ.
       That disagreement is the product behaviour under test, not a fixture
       mistake: the selector expresses a preference for the NEXT turn. */
    route: { provider: 'google', model: 'gemini-stub-fast', usable: true, fallback: false,
             mode: 'AUTO', alternatives: ['groq'], reason: 'Google disponível.' },
  });

  const RESPONSES = {
    get_providers: () => providersPayload(),
    list_conversations: () => ({ ok: true, conversations: THREADS, activeId: ACTIVE }),
    get_conversation_history: () => HISTORY,
    open_conversation: (id) => {
      ACTIVE = id;
      return { ok: true, conversation: THREADS.find((t) => t.id === id), messages: HISTORY,
               summary: { summary: '', coveredThrough: 0, coveredMessages: 0, generator: '', updatedAt: '' },
               contextMessages: HISTORY.length };
    },
    delete_conversation: (id) => {
      THREADS = THREADS.filter((t) => t.id !== id);
      return { ok: true, id, messages: 4, indexEntries: 4 };
    },
    delete_conversations: (ids) => {
      const set = new Set(ids || []);
      THREADS = THREADS.filter((t) => !set.has(t.id));
      return { ok: true, removed: set.size, requested: set.size,
               deleted: [...set], failed: [], messages: 12, indexEntries: 12,
               activeId: ACTIVE };
    },
    get_memory_overview: () => ({ profile: {}, facts: [], memories: [], kinds: [],
      stats: { total: 0, active: 0, candidates: 0, archived: 0, byKind: {} },
      knowledge: { nodes: 0, edges: 0, byType: {} },
      retrieval: { mode: 'fts5', engine: 'SQLite FTS5 (BM25)', entries: 0, byKind: {} },
      conversationCount: THREADS.length, messageCount: 21, ready: true,
      migration: { ok: true }, longTermEnabled: true, captureEnabled: true,
      documentsSupported: false, documentsNote: '' }),
    get_system_readiness: () => ({ agent: { state: 'READY', pending_permissions: 0 },
      voice: { state: 'READY', blockers: [], enabled: true },
      wakeWord: { state: 'DISABLED', modelStatus: '' }, wakePhrase: { state: 'DISABLED' },
      model: { state: 'READY', local: { model: 'qwen3:8b', online: true, modelReady: true, enabled: true },
               cloud: { model: 'openai/gpt-oss-20b', configured: true }, provider: 'google' },
      worker: { state: 'READY', running: true, queue_size: 0, poll_interval: 5 },
      providers: {}, emergencyStop: false, autonomyMode: 'SAFE',
      browser: { state: 'DISABLED' }, vision: { state: 'DISABLED' } }),
    get_command_center_state: () => ({ activities: [], tasks: [], counts: {} }),
    /* send_message returns immediately; the ANSWER arrives through the exposed
       callbacks, exactly as the real backend delivers it. */
    send_message: () => ({ ok: true }),
  };

  const handler = (name) => (...args) => {
    report.calls.push({ name, args });
    const make = RESPONSES[name];
    const value = make ? make(...args) : null;
    return (cb) => { if (typeof cb === 'function') setTimeout(() => cb(value), 0); };
  };
  window.eel = new Proxy({}, { get: (_t, name) => (typeof name === 'string' ? handler(name) : undefined) });

  window.dispatchEvent(new Event('resize'));
  await sleep(1600);

  /* THE PAGE'S OWN CALLBACK REGISTRY, not eel's.
     lib/backend.expose() stores handlers on window.__nanoHandlers so a stream
     that arrives before React has mounted can be replayed. Reaching for
     eel.expose here would capture nothing and silently skip every streaming
     assertion, which is exactly the kind of quietly-vacuous test this suite is
     meant not to contain. */
  const exposed = window.__nanoHandlers || {};

  const countThinking = () => qa('.thinking').length;

  /* ================================================================== 1
     EXACTLY ONE THINKING INDICATOR, at every point in a turn.
     Human testing saw two at once: "O Nano está a pensar…" inside the pending
     bubble AND "A pensar…" underneath it. */

  ok('the chat view is open', !!q('.conversation'), q('.conversation')?.className || 'missing');
  ok('an idle conversation shows no thinking indicator', countThinking() === 0,
     'indicators=' + countThinking());

  const start = exposed['on_stream_start'];
  const status = exposed['on_stream_status'];
  const chunk = exposed['on_stream_chunk'];
  const end = exposed['on_stream_end'];
  const fail = exposed['on_stream_error'];
  const limited = exposed['on_rate_limited'];
  ok('the page registered the streaming callbacks',
     !!(start && status && chunk && end),
     Object.keys(exposed).join(', '));
  ok('the page registered the failure callbacks too', !!(fail && limited),
     'error=' + !!fail + ' rateLimited=' + !!limited);

  if (start && status && chunk && end) {
    start('m1', 'olá nano');
    await sleep(400);
    ok('a pending turn shows exactly ONE thinking indicator', countThinking() === 1,
       'indicators=' + countThinking() + ' :: ' +
       qa('.thinking').map((el) => el.textContent.trim()).join(' | '));

    /* Tool activity used to be the reason the second indicator existed. It has
       to keep narrating itself, in the one indicator that remains. */
    status('m1', '⚙️ pc_volume_get...');
    await sleep(350);
    ok('tool activity still narrates itself, and still in ONE indicator',
       countThinking() === 1 && /pc_volume_get/i.test(q('.thinking')?.textContent || ''),
       'indicators=' + countThinking() + ' :: ' + (q('.thinking')?.textContent || ''));

    /* A LAYOUT JUMP IS CONTENT MOVING, not the list growing downward as an
       answer arrives. So the measurement is the TOP of the pending bubble:
       if that shifts when the first token lands, something above it appeared
       or disappeared -- which is precisely what the second indicator did. */
    /* NOTHING IS INSERTED OR REMOVED BETWEEN THE TURN'S TWO BUBBLES.
       Measured as the GAP between the user's message and its answer, which is
       the only quantity that isolates this question: absolute positions move
       because the log auto-scrolls and because anything above the list may
       resize, and neither has to do with the indicator. If a separate element
       lived between them and vanished when the first token landed, this gap
       would change. */
    const turnGap = () => {
      const users = qa('.msg--user');
      const answers = qa('.msg--assistant');
      const user = users[users.length - 1];
      const answer = answers[answers.length - 1];
      return (user && answer) ? answer.offsetTop - user.offsetTop : null;
    };
    const gapBefore = turnGap();
    chunk('m1', 'Olá! ');
    await sleep(350);
    ok('the first token replaces the indicator in the SAME bubble',
       countThinking() === 0 && /Olá!/.test(qa('.msg--assistant').slice(-1)[0]?.textContent || ''),
       'indicators=' + countThinking());
    const gapAfter = turnGap();
    ok('nothing is inserted or removed between the question and its answer',
       gapBefore !== null && Math.abs((gapAfter || 0) - gapBefore) < 4,
       'gap before=' + gapBefore + ' after=' + gapAfter);

    end('m1', { msg_id: 'm1', text: 'Olá! Em que posso ajudar?', ok: true,
                status: [], meta: FALLBACK_META });
    await sleep(500);
    ok('a finished turn shows no thinking indicator', countThinking() === 0,
       'indicators=' + countThinking());

    /* ---- a turn that is still pending when the user leaves the thread ----
       The stream events carry a turn id and nothing about WHICH conversation
       the turn belongs to, so every handler that writes into "messages" writes
       into whatever conversation happens to be on screen when the event lands.

       Two things went wrong, and both are visible to a user who clicks another
       conversation while an answer is still arriving:

         * on_stream_chunk CREATED the assistant bubble when it could not find
           it, so the answer to a question asked in thread A was appended to
           thread B's transcript;
         * "thinking" was never cleared by the switch, so B also showed a
           standalone "O Nano está a pensar…" for a turn it had no part in.

       The backend always emits on_stream_start before any chunk for the same
       id, so a chunk whose bubble is missing means the turn is not on screen
       -- and the honest thing to do with it is nothing. */
    start('m2', 'uma pergunta na conversa A');
    await sleep(350);
    chunk('m2', 'Primeira parte da resposta… ');
    await sleep(300);
    const answeringInA = qa('.msg--assistant').length;

    // Leave for another conversation while the answer is still streaming.
    const otherRow = qa('.chat-item-row .chat-item')[1];
    ok('there is a second conversation to switch to', !!otherRow,
       'rows=' + qa('.chat-item-row .chat-item').length);
    if (otherRow) {
      otherRow.click();
      /* WAIT FOR THE SWITCH TO LAND, and wait for it on a signal that is NOT
         the thing being measured.

         open_conversation is async, so a fixed sleep samples a transcript
         that still holds thread A's bubbles; the count then drops between
         that sample and the assertion, and the drop reads as "a chunk was
         written into the wrong thread" -- the exact opposite of what
         happened. That failed one run in five at 600ms, and once in ten even
         after settling on a stable bubble COUNT, because three frames can
         agree on the old value while the round trip is still in flight.

         The signal used here is the disappearance of thread A's streamed
         text. That is what "the transcript was replaced" means, and it is
         independent of the assertion below, which is about a chunk sent
         AFTER the switch. Waiting on the bubble count itself would have made
         the wait and the assertion the same statement. */
      const switched = await waitGone(
        () => /Primeira parte da resposta/.test(q('.conversation')?.textContent || ''));
      ok('the transcript is replaced by the conversation we switched to', switched,
         (q('.conversation')?.textContent || '').slice(0, 100));
      ok('switching conversations clears the pending indicator',
         countThinking() === 0,
         'indicators=' + countThinking() + ' :: '
         + qa('.thinking').map((el) => el.textContent.trim()).join(' | '));

      const bubblesAfterSwitch = qa('.msg--assistant').length;
      chunk('m2', 'resto da resposta que pertence à outra conversa.');
      /* A chunk that is correctly IGNORED produces no DOM change to wait for,
         so this one genuinely is a quiet period: give the handler ample time
         to do the wrong thing, then assert it did not. */
      await sleep(400);
      ok('a chunk from the thread we left is not written into this one',
         qa('.msg--assistant').length === bubblesAfterSwitch
           && !/resto da resposta/.test(q('.conversation')?.textContent || ''),
         'bubbles ' + bubblesAfterSwitch + ' -> ' + qa('.msg--assistant').length);

      end('m2', { msg_id: 'm2', text: 'Resposta completa da conversa A.', ok: true,
                  status: [], meta: FALLBACK_META });
      await sleep(450);
      ok('the finished answer does not land in the conversation on screen',
         !/Resposta completa da conversa A/.test(q('.conversation')?.textContent || ''),
         (q('.conversation')?.textContent || '').slice(0, 120));
      ok('no thinking indicator survives the finished turn either',
         countThinking() === 0, 'indicators=' + countThinking());

      /* ---- a turn that FAILS ------------------------------------------
         An error is the other way a turn ends, and it has to release the
         indicator exactly as an answer does. A spinner left running after a
         failure tells the user to keep waiting for something that is never
         coming. */
      if (fail) {
        start('m3', 'pergunta que vai falhar');
        await sleep(350);
        const spinningBefore = countThinking();
        fail('m3', { code: 'provider_unavailable', detail: 'sem ligação' });
        await sleep(500);
        ok('a pending turn shows an indicator before it fails',
           spinningBefore === 1, 'indicators=' + spinningBefore);
        ok('an error clears the thinking indicator',
           countThinking() === 0, 'indicators=' + countThinking());
        ok('an error says so in the bubble rather than leaving it blank',
           /não foi possível responder/i.test(q('.conversation')?.textContent || ''),
           (qa('.msg--assistant').slice(-1)[0]?.textContent || '').slice(0, 90));
        ok('a failed turn is not left marked as still streaming',
           qa('.msg .caret').length === 0 && qa('.thinking').length === 0,
           'carets=' + qa('.msg .caret').length);
      }

      /* ---- a turn stopped by a rate limit -------------------------------
         DRIVEN AS THE BACKEND REALLY EMITS IT. core/main.py sends
         on_rate_limited and then ALWAYS on_stream_end, on the success path and
         from the exception handler alike, so a rate limit is a decoration on a
         turn that still terminates normally.

         An earlier version of this step sent on_rate_limited alone and
         asserted the indicator had stopped. It failed -- correctly, because
         only on_stream_end clears the bubble's own streaming flag -- but it
         was asserting about a sequence the product never produces. The payload
         key matters for the same reason: providers.py sends wait_seconds, and
         a test that invents waitSeconds would pass while the banner silently
         showed the wrong number.

         The residual asymmetry is deliberate and recorded rather than
         defended: on_stream_error clears the bubble itself, on_rate_limited
         leaves that to the stream_end behind it. */
      if (limited) {
        start('m4', 'pergunta que apanha um 429');
        await sleep(350);
        limited('m4', { provider: 'groq', message: 'Limite temporário atingido.',
                        wait_seconds: 30 });
        await sleep(300);
        end('m4', { msg_id: 'm4', text: 'Não consegui responder agora.', ok: true,
                    status: [], meta: FALLBACK_META,
                    rate_limit: { provider: 'groq', wait_seconds: 30 } });
        await sleep(450);
        ok('a rate-limited turn stops showing a thinking indicator',
           countThinking() === 0, 'indicators=' + countThinking());
        ok('the rate limit is announced with its real wait, not a generic error',
           /30/.test(document.body.textContent || '')
             && /limite/i.test(document.body.textContent || ''),
           (q('.rate-limit, .banner, .notice')?.textContent
             || document.body.textContent || '').slice(0, 120));
      }
    } else {
      ok('another conversation is available to switch to', false,
         'rows=' + qa('.chat-item-row').length + ' answering=' + answeringInA);
    }
  }

  /* ================================================================== 2
     THE TECHNICAL-DETAILS DISCLOSURE: discoverable, keyboard-operable, and
     reporting THIS message's provider rather than the current selection. */

  const toggle = q('.msg__meta-toggle');
  ok('the disclosure is a real button with aria-expanded',
     !!toggle && toggle.tagName === 'BUTTON' && toggle.getAttribute('aria-expanded') === 'false',
     toggle ? (toggle.tagName + ' aria-expanded=' + toggle.getAttribute('aria-expanded')) : 'missing');

  if (toggle) {
    const style = getComputedStyle(toggle);
    const rect = toggle.getBoundingClientRect();
    ok('the disclosure is visible at rest',
       Number(style.opacity) > 0.5 && style.visibility !== 'hidden' && rect.height > 0,
       'opacity=' + style.opacity + ' height=' + rect.height);
    ok('the disclosure stays compact and does not become a large button',
       rect.height <= 34 && rect.width <= 260,
       rect.width.toFixed(0) + 'x' + rect.height.toFixed(0));
    ok('the disclosure has a real chevron, not a text glyph',
       !!toggle.querySelector('svg path'),
       toggle.innerHTML.slice(0, 80));
    ok('the disclosure points at the region it controls',
       !!toggle.getAttribute('aria-controls')
         && !!document.getElementById(toggle.getAttribute('aria-controls')),
       toggle.getAttribute('aria-controls') || 'no aria-controls');
    ok('the panel is hidden while the disclosure is closed',
       document.getElementById(toggle.getAttribute('aria-controls'))?.hidden === true,
       String(document.getElementById(toggle.getAttribute('aria-controls'))?.hidden));

    /* Keyboard: focus it and press Enter, the way a keyboard user opens it. */
    toggle.focus();
    ok('the disclosure is focusable', document.activeElement === toggle,
       document.activeElement?.className || 'nothing focused');
    /* READ THE STYLE ONLY ONCE IT HAS STOPPED MOVING. The rule that answers
       this assertion is

           .msg__meta-toggle:focus { background: rgba(255,240,240,.055); ... }

       and the control carries a background transition, so
       getComputedStyle on the tick focus() lands returns the value being
       transitioned away FROM -- transparent. That is a correct stylesheet
       mid-fade being reported as an invisible control, and it failed two runs
       in five. settledStyle waits for three consecutive frames that agree. */
    const focusBackground = await settledStyle(toggle, 'backgroundColor');
    const focusOutline = await settledStyle(toggle, 'outlineStyle');
    const focusBorder = await settledStyle(toggle, 'borderColor');
    ok('the focused disclosure is visually distinguished',
       focusBackground !== 'rgba(0, 0, 0, 0)' || focusOutline !== 'none'
         || focusBorder !== 'rgba(0, 0, 0, 0)',
       'hasFocus=' + document.hasFocus() + ' bg=' + focusBackground
         + ' outline=' + focusOutline + ' border=' + focusBorder);

    toggle.click();
    await sleep(300);
    ok('clicking opens the panel and updates aria-expanded',
       toggle.getAttribute('aria-expanded') === 'true'
         && document.getElementById(toggle.getAttribute('aria-controls'))?.hidden === false,
       toggle.getAttribute('aria-expanded'));

    const panel = document.getElementById(toggle.getAttribute('aria-controls'));
    const panelText = (panel?.textContent || '');

    /* ROW BY ROW, not "somewhere in the panel".
       The provider's name appears in the Origem row too ("Google Gemini →
       Groq"), so a panel that had lost its Provedor value entirely would still
       contain the word. Pairing each <dt> with its own <dd> is what makes each
       row independently guarded. */
    const rows = {};
    const terms = Array.from(panel?.querySelectorAll('dt') || []);
    for (const dt of terms) {
      const dd = dt.nextElementSibling;
      if (dd && dd.tagName === 'DD') rows[dt.textContent.trim()] = dd.textContent.trim();
    }
    ok('the panel names the provider that ANSWERED this message',
       /Groq/.test(rows['Provedor'] || '') && /gpt-oss-20b/.test(rows['Modelo'] || ''),
       JSON.stringify(rows));
    ok('the panel reports the mode and tier of that turn',
       /AUTO/.test(rows['Modo'] || '') && /FAST/.test(rows['Modo'] || ''),
       rows['Modo'] || 'no Modo row');
    ok('the panel does not claim the currently selected provider answered it',
       !/gemini-stub-fast/.test(panelText),
       panelText.replace(/\s+/g, ' ').slice(0, 200));
    ok('the panel explains the fallback in words, with its origin',
       /Google Gemini/.test(rows['Origem'] || '') && /Groq/.test(rows['Origem'] || '')
         && /provedor|serviço/i.test(rows['Motivo'] || ''),
       'Origem=' + (rows['Origem'] || '-') + ' Motivo=' + (rows['Motivo'] || '-'));
    /* Scoped to THIS panel. Counting every .meta-chain__step in the document
       counts the other message's chain too, and the assertion then measures
       how many messages the fixture has. */
    ok('the panel shows the hop chain that produced the answer',
       panel.querySelectorAll('.meta-chain__step').length === 2,
       Array.from(panel.querySelectorAll('.meta-chain__step'))
         .map((el) => el.textContent.trim()).join(' | '));
    ok('the panel shows no raw provider exception text',
       !/UNAVAILABLE|high demand|Traceback|status_code/i.test(panelText),
       panelText.replace(/\s+/g, ' ').slice(0, 200));

    /* The pill, meanwhile, still reports the live route. Both are true. */
    ok('the top selector still shows the configured preference',
       /Gemini|Google/i.test(q('.status-pill__text')?.textContent || ''),
       q('.status-pill__text')?.textContent || 'no pill');

    toggle.click();
    await sleep(250);
    ok('clicking again closes the panel',
       toggle.getAttribute('aria-expanded') === 'false'
         && document.getElementById(toggle.getAttribute('aria-controls'))?.hidden === true,
       toggle.getAttribute('aria-expanded'));
  }

  /* ================================================================== 3
     THE CONVERSATION RAIL: action discoverability, no left stripe,
     multi-select, bulk delete, Escape. */

  const rows = qa('.chat-item-row');
  ok('the rail lists the stored conversations', rows.length === 4, 'rows=' + rows.length);

  const menuButton = q('.chat-item-row .chat-item__menu');
  ok('each conversation row carries an actions menu', !!menuButton,
     'menus=' + qa('.chat-item-row .chat-item__menu').length);
  if (menuButton) {
    const style = getComputedStyle(menuButton);
    const rect = menuButton.getBoundingClientRect();
    /* THE HUMAN-TESTING FINDING. The control used to sit at opacity 0 until the
       row was hovered, so on a first visit there was no evidence a row could be
       renamed or deleted at all. */
    ok('the row action button is visible without hovering',
       Number(style.opacity) >= 0.35 && style.visibility !== 'hidden' && style.display !== 'none',
       'opacity=' + style.opacity);
    ok('the row action button has a usable hit target',
       rect.width >= 24 && rect.height >= 24,
       rect.width.toFixed(0) + 'x' + rect.height.toFixed(0));
    ok('the row action button is reachable by keyboard',
       menuButton.tagName === 'BUTTON' && menuButton.tabIndex >= 0,
       menuButton.tagName + ' tabIndex=' + menuButton.tabIndex);
    ok('the row action button names what it does', !!menuButton.getAttribute('aria-label'),
       menuButton.getAttribute('aria-label') || 'no aria-label');
  } else {
    ok('the row action button exists', false, 'no .chat-item__menu found');
  }

  /* THE LEFT STRIPE IS GONE. It was a second "you are here" marker sitting on
     one that already worked, and against the rail's own edge it read as a
     scrollbar. Checking the computed ::before is the only honest way: a class
     name says nothing about what is painted. */
  const activeRow = q('.chat-item[aria-current="true"]');
  ok('one conversation is marked as active', !!activeRow,
     activeRow?.textContent?.trim() || 'none');
  if (activeRow) {
    const before = getComputedStyle(activeRow, '::before');
    const drawsStripe = before.content !== 'none' && before.content !== 'normal'
      && parseFloat(before.width || '0') > 0 && parseFloat(before.height || '0') > 0;
    ok('the active conversation has no vertical bar on its left edge',
       !drawsStripe,
       'content=' + before.content + ' ' + before.width + 'x' + before.height);
    const style = getComputedStyle(activeRow);
    ok('the active conversation is still unmistakable without the bar',
       style.backgroundImage !== 'none' || style.backgroundColor !== 'rgba(0, 0, 0, 0)',
       'bg=' + style.backgroundColor + ' image=' + style.backgroundImage.slice(0, 60));
  }

  /* ---- multi-select ----------------------------------------------------- */
  ok('checkboxes do not clutter the rail before selection mode',
     qa('.chat-item__check').length === 0,
     String(qa('.chat-item__check').length));

  const selectButton = byText('.rail__select-action', 'Selecionar');
  ok('the rail offers a way into selection mode', !!selectButton,
     selectButton?.textContent?.trim() || 'missing');

  if (selectButton) {
    selectButton.click();
    await sleep(350);
    ok('selection mode shows a checkbox on every row',
       qa('.chat-item__check').length === 4,
       String(qa('.chat-item__check').length));
    ok('selection mode starts with nothing selected',
       (q('.rail__select-count')?.textContent || '').startsWith('0'),
       q('.rail__select-count')?.textContent || 'no counter');
    ok('the per-row menu steps aside in selection mode',
       qa('.chat-item__menu').length === 0,
       String(qa('.chat-item__menu').length));

    qa('.chat-item-row .chat-item')[0].click();
    qa('.chat-item-row .chat-item')[2].click();
    await sleep(300);
    ok('selecting two rows is counted',
       (q('.rail__select-count')?.textContent || '').startsWith('2'),
       q('.rail__select-count')?.textContent || 'no counter');
    ok('a selected row is visibly selected',
       qa('.chat-item-row[data-selected="true"]').length === 2,
       String(qa('.chat-item-row[data-selected="true"]').length));

    /* Deselecting one individually. */
    qa('.chat-item-row .chat-item')[2].click();
    await sleep(250);
    ok('a selected row can be deselected individually',
       (q('.rail__select-count')?.textContent || '').startsWith('1'),
       q('.rail__select-count')?.textContent || 'no counter');

    const selectAll = byText('.rail__select-action', 'Selecionar tudo');
    ok('selection mode offers Selecionar tudo', !!selectAll,
       qa('.rail__select-action').map((el) => el.textContent.trim()).join(' | '));
    if (selectAll) {
      selectAll.click();
      await sleep(300);
      ok('Select all selects every visible conversation',
         (q('.rail__select-count')?.textContent || '').startsWith('4'),
         q('.rail__select-count')?.textContent || 'no counter');
    } else {
      ok('Select all is offered', false, 'missing');
    }

    /* ---- bulk delete needs one clear, counted confirmation ------------- */
    const deleteButton = byText('.rail__select-action--danger', 'Eliminar');
    ok('a delete action is offered once something is selected', !!deleteButton,
       deleteButton?.textContent?.trim() || 'missing');
    if (deleteButton) {
      deleteButton.click();
      await sleep(400);
      const dialog = q('[role="dialog"], .modal');
      const dialogText = (dialog?.textContent || '');
      ok('bulk delete asks for confirmation', !!dialog, dialog ? 'dialog shown' : 'no dialog');
      ok('the confirmation counts what will be removed',
         /4\s*conversas/i.test(dialogText),
         dialogText.replace(/\s+/g, ' ').slice(0, 200));
      ok('the confirmation says long-term memories survive',
         /mem[óo]rias de longo prazo/i.test(dialogText),
         dialogText.replace(/\s+/g, ' ').slice(0, 240));
      ok('nothing has been deleted before the user confirms',
         report.calls.every((c) => c.name !== 'delete_conversations'),
         report.calls.map((c) => c.name).join(', '));

      /* Cancel first: a confirmation that cannot be refused is not one. */
      const cancel = Array.from(dialog?.querySelectorAll('button') || [])
        .find((b) => /cancelar/i.test(b.textContent || ''));
      ok('the confirmation can be refused', !!cancel,
         Array.from(dialog?.querySelectorAll('button') || [])
           .map((b) => b.textContent.trim()).join(' | ') || 'no dialog');
      if (cancel) {
        cancel.click();
        await sleep(350);
        ok('cancelling the confirmation deletes nothing',
           report.calls.every((c) => c.name !== 'delete_conversations')
             && qa('.chat-item-row').length === 4,
           'rows=' + qa('.chat-item-row').length);
      } else {
        ok('the confirmation can be cancelled', false, 'no cancel button');
      }

      /* Starting a new conversation leaves selection mode too: the mode is
         modal everywhere else, and a fresh chat with the rail still ticking
         rows is a trap the next click walks into. */
      byText('.rail__select-action', 'Selecionar')?.click();
      await sleep(300);
      qa('.chat-item-row .chat-item')[0]?.click();
      await sleep(200);
      const inModeBeforeNew = qa('.chat-item__check').length > 0;
      byText('.btn', 'Nova conversa')?.click();
      await sleep(700);
      ok('starting a new conversation leaves selection mode',
         inModeBeforeNew && qa('.chat-item__check').length === 0,
         'before=' + inModeBeforeNew + ' after=' + qa('.chat-item__check').length);

      /* Escape leaves selection mode. */
      byText('.rail__select-action', 'Selecionar')?.click();
      await sleep(300);
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      await sleep(350);
      ok('Escape leaves selection mode',
         qa('.chat-item__check').length === 0 && qa('.chat-item__menu').length > 0,
         'checks=' + qa('.chat-item__check').length + ' menus=' + qa('.chat-item__menu').length);

      /* Now do it for real: enter, select two, delete, confirm. */
      byText('.rail__select-action', 'Selecionar')?.click();
      await sleep(300);
      qa('.chat-item-row .chat-item')[0].click();
      qa('.chat-item-row .chat-item')[1].click();
      await sleep(250);
      byText('.rail__select-action--danger', 'Eliminar')?.click();
      await sleep(400);
      const confirmDialog = q('[role="dialog"], .modal');
      const confirmButton = Array.from(confirmDialog?.querySelectorAll('button') || [])
        .find((b) => /^apagar/i.test((b.textContent || '').trim()));
      ok('the confirmation offers an Apagar button', !!confirmButton,
         confirmDialog ? 'dialog present, no Apagar button' : 'no dialog rendered');
      if (confirmButton) {
        confirmButton.click();
        /* The rail re-renders when delete_conversations resolves. Waiting a
           fixed 700ms sampled four rows on one run in five. */
        await waitFor(() => qa('.chat-item-row').length === 2);
        const bulk = report.calls.filter((c) => c.name === 'delete_conversations');
        ok('confirming issues ONE bulk call, not one call per conversation',
           bulk.length === 1 && Array.isArray(bulk[0].args[0]) && bulk[0].args[0].length === 2,
           JSON.stringify(bulk.map((c) => c.args)));
        ok('no per-conversation delete was issued alongside it',
           report.calls.every((c) => c.name !== 'delete_conversation'),
           report.calls.filter((c) => c.name === 'delete_conversation').length + ' single deletes');
        ok('the rail reflects the deletion', qa('.chat-item-row').length === 2,
           'rows=' + qa('.chat-item-row').length);
        ok('selection mode ends after a delete',
           qa('.chat-item__check').length === 0,
           String(qa('.chat-item__check').length));
      } else {
        ok('the confirmation can be confirmed', false,
           Array.from(confirmDialog?.querySelectorAll('button') || [])
             .map((b) => b.textContent).join(' / ') || 'no dialog');
      }
    }

    /* ---- the selection versus the search filter -------------------------
       The counter, the confirmation title, the confirm button and the ids that
       reach the backend are computed from different lists, so this is exactly
       where they drift apart.

       The case that exposes it: select a row, then filter it OFF SCREEN. The
       selection still names one conversation and the dialog still offers to
       delete one, so the backend must receive that one. Resolving the ids
       against the filtered view instead sent an empty list -- and an empty list
       is dropped before the call, so the user confirmed a deletion that never
       happened and the row was still there afterwards.

       Deletes exactly one of the two remaining conversations, so the later
       per-size render checks still have a rail with content. */
    byText('.rail__select-action', 'Selecionar')?.click();
    await waitFor(() => qa('.chat-item__check').length > 0);
    const titles = qa('.chat-item__title').map((n) => (n.textContent || '').trim());
    const rowButtons = qa('.chat-item-row .chat-item');
    const searchBox = q('.rail__search input, .rail input');

    ok('the two surviving conversations and the search box are both present',
       rowButtons.length === 2 && !!searchBox && !!titles[0] && !!titles[1],
       'rows=' + rowButtons.length + ' search=' + !!searchBox
         + ' titles=' + JSON.stringify(titles));
    if (rowButtons.length === 2 && searchBox && titles[0] && titles[1]) {
      rowButtons[1].click();               // select the SECOND row only
      await waitFor(() => (q('.rail__select-count')?.textContent || '').startsWith('1'));
      const selectedId = titles[1];
      ok('one row can be selected on its own',
         (q('.rail__select-count')?.textContent || '').startsWith('1'),
         q('.rail__select-count')?.textContent || 'no counter');

      const setValue = (el, value) => {
        Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
          .set.call(el, value);
        el.dispatchEvent(new Event('input', { bubbles: true }));
      };
      // A query that shows the FIRST row and hides the selected one.
      setValue(searchBox, titles[0].split(' ')[0]);
      /* Settle on the filtered list rather than on 400ms: the filter is
         debounced, so a short sleep samples the UNfiltered rail and the whole
         block below is skipped without a word. */
      await waitStable(() => qa('.chat-item__title').map((n) => (n.textContent || '').trim()),
                       { frames: 3 });
      const visibleTitles = qa('.chat-item__title').map((n) => (n.textContent || '').trim());

      ok('the query hides the selected row and leaves the other one',
         visibleTitles.length === 1 && visibleTitles[0] !== selectedId,
         'visible=' + JSON.stringify(visibleTitles) + ' selected=' + selectedId);
      if (visibleTitles.length === 1 && visibleTitles[0] !== selectedId) {
        ok('filtering does not silently shrink the selection',
           (q('.rail__select-count')?.textContent || '').startsWith('1'),
           'counter=' + (q('.rail__select-count')?.textContent || '')
           + ' visible=' + visibleTitles.length);

        byText('.rail__select-action--danger', 'Eliminar')?.click();
        await sleep(400);
        const dlg = q('[role="dialog"], .modal');
        const dlgText = dlg?.textContent || '';
        const confirmBtn = Array.from(dlg?.querySelectorAll('button') || [])
          .find((b) => /^apagar/i.test((b.textContent || '').trim()));
        ok('the confirmation still counts the selected conversation',
           /apagar 1 conversa/i.test(dlgText), 'dialog=' + dlgText.slice(0, 90));

        const before = report.calls.filter((c) => c.name === 'delete_conversations').length;
        confirmBtn?.click();
        await sleep(700);
        const bulk = report.calls.filter((c) => c.name === 'delete_conversations');
        const sent = bulk.length > before ? bulk[bulk.length - 1].args[0] : [];
        ok('a selection hidden by the filter is still what gets deleted',
           bulk.length === before + 1 && sent.length === 1,
           'calls=' + (bulk.length - before) + ' ids=' + JSON.stringify(sent));
        // CLEAR THE FILTER BEFORE LOOKING. Asserting the row is gone while the
        // search is still hiding it would pass for the wrong reason -- and did,
        // on the run that proved this guard catches the defect.
        setValue(searchBox, '');
        await sleep(350);
        ok('the conversation the user selected is the one that went',
           qa('.chat-item__title').map((n) => (n.textContent || '').trim())
             .indexOf(selectedId) === -1,
           'still listed: ' + qa('.chat-item__title').map((n) => n.textContent).join(' | '));
      } else {
        ok('the filter isolates a row for the selection check', false,
           'visible=' + JSON.stringify(visibleTitles) + ' selected=' + selectedId);
      }
      setValue(searchBox, '');
      await sleep(300);
    }
  }

  /* ---- nothing overflows horizontally at this size --------------------- */
  ok('the window does not scroll horizontally',
     document.documentElement.scrollWidth <= window.innerWidth + 1,
     document.documentElement.scrollWidth + ' vs ' + window.innerWidth);

  return report;
})();
  `);

  /* ================================================================== 4
     EVERY STATE, AT EVERY SUPPORTED SIZE.
     The sequence above runs once, at one size, because it is about behaviour.
     Clipping is about geometry, so the three interesting states are re-entered
     at each supported window size and measured. 940x620 is the shell's own
     minimum; anything that overflows there overflows for a real user. */
  const VIEWPORTS = [
    { name: '1920x1080', width: 1920, height: 1080 },
    { name: '1440x900', width: 1440, height: 900 },
    { name: '1366x768', width: 1366, height: 768 },
    { name: '940x620-min', width: 940, height: 620 },
  ];

  for (const viewport of VIEWPORTS) {
    guard.phase('measuring the layout at ' + viewport.name);
    win.setSize(viewport.width, viewport.height);
    await new Promise((r) => setTimeout(r, 700));
    const measured = await win.webContents.executeJavaScript(PAGE_READY + String.raw`
(async () => {
  const { sleep, waitFor, settleLayout } = window.__ready;
  const q  = (s) => document.querySelector(s);
  const qa = (s) => Array.from(document.querySelectorAll(s));
  const byText = (sel, text) =>
    qa(sel).find((el) => (el.textContent || '').trim().toLowerCase().includes(text.toLowerCase()));
  const doc = document.documentElement;

  /* Anything whose right edge is past the viewport is clipped. Reported by
     class so a failure names the element instead of just the page. */
  const offenders = () => qa('.app *').filter((el) => {
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.right > doc.clientWidth + 1;
  }).map((el) => el.tagName.toLowerCase() + '.' + String(el.className || '').split(' ')[0])
    .slice(0, 6);

  /* WAIT FOR THE RESIZE TO LAND BEFORE BELIEVING ANY GEOMETRY. settleLayout
     holds until two consecutive animation frames agree about the viewport AND
     about the app's own width, rather than trusting a stopwatch -- see
     lib/page-ready.js for the half-applied relayout this stops misreporting as
     six clipped elements.

     (No backticks in this comment: it lives inside a template literal.) */
  const settle = settleLayout;
  const settled = await settle();

  const state = {};
  // A settle that gave up means the geometry below was never stable, so every
  // number after this point would be a guess. Recorded rather than swallowed.
  if (!settled) state.settleTimedOut = true;

  /* A. the details panel, open */
  const toggle = q('.msg__meta-toggle');
  if (toggle && toggle.getAttribute('aria-expanded') === 'false') toggle.click();
  await sleep(250);
  await settle();
  const panel = toggle ? document.getElementById(toggle.getAttribute('aria-controls')) : null;
  const panelRect = panel ? panel.getBoundingClientRect() : null;
  state.detailsOpen = {
    rendered: !!panelRect && panelRect.height > 0,
    withinViewport: !!panelRect && panelRect.right <= doc.clientWidth + 1,
    overflow: doc.scrollWidth > doc.clientWidth,
    offenders: offenders(),
  };
  if (toggle) toggle.click();
  await sleep(200);

  /* B. the rail in selection mode, with rows selected.
     BELOW 1080px THE RAIL IS A DRAWER, by design, so it has to be opened
     before it can be measured. Measuring it closed would report "not rendered"
     and read as a clipping failure -- which is a harness bug dressed up as a
     product one. */
  if (!q('.rail')) {
    document.querySelector('[aria-label="Abrir conversas"]')?.click();
    /* The drawer animates open. A fixed 500ms missed it at 940x620, so
       nothing below found a rail to click, the toolbar measured as "not
       rendered", and a clean page reported a clipping failure. */
    await waitFor(() => q('.rail'));
  }
  state.railOpened = !!q('.rail');
  byText('.rail__select-action', 'Selecionar')?.click();
  await waitFor(() => qa('.chat-item-row .chat-item').length > 0);
  qa('.chat-item-row .chat-item')[0]?.click();
  await settle();
  const bar = q('.rail__select-bar');
  const barRect = bar ? bar.getBoundingClientRect() : null;
  state.selectionMode = {
    rendered: !!barRect && barRect.height > 0,
    /* The toolbar wraps rather than clipping: on a narrow rail its buttons
       must still all be reachable, which is a height question, not a width
       one. */
    withinRail: !!barRect && barRect.right <= (q('.rail')?.getBoundingClientRect().right ?? 0) + 1,
    actionsVisible: qa('.rail__select-action').filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    }).length,
    overflow: doc.scrollWidth > doc.clientWidth,
    offenders: offenders(),
  };

  /* C. the bulk-delete confirmation */
  byText('.rail__select-action--danger', 'Eliminar')?.click();
  await waitFor(() => q('[role="dialog"], .modal'));
  await settle();
  const dialog = q('[role="dialog"], .modal');
  const dialogRect = dialog ? dialog.getBoundingClientRect() : null;
  state.confirmation = {
    rendered: !!dialogRect && dialogRect.height > 0,
    withinViewport: !!dialogRect
      && dialogRect.left >= -1 && dialogRect.top >= -1
      && dialogRect.right <= window.innerWidth + 1
      && dialogRect.bottom <= window.innerHeight + 1,
    overflow: doc.scrollWidth > doc.clientWidth,
    offenders: offenders(),
  };
  Array.from(dialog?.querySelectorAll('button') || [])
    .find((b) => /cancelar/i.test(b.textContent || ''))?.click();
  await sleep(250);
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await sleep(250);

  state.settled = settled;
  return state;
})();
    `, true);

    for (const [name, measurement] of Object.entries(measured)) {
      // `settled` rides along in the same object as a diagnostic, not as a
      // measured state. Skip anything that is not a measurement rather than
      // reading `.offenders` off a boolean.
      if (!measurement || typeof measurement !== 'object'
          || !Array.isArray(measurement.offenders)) continue;
      const clean = !measurement.overflow && !measurement.offenders.length;
      const fits = measurement.withinViewport !== false && measurement.withinRail !== false;
      result.steps.push({
        label: `${name} renders without clipping at ${viewport.name}`,
        pass: Boolean(measurement.rendered && clean && fits),
        detail: JSON.stringify(measurement),
      });
      // Keep the raw measurement addressable too. Clipping is one question a
      // caller can ask of it; "are all the buttons still reachable" is another,
      // and squeezing that out of a step's detail string is worse than
      // reporting the numbers once.
      result.views.push({ viewport: viewport.name, state: name, ...measurement });
    }
  }

  const failed = result.steps.filter((s) => !s.pass);
  for (const step of result.steps) {
    console.error('  [' + (step.pass ? 'PASS' : 'FAIL') + '] ' + step.label +
      (step.detail ? ' - ' + step.detail : ''));
  }
  console.error('FAILURES: ' + (failed.length ? failed.map((f) => f.label).join('; ') : 'none'));

  process.stdout.write(JSON.stringify({
    ok: failed.length === 0,
    steps: result.steps,
    views: result.views,
    calledFunctions: [...new Set(result.calls.map((c) => c.name))].sort(),
    bulkDeleteArgs: result.calls.filter((c) => c.name === 'delete_conversations').map((c) => c.args),
  }, null, 2));

  clearInterval(focusKeeper);
  if (focusEmulated) { try { win.webContents.debugger.detach(); } catch (_) { /* already gone */ } }
  guard.disarm();
  server.close();
  app.exit(failed.length ? 1 : 0);
}).catch((err) => { guard.disarm(); console.error(err); app.exit(1); });
