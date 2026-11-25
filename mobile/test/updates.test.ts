/**
 * The phone checks update feeds exactly like the PC: same format, same keys,
 * same rules. Feeds here are signed with a throwaway key made by node:crypto,
 * the way the release workflow signs (scripts/update_feed.mjs).
 */
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";
import { FEED_URL, isAllowedDownloadUrl, verifyFeed } from "../src/lib/updates";

const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
const spkiDer = publicKey.export({ type: "spki", format: "der" });
const keyId = createHash("sha256").update(spkiDer).digest("hex").slice(0, 16);
const KEYS = [{ id: keyId, spki: spkiDer.toString("base64") }];
const APK = "https://github.com/coelho26101009-source/Nano_Assistant/releases/download/android-test/NANO-android-test.apk";

function feed(extra: Record<string, unknown> = {}, key = privateKey, id = keyId) {
  const payload = Buffer.from(JSON.stringify({
    product: "nano-android", channel: "test", version: "0.3.0-beta.1", versionCode: 42,
    url: APK, sha256: "a".repeat(64), size: 12_000_000, ...extra,
  }));
  const signature = sign("sha256", payload, { key, dsaEncoding: "ieee-p1363" });
  return JSON.stringify({ format: 1, keyId: id, payload: payload.toString("base64"), signature: signature.toString("base64") });
}

describe("update feed on the phone", () => {
  it("accepts a feed signed by a trusted key", async () => {
    const offer = await verifyFeed(feed(), KEYS);
    expect(offer.versionCode).toBe(42);
    expect(offer.url).toBe(APK);
  });

  it("rejects a changed payload", async () => {
    const envelope = JSON.parse(feed());
    const payload = JSON.parse(Buffer.from(envelope.payload, "base64").toString());
    payload.versionCode = 99;
    envelope.payload = Buffer.from(JSON.stringify(payload)).toString("base64");
    await expect(verifyFeed(JSON.stringify(envelope), KEYS)).rejects.toThrow("feed_bad_signature");
  });

  it("rejects another key, even under a trusted key id", async () => {
    const other = generateKeyPairSync("ec", { namedCurve: "P-256" }).privateKey;
    await expect(verifyFeed(feed({}, other, "0000000000000000"), KEYS)).rejects.toThrow("feed_unknown_key");
    await expect(verifyFeed(feed({}, other), KEYS)).rejects.toThrow("feed_bad_signature");
  });

  it("rejects a PC feed and downloads from anywhere else", async () => {
    await expect(verifyFeed(feed({ product: "nano-desktop" }), KEYS)).rejects.toThrow("feed_wrong_product");
    await expect(verifyFeed(feed({ url: "https://evil.example/a.apk" }), KEYS)).rejects.toThrow("feed_bad_url");
    expect(isAllowedDownloadUrl("http://github.com/coelho26101009-source/Nano_Assistant/releases/download/x/a.apk")).toBe(false);
    expect(isAllowedDownloadUrl("https://github.com/other/Nano_Assistant/releases/download/x/a.apk")).toBe(false);
    expect(isAllowedDownloadUrl(APK)).toBe(true);
  });

  it("trusts the renamed GitHub account as well as the old name", async () => {
    // Apps installed before 2026-11-25 accept only the old name; this one both.
    const renamed = "https://github.com/coelho97sc/Nano_Assistant/releases/download/android-test/NANO-android-test.apk";
    expect(isAllowedDownloadUrl(renamed)).toBe(true);
    expect((await verifyFeed(feed({ url: renamed }), KEYS)).url).toBe(renamed);
    expect(isAllowedDownloadUrl("https://github.com/coelho97sc-evil/Nano_Assistant/releases/download/x/a.apk")).toBe(false);
    expect(isAllowedDownloadUrl("https://github.com/coelho97sc/Other/releases/download/x/a.apk")).toBe(false);
    expect(isAllowedDownloadUrl("https://github.com/coelho97sc/Nano_Assistant/releases/download/../x/a.apk")).toBe(false);
    expect(FEED_URL.startsWith("https://github.com/coelho97sc/Nano_Assistant/releases/download/")).toBe(true);
  });

  it("rejects malformed numbers and hashes", async () => {
    await expect(verifyFeed(feed({ versionCode: "42" }), KEYS)).rejects.toThrow("feed_format");
    await expect(verifyFeed(feed({ sha256: "xyz" }), KEYS)).rejects.toThrow("feed_format");
    await expect(verifyFeed(feed({ size: 0 }), KEYS)).rejects.toThrow("feed_format");
    await expect(verifyFeed("{", KEYS)).rejects.toThrow("feed_not_json");
  });

  it("checks a feed made by the PC's signing code the same way", async () => {
    // The PC module is plain CommonJS; reuse its signer so the two sides
    // can never disagree on the format.
    const { signFeed } = createRequire(import.meta.url)("../../electron/lib/update-feed.js");
    const text = signFeed({ product: "nano-android", channel: "test", version: "0.3.0-beta.1", versionCode: 7, url: APK, sha256: "b".repeat(64), size: 5 }, privateKey);
    expect((await verifyFeed(text, KEYS)).versionCode).toBe(7);
  });
});
