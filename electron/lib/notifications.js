/**
 * Windows notifications for things the backend says are due.
 *
 * The backend decides WHEN something is due (the reminders scheduler) and
 * sends a `reminder_due` event over the control pipe. This module only turns
 * that event into the title and body of a native notification. It is a pure
 * function so it can be tested without Electron.
 *
 * The payload is data from the backend, and part of it (the reminder text) was
 * typed by the user or written by the model. It is therefore treated as text:
 * control characters are stripped and the length is bounded, so a hostile or
 * malformed reminder cannot produce a giant or garbled toast.
 */
'use strict';

const MAX_TITLE = 64;
const MAX_BODY = 240;

function cleanText(value, max) {
  if (typeof value !== 'string') return '';
  // Drop control characters (keep plain spaces), collapse whitespace.
  const flat = value.replace(/[\u0000-\u001f\u007f-\u009f]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (flat.length <= max) return flat;
  return flat.slice(0, max - 1).trimEnd() + '…';
}

/**
 * The notification to show for a backend event, or null to show nothing.
 *
 * Only events the shell knows about produce a notification; anything else is
 * ignored, so a future backend event cannot make a toast appear by accident.
 */
function fromBackendEvent(event, payload) {
  if (event !== 'reminder_due') return null;
  const body = cleanText(payload && payload.text, MAX_BODY);
  if (!body) return null;
  const when = cleanText(payload && payload.due_label, 24);
  return {
    title: cleanText(when ? `Lembrete · ${when}` : 'Lembrete', MAX_TITLE),
    body,
    silent: false,
    reminderId: Number.isInteger(payload && payload.id) ? payload.id : null,
  };
}

module.exports = { fromBackendEvent, cleanText, MAX_TITLE, MAX_BODY };
