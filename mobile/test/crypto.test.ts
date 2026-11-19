import { describe, expect, it } from "vitest";
import { AAD, b64d, b64e, importKey, openSealed, seal } from "../src/lib/crypto";

const KEY = Uint8Array.from({ length: 32 }, (_, i) => i);
const NONCE = Uint8Array.from({ length: 12 }, (_, i) => i);

describe("crypto", () => {
  it("decrypts the vector the PC produces (tests/test_mobile_link.py)", async () => {
    const key = await importKey(KEY);
    const value = await openSealed(key, { n: b64e(NONCE), c: "PCC-fqmJrTm3Y9nK_6ZaECwJFt6kwHZ2ePOXwwvBLvY" }, "nano-rpc-v1|dev_test");
    expect(value).toEqual({ hello: "NANO" });
  });

  it("round-trips and rejects the wrong context", async () => {
    const key = await importKey(KEY);
    const sealed = await seal(key, { text: "olá" }, AAD.rpc("dev_1"));
    expect(await openSealed(key, sealed, AAD.rpc("dev_1"))).toEqual({ text: "olá" });
    await expect(openSealed(key, sealed, AAD.rpc("dev_2"))).rejects.toThrow("bad_reply");
  });

  it("base64url has no padding and survives every byte", () => {
    const all = Uint8Array.from({ length: 256 }, (_, i) => i);
    const text = b64e(all);
    expect(text).not.toMatch(/[=+/]/);
    expect(Array.from(b64d(text))).toEqual(Array.from(all));
  });
});
