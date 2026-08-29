/**
 * Drive the REAL built Nano UI in Electron's own Chromium and assert on what it
 * actually renders and does.
 *
 * WHY THIS EXISTS RATHER THAN MORE SOURCE ASSERTIONS. A test that greps for
 * `role="menuitemradio"` keeps passing the day the popover stops opening. The
 * claims this pass makes -- the pill is interactive, Escape closes it and
 * returns focus, choosing a mode reaches the backend, and the pill's label
 * follows the backend's answer rather than the click -- are all claims about
 * BEHAVIOUR, and the only honest way to check them is to perform them.
 *
 * The eel bridge is a scripted stub so the run is deterministic without a live
 * Python process, but it speaks the REAL payload shapes and RECORDS the real
 * function names the UI calls. A UI that repainted optimistically without
 * calling the backend, or that called a function the backend does not expose,
 * fails here -- tests/test_settings_v2.py cross-checks every recorded name
 * against what core/main.py actually exposes.
 *
 * TWO SELECTOR TRAPS, LEARNED THE HARD WAY. Menu items and rail items carry a
 * hint beside the label, and the hints contain the other options' words: the
 * AUTO hint ends "...continua no modelo LOCAL", and the Memoria hint reads "o
 * que o Nano guarda SOBRE ti". Matching an item by its whole textContent
 * therefore clicks the wrong control and the test passes or fails for a reason
 * that has nothing to do with the product. Always match the label element.
 *
 *     npx electron test/settings-drive.js      # JSON on stdout, log on stderr
 */
'use strict';

const { app, BrowserWindow } = require('electron');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..');   // electron/test -> repo root
const OUT_DIR = path.join(ROOT, 'frontend', 'out');

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.ico': 'image/x-icon',
  '.png': 'image/png',
};

function serve() {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      let rel = decodeURIComponent(req.url.split('?')[0]);
      if (rel === '/') rel = '/index.html';
      let file = path.join(OUT_DIR, rel);
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
  const server = await serve();
  const port = server.address().port;

  const win = new BrowserWindow({
    width: 1366, height: 768, show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });

  await win.loadURL(`http://127.0.0.1:${port}/`);
  await new Promise((r) => setTimeout(r, 1200));

  const result = await win.webContents.executeJavaScript(String.raw`
(async () => {
  const report = { steps: [], calls: [] };
  const ok = (label, pass, detail) => report.steps.push({ label, pass: !!pass, detail: detail ?? '' });
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const q  = (s) => document.querySelector(s);
  const qa = (s) => Array.from(document.querySelectorAll(s));
  const byText = (sel, text) =>
    qa(sel).find((el) => (el.textContent || '').trim().toLowerCase().includes(text.toLowerCase()));
  /* Rail items and menu items carry a hint next to the label, and the hints
     contain the other options' words ("...continua no modelo LOCAL", "o que o
     Nano guarda SOBRE ti"). Matching the label element exactly is the only
     way to click the control the test means to click. */
  const byLabel = (sel, labelSel, text) =>
    qa(sel).find((el) => (el.querySelector(labelSel)?.textContent || '').trim() === text);
  const railItem = (text) => byLabel('.settings-rail__item', '.settings-rail__label', text);

  /* ---- a scripted backend, speaking the real payload shapes ------------- */
  let MODE = 'AUTO';
  const providersPayload = () => {
    const local = MODE === 'LOCAL';
    return {
      mode: MODE, modes: ['AUTO', 'CLOUD', 'LOCAL'],
      groq: {
        id: 'groq', name: 'Groq', kind: 'cloud', role: 'primary',
        state: local ? 'DISABLED' : 'READY',
        model: 'openai/gpt-oss-20b', models: ['openai/gpt-oss-20b', 'openai/gpt-oss-120b'],
        secret: { configured: true, masked: 'gsk_…abcd', source: 'encrypted_store', encrypted: true },
        tiers: { fast: 'openai/gpt-oss-20b', complex: 'openai/gpt-oss-120b' },
        tiers_ok: { fast: true, complex: true },
        detail: local ? 'Modo Local: o Groq não é contactado.' : 'Groq pronto.',
      },
      ollama: {
        id: 'ollama', name: 'Ollama', kind: 'local', role: 'fallback',
        state: MODE === 'CLOUD' ? 'DISABLED' : 'READY',
        model: 'qwen3:8b', models: ['qwen3:8b', 'llama3.1:8b'],
        secret: { configured: true, masked: '', source: 'none', encrypted: false },
        detail: 'Ollama disponível com qwen3:8b.', url: 'http://127.0.0.1:11434',
      },
      route: {
        provider: local ? 'ollama' : 'groq',
        model: local ? 'qwen3:8b' : 'openai/gpt-oss-20b',
        usable: true, fallback: false, mode: MODE,
        reason: local ? 'Modo Local: apenas o Ollama é usado.' : 'Groq disponível.',
      },
    };
  };

  const settingsPayload = () => ({
    providers: providersPayload(),
    voice: { enabled: true, ttsEnabled: true, wakePhrase: 'ei nano', wakePhraseEnabled: false,
             allowNanoOnly: false, cooldownSeconds: 3, commandTimeoutSeconds: 7, state: 'READY',
             typedChatTts: false, voiceReplyTts: true, recentTranscripts: [] },
    devices: { inputs: [{ id: 1, name: 'Microfone' }], outputs: [{ id: 2, name: 'Altifalantes' }] },
    security: { autonomyMode: 'SAFE', emergencyStop: false, persistentAllowDisabled: true, secretsEncrypted: true },
    memory: { factsEnabled: true, ragEnabled: false, ragSupported: false, ragNote: 'chromadb não instalado.' },
    stored: { provider_mode: MODE },
    runtime: { version: '1.0.0', versionDisplay: 'v1.0', python: '3.12.10', platform: 'win32',
               ramTotalGb: 16, recommendedLocalModel: 'qwen3:8b' },
  });

  const catalogue = {
    categories: [
      { id: 'apps', label: 'Aplicações', hint: 'Abrir e alternar programas.', capabilities: [
        { tool: 'pc_app_launch', description: 'Abre uma aplicação instalada pelo nome.',
          capability: 'pc.app.launch', risk: 'medium', status: 'available' }]},
      { id: 'windows', label: 'Janelas', hint: 'Mover e fechar janelas.', capabilities: [
        { tool: 'pc_window_close', description: 'Fecha uma janela.',
          capability: 'pc.window.close', risk: 'high', status: 'confirm' }]},
    ],
    unsupported: [{ tool: 'shell.execution', description: 'O Nano não executa comandos arbitrários.',
                    capability: null, risk: 'none', status: 'unsupported',
                    alternatives: ['pc_system_info — estado da máquina'] }],
    totals: { available: 1, confirm: 1, unsupported: 1, capabilities: 2 },
  };

  const RESPONSES = {
    get_providers: () => providersPayload(),
    get_settings: () => settingsPayload(),
    get_capability_catalogue: () => catalogue,
    set_provider_mode: (mode) => { MODE = mode; return { ok: true, mode, providers: providersPayload() }; },
    get_memory_overview: () => ({ profile: {}, facts: [{ key: 'cidade', value: 'Lisboa' }],
                                  messageCount: 3, ragEnabled: false, documents: [],
                                  documentsSupported: false, documentsNote: '' }),
    get_data_location: () => ({ data_dir: 'C:\\Users\\test\\AppData\\Roaming\\Nano' }),
    get_loaded_plugins: () => ({ pc_control: ['pc_app_launch'] }),
    get_system_readiness: () => ({ agent: { state: 'READY', pending_permissions: 0 },
      voice: { state: 'READY', blockers: [], enabled: true },
      wakeWord: { state: 'DISABLED', modelStatus: '' },
      wakePhrase: { state: 'DISABLED' },
      model: { state: 'READY', local: { model: 'qwen3:8b', online: true, modelReady: true, enabled: true },
               cloud: { model: 'openai/gpt-oss-20b', configured: true }, provider: 'groq' },
      worker: { state: 'READY', running: true, queue_size: 0, poll_interval: 5 },
      providers: {}, emergencyStop: false, autonomyMode: 'SAFE',
      browser: { state: 'DISABLED' }, vision: { state: 'DISABLED' } }),
    // PC -> Atividade's real, PC-scoped, already-authorised action history.
    // Deliberately zero "failed" rows: this stub is what lets the drive prove
    // the FILTERED-empty state ("Sem eventos nesta categoria") is distinct
    // from the true-empty state, without needing a second full run.
    get_pc_activity: (category) => {
      const rows = [
        { action: 'CENTRAR JANELA', target: 'Calculadora', capability: 'pc.window.control',
          decision: 'executed', risk: 'low', requiresConfirmation: false, at: new Date().toISOString() },
        { action: 'FECHAR JANELA', target: 'Discord', capability: 'pc.window.close',
          decision: 'deny', risk: 'high', requiresConfirmation: true, at: new Date().toISOString() },
        { action: 'CAPTURAR O ECRÃ', target: 'Ecrã completo', capability: 'pc.screen.capture',
          decision: 'executed', risk: 'high', requiresConfirmation: true, at: new Date().toISOString() },
      ];
      const byCategory = {
        acoes: (r) => r.decision === 'executed',
        permissoes: (r) => r.decision === 'allow_once' || r.decision === 'deny',
        erros: (r) => r.decision === 'failed',
      };
      const filterFn = byCategory[category];
      return filterFn ? rows.filter(filterFn) : rows;
    },
  };

  const handler = (name) => (...args) => {
    report.calls.push({ name, args });
    const make = RESPONSES[name];
    const value = make ? make(...args) : null;
    return (cb) => { if (typeof cb === 'function') setTimeout(() => cb(value), 0); };
  };
  window.eel = new Proxy({}, { get: (_t, name) => (typeof name === 'string' ? handler(name) : undefined) });

  // Re-mount so the app picks up the bridge.
  window.dispatchEvent(new Event('resize'));
  await sleep(1500);

  /* ---- 1. every top-level destination opens real content ---------------- */
  const navButtons = qa('.topnav-item');
  ok('the five destinations are present', navButtons.length === 5,
     navButtons.map((b) => b.textContent.trim()).join(' / '));

  for (const [label, expect] of [
    ['Ferramentas', 'Capacidades'],
    ['PC', null],
    ['Memória', null],
    ['Definições', 'Definições'],
    ['Chat', null],
  ]) {
    const btn = byText('.topnav-item', label);
    if (!btn) { ok('destination ' + label + ' exists', false); continue; }
    btn.click();
    await sleep(700);
    const stage = q('.page__inner, .settings-layout, .conversation');
    const text = (document.body.textContent || '');
    ok('destination ' + label + ' opens content',
       !!stage && text.length > 200 && !/undefined|NaN|\[object Object\]/.test(text.slice(0, 4000)),
       stage ? stage.className : 'no stage');
  }

  /* ---- 2. the AI pill opens the selector -------------------------------- */
  byText('.topnav-item', 'Chat')?.click();
  await sleep(500);
  const pill = q('.status-pill--menu');
  ok('the AI pill is an interactive menu trigger',
     !!pill && pill.getAttribute('aria-haspopup') === 'menu', pill ? pill.className : 'missing');
  const labelBefore = q('.status-pill__text')?.textContent?.trim();
  ok('the pill shows the real provider and mode', labelBefore === 'Groq · AUTO', labelBefore);

  if (!pill) {
    report.dom = {
      hasApp: !!q('.app'), hasTopbar: !!q('.topbar'),
      pills: qa('[class*=status-pill]').map((e) => e.className),
      bodyStart: (document.body.textContent || '').slice(0, 400),
      eelSeen: typeof window.eel,
      calls: [...new Set(report.calls.map((c) => c.name))],
    };
    return report;
  }
  pill.click();
  await sleep(350);
  ok('clicking the pill opens a popover', !!q('.popover[role="menu"]'));
  ok('the popover offers all three modes', qa('.popover [role="menuitemradio"]').length === 3);
  ok('the active mode is marked',
     q('.popover [role="menuitemradio"][aria-checked="true"]')?.textContent?.includes('Automático'));

  /* ---- 2b. the human retest's exact regression: TopNav must stay visible,
     and the popover must not be clipped -- with the popover OPEN.
     This is the direct check for the bug: opening the pill used to hide most
     of the top navigation and clip the popover near the top/right edge of the
     window. Both were symptoms of the popover living INSIDE .topbar, whose
     own overflow:hidden and backdrop-filter (kept there deliberately, to hold
     the active-tab glow inside the bar) clipped it and composited badly. */
  {
    const openPopover = q('.popover[role="menu"]');
    const topbar = q('.topbar');
    ok('the popover is portaled to <body>, not nested inside .topbar',
       !!openPopover && !topbar.contains(openPopover),
       openPopover ? (topbar.contains(openPopover) ? 'still inside .topbar' : 'outside .topbar') : 'no popover found');

    const navLabels = qa('.topnav-item').map((el) => el.textContent.trim());
    ok('all five destinations are still present with the popover open',
       navLabels.length === 5 && navLabels.every((label) => label.length > 0),
       navLabels.join(' / '));
    const navRect = q('.topbar__nav')?.getBoundingClientRect();
    ok('the nav bar itself still has real, visible dimensions',
       !!navRect && navRect.width > 100 && navRect.height > 0,
       navRect ? (navRect.width + 'x' + navRect.height) : 'no .topbar__nav');
    const brand = q('.topbar__brand');
    const brandStyle = brand ? getComputedStyle(brand) : null;
    ok('the Nano brand lockup is not hidden',
       !!brand && brandStyle.visibility !== 'hidden' && brandStyle.display !== 'none'
         && Number(brandStyle.opacity) > 0,
       brandStyle ? ('visibility=' + brandStyle.visibility + ' display=' + brandStyle.display + ' opacity=' + brandStyle.opacity) : 'no brand element');

    if (openPopover) {
      const panelRect = openPopover.getBoundingClientRect();
      const withinViewport =
        panelRect.left >= 0 && panelRect.top >= 0 &&
        panelRect.right <= window.innerWidth + 1 && panelRect.bottom <= window.innerHeight + 1;
      ok('the popover stays fully inside the Electron viewport',
         withinViewport,
         'rect=' + JSON.stringify({ left: panelRect.left, top: panelRect.top, right: panelRect.right, bottom: panelRect.bottom }) +
         ' viewport=' + window.innerWidth + 'x' + window.innerHeight);
      ok('the popover is not clipped to zero size',
         panelRect.width > 100 && panelRect.height > 50,
         panelRect.width + 'x' + panelRect.height);
    }
  }

  /* ---- 3. Escape closes it and focus returns ---------------------------- */
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await sleep(300);
  ok('Escape closes the popover', !q('.popover[role="menu"]'));
  ok('Escape returns focus to the pill', document.activeElement === pill,
     document.activeElement ? document.activeElement.className : 'none');

  /* ---- 4. outside click closes it --------------------------------------- */
  pill.click(); await sleep(300);
  ok('the popover reopens', !!q('.popover[role="menu"]'));
  document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
  await sleep(300);
  ok('an outside click closes the popover', !q('.popover[role="menu"]'));

  /* ---- 5. AUTO -> LOCAL through the pill, label follows real state ------ */
  pill.click(); await sleep(300);
  const callsBefore = report.calls.filter((c) => c.name === 'set_provider_mode').length;
  (() => {
    const item = qa('.popover [role="menuitemradio"]').find(
      (el) => (el.querySelector('.popover__item-label')?.textContent || '').trim() === 'Local');
    item?.click();
  })();
  await sleep(1600);
  const modeCalls = report.calls.filter((c) => c.name === 'set_provider_mode');
  ok('choosing LOCAL calls the real backend setter',
     modeCalls.length === callsBefore + 1 && modeCalls[modeCalls.length - 1].args[0] === 'LOCAL',
     JSON.stringify(modeCalls.map((c) => c.args[0])));
  const labelAfter = q('.status-pill__text')?.textContent?.trim();
  ok('the pill now names the REAL local model', labelAfter === 'Qwen3 · LOCAL', labelAfter);

  /* ---- 6. Settings -> IA agrees with the pill --------------------------- */
  pill.click(); await sleep(300);
  byText('.popover__item--action', 'Abrir definições')?.click();
  await sleep(1400);
  ok('the pill deep-links into Settings', !!q('.settings-layout'));
  const openCategory = q('.settings-rail__item[aria-current="page"]')?.textContent || '';
  ok('it lands on the IA category', openCategory.includes('IA'), openCategory.trim());
  const segActive = qa('.settings-layout .segmented__option')
    .find((el) => el.getAttribute('aria-pressed') === 'true');
  ok('Settings shows the same mode the pill does',
     !!segActive && /local/i.test(segActive.textContent || ''), segActive ? segActive.textContent.trim() : 'none');

  /* ---- 7. the seven settings categories are all real -------------------- */
  const railItems = qa('.settings-rail__item').map((el) => el.querySelector('.settings-rail__label')?.textContent?.trim());
  ok('Settings has the seven categories',
     railItems.length === 7, railItems.join(' / '));
  for (const name of ['Geral', 'IA', 'Voz', 'PC Control', 'Memória', 'Privacidade', 'Sobre']) {
    const item = railItem(name);
    if (!item) { ok('category ' + name + ' present', false); continue; }
    item.click(); await sleep(500);
    const body = q('.settings-body');
    const len = (body?.textContent || '').trim().length;
    ok('category ' + name + ' renders real content', len > 120, len + ' chars');
  }

  /* ---- 8. Sobre shows one version, from the canonical source ------------ */
  railItem('Sobre')?.click();
  await sleep(500);
  const aboutText = q('.settings-body')?.textContent || '';
  report.aboutText = aboutText.slice(0, 700);
  ok('About shows the product version', aboutText.includes('1.0.0'), aboutText.slice(0, 160));
  ok('About does not show the legacy 8.1.0', !aboutText.includes('8.1.0'), '');

  /* ---- 9. PC Control shows guarantees, not switches --------------------- */
  railItem('PC Control')?.click();
  await sleep(600);
  const pcBody = q('.settings-body');
  ok('PC Control lists the security guarantees', qa('.guarantee').length >= 4,
     qa('.guarantee').length + ' guarantees');
  ok('no guarantee is a switch', qa('.guarantee input[type=checkbox]').length === 0);
  ok('the shell guarantee is stated',
     /sem shell/i.test(pcBody?.textContent || ''), '');

  /* ---- 9b. PC -> Atividade is real, filterable, and distinct from Tarefas.

     The information-architecture bug this checks for: Atividade used to offer
     a "Tarefas" filter tab that duplicated what the separate Tarefas subview
     already shows in detail. It now reads PC-scoped action history through
     get_pc_activity, with categories the audit trail actually distinguishes
     (Tudo / Ações / Permissões / Erros) and no fourth tab standing in for
     something the data cannot tell apart. */
  byText('.topnav-item', 'PC')?.click();
  await sleep(600);
  const openSubtab = (label) => qa('.subtab').find((el) => el.textContent.trim().startsWith(label))?.click();
  openSubtab('Atividade');
  await sleep(700);

  const activityTabs = qa('.page__inner .tab').map((el) => el.textContent.trim());
  ok('Atividade offers exactly the real categories: Tudo / Ações / Permissões / Erros',
     activityTabs.join(',') === 'Tudo,Ações,Permissões,Erros', activityTabs.join(' / '));
  ok('Atividade does NOT offer a Tarefas filter (that would duplicate the Tarefas subview)',
     !activityTabs.includes('Tarefas'), activityTabs.join(' / '));
  ok('Atividade renders real rows from the stubbed history',
     qa('.row-item').length === 3, qa('.row-item').length + ' rows');

  const erroTab = qa('.tab').find((el) => el.textContent.trim() === 'Erros');
  erroTab?.click();
  await sleep(400);
  ok('filtering to a category with no matching rows shows the FILTERED-empty state',
     !!byText('.page__inner', 'Sem eventos nesta categoria'));
  ok('the filtered-empty state is distinct from the true-empty state',
     !byText('.page__inner', 'Ainda não há atividade recente'));

  const tudoTab = qa('.tab').find((el) => el.textContent.trim() === 'Tudo');
  tudoTab?.click();
  await sleep(400);
  ok('switching back to Tudo restores the full list', qa('.row-item').length === 3);

  openSubtab('Tarefas');
  await sleep(600);
  const taskTabLabels = qa('.page__inner .tab').map((el) => el.textContent.trim());
  ok('Tarefas is a genuinely separate page with its own lifecycle vocabulary',
     taskTabLabels.some((l) => l.startsWith('Ativas')) && !taskTabLabels.includes('Ações'),
     taskTabLabels.join(' / '));

  /* ---- 10. Ferramentas is a real catalogue ------------------------------ */
  byText('.topnav-item', 'Ferramentas')?.click();
  await sleep(900);
  ok('the catalogue renders capability rows', qa('.cap-item').length >= 2,
     qa('.cap-item').length + ' rows');
  ok('a confirmation-gated capability is labelled',
     !!byText('.cap-item', 'Pede confirmação'));
  ok('an unavailable capability is shown as such',
     !!byText('.cap-item', 'Indisponível'));
  const catalogueText = q('.page__inner')?.textContent || '';
  ok('no raw schema is exposed',
     !/"type":\s*"object"|input_schema|properties/.test(catalogueText), '');

  /* ---- 11. keyboard reachability --------------------------------------- */
  const focusables = qa('.topnav-item, .status-pill--menu, .settings-rail__item')
    .filter((el) => el.tabIndex >= 0 || el.tagName === 'BUTTON');
  ok('navigation and the pill are keyboard reachable',
     focusables.length >= 6, focusables.length + ' focusable controls');

  return report;
})();
  `);

  /*
   * SECOND PASS: the AI selector, at every required desktop size.
   *
   * The main run above proves the selector works at one size. The brief calls
   * for responsive validation specifically for this fix -- "fully visible, no
   * clipping... at all required resolutions" -- and the positioning logic is
   * viewport-aware BY CONSTRUCTION (see popoverPosition in ui.tsx), so this is
   * the check that actually exercises that rather than just reading the code
   * and trusting the math. Each size gets a fresh page load: reusing the
   * mounted app across a resize would test React's resize handling as much as
   * the popover's, and the two are meant to be independent claims.
   */
  const REQUIRED_VIEWPORTS = [
    { name: '1920x1080', width: 1920, height: 1080 },
    { name: '1600x900', width: 1600, height: 900 },
    { name: '1366x768', width: 1366, height: 768 },
    { name: '1280x720', width: 1280, height: 720 },
    { name: '940x620', width: 940, height: 620 },
  ];
  const viewportSteps = [];
  for (const viewport of REQUIRED_VIEWPORTS) {
    win.setSize(viewport.width, viewport.height);
    await win.loadURL(`http://127.0.0.1:${port}/`);
    await new Promise((r) => setTimeout(r, 900));
    let outcome;
    try {
      outcome = await win.webContents.executeJavaScript(String.raw`
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  window.eel = new Proxy({}, { get: (_t, name) => (...args) => (cb) => {
    const data = {
      get_providers: { mode: 'AUTO', modes: ['AUTO', 'CLOUD', 'LOCAL'],
        groq: { id: 'groq', name: 'Groq', kind: 'cloud', role: 'primary', state: 'READY',
                model: 'openai/gpt-oss-20b', models: [], secret: { configured: true, masked: '', source: 'encrypted_store', encrypted: true } },
        ollama: { id: 'ollama', name: 'Ollama', kind: 'local', role: 'fallback', state: 'READY',
                  model: 'qwen3:8b', models: ['qwen3:8b'], secret: { configured: true, masked: '', source: 'none', encrypted: false } },
        route: { provider: 'groq', model: 'openai/gpt-oss-20b', usable: true, fallback: false, mode: 'AUTO', reason: 'Groq disponível.' } },
    };
    setTimeout(() => cb(data[name] ?? null), 0);
  } });
  window.dispatchEvent(new Event('resize'));
  await sleep(1000);
  const pill = document.querySelector('.status-pill--menu');
  if (!pill) return { ok: false, reason: 'pill not found' };
  pill.click();
  await sleep(350);
  const panel = document.querySelector('.popover[role="menu"]');
  if (!panel) return { ok: false, reason: 'popover did not open' };
  const rect = panel.getBoundingClientRect();
  const withinViewport =
    rect.left >= 0 && rect.top >= 0 &&
    rect.right <= window.innerWidth + 1 && rect.bottom <= window.innerHeight + 1;
  const navLabels = Array.from(document.querySelectorAll('.topnav-item')).map((el) => el.textContent.trim());
  return {
    ok: withinViewport && rect.width > 100 && rect.height > 50 && navLabels.length === 5,
    withinViewport, width: rect.width, height: rect.height,
    viewport: { w: window.innerWidth, h: window.innerHeight },
    navCount: navLabels.length,
  };
})();
      `);
    } catch (err) {
      outcome = { ok: false, reason: String(err && err.message || err) };
    }
    viewportSteps.push({
      label: `AI selector fits inside the viewport at ${viewport.name}`,
      pass: !!(outcome && outcome.ok),
      detail: JSON.stringify(outcome),
    });
  }
  result.steps.push(...viewportSteps);

  const failed = result.steps.filter((s) => !s.pass);
  for (const step of result.steps) {
    console.error('  [' + (step.pass ? 'PASS' : 'FAIL') + '] ' + step.label +
      (step.detail ? ' - ' + step.detail : ''));
  }
  console.error('FAILURES: ' + (failed.length ? failed.map((f) => f.label).join('; ') : 'none'));

  // stdout carries ONLY the JSON, so pytest can parse it.
  process.stdout.write(JSON.stringify({
    ok: failed.length === 0,
    steps: result.steps,
    calledFunctions: [...new Set(result.calls.map((c) => c.name))].sort(),
    modeCalls: result.calls.filter((c) => c.name === 'set_provider_mode').map((c) => c.args[0]),
  }, null, 2));

  server.close();
  app.exit(failed.length ? 1 : 0);
}).catch((err) => { console.error(err); app.exit(1); });
