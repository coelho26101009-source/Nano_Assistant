import { afterEach, describe, expect, it, vi } from "vitest";
import { AAD, b64d, b64e, importKey, openSealed, pairingCode, seal } from "../src/lib/crypto";
import { pair, parsePairLink } from "../src/lib/link";

const SESSION = "0123456789abcdef";
// A fresh QR secret for every run, as the PC makes one per pairing.
const SECRET = b64e(crypto.getRandomValues(new Uint8Array(32)));
const LINK = `nano://pair?v=3&h=192.168.1.20&p=47821&i=${SESSION}&s=${SECRET}&n=PC`;

/** A PC that follows protocol 3, closely enough for the phone's side. */
function fakePc(options: { loseFirstRequestReply: boolean }) {
  const pcNonce = crypto.getRandomValues(new Uint8Array(32));
  const seen: string[] = [];
  let state = "waiting";
  let code = "";
  let lost = false;
  const fetchMock = vi.fn(async (_url: string, init: { body: string }) => {
    const key = await importKey(b64d(SECRET));
    const envelope = JSON.parse(init.body);
    const request = await openSealed<any>(key, envelope, AAD.pair(SESSION));
    seen.push(request.op);
    let reply: Record<string, unknown>;
    if (request.op === "commit") {
      state = state === "waiting" ? "committed" : state;
      reply = { state, id: request.id, pn: b64e(pcNonce) };
    } else if (request.op === "request") {
      if (state === "committed") {
        state = "pending";
        code = await pairingCode(SESSION, b64d(request.pk), b64d(request.nn), pcNonce);
      }
      reply = { state, id: request.id, code };
      if (options.loseFirstRequestReply && !lost) {
        lost = true;                       // the PC acted, the answer never arrived
        throw new TypeError("network down");
      }
    } else {
      reply = { state: "denied", id: request.id, code };   // the person said no
    }
    const sealed = await seal(key, reply, AAD.pairReply(SESSION, request.id));
    return new Response(JSON.stringify(sealed), { status: 200 });
  });
  return { fetchMock, seen, codeShown: () => code };
}

describe("pairing, protocol 3", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("asks again with the same key when the answer to the reveal is lost", async () => {
    const pc = fakePc({ loseFirstRequestReply: true });
    vi.stubGlobal("fetch", pc.fetchMock);
    const states: string[] = [];
    let phoneCode = "";
    const result = await pair(parsePairLink(LINK), "Pixel", "android",
      (state) => states.push(state), undefined, (c) => { phoneCode = c; });

    expect(result).toBeNull();
    expect(pc.seen.slice(0, 3)).toEqual(["commit", "request", "request"]);
    expect(states).toContain("pending");
    expect(states.at(-1)).toBe("denied");            // the person's answer, not an error
    expect(phoneCode).toBe(pc.codeShown());           // both screens showed the same digits
  }, 10_000);

  it("a reveal that never gets through ends as unreachable, not as a broken pairing", async () => {
    const fetchMock = vi.fn(async (_url: string, init: { body: string }) => {
      const key = await importKey(b64d(SECRET));
      const request = await openSealed<any>(key, JSON.parse(init.body), AAD.pair(SESSION));
      if (request.op !== "commit") throw new TypeError("network down");
      const sealed = await seal(key, { state: "committed", id: request.id, pn: b64e(new Uint8Array(32)) },
        AAD.pairReply(SESSION, request.id));
      return new Response(JSON.stringify(sealed), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const states: string[] = [];
    const result = await pair(parsePairLink(LINK), "Pixel", "android", (state) => states.push(state));
    expect(result).toBeNull();
    expect(states.at(-1)).toBe("unreachable");
    expect(fetchMock.mock.calls.length).toBe(4);      // one commit, three tries of the reveal
  }, 10_000);
});
