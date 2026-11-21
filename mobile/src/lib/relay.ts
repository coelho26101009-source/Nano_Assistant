/**
 * Reaching the PC away from home: NANO's relay.
 *
 * The PC keeps one outgoing connection to the relay (core/mobile_relay.py)
 * and this phone opens another to the same room; the relay copies messages
 * between them (relay/src/index.js). What travels is exactly what the phone
 * sends on the home Wi-Fi -- the AES-256-GCM envelope sealed with this
 * phone's own key -- so the relay sees sizes and times, never the content,
 * and cannot change or replay a message.
 *
 * `post` behaves like `httpPost`: it resolves with the PC's reply body, or
 * throws HttpError(status, code). "The PC is not connected" and every network
 * failure are HttpError(0, ...), which the link treats as "unreachable".
 */
import { HttpError } from "./http";

export type RelayInfo = { url: string; room: string };

const ROOM_RE = /^[A-Za-z0-9_-]{43}$/;
const KEEPALIVE_MS = 25_000;

/** The relay from a pairing QR code, or null when it is missing or not https. */
export function parseRelay(url: string | null, room: string | null): RelayInfo | null {
  const text = String(url || "").trim().replace(/\/+$/, "");
  if (!text || text.length > 200 || !ROOM_RE.test(String(room || ""))) return null;
  let parsed: URL;
  try { parsed = new URL(text); } catch { return null; }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) return null;
  if (parsed.pathname !== "/" && parsed.pathname !== "") return null;
  const local = parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost";
  if (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && local)) return null;
  return { url: `${parsed.protocol}//${parsed.host}`, room: String(room) };
}

export function relaySocketUrl(relay: RelayInfo): string {
  const base = relay.url.replace(/^https:/, "wss:").replace(/^http:/, "ws:");
  return `${base}/v1/phone/${relay.room}`;
}

type Pending = { resolve: (value: unknown) => void; reject: (error: unknown) => void; timer: ReturnType<typeof setTimeout> };
type SocketLike = {
  readonly readyState: number;
  send(data: string): void;
  close(): void;
  onopen: ((ev: any) => void) | null;
  onmessage: ((ev: any) => void) | null;
  onclose: ((ev: any) => void) | null;
  onerror: ((ev: any) => void) | null;
};

/** One WebSocket to the relay, opened when needed and shared by every call. */
export class RelayChannel {
  private socket: SocketLike | null = null;
  private opening: Promise<SocketLike> | null = null;
  private pending = new Map<number, Pending>();
  private next = 1;
  private keepalive: ReturnType<typeof setInterval> | null = null;

  constructor(
    readonly relay: RelayInfo,
    private readonly makeSocket: (url: string) => SocketLike = (url) => new WebSocket(url) as unknown as SocketLike,
  ) {}

  private open(timeoutMs: number): Promise<SocketLike> {
    if (this.socket && this.socket.readyState === 1) return Promise.resolve(this.socket);
    if (this.opening) return this.opening;
    this.opening = new Promise<SocketLike>((resolve, reject) => {
      let socket: SocketLike;
      try {
        socket = this.makeSocket(relaySocketUrl(this.relay));
      } catch {
        reject(new HttpError(0, "unreachable"));
        return;
      }
      const timer = setTimeout(() => {
        try { socket.close(); } catch { /* not open */ }
        reject(new HttpError(0, "unreachable"));
      }, Math.min(timeoutMs, 10_000));
      socket.onopen = () => {
        clearTimeout(timer);
        this.socket = socket;
        this.startKeepalive();
        resolve(socket);
      };
      socket.onmessage = (event) => this.receive(event.data);
      socket.onerror = () => { /* onclose follows */ };
      socket.onclose = () => {
        clearTimeout(timer);
        if (this.socket === socket) this.socket = null;
        this.stopKeepalive();
        // A relay that refuses the room (the PC is not there) closes before
        // opening: the same answer as a PC that cannot be reached.
        reject(new HttpError(0, "unreachable"));
        this.failAll();
      };
    }).finally(() => { this.opening = null; });
    return this.opening;
  }

  private startKeepalive() {
    this.stopKeepalive();
    // Mobile networks drop silent connections. "ping" is answered by the
    // relay itself without waking the room.
    this.keepalive = setInterval(() => {
      try { this.socket?.send("ping"); } catch { /* closing */ }
    }, KEEPALIVE_MS);
  }

  private stopKeepalive() {
    if (this.keepalive) clearInterval(this.keepalive);
    this.keepalive = null;
  }

  private receive(data: unknown) {
    if (typeof data !== "string" || data === "pong") return;
    let message: any;
    try { message = JSON.parse(data); } catch { return; }
    const waiter = typeof message?.q === "number" ? this.pending.get(message.q) : undefined;
    if (!waiter) return;
    this.pending.delete(message.q);
    clearTimeout(waiter.timer);
    const status = Number(message.s);
    const body = message.b && typeof message.b === "object" ? message.b : {};
    if (status === 200) waiter.resolve(body);
    // 503 is "the PC is not there" (or phone access is off on it): to the
    // phone that is the same as a PC it cannot reach.
    else if (status === 503) waiter.reject(new HttpError(0, String(body.error || "pc_offline")));
    else waiter.reject(new HttpError(status || 0, String(body.error || "")));
  }

  private failAll() {
    for (const [, waiter] of this.pending) {
      clearTimeout(waiter.timer);
      waiter.reject(new HttpError(0, "unreachable"));
    }
    this.pending.clear();
  }

  /** Send one sealed body ("rpc" or "pair") and wait for the PC's answer. */
  async post(kind: "rpc" | "pair", body: unknown, timeoutMs: number): Promise<unknown> {
    const socket = await this.open(timeoutMs);
    const q = this.next++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(q);
        reject(new HttpError(0, "unreachable"));
      }, timeoutMs);
      this.pending.set(q, { resolve, reject, timer });
      try {
        socket.send(JSON.stringify({ t: kind, q, b: body }));
      } catch {
        clearTimeout(timer);
        this.pending.delete(q);
        reject(new HttpError(0, "unreachable"));
      }
    });
  }

  close() {
    this.stopKeepalive();
    const socket = this.socket;
    this.socket = null;
    try { socket?.close(); } catch { /* already closed */ }
    this.failAll();
  }
}
