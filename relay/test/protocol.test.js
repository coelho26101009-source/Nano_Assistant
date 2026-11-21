// The relay's wire rules on plain Node: `npm test`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { pcFrame, phoneFrame, roomForToken } from "../src/protocol.js";

// Shared with tests/test_mobile_relay.py: the PC and the relay must agree on
// which room a token owns, or the PC is refused at the door.
test("room name matches core/mobile_relay.room_for_token", async () => {
  assert.equal(await roomForToken("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"),
    "os4_ZZLglcyzI3aTrp6Q2vQecsaZEc8pR0_If--cIc8");
});

test("phone frames: only rpc/pair with a number and a body pass", () => {
  assert.deepEqual(phoneFrame('{"t":"rpc","q":1,"b":{"n":"x","c":"y"},"extra":1}'),
    { t: "rpc", q: 1, b: { n: "x", c: "y" } });
  for (const bad of ['{"t":"admin","q":1,"b":{}}', '{"t":"rpc","q":-1,"b":{}}', '{"t":"rpc","q":1,"b":[]}',
    '{"t":"rpc","q":"1","b":{}}', "not json", "x".repeat(400 * 1024), 42]) {
    assert.equal(phoneFrame(bad), null, String(bad).slice(0, 40));
  }
});

test("PC frames must name a real connection and a status", () => {
  const c = "0123456789abcdef0123456789abcdef";
  assert.deepEqual(pcFrame(JSON.stringify({ q: 3, c, s: 200, b: { n: "a" } })), { q: 3, c, s: 200, b: { n: "a" } });
  for (const bad of [{ q: 3, c: "phone", s: 200, b: {} }, { q: 3, c, s: 99, b: {} }, { q: 3, c, s: 200 },
    { q: 1.5, c, s: 200, b: {} }]) {
    assert.equal(pcFrame(JSON.stringify(bad)), null, JSON.stringify(bad));
  }
});
