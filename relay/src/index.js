/**
 * NANO relay: lets the phone reach the NANO on the PC away from home.
 *
 * WHAT IT DOES
 * ------------
 * It passes sealed messages between a phone and a PC that both connect OUT to
 * it. The PC never listens on the internet and no port is opened on any
 * router: the PC keeps one outgoing WebSocket here, the phone opens another,
 * and this relay copies each message from one to the other.
 *
 * WHAT IT CANNOT DO
 * -----------------
 * Read anything. Every message is the same AES-256-GCM envelope the phone and
 * the PC exchange on the home Wi-Fi (core/mobile_link.py), sealed with a key
 * that only those two hold. The relay sees a room name, sizes and times. It
 * cannot read, change or replay a message: the PC refuses anything that does
 * not open with a paired phone's key, and every request id is used once.
 *
 * ROOMS
 * -----
 * A PC owns one room. The room name is SHA-256 of a random 256-bit token
 * that only the PC holds ("nano-relay-room-v1|" + token, base64url). The PC
 * proves the token when it connects, so nobody else can take its place; the
 * relay keeps no accounts and stores nothing. Phones learn the room name from
 * the pairing QR code.
 *
 * WIRE FORMAT (JSON text frames)
 *   phone -> relay : {"t": "rpc" | "pair", "q": <request number>, "b": <sealed body>}
 *   relay -> PC    : {"t", "q", "c": <phone connection id>, "b"}
 *   PC -> relay    : {"q", "c", "s": <HTTP-like status>, "b": <sealed reply or {"error"}>}
 *   relay -> phone : {"q", "s", "b"}
 *   "ping" -> "pong" is answered without waking the room (keep-alive).
 *
 * Runs on Cloudflare Workers with a Durable Object per room, using the
 * WebSocket Hibernation API: a room with nothing to forward sleeps and costs
 * nothing, which keeps one free account enough for many PCs.
 */
import { DurableObject } from "cloudflare:workers";

import { MAX_PHONES, ROOM_RE, TOKEN_RE, pcFrame, phoneFrame, plain, roomForToken } from "./protocol.js";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length === 0) return plain(200, "NANO relay. It only passes sealed messages it cannot read.\n");
    if (parts.length !== 3 || parts[0] !== "v1" || !["pc", "phone"].includes(parts[1])) {
      return plain(404, "not_found");
    }
    const [, role, room] = parts;
    if (!ROOM_RE.test(room)) return plain(404, "not_found");
    if (request.method !== "GET" || (request.headers.get("Upgrade") || "").toLowerCase() !== "websocket") {
      return plain(426, "websocket_required");
    }
    if (role === "pc") {
      const auth = request.headers.get("Authorization") || "";
      const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
      if (!TOKEN_RE.test(token) || (await roomForToken(token)) !== room) return plain(401, "unauthorized");
    }
    const stub = env.ROOMS.get(env.ROOMS.idFromName(room));
    return stub.fetch(request);
  },
};

export class Room extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    // Answered by the runtime itself, so a keep-alive never wakes the room.
    ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
  }

  async fetch(request) {
    const role = new URL(request.url).pathname.split("/")[2];
    const pc = this.ctx.getWebSockets("pc");
    if (role === "phone") {
      // A phone only joins a room whose PC is here: unknown rooms stay empty.
      if (pc.length === 0) return plain(503, "pc_offline");
      if (this.ctx.getWebSockets("phone").length >= MAX_PHONES) return plain(429, "too_many_phones");
    }
    const [client, server] = Object.values(new WebSocketPair());
    if (role === "pc") {
      // One PC per room: a reconnecting PC replaces its own stale socket.
      for (const old of pc) {
        try { old.close(4000, "replaced"); } catch { /* already gone */ }
      }
      this.ctx.acceptWebSocket(server, ["pc"]);
    } else {
      const id = crypto.randomUUID().replace(/-/g, "");
      this.ctx.acceptWebSocket(server, ["phone", `c:${id}`]);
    }
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws, message) {
    const tags = this.ctx.getTags(ws);
    if (tags.includes("pc")) {
      const reply = pcFrame(message);
      if (!reply) return;
      const [phone] = this.ctx.getWebSockets(`c:${reply.c}`);
      if (!phone) return;          // the phone left before its answer
      try { phone.send(JSON.stringify({ q: reply.q, s: reply.s, b: reply.b })); } catch { /* gone */ }
      return;
    }
    const conn = tags.find((tag) => tag.startsWith("c:"));
    const request = phoneFrame(message);
    if (!conn || !request) return;
    const [pc] = this.ctx.getWebSockets("pc");
    if (!pc) {
      try { ws.send(JSON.stringify({ q: request.q, s: 503, b: { error: "pc_offline" } })); } catch { /* gone */ }
      return;
    }
    try {
      pc.send(JSON.stringify({ t: request.t, q: request.q, c: conn.slice(2), b: request.b }));
    } catch {
      try { ws.send(JSON.stringify({ q: request.q, s: 503, b: { error: "pc_offline" } })); } catch { /* gone */ }
    }
  }

  async webSocketClose(ws, code) {
    const usable = code >= 1000 && code < 5000 && ![1004, 1005, 1006, 1015].includes(code);
    try { ws.close(usable ? code : 1000, "bye"); } catch { /* already closed */ }
  }

  async webSocketError(ws) {
    try { ws.close(1011, "error"); } catch { /* already closed */ }
  }
}
