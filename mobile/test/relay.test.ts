import { describe, expect, it } from "vitest";
import { HttpError } from "../src/lib/http";
import { parseRelay, RelayChannel, relaySocketUrl } from "../src/lib/relay";

const ROOM = "os4_ZZLglcyzI3aTrp6Q2vQecsaZEc8pR0_If--cIc8";

describe("parseRelay", () => {
  it("accepts an https relay and a 43-character room", () => {
    expect(parseRelay("https://nano-relay.example.workers.dev/", ROOM))
      .toEqual({ url: "https://nano-relay.example.workers.dev", room: ROOM });
    expect(relaySocketUrl({ url: "https://r.example", room: ROOM })).toBe(`wss://r.example/v1/phone/${ROOM}`);
  });
  it("refuses anything else", () => {
    for (const [url, room] of [
      ["http://r.example", ROOM], ["https://r.example/x", ROOM], ["https://u:p@r.example", ROOM],
      ["https://r.example", "short"], ["", ROOM], [null, null], ["javascript:alert(1)", ROOM],
    ] as const) {
      expect(parseRelay(url, room)).toBeNull();
    }
    expect(parseRelay("http://127.0.0.1:8787", ROOM)).not.toBeNull();   // a developer's local relay
  });
});

class FakeSocket {
  readyState = 0;
  sent: string[] = [];
  onopen: ((ev: any) => void) | null = null;
  onmessage: ((ev: any) => void) | null = null;
  onclose: ((ev: any) => void) | null = null;
  onerror: ((ev: any) => void) | null = null;
  constructor(public url: string) { setTimeout(() => { this.readyState = 1; this.onopen?.({}); }, 0); }
  send(data: string) { this.sent.push(data); }
  close() { this.readyState = 3; this.onclose?.({}); }
  reply(frame: unknown) { this.onmessage?.({ data: JSON.stringify(frame) }); }
}

describe("RelayChannel", () => {
  it("sends the sealed body and resolves with the PC's answer", async () => {
    let socket!: FakeSocket;
    const channel = new RelayChannel({ url: "https://r.example", room: ROOM }, (url) => (socket = new FakeSocket(url)));
    const pending = channel.post("rpc", { d: "dev", n: "a", c: "b" }, 5000);
    await new Promise((r) => setTimeout(r, 5));
    expect(socket.url).toBe(`wss://r.example/v1/phone/${ROOM}`);
    const sent = JSON.parse(socket.sent[0]);
    expect(sent).toEqual({ t: "rpc", q: 1, b: { d: "dev", n: "a", c: "b" } });
    socket.reply({ q: 1, s: 200, b: { n: "x", c: "y" } });
    await expect(pending).resolves.toEqual({ n: "x", c: "y" });
    channel.close();
  });

  it("treats a missing PC like an unreachable one, and keeps other errors", async () => {
    let socket!: FakeSocket;
    const channel = new RelayChannel({ url: "https://r.example", room: ROOM }, (url) => (socket = new FakeSocket(url)));
    const offline = channel.post("rpc", {}, 5000);
    await new Promise((r) => setTimeout(r, 5));
    socket.reply({ q: 1, s: 503, b: { error: "pc_offline" } });
    await expect(offline).rejects.toMatchObject({ status: 0, code: "pc_offline" });
    const revoked = channel.post("rpc", {}, 5000);
    await new Promise((r) => setTimeout(r, 5));
    socket.reply({ q: 2, s: 401, b: { error: "unauthorized" } });
    await expect(revoked).rejects.toMatchObject({ status: 401, code: "unauthorized" });
    channel.close();
  });

  it("fails pending calls when the connection drops", async () => {
    let socket!: FakeSocket;
    const channel = new RelayChannel({ url: "https://r.example", room: ROOM }, (url) => (socket = new FakeSocket(url)));
    const pending = channel.post("pair", {}, 5000);
    await new Promise((r) => setTimeout(r, 5));
    socket.close();
    const error: any = await pending.catch((e: unknown) => e);
    expect(error).toBeInstanceOf(HttpError);
    expect(error.status).toBe(0);
  });
});

describe("pairing QR with a relay", async () => {
  const { parsePairLink } = await import("../src/lib/link");
  const base = "nano://pair?v=2&p=47821&i=0123456789abcdef&s=AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8&n=PC";
  it("reads the relay, and a code with only the relay is enough", () => {
    const link = parsePairLink(`${base}&h=&r=${encodeURIComponent("https://r.example")}&rr=${ROOM}`);
    expect(link.relay).toEqual({ url: "https://r.example", room: ROOM });
    expect(link.hosts).toEqual([]);
  });
  it("still reads codes from a PC without a relay", () => {
    expect(parsePairLink(`${base}&h=192.168.1.20`).relay).toBeNull();
    expect(() => parsePairLink(`${base}&h=`)).toThrow("not_a_nano_code");
  });
});
