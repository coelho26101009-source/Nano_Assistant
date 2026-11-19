/**
 * The link to the NANO on the PC: pairing, sealed calls, and the event feed.
 *
 * The protocol is described in core/mobile_link.py on the PC. In short:
 *   1. The QR code holds the PC's addresses, a port, a pairing session id and a
 *      one-time secret. The phone proves it has the secret and waits.
 *   2. The person says yes ON THE PC. Only then does the PC hand this phone its
 *      own 256-bit key, sealed with the QR secret.
 *   3. Every later request and answer is sealed with that key. Requests carry a
 *      fresh id and the time, so a copied request cannot be replayed.
 *
 * The PC is tried at every address the QR listed (home Wi-Fi first, then a
 * private network such as Tailscale), plus any address the person added by
 * hand, and the one that answered last is tried first next time.
 */
import { AAD, b64d, importKey, openSealed, randomId, seal, type Envelope } from "./crypto";
import { httpPost, HttpError } from "./http";
import { loadPairing, savePairing, clearPairing, type Pairing } from "./storage";

export type PairLink = {
  version: number;
  hosts: string[];
  port: number;
  session: string;
  secret: Uint8Array;
  pcName: string;
};

export type PairState = "connecting" | "pending" | "approved" | "denied" | "expired" | "unreachable" | "error";

export type LinkState = "unpaired" | "connecting" | "online" | "offline" | "revoked";

export type NanoEvent = { seq: number; name: string; data: any[] };

const HOST_RE = /^[0-9a-zA-Z.:\-[\]]{1,80}$/;

/** Read the text of the QR code. Throws "not_a_nano_code" for anything else. */
export function parsePairLink(text: string): PairLink {
  const raw = String(text || "").trim();
  if (!raw.startsWith("nano://pair?")) throw new Error("not_a_nano_code");
  const query = new URLSearchParams(raw.slice("nano://pair?".length));
  const version = Number(query.get("v"));
  const port = Number(query.get("p"));
  const session = query.get("i") || "";
  const secretText = query.get("s") || "";
  const hosts = (query.get("h") || "").split(",").map((h) => h.trim()).filter((h) => HOST_RE.test(h));
  let secret: Uint8Array;
  try {
    secret = b64d(secretText);
  } catch {
    throw new Error("not_a_nano_code");
  }
  if (version !== 1) throw new Error("unsupported_version");
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("not_a_nano_code");
  if (!/^[0-9a-f]{16}$/.test(session) || secret.length !== 32 || hosts.length === 0) {
    throw new Error("not_a_nano_code");
  }
  return { version, hosts, port, session, secret, pcName: (query.get("n") || "NANO PC").slice(0, 60) };
}

function urlFor(host: string, port: number, path: string): string {
  const bracketed = host.includes(":") && !host.startsWith("[") ? `[${host}]` : host;
  return `http://${bracketed}:${port}${path}`;
}

/** Turn "100.1.2.3", "100.1.2.3:47821" or "mypc.tailnet.ts.net" into host + port. */
export function parseManualAddress(text: string, defaultPort: number): { host: string; port: number } | null {
  const value = String(text || "").trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  if (!value) return null;
  const match = value.match(/^([^:]+)(?::(\d{1,5}))?$/);
  if (!match || !HOST_RE.test(match[1])) return null;
  const port = match[2] ? Number(match[2]) : defaultPort;
  if (port < 1 || port > 65535) return null;
  return { host: match[1], port };
}

/** Pair with the PC. Calls onState as the PC moves from "asked" to an answer. */
export async function pair(
  link: PairLink,
  deviceName: string,
  platform: string,
  onState: (state: PairState, host?: string) => void,
  signal?: { cancelled: boolean },
): Promise<Pairing | null> {
  const key = await importKey(link.secret);
  const call = async (host: string, op: "request" | "status", extra: Record<string, unknown> = {}) => {
    const id = randomId();
    const body: Envelope & { i?: string } = await seal(key, { op, id, ts: Date.now(), ...extra }, AAD.pair(link.session));
    body.i = link.session;
    const reply = await httpPost(urlFor(host, link.port, "/v1/pair"), body, 8000);
    return openSealed<{ state: string; deviceId?: string; key?: string; pcName?: string }>(
      key, reply as Envelope, AAD.pairReply(link.session, id));
  };

  onState("connecting");
  let host = "";
  let first: { state: string } | null = null;
  for (const candidate of link.hosts) {
    try {
      first = await call(candidate, "request", { name: deviceName, platform });
      host = candidate;
      break;
    } catch (error) {
      if (error instanceof HttpError && error.status === 410) { onState("expired"); return null; }
      if (error instanceof HttpError && error.status !== 0) { onState("error"); return null; }
    }
  }
  if (!first) { onState("unreachable"); return null; }

  const deadline = Date.now() + 200_000;
  let reply: { state: string; deviceId?: string; key?: string; pcName?: string } = first;
  while (reply.state === "pending" || reply.state === "waiting") {
    onState("pending", host);
    if (signal?.cancelled) return null;
    if (Date.now() > deadline) { onState("expired"); return null; }
    await new Promise((resolve) => setTimeout(resolve, 1500));
    try {
      reply = await call(host, "status");
    } catch (error) {
      if (error instanceof HttpError && error.status === 410) { onState("expired"); return null; }
      // A dropped request while waiting is not an answer; keep asking.
    }
  }
  if (reply.state === "denied") { onState("denied"); return null; }
  if (reply.state !== "approved" || !reply.deviceId || !reply.key) { onState("error"); return null; }
  const pairing: Pairing = {
    deviceId: reply.deviceId,
    key: reply.key,
    pcName: reply.pcName || link.pcName,
    hosts: [host, ...link.hosts.filter((h) => h !== host)],
    port: link.port,
    extraHosts: [],
    pairedAt: Date.now(),
  };
  await savePairing(pairing);
  onState("approved", host);
  return pairing;
}

export class RpcError extends Error {
  constructor(public code: string) { super(code); }
}

type Listener = (event: NanoEvent) => void;
type StateListener = (state: LinkState) => void;

/** One paired PC. Created once per app run by `useLink`. */
export class NanoLink {
  private pairing: Pairing | null = null;
  private key: CryptoKey | null = null;
  private listeners = new Set<Listener>();
  private stateListeners = new Set<StateListener>();
  private running = false;
  private loopToken = 0;
  private after = 0;
  state: LinkState = "unpaired";
  pcName = "";

  async load(): Promise<boolean> {
    this.pairing = await loadPairing();
    if (!this.pairing) { this.setState("unpaired"); return false; }
    this.key = await importKey(b64d(this.pairing.key));
    this.pcName = this.pairing.pcName;
    this.setState("connecting");
    return true;
  }

  get paired(): boolean { return this.pairing !== null; }
  get info(): Pairing | null { return this.pairing; }

  onEvent(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onState(listener: StateListener): () => void {
    this.stateListeners.add(listener);
    return () => this.stateListeners.delete(listener);
  }

  private setState(state: LinkState) {
    if (this.state === state) return;
    this.state = state;
    this.stateListeners.forEach((fn) => fn(state));
  }

  private candidates(): { host: string; port: number }[] {
    if (!this.pairing) return [];
    const list = this.pairing.hosts.map((host) => ({ host, port: this.pairing!.port }));
    for (const manual of this.pairing.extraHosts) {
      const parsed = parseManualAddress(manual, this.pairing.port);
      if (parsed) list.push(parsed);
    }
    const seen = new Set<string>();
    return list.filter((c) => {
      const id = `${c.host}:${c.port}`;
      if (seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  }

  private async remember(host: string) {
    if (!this.pairing || this.pairing.hosts[0] === host) return;
    if (this.pairing.hosts.includes(host)) {
      this.pairing = { ...this.pairing, hosts: [host, ...this.pairing.hosts.filter((h) => h !== host)] };
      await savePairing(this.pairing);
    }
  }

  /** Call one method on the PC. Throws RpcError with the PC's error code. */
  async call<T = any>(method: string, args: Record<string, unknown> = {}, timeoutMs = 15000): Promise<T> {
    if (!this.pairing || !this.key) throw new RpcError("not_paired");
    const deviceId = this.pairing.deviceId;
    let lastError: unknown = null;
    for (const candidate of this.candidates()) {
      const id = randomId();
      const body: Envelope & { d?: string } = await seal(
        this.key, { id, ts: Date.now(), m: method, a: args }, AAD.rpc(deviceId));
      body.d = deviceId;
      try {
        const reply = await httpPost(urlFor(candidate.host, candidate.port, "/v1/rpc"), body, timeoutMs);
        const value = await openSealed<{ id: string; ok: boolean; r?: T; error?: string }>(
          this.key, reply as Envelope, AAD.rpcReply(deviceId, id));
        void this.remember(candidate.host);
        if (this.state !== "online") this.setState("online");
        if (!value.ok) throw new RpcError(value.error || "error");
        return value.r as T;
      } catch (error) {
        if (error instanceof RpcError) throw error;
        if (error instanceof HttpError && error.status === 401 && error.code === "unauthorized") {
          this.setState("revoked");
          throw new RpcError("revoked");
        }
        if (error instanceof HttpError && error.status !== 0) throw new RpcError(error.code || "http_error");
        lastError = error;
      }
    }
    this.setState("offline");
    throw new RpcError(lastError instanceof HttpError ? "unreachable" : "unreachable");
  }

  /** Read the event feed until stop(). Reconnects on its own, with backoff. */
  start() {
    if (this.running || !this.pairing) return;
    this.running = true;
    const token = ++this.loopToken;
    void (async () => {
      let backoff = 1000;
      while (this.running && token === this.loopToken) {
        try {
          const result = await this.call<{ events: NanoEvent[]; last: number; reset: boolean }>(
            "events.poll", { after: this.after, wait: 20 }, 30000);
          if (token !== this.loopToken) return;
          if (result.reset) this.listeners.forEach((fn) => fn({ seq: result.last, name: "resync", data: [] }));
          for (const event of result.events) {
            this.listeners.forEach((fn) => {
              try { fn(event); } catch (err) { console.error("[nano] event handler", err); }
            });
          }
          this.after = result.last;
          backoff = 1000;
        } catch (error) {
          if (error instanceof RpcError && (error.code === "revoked" || error.code === "not_paired")) {
            this.running = false;
            return;
          }
          await new Promise((resolve) => setTimeout(resolve, backoff));
          backoff = Math.min(backoff * 2, 15000);
        }
      }
    })();
  }

  stop() {
    this.running = false;
    this.loopToken++;
  }

  /** Begin the feed at "now": old events from before the app opened are not replayed. */
  async syncCursor() {
    try {
      const result = await this.call<{ last: number }>("events.poll", { after: -1, wait: 0 }, 8000);
      this.after = result.last;
    } catch { /* the loop will retry */ }
  }

  async addManualHost(text: string): Promise<boolean> {
    if (!this.pairing) return false;
    if (!parseManualAddress(text, this.pairing.port)) return false;
    const extraHosts = Array.from(new Set([...this.pairing.extraHosts, text.trim()])).slice(0, 5);
    this.pairing = { ...this.pairing, extraHosts };
    await savePairing(this.pairing);
    return true;
  }

  async removeManualHost(text: string) {
    if (!this.pairing) return;
    this.pairing = { ...this.pairing, extraHosts: this.pairing.extraHosts.filter((h) => h !== text) };
    await savePairing(this.pairing);
  }

  /** Forget this PC. Tells the PC first when it can be reached. */
  async unpair(tellPc = true) {
    this.stop();
    if (tellPc) {
      try { await this.call("device.unpair", {}, 5000); } catch { /* removed here either way */ }
    }
    await clearPairing();
    this.pairing = null;
    this.key = null;
    this.after = 0;
    this.setState("unpaired");
  }
}
