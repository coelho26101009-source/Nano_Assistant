/* Nano <-> Python callback bridge.
 *
 * WHY THIS FILE EXISTS AND MUST STAY PLAIN JS
 * -------------------------------------------
 * Eel discovers Python-callable JS functions by TEXT-SCANNING the served files
 * at eel.init() time. It looks for the registration calls below; it does not
 * execute the bundle. Our React code registered callbacks through a helper
 * (see `expose()` in lib/backend.ts), which the Next.js production minifier
 * rewrites into something like `t.expose(e,n)`. The token eel searches for
 * disappears, eel finds zero functions, and every `eel.on_xxx()` call from
 * Python then raises AttributeError. That silently broke streaming chat, wake
 * notifications and permission dialogs.
 *
 * This file is served verbatim from /public (Next.js never minifies it), so
 * the registration calls survive and eel picks them up. Each stub forwards to
 * a handler the React app installs at runtime, so the UI keeps full control of
 * behaviour while registration stays static.
 *
 * TWO RULES WHEN EDITING THIS FILE
 * 1. To add a Python -> UI callback: add a stub here AND attach the handler in
 *    React. Adding it only in React will not work.
 * 2. Never write the registration token in a comment. Eel's parser scans from
 *    the first textual occurrence, so a mention inside a comment makes it
 *    misparse and silently return ZERO functions for the whole file.
 */
(function () {
  "use strict";

  window.__nanoHandlers = window.__nanoHandlers || {};
  // Buffers events that arrive before React has mounted its handlers, so a
  // fast backend cannot lose the first stream chunk of a conversation.
  window.__nanoPending = window.__nanoPending || {};

  function dispatch(name, args) {
    var handler = window.__nanoHandlers[name];
    if (typeof handler === "function") {
      try {
        return handler.apply(null, args);
      } catch (err) {
        console.error("[nano-bridge] handler failed:", name, err);
      }
      return undefined;
    }
    (window.__nanoPending[name] = window.__nanoPending[name] || []).push(args);
    if (window.__nanoPending[name].length > 50) {
      window.__nanoPending[name].shift();
    }
    return undefined;
  }

  if (!window.eel) {
    console.error("[nano-bridge] eel.js did not load; Python cannot reach the UI.");
    return;
  }

  function on_stream_start(msgId, userText) { return dispatch("on_stream_start", [msgId, userText]); }
  eel.expose(on_stream_start, "on_stream_start");

  function on_stream_status(msgId, status) { return dispatch("on_stream_status", [msgId, status]); }
  eel.expose(on_stream_status, "on_stream_status");

  function on_stream_chunk(msgId, chunk) { return dispatch("on_stream_chunk", [msgId, chunk]); }
  eel.expose(on_stream_chunk, "on_stream_chunk");

  function on_stream_end(msgId, result) { return dispatch("on_stream_end", [msgId, result]); }
  eel.expose(on_stream_end, "on_stream_end");

  // A model/provider failure, as distinct from the bridge being unreachable.
  function on_stream_error(msgId, error) { return dispatch("on_stream_error", [msgId, error]); }
  eel.expose(on_stream_error, "on_stream_error");

  // Groq rate limit: carries the real wait time so the UI can explain it.
  function on_rate_limited(msgId, info) { return dispatch("on_rate_limited", [msgId, info]); }
  eel.expose(on_rate_limited, "on_rate_limited");

  function on_confirm_request(requestId, message, meta) { return dispatch("on_confirm_request", [requestId, message, meta]); }
  eel.expose(on_confirm_request, "on_confirm_request");

  function on_wake_detected(transcript) { return dispatch("on_wake_detected", [transcript]); }
  eel.expose(on_wake_detected, "on_wake_detected");

  function on_voice_exchange(turnId, userText, assistantText) { return dispatch("on_voice_exchange", [turnId, userText, assistantText]); }
  eel.expose(on_voice_exchange, "on_voice_exchange");

  function on_voice_state(state, detail) { return dispatch("on_voice_state", [state, detail]); }
  eel.expose(on_voice_state, "on_voice_state");

  // Which phase of a voice turn Nano is in, so the UI can narrate it.
  function on_voice_phase(phase, detail) { return dispatch("on_voice_phase", [phase, detail]); }
  eel.expose(on_voice_phase, "on_voice_phase");

  console.info("[nano-bridge] ready: 11 Python -> UI callbacks registered.");
})();
