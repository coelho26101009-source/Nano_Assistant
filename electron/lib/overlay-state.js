/**
 * Turns real backend voice events into what the overlay shows.
 *
 * THE RULE THIS FILE ENFORCES: the overlay never runs a timeline of its own.
 * Every state below is produced by an event the Python voice runtime actually
 * emitted -- `voice_phase` while a turn progresses, `voice_turn_ended` when it
 * finishes, and the busy answer the backend gives when a second hotkey press
 * arrives mid-turn. There is no "probably transcribing by now" and no animated
 * guess: if the backend goes quiet, the overlay stays on the last real phase.
 *
 * Kept pure and Electron-free so the mapping is unit-testable without a window.
 */
'use strict';

/** Hidden. Not a phase -- the absence of one. */
const HIDDEN = Object.freeze({ visible: false, state: 'idle', label: '', hideAfterMs: 0 });

/**
 * Phases emitted by VoiceRuntime.run_voice_turn, in the order they occur.
 * WAKE_LISTENING and IDLE are the two ways a turn returns to rest.
 */
const PHASE_VIEW = Object.freeze({
  WAKE_DETECTED:     { state: 'activated',    label: 'A ouvir…' },
  COMMAND_LISTENING: { state: 'listening',    label: 'A ouvir…' },
  TRANSCRIBING:      { state: 'transcribing', label: 'A transcrever…' },
  PROCESSING:        { state: 'processing',   label: 'A processar…' },
  SPEAKING:          { state: 'speaking',     label: 'A falar…' },
  IDLE:              null,
  WAKE_LISTENING:    null,
});

/**
 * Short, human error text. Never a traceback, never a provider message, never
 * a model name: the overlay is a status light, not a debugging surface. The
 * full detail is in nano.log where it belongs.
 */
const ERROR_LABEL = Object.freeze({
  voice_disabled:        'A voz está desligada',
  microphone_busy:       'Microfone ocupado',
  microphone_failed:     'Sem acesso ao microfone',
  no_speech:             'Não ouvi nada',
  // Distinct from no_speech on purpose: the microphone returned nothing at
  // all, which is a device problem, not a quiet room.
  no_audio:              'Sem áudio do microfone',
  no_usable_command:     'Não percebi',
  voice_turn_in_progress:'O Nano já está ocupado',
});

const GENERIC_ERROR = 'Não consegui responder';

/** How long a terminal state lingers before the overlay hides itself. */
const HIDE_DELAY = Object.freeze({ ok: 700, quiet: 1400, error: 2200, busy: 1600 });

/** The view for one `voice_phase` event, or HIDDEN when the turn is at rest. */
function fromPhase(phase) {
  const view = PHASE_VIEW[phase];
  if (view === undefined) return null;   // unknown phase: change nothing
  if (view === null) return HIDDEN;      // IDLE / WAKE_LISTENING: at rest
  return { visible: true, hideAfterMs: 0, ...view };
}

/** The view for a `voice_turn_ended` event. */
function fromTurnEnd(payload) {
  const data = payload || {};
  if (data.ok) {
    // The phase stream already showed SPEAKING; fade out rather than flash a
    // redundant "done" state.
    return { visible: true, state: 'done', label: 'Pronto', hideAfterMs: HIDE_DELAY.ok };
  }
  const code = data.error ? String(data.error) : '';
  if (data.cancelled) {
    return {
      visible: true, state: 'quiet',
      label: ERROR_LABEL[code] || 'Sem comando',
      hideAfterMs: HIDE_DELAY.quiet,
    };
  }
  return {
    visible: true, state: 'error',
    label: ERROR_LABEL[code] || GENERIC_ERROR,
    hideAfterMs: HIDE_DELAY.error,
  };
}

/**
 * The view for a refused activation: the backend answered "a turn is already
 * running". This is the honest busy state Part 8 requires -- no second
 * microphone reader was opened, and the user is told so.
 */
function busyView(payload) {
  const active = payload && payload.active_source ? String(payload.active_source) : '';
  return {
    visible: true,
    state: 'busy',
    label: 'O Nano já está ocupado',
    detail: active ? `turno de ${active}` : '',
    hideAfterMs: HIDE_DELAY.busy,
  };
}

/** The view for a failure to even reach the backend. */
function unavailableView(reason) {
  return {
    visible: true, state: 'error',
    label: reason === 'not_ready' ? 'O Nano ainda está a arrancar' : GENERIC_ERROR,
    hideAfterMs: HIDE_DELAY.error,
  };
}

module.exports = {
  ERROR_LABEL, GENERIC_ERROR, HIDDEN, HIDE_DELAY, PHASE_VIEW,
  busyView, fromPhase, fromTurnEnd, unavailableView,
};
