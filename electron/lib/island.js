/**
 * The NANO island: what the floating pill at the top of the screen shows.
 *
 * The island replaced the old voice capsule. It is a black pill, in the spirit
 * of a phone's "dynamic island", that sits at the top centre of the screen and
 * changes shape for what NANO is doing: a small pill at rest, a wider one while
 * it listens, thinks or speaks, and a card for things that need a moment of
 * attention (an approval, a reminder, a PC alert, a finished download).
 *
 * THE RULES, carried over from the voice capsule (lib/overlay-state.js):
 *   - every view comes from a real backend event; nothing here runs a timeline
 *     or guesses that something is "probably done by now";
 *   - the island NEVER decides anything. An approval shows that NANO is
 *     waiting and where to answer (NANO itself); there is no Yes on the island;
 *   - text from the backend is data: control characters are stripped and the
 *     length is bounded before it can reach the window;
 *   - no internal code, traceback or provider message is ever shown.
 *
 * This module is pure (no Electron), so all of it is unit-tested.
 */
'use strict';

const { cleanText } = require('./notifications');

/* ── Layers ──────────────────────────────────────────────────────────────── */

/**
 * Several things can be true at once (NANO is thinking AND a download just
 * finished). The island shows the most important one; the others wait their
 * turn underneath and come back when it goes away.
 */
const LAYERS = Object.freeze(['approval', 'voice', 'hud', 'notice', 'chat', 'media']);

/** How long each kind of moment stays before the island shrinks back. */
const TTL = Object.freeze({
  reminder: 9000,
  notice: 8000,
  alert: 7000,
  download: 6000,
  chatDone: 1300,
  volume: 1800,
  track: 5000,
  // Play / pause pressed (on the keyboard, in Spotify, or by NANO): the
  // hidden island shows it for a moment and goes away again.
  mediaBlip: 2600,
  // A paused song stays a little while (press play again), then the island
  // goes back to rest.
  paused: 30000,
  chatError: 2600,
  // Safety nets only: the backend always sends the end of a turn and the
  // resolution of an approval. If it ever did not, the island must not stay
  // stuck on "thinking" forever.
  chatWorking: 120000,
  approval: 75000,
});

/** A bounded string for one line on the island. */
function line(value, max) {
  return cleanText(typeof value === 'string' ? value : '', max);
}

/* ── Views ───────────────────────────────────────────────────────────────── */
/*
 * A view is plain data. `size` is the island's shape:
 *   idle     the small resting pill
 *   compact  a wider pill: one line and a live indicator
 *   wide     two lines (a tool, a choice, a short error)
 *   card     a card with an icon, a title and up to two lines of text
 * `tone` colours the indicator only; the island itself is always black.
 * Labels are written in Portuguese, like every NANO source string, and
 * translated by translate() just before they are sent to the window.
 */

const IDLE = Object.freeze({
  visible: true, layer: 'idle', state: 'idle', size: 'idle', tone: 'neutral',
  icon: 'mark', label: 'NANO', detail: '', hint: 'Para falar',
});

/** The views for a chat turn (typed on the PC or sent from the phone). */
function fromChatActivity(payload) {
  const data = payload && typeof payload === 'object' ? payload : {};
  switch (data.phase) {
    case 'thinking':
      return { view: { state: 'thinking', size: 'compact', tone: 'info', icon: 'mark',
        label: 'A pensar…', detail: '' }, ttl: TTL.chatWorking };
    case 'working': {
      const tools = Array.isArray(data.tools) ? data.tools.filter((n) => typeof n === 'string' && n.trim()) : [];
      const friendly = tools.length ? toolLabel(tools[0]) : null;
      const label = friendly || 'A executar…';
      const detail = tools.length > 1
        ? `+${tools.length - 1}` // more tools in the same step
        : (friendly ? '' : line(humaniseTool(tools[0] || ''), 48));
      return { view: { state: 'working', size: 'wide', tone: 'info', icon: 'gear',
        label, detail }, ttl: TTL.chatWorking };
    }
    case 'done':
      return { view: { state: 'done', size: 'compact', tone: 'success', icon: 'check',
        label: 'Pronto', detail: '' }, ttl: TTL.chatDone };
    case 'error':
      return { view: { state: 'error', size: 'compact', tone: 'danger', icon: 'alert',
        label: 'Não consegui responder', detail: '' }, ttl: TTL.chatError };
    default:
      return null;
  }
}

/**
 * What a tool step is called on the island. The chat window shows the raw
 * tool name in its tool card; the island has one line, so the common tools get
 * a sentence and the rest a readable name ("web navigate extract").
 */
const TOOL_LABELS = Object.freeze({
  web_search: 'A pesquisar na web…',
  deep_research: 'A investigar a fundo…',
  web_navigate_extract: 'A ler uma página…',
  web_extract_prices: 'A comparar preços…',
  web_interact: 'A usar uma página…',
  web_screenshot: 'A capturar uma página…',
  set_reminder: 'A criar um lembrete…',
  list_reminders: 'A ver os lembretes…',
  cancel_reminder: 'A cancelar um lembrete…',
  calendar_add_event: 'A marcar na agenda…',
  calendar_list_events: 'A ver a agenda…',
  calendar_delete_event: 'A mudar a agenda…',
  remember_fact: 'A guardar na memória…',
  list_facts: 'A ver a memória…',
  forget_fact: 'A esquecer…',
  memory_search_history: 'A procurar na memória…',
  rag_search: 'A procurar nos documentos…',
  system_stats: 'A ver o estado do PC…',
  organize_downloads: 'A arrumar as transferências…',
  task_plan: 'A executar um plano…',
});

function toolLabel(name) {
  return Object.prototype.hasOwnProperty.call(TOOL_LABELS, name) ? TOOL_LABELS[name] : null;
}

function humaniseTool(name) {
  return String(name || '').replace(/[._]+/g, ' ').replace(/\s+/g, ' ').trim();
}

/**
 * An approval NANO is waiting for. The island says that, and where to answer.
 * It carries the action and its target so the person knows WHAT before they
 * open NANO, but no arguments and no button: deciding happens in NANO only.
 */
function fromApproval(payload, pendingCount = 1) {
  const data = payload && typeof payload === 'object' ? payload : {};
  const action = line(data.action, 60);
  const target = line(data.target, 60);
  return {
    state: 'approval', size: 'card', tone: 'warning', icon: 'shield',
    label: pendingCount > 1 ? 'O NANO precisa de {0} aprovações' : 'O NANO precisa da tua aprovação',
    labelValues: pendingCount > 1 ? [String(pendingCount)] : undefined,
    detail: [action, target].filter(Boolean).join(' — '),
    hint: 'Clica para decidir no NANO',
  };
}

/** A due reminder. */
function fromReminder(payload) {
  const data = payload && typeof payload === 'object' ? payload : {};
  const text = line(data.text, 140);
  if (!text) return null;
  const when = line(data.due_label, 16);
  return {
    view: { state: 'reminder', size: 'card', tone: 'accent', icon: 'bell',
      label: 'Lembrete', labelSuffix: when, detail: text },
    ttl: TTL.reminder,
  };
}

/** Icon and tone per PC alert kind (core/pc_alerts.py). */
const NOTICE_KIND = Object.freeze({
  battery: { icon: 'battery', tone: 'danger', state: 'alert', ttl: TTL.alert },
  disk: { icon: 'disk', tone: 'warning', state: 'alert', ttl: TTL.alert },
  network: { icon: 'wifi', tone: 'info', state: 'alert', ttl: TTL.alert },
  download: { icon: 'download', tone: 'success', state: 'download', ttl: TTL.download },
});

/**
 * NANO speaking first: a PC alert (battery, disk, network, a finished
 * download) or a briefing / heads-up (core/proactive.py). Title and text were
 * written by the backend in the interface language.
 */
function fromNotice(payload) {
  const data = payload && typeof payload === 'object' ? payload : {};
  const text = line(data.text, 160);
  const title = line(data.title, 48);
  if (!text && !title) return null;
  const kind = Object.prototype.hasOwnProperty.call(NOTICE_KIND, data.kind) ? NOTICE_KIND[data.kind] : null;
  // "Sem rede" is a problem; "A ligação à rede voltou" is good news.
  const tone = kind && data.kind === 'network' && /voltou|back/i.test(text) ? 'success' : (kind ? kind.tone : 'accent');
  return {
    view: {
      state: kind ? kind.state : 'notice', size: 'card', tone,
      icon: kind ? kind.icon : 'spark',
      label: title || 'NANO', labelIsData: true, detail: text,
    },
    ttl: kind ? kind.ttl : TTL.notice,
  };
}

/* ── Music and volume ───────────────────────────────────────────────────── */

/** Bound and check one now-playing payload from core/now_playing.py. */
function mediaData(payload) {
  const data = payload && typeof payload === 'object' ? payload : {};
  const title = line(data.title, 90);
  if (!title || (data.status !== 'playing' && data.status !== 'paused')) return null;
  const number = (value) => (Number.isFinite(value) && value >= 0 ? value : 0);
  const art = typeof data.art === 'string' && /^data:image\/(png|jpeg|jpg|webp|bmp);base64,[A-Za-z0-9+/=]+$/.test(data.art)
    && data.art.length <= 400000 ? data.art : '';
  return {
    title,
    artist: line(data.artist, 80),
    app: line(data.app, 40),
    playing: data.status === 'playing',
    position: number(data.position),
    duration: number(data.duration),
    // When `position` was true, as this PC's clock (ms). The window moves the
    // bar on from there while the song plays -- the same thing Windows' own
    // media flyout does -- and every pause, seek or new song resends it.
    positionAt: number(data.position_at),
    canPrevious: data.can_previous !== false,
    canNext: data.can_next !== false,
    canToggle: data.can_toggle !== false,
    art,
    key: line(data.key, 200),
  };
}

/**
 * The song playing on this PC (Spotify, a browser, any app Windows lists in
 * its media controls). Resting: a small pill with the cover and moving bars.
 * Hovered, or for a few seconds when the song changes: the player card.
 */
function fromMedia(payload) {
  const media = mediaData(payload);
  if (!media) return null;
  return {
    view: {
      state: 'media', size: 'compact', tone: 'success', icon: 'music',
      label: media.playing ? 'A tocar' : 'Em pausa', detail: '', media,
    },
    ttl: media.playing ? 0 : TTL.paused,
  };
}

/** A new song: the player card opens for a moment, then folds back. */
function trackCard(payload) {
  const media = mediaData(payload);
  if (!media || !media.playing) return null;
  return {
    view: { state: 'media', size: 'card', tone: 'success', icon: 'music',
      label: 'A tocar', detail: '', media, expanded: true },
    ttl: TTL.track,
  };
}

/**
 * The song was paused or played again. With the island hidden at rest, the
 * small music pill shows for a moment so the person sees it took, then goes.
 */
function mediaBlip(payload) {
  const next = fromMedia(payload);
  if (!next) return null;
  return { view: next.view, ttl: TTL.mediaBlip };
}

/* ── Calling the island with the mouse ───────────────────────────────────── */

/**
 * Where the pointer calls the hidden island: the top edge of a screen, around
 * its centre, where the island lives. Only the very top rows count, so moving
 * across the screen or reaching for a tab does not call it; the pointer has to
 * be pushed up against the edge (see PEEK in main.js for the short wait).
 */
const PEEK_ZONE = Object.freeze({ halfWidth: 260, depth: 3 });

function inPeekZone(cursor, bounds, zone = PEEK_ZONE) {
  if (!cursor || !bounds) return false;
  const { x, y } = cursor;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
  const centre = bounds.x + bounds.width / 2;
  return y >= bounds.y && y < bounds.y + zone.depth
    && Math.abs(x - centre) <= zone.halfWidth;
}

/** The system volume changed: a level bar, briefly. */
function fromVolume(payload) {
  const data = payload && typeof payload === 'object' ? payload : {};
  if (!Number.isFinite(data.level)) return null;
  const level = Math.max(0, Math.min(100, Math.round(data.level)));
  const muted = data.muted === true;
  return {
    view: { state: 'volume', size: 'compact', tone: muted ? 'neutral' : 'accent',
      icon: muted || level === 0 ? 'mute' : 'speaker',
      label: muted ? 'Sem som' : 'Volume', detail: '', level: muted ? 0 : level },
    ttl: TTL.volume,
  };
}

/** The only media commands the island can send, and to the backend only. */
const MEDIA_ACTIONS = Object.freeze(['play_pause', 'next', 'previous']);

/** Voice views (lib/overlay-state.js) dressed for the island. */
const VOICE_STYLE = Object.freeze({
  activated: { size: 'compact', tone: 'info', icon: 'mic', clock: true },
  listening: { size: 'compact', tone: 'info', icon: 'mic', clock: true },
  transcribing: { size: 'compact', tone: 'info', icon: 'mark' },
  processing: { size: 'compact', tone: 'info', icon: 'mark' },
  speaking: { size: 'compact', tone: 'accent', icon: 'mark' },
  done: { size: 'compact', tone: 'success', icon: 'check' },
  quiet: { size: 'compact', tone: 'neutral', icon: 'mark' },
  busy: { size: 'compact', tone: 'neutral', icon: 'mark' },
  error: { size: 'compact', tone: 'danger', icon: 'alert' },
});

function fromVoiceView(view) {
  if (!view || !view.visible) return null;
  const style = VOICE_STYLE[view.state] || VOICE_STYLE.processing;
  const detail = typeof view.detail === 'string' ? view.detail : '';
  return {
    view: {
      state: view.state, size: detail ? 'wide' : style.size, tone: style.tone, icon: style.icon,
      label: view.label || '', detail, clock: Boolean(style.clock),
      // Only the clarification list is data; the other details are fixed text.
      detailIsData: view.label === 'Qual delas?',
    },
    ttl: view.hideAfterMs > 0 ? view.hideAfterMs : 0,
  };
}

/* ── The stack ───────────────────────────────────────────────────────────── */

class IslandStack {
  constructor({ now = () => Date.now() } = {}) {
    this.now = now;
    this.layers = new Map();      // layer -> { view, expiresAt, since }
    this.approvals = new Map();   // request id -> payload
  }

  /** Put a view on a layer. ttl 0 keeps it until it is cleared. */
  set(layer, view, ttl = 0) {
    if (!LAYERS.includes(layer) || !view) return;
    const previous = this.layers.get(layer);
    const now = this.now();
    // The clock of a listening turn counts from when the person started
    // speaking, not from the last phase change.
    const since = previous && previous.view.clock && view.clock ? previous.since : now;
    this.layers.set(layer, { view, expiresAt: ttl > 0 ? now + ttl : 0, since });
  }

  clear(layer) { this.layers.delete(layer); }

  has(layer) { this.prune(); return this.layers.has(layer); }

  /** The view on a layer, or null. */
  peek(layer) { const entry = this.layers.get(layer); return entry ? entry.view : null; }

  approvalRequested(id, payload) {
    if (typeof id !== 'string' || !id) return;
    this.approvals.set(id, payload || {});
    this.refreshApproval();
  }

  approvalResolved(id) {
    this.approvals.delete(id);
    this.refreshApproval();
  }

  refreshApproval() {
    if (!this.approvals.size) { this.clear('approval'); return; }
    const newest = [...this.approvals.values()].at(-1);
    this.set('approval', fromApproval(newest, this.approvals.size), TTL.approval);
  }

  /** Drop what has expired. Returns true when something went away. */
  prune() {
    const now = this.now();
    let changed = false;
    for (const [layer, entry] of this.layers) {
      if (entry.expiresAt && entry.expiresAt <= now) {
        this.layers.delete(layer);
        if (layer === 'approval') this.approvals.clear();
        changed = true;
      }
    }
    return changed;
  }

  /** The soonest expiry still pending, or 0. */
  nextExpiry() {
    let soonest = 0;
    for (const entry of this.layers.values()) {
      if (entry.expiresAt && (!soonest || entry.expiresAt < soonest)) soonest = entry.expiresAt;
    }
    return soonest;
  }

  /**
   * What the island shows now.
   *
   * `context.mainFocused`: the NANO window is in front of the person. It
   * already shows the chat, the approval card and the toasts, so the island
   * only keeps showing a voice turn (as the voice capsule always did) and
   * otherwise steps aside instead of covering NANO's own title bar.
   * `context.alwaysVisible`: the resting pill stays on screen when nothing is
   * happening. Off (the default since Simão asked for it), the island is
   * HIDDEN at rest and only comes out for a moment that matters: a voice turn,
   * an approval, a reminder or alert, a tool at work, the volume or the song
   * changing. A song that simply keeps playing is not such a moment, so the
   * music pill waits underneath until the island is called.
   * `context.peek`: the person called the island (the pointer at the top of
   * the screen). It shows what is underneath: the song, or the resting pill.
   */
  current(context = {}) {
    this.prune();
    const resting = Boolean(context.alwaysVisible || context.peek);
    for (const layer of LAYERS) {
      if (context.mainFocused && layer !== 'voice') continue;
      if (layer === 'media' && !resting) continue;
      const entry = this.layers.get(layer);
      if (entry) {
        return { visible: true, layer, ...entry.view, since: entry.view.clock ? entry.since : 0 };
      }
    }
    if (resting && !context.mainFocused) return { ...IDLE };
    return null;
  }
}

/* ── Language ────────────────────────────────────────────────────────────── */

/**
 * English for every fixed island string, keyed by the Portuguese source like
 * the rest of NANO. The voice labels come from lib/overlay-state.js.
 * electron/test/island.test.js fails on a fixed string with no entry here.
 */
const EN = Object.freeze({
  'NANO': 'NANO',
  'Para falar': 'To talk',
  'Diz "{0}" ou': 'Say "{0}" or',
  'Clica para decidir no NANO': 'Click to decide in NANO',
  'A pensar…': 'Thinking…',
  'A executar…': 'Working…',
  'Pronto': 'Done',
  'Não consegui responder': "I couldn't answer",
  'O NANO precisa da tua aprovação': 'NANO needs your approval',
  'O NANO precisa de {0} aprovações': 'NANO needs {0} approvals',
  'Lembrete': 'Reminder',
  'A tocar': 'Now playing',
  'Em pausa': 'Paused',
  'Volume': 'Volume',
  'Sem som': 'Muted',
  'A pesquisar na web…': 'Searching the web…',
  'A investigar a fundo…': 'Researching in depth…',
  'A ler uma página…': 'Reading a page…',
  'A comparar preços…': 'Comparing prices…',
  'A usar uma página…': 'Using a page…',
  'A capturar uma página…': 'Capturing a page…',
  'A criar um lembrete…': 'Setting a reminder…',
  'A ver os lembretes…': 'Checking reminders…',
  'A cancelar um lembrete…': 'Cancelling a reminder…',
  'A marcar na agenda…': 'Adding to the calendar…',
  'A ver a agenda…': 'Checking the calendar…',
  'A mudar a agenda…': 'Changing the calendar…',
  'A guardar na memória…': 'Saving to memory…',
  'A ver a memória…': 'Checking memory…',
  'A esquecer…': 'Forgetting…',
  'A procurar na memória…': 'Searching memory…',
  'A procurar nos documentos…': 'Searching documents…',
  'A ver o estado do PC…': 'Checking the PC…',
  'A arrumar as transferências…': 'Tidying downloads…',
  'A executar um plano…': 'Running a plan…',
  // Voice (lib/overlay-state.js)
  'A ouvir…': 'Listening…',
  'A transcrever…': 'Transcribing…',
  'A processar…': 'Thinking…',
  'A falar…': 'Speaking…',
  'Qual delas?': 'Which one?',
  'Sem comando': 'No command',
  'O Nano ainda está a arrancar': 'NANO is still starting',
  'A voz está desligada': 'Voice is off',
  'Microfone ocupado': 'Microphone busy',
  'Sem acesso ao microfone': 'No microphone access',
  'Não ouvi nada': "I didn't hear anything",
  'Sem áudio do microfone': 'No audio from the microphone',
  'Não percebi': "I didn't catch that",
  'O Nano já está ocupado': 'NANO is already busy',
  'turno de {0}': '{0} turn',
});

function fill(text, values) {
  if (!values) return text;
  return text.replace(/\{(\d+)\}/g, (match, index) => (values[index] !== undefined ? values[index] : match));
}

/** Portuguese source -> the interface language. Data passes through as is. */
function say(text, language) {
  if (language !== 'en') return text;
  return Object.prototype.hasOwnProperty.call(EN, text) ? EN[text] : text;
}

/**
 * The view as the window receives it: fixed strings translated, data left as
 * it came, and nothing the renderer does not draw.
 */
function translate(view, language) {
  if (!view) return null;
  const lang = language === 'en' ? 'en' : 'pt';
  let label = view.labelIsData ? view.label : say(view.label || '', lang);
  label = fill(label, view.labelValues);
  if (view.labelSuffix) label = `${label} · ${view.labelSuffix}`;
  let detail = view.detail || '';
  if (!view.detailIsData && view.layer === 'voice' && detail.startsWith('turno de ')) {
    detail = fill(say('turno de {0}', lang), [detail.slice('turno de '.length)]);
  } else if (!view.detailIsData && view.layer !== 'notice' && view.layer !== 'approval') {
    detail = say(detail, lang);
  }
  return {
    visible: true,
    layer: view.layer,
    state: view.state,
    size: view.size,
    tone: view.tone,
    icon: view.icon,
    label: line(label, 80),
    detail: line(detail, 160),
    hint: view.hint ? say(view.hint, lang) : '',
    // Listening for its name: the resting pill says what to say.
    wake: view.wake ? fill(say('Diz "{0}" ou', lang), [line(view.wake, 40)]) : '',
    since: view.since || 0,
    media: view.media || null,
    expanded: view.expanded === true,
    level: Number.isFinite(view.level) ? view.level : null,
    lang,
  };
}

/** The language from a tag such as "pt-PT", "pt-BR" or "en-US". */
function languageOf(tag) {
  return String(tag || '').toLowerCase().startsWith('pt') ? 'pt' : 'en';
}

module.exports = {
  EN, IDLE, LAYERS, MEDIA_ACTIONS, PEEK_ZONE, TOOL_LABELS, TTL,
  IslandStack, fromApproval, fromChatActivity, fromMedia, fromNotice, fromReminder,
  fromVoiceView, fromVolume, humaniseTool, inPeekZone, languageOf, mediaBlip, say, trackCard,
  translate,
};
