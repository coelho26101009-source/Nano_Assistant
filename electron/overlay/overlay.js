/**
 * The island renderer: draw the view it is given, and nothing else.
 *
 * No timer here advances a state. Every view arrives from the main process,
 * which builds it from a real backend event (lib/island.js). The only clocks
 * are facts, not guesses: the elapsed time of a listening turn, and a playing
 * song's position moved on from the moment Windows reported it.
 *
 * What the island can say back (see preload.js): "the pointer is over me" (so
 * the window stops being click-through just there), "I was clicked" (opens
 * NANO) and, on the music player only, previous / play-pause / next.
 */
'use strict';

(function () {
  const $ = (id) => document.getElementById(id);
  const island = $('island');
  const api = window.nanoOverlay || {};

  let tickTimer = null;
  let current = null;
  let pointerInside = false;

  function setText(node, value) {
    const text = typeof value === 'string' ? value : '';
    if (node.textContent === text) return;
    node.textContent = text;
    // Re-run the soft entrance for new text.
    node.classList.remove('swap');
    void node.offsetWidth;
    if (text) node.classList.add('swap');
  }

  function setImage(node, src) {
    const value = typeof src === 'string' && src.startsWith('data:image/') ? src : '';
    if (value) { if (node.getAttribute('src') !== value) node.setAttribute('src', value); }
    else node.removeAttribute('src');
    return Boolean(value);
  }

  function mmss(totalSeconds) {
    const seconds = Math.max(0, Math.floor(totalSeconds));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const rest = String(seconds % 60).padStart(2, '0');
    return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${rest}` : `${minutes}:${rest}`;
  }

  /** Once a second: the listening clock and the song's progress. */
  function tick() {
    const view = current;
    if (!view) return;
    $('clock').textContent = view.since > 0 ? mmss((Date.now() - view.since) / 1000) : '';
    const media = view.media;
    if (media && media.duration > 0) {
      let position = media.position;
      if (media.playing && media.positionAt > 0) position += (Date.now() - media.positionAt) / 1000;
      position = Math.min(media.duration, Math.max(0, position));
      $('p-pos').textContent = mmss(position);
      $('p-left').textContent = '-' + mmss(media.duration - position);
      $('p-fill').style.width = `${(100 * position) / media.duration}%`;
    } else {
      $('p-pos').textContent = '';
      $('p-left').textContent = '';
      $('p-fill').style.width = '0%';
    }
  }

  function startTicking(needed) {
    clearInterval(tickTimer);
    tickTimer = null;
    tick();
    if (needed) tickTimer = setInterval(tick, 1000);
  }

  function setPointer(inside) {
    if (inside === pointerInside) return;
    pointerInside = inside;
    island.dataset.hover = inside ? 'true' : 'false';
    if (typeof api.setPointer === 'function') api.setPointer(inside);
    if (current) layout(current);
  }

  /** Shape and layout: the view's size, except that music opens on hover. */
  function layout(view) {
    let size = typeof view.size === 'string' ? view.size : 'compact';
    if (view.state === 'media' && (view.expanded || pointerInside)) size = 'player';
    island.dataset.size = size;
    island.dataset.layout = size === 'idle' ? 'idle'
      : size === 'card' ? 'card'
        : size === 'player' ? 'player' : 'pill';
  }

  function render(view) {
    if (!view || typeof view !== 'object') return;
    const visible = view.visible !== false;
    island.dataset.visible = visible ? 'true' : 'false';
    if (!visible) {
      current = null;
      startTicking(false);
      setPointer(false);
      return;
    }
    current = view;
    const pick = (value, fallback) => (typeof value === 'string' && value ? value : fallback);
    island.dataset.state = pick(view.state, 'idle');
    island.dataset.tone = pick(view.tone, 'neutral');
    island.dataset.icon = pick(view.icon, 'mark');
    island.dataset.wake = view.wake ? 'true' : 'false';
    if (view.lang) document.documentElement.lang = view.lang === 'en' ? 'en' : 'pt';
    layout(view);

    const media = view.media && typeof view.media === 'object' ? view.media : null;
    island.dataset.playing = media && media.playing ? 'true' : 'false';
    const hasArt = media ? setImage($('pill-art'), media.art) && setImage($('p-art'), media.art) : false;
    if (!media) { setImage($('pill-art'), ''); setImage($('p-art'), ''); }
    island.dataset.hasArt = hasArt ? 'true' : 'false';

    // Idle
    const idleHint = $('idle-hint');
    idleHint.textContent = '';
    if (view.size === 'idle' && view.hint) {
      // Listening for its name: 'Diz "Ei Nano" ou' before the shortcut.
      idleHint.append(document.createTextNode((view.wake ? view.wake : view.hint) + ' '));
      for (const key of ['Ctrl', 'Shift', view.lang === 'en' ? 'Space' : 'Espaço']) {
        const kbd = document.createElement('kbd');
        kbd.textContent = key;
        idleHint.append(kbd);
      }
    }
    // Card
    setText($('card-label'), view.size === 'card' ? view.label : '');
    setText($('card-detail'), view.size === 'card' ? view.detail : '');
    setText($('card-hint'), view.size === 'card' ? view.hint : '');
    // Pill: a song shows its title there, everything else its label.
    setText($('pill-label'), media ? media.title : view.label);
    setText($('pill-detail'), media ? '' : view.detail);
    // Volume
    const level = Number.isFinite(view.level) ? view.level : 0;
    $('level-fill').style.width = `${level}%`;
    $('level-num').textContent = view.state === 'volume' ? String(level) : '';
    // Player
    setText($('p-title'), media ? media.title : '');
    setText($('p-artist'), media ? (media.artist || media.app) : '');
    $('p-prev').disabled = !(media && media.canPrevious);
    $('p-next').disabled = !(media && media.canNext);
    $('p-toggle').disabled = !(media && media.canToggle);
    const say = view.lang === 'en'
      ? { previous: 'Previous', play_pause: media && media.playing ? 'Pause' : 'Play', next: 'Next' }
      : { previous: 'Anterior', play_pause: media && media.playing ? 'Pausa' : 'Reproduzir', next: 'Seguinte' };
    for (const id of ['p-prev', 'p-toggle', 'p-next']) {
      const button = $(id);
      button.setAttribute('aria-label', say[button.dataset.action]);
      button.title = say[button.dataset.action];
    }

    startTicking(view.since > 0 || Boolean(media && media.playing));
  }

  island.addEventListener('mouseenter', () => setPointer(true));
  island.addEventListener('mouseleave', () => setPointer(false));
  island.addEventListener('click', (event) => {
    if (island.dataset.visible !== 'true') return;
    const button = event.target instanceof Element ? event.target.closest('button[data-action]') : null;
    if (button) {
      // A player button: previous, play/pause or next. Never opens NANO.
      if (!button.disabled && typeof api.media === 'function') api.media(button.dataset.action);
      return;
    }
    if (typeof api.open === 'function') api.open();
  });

  if (typeof api.onState === 'function') api.onState(render);
})();
