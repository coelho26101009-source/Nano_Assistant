import { describe, expect, it } from "vitest";
import { AAD, b64d, b64e, importKey, openFromPc, openSealed, pairingCode, seal } from "../src/lib/crypto";

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

  // Pairing protocol 2. The same vector is produced by core/mobile_link.py
  // (seal_to_phone with a fixed ephemeral key and nonce, pairing_code) and
  // checked in tests/test_mobile_link.py.
  const PHONE_JWK = {
    kty: "EC", crv: "P-256",
    d: "Hy49TFtqeYgfLj1MW2p5iB8uPUxbanmIHy49TFtqeYg",
    x: "vXxzuIsum0ztpiAistqL4TGTpbVu3Cbn33hC4kzQtes",
    y: "BgWtp72oOsaiuA1-MUBA-kf_Frg7rIXO2wFEUbt85xo",
  };
  const PHONE_PUB = "BL18c7iLLptM7aYgIrLai-Exk6W1btwm5994QuJM0LXrBgWtp72oOsaiuA1-MUBA-kf_Frg7rIXO2wFEUbt85xo";
  const BOX = {
    epk: "BLGX90HjurBl9wAmEKL1-QJ5YRZFBN_G4nZSmf5Efvk3tt8br3iBApnKASIXhdTaMCwp9HfzYAXuHfDsJ-B-sNM",
    n: "AAECAwQFBgcICQoL",
    c: "57ncfJbEqIvfOcEunXAl1WnbiTUyhvRNmiQnMyYiIpgIvNFkS9I6EJpKJMinU7FGu9foPycuETXAF8RjvQ2Bbu89lG_X",
  };

  it("opens the key the PC sealed to this phone (ECDH + HKDF + AES-GCM)", async () => {
    const privateKey = await crypto.subtle.importKey(
      "jwk", PHONE_JWK, { name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]);
    const salt = Uint8Array.from({ length: 32 }, (_, i) => i);
    const opened = await openFromPc<{ key: string }>(privateKey, salt, AAD.pairKey("0123456789abcdef", "dev123"), BOX);
    expect(opened.key).toBe("BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc");
    // Another device id is another context: the box does not open.
    await expect(openFromPc(privateKey, salt, AAD.pairKey("0123456789abcdef", "dev999"), BOX)).rejects.toThrow("bad_reply");
  });

  it("derives the same six-digit code as the PC", async () => {
    expect(await pairingCode("0123456789abcdef", b64d(PHONE_PUB))).toBe("175500");
  });
});
