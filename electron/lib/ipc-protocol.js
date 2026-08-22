/**
 * The Electron half of Nano's control-channel wire format.
 *
 * The authoritative description of this protocol -- and of why it is a pipe to
 * our own child process rather than an authenticated localhost port -- lives in
 * core/desktop_bridge.py. This file must stay byte-compatible with it; a test
 * asserts the two agree on the tag.
 *
 * Deliberately dependency-free and Electron-free so it can be unit-tested in
 * plain Node.
 */
'use strict';

/** Marks a line as protocol. Anything else on the stream is log output. */
const PROTOCOL_TAG = '@@NANO_IPC@@';

/** Refuse absurd lines rather than buffering without bound. */
const MAX_LINE_BYTES = 64 * 1024;

function encode(message) {
  return PROTOCOL_TAG + JSON.stringify(message) + '\n';
}

/**
 * Parse one line. Returns null for anything that is not a well-formed
 * protocol message -- ordinary stdout, a partial line, malformed JSON.
 */
function decode(line) {
  if (typeof line !== 'string') return null;
  const index = line.indexOf(PROTOCOL_TAG);
  if (index < 0) return null;
  try {
    const parsed = JSON.parse(line.slice(index + PROTOCOL_TAG.length).trim());
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch (_) {
    return null;
  }
}

/**
 * Splits a byte stream into lines and separates protocol messages from log
 * output, so the caller never has to think about chunk boundaries.
 *
 * `onMessage` receives decoded protocol objects; `onLog` receives every other
 * line verbatim, which is what makes the Python backend's own reporting show
 * up in the desktop log.
 */
class LineReader {
  constructor({ onMessage, onLog } = {}) {
    this._buffer = '';
    this._onMessage = onMessage || (() => {});
    this._onLog = onLog || (() => {});
  }

  push(chunk) {
    this._buffer += chunk.toString();
    // A line that never terminates must not grow forever.
    if (this._buffer.length > MAX_LINE_BYTES * 4) {
      this._buffer = this._buffer.slice(-MAX_LINE_BYTES);
    }
    let newline;
    while ((newline = this._buffer.indexOf('\n')) >= 0) {
      const line = this._buffer.slice(0, newline).replace(/\r$/, '');
      this._buffer = this._buffer.slice(newline + 1);
      if (!line) continue;
      const message = decode(line);
      if (message) this._onMessage(message);
      else this._onLog(line);
    }
  }
}

module.exports = { MAX_LINE_BYTES, PROTOCOL_TAG, LineReader, decode, encode };
