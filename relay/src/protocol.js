/**
 * The relay's wire rules, without the Cloudflare runtime: room names and the
 * checks every frame passes. Kept apart so `npm test` can run them on Node,
 * and so core/mobile_relay.py can be checked against the same vector.
 */
export const ROOM_RE = /^[A-Za-z0-9_-]{43}$/;
export const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;
export const CONN_RE = /^[0-9a-f]{32}$/;
/** The PC accepts bodies up to 256 KiB (core/mobile_server.MAX_BODY_BYTES). */
export const MAX_FRAME = 300 * 1024;
export const MAX_PHONES = 16;
const KINDS = new Set(["rpc", "pair"]);

function b64url(bytes) {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(text) {
  const clean = text.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(clean + "=".repeat((4 - (clean.length % 4)) % 4));
  return Uint8Array.from(binary, (ch) => ch.charCodeAt(0));
}

/** The room a token owns. Same as core/mobile_relay.room_for_token. */
export async function roomForToken(token) {
  const prefix = new TextEncoder().encode("nano-relay-room-v1|");
  const raw = b64urlDecode(token);
  const data = new Uint8Array(prefix.length + raw.length);
  data.set(prefix);
  data.set(raw, prefix.length);
  return b64url(new Uint8Array(await crypto.subtle.digest("SHA-256", data)));
}

const PATH_RE = /^\/v1\/(pc|phone)\/([A-Za-z0-9_-]{43})$/;

/**
 * The role and room a request is for, or null. Exactly "/v1/pc/<room>" or
 * "/v1/phone/<room>": the Worker and the room read the path with this ONE
 * function. They used to parse it two ways, and "//v1/phone/<room>" passed the
 * Worker as a phone but reached the room with no role, skipping both the
 * "PC must be online" check and the phone limit.
 */
export function parsePath(pathname) {
  const match = PATH_RE.exec(typeof pathname === "string" ? pathname : "");
  return match ? { role: match[1], room: match[2] } : null;
}

/**
 * The PC socket phones should reach: the newest one that is still open. A PC
 * that reconnects after sleep replaces its old socket, but the old one can
 * still be listed while its close handshake hangs; sending to it threw, and
 * phones were told "pc_offline" while the PC was connected.
 * `sockets` are [{ ws, tags, open }].
 */
export function currentPc(sockets) {
  let best = null;
  let bestGen = -1;
  for (const item of sockets) {
    if (!item.open) continue;
    const tag = (item.tags || []).find((t) => t.startsWith("g:"));
    const gen = tag ? Number(tag.slice(2)) : 0;
    if (Number.isFinite(gen) && gen >= bestGen) {
      best = item.ws;
      bestGen = gen;
    }
  }
  return best;
}

export function plain(status, text) {
  return new Response(text, {
    status,
    headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" },
  });
}

/** Validates one frame from a phone. Returns null for anything malformed. */
export function phoneFrame(text) {
  if (typeof text !== "string" || text.length > MAX_FRAME) return null;
  let msg;
  try { msg = JSON.parse(text); } catch { return null; }
  if (!msg || typeof msg !== "object" || !KINDS.has(msg.t)) return null;
  if (!Number.isSafeInteger(msg.q) || msg.q < 0) return null;
  if (!msg.b || typeof msg.b !== "object" || Array.isArray(msg.b)) return null;
  return { t: msg.t, q: msg.q, b: msg.b };
}

/** Validates one frame from the PC. Returns null for anything malformed. */
export function pcFrame(text) {
  if (typeof text !== "string" || text.length > MAX_FRAME) return null;
  let msg;
  try { msg = JSON.parse(text); } catch { return null; }
  if (!msg || typeof msg !== "object") return null;
  if (!Number.isSafeInteger(msg.q) || msg.q < 0) return null;
  if (typeof msg.c !== "string" || !CONN_RE.test(msg.c)) return null;
  if (!Number.isInteger(msg.s) || msg.s < 100 || msg.s > 599) return null;
  if (!msg.b || typeof msg.b !== "object" || Array.isArray(msg.b)) return null;
  return { q: msg.q, c: msg.c, s: msg.s, b: msg.b };
}
