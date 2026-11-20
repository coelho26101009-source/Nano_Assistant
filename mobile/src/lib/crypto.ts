/**
 * Sealing every message between the phone and the PC.
 *
 * Pairing (protocol 2) adds ECDH P-256 + HKDF-SHA256: see newPairingKeys,
 * openFromPc and pairingCode below.
 *
 * AES-256-GCM through WebCrypto, byte-for-byte compatible with
 * core/mobile_link.py on the PC: a 12-byte random nonce, base64url without
 * padding, and an "additional data" string that binds each message to its
 * purpose, device and request. A message sealed for one request cannot be
 * replayed as the answer to another, and nothing can be changed in transit
 * without the other side noticing. test/crypto.test.ts checks a vector the
 * Python tests also check.
 */

export type Envelope = { n: string; c: string };

const enc = new TextEncoder();
const dec = new TextDecoder();

export function b64e(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64d(text: string): Uint8Array {
  const clean = text.replace(/-/g, "+").replace(/_/g, "/");
  const padded = clean + "=".repeat((4 - (clean.length % 4)) % 4);
  const binary = atob(padded);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

export async function importKey(raw: Uint8Array): Promise<CryptoKey> {
  if (raw.length !== 32) throw new Error("bad_key");
  return crypto.subtle.importKey("raw", raw as BufferSource, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
}

export async function seal(key: CryptoKey, payload: unknown, aad: string): Promise<Envelope> {
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const data = enc.encode(JSON.stringify(payload));
  const cipher = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: nonce as BufferSource, additionalData: enc.encode(aad) as BufferSource }, key, data as BufferSource);
  return { n: b64e(nonce), c: b64e(new Uint8Array(cipher)) };
}

export async function openSealed<T = any>(key: CryptoKey, envelope: Envelope, aad: string): Promise<T> {
  if (!envelope || typeof envelope.n !== "string" || typeof envelope.c !== "string") {
    throw new Error("bad_reply");
  }
  let plain: ArrayBuffer;
  try {
    plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: b64d(envelope.n) as BufferSource, additionalData: enc.encode(aad) as BufferSource },
      key, b64d(envelope.c) as BufferSource);
  } catch {
    // Wrong key, tampered reply, or a reply to some other request.
    throw new Error("bad_reply");
  }
  return JSON.parse(dec.decode(plain)) as T;
}

/** The PC's sealed delivery of this phone's key: core/mobile_link.seal_to_phone. */
export type PhoneBox = { epk: string; n: string; c: string };

/**
 * A fresh P-256 key pair for ONE pairing. The private half never leaves this
 * phone and is not even exportable; the public half goes to the PC, which
 * seals the phone's long-term key to it. A photo of the QR code is therefore
 * no longer enough to receive that key (pairing protocol 2).
 */
export async function newPairingKeys(): Promise<{ privateKey: CryptoKey; publicRaw: Uint8Array }> {
  const keys = await crypto.subtle.generateKey(
    { name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]) as CryptoKeyPair;
  const publicRaw = new Uint8Array(await crypto.subtle.exportKey("raw", keys.publicKey));
  return { privateKey: keys.privateKey, publicRaw };
}

/** Open what the PC sealed to this phone's public key. Throws "bad_reply". */
export async function openFromPc<T = any>(
  privateKey: CryptoKey, salt: Uint8Array, context: string, box: PhoneBox,
): Promise<T> {
  if (!box || typeof box.epk !== "string") throw new Error("bad_reply");
  let aes: CryptoKey;
  try {
    const peer = await crypto.subtle.importKey(
      "raw", b64d(box.epk) as BufferSource, { name: "ECDH", namedCurve: "P-256" }, false, []);
    const shared = await crypto.subtle.deriveBits({ name: "ECDH", public: peer }, privateKey, 256);
    const material = await crypto.subtle.importKey("raw", shared, "HKDF", false, ["deriveKey"]);
    aes = await crypto.subtle.deriveKey(
      { name: "HKDF", hash: "SHA-256", salt: salt as BufferSource, info: enc.encode(context) as BufferSource },
      material, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  } catch {
    throw new Error("bad_reply");
  }
  return openSealed<T>(aes, { n: box.n, c: box.c }, context);
}

/**
 * The six digits both screens show (core/mobile_link.pairing_code): SHA-256 of
 * the session and this phone's public key. The person compares them; a phone
 * that slipped in first with a photo of the QR shows different digits.
 */
export async function pairingCode(session: string, phonePublicRaw: Uint8Array): Promise<string> {
  const prefix = enc.encode(`nano-pair-code-v2|${session}|`);
  const data = new Uint8Array(prefix.length + phonePublicRaw.length);
  data.set(prefix);
  data.set(phonePublicRaw, prefix.length);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", data as BufferSource));
  const value = ((digest[0] << 24) >>> 0) + (digest[1] << 16) + (digest[2] << 8) + digest[3];
  return String(value % 1_000_000).padStart(6, "0");
}

export function randomId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export const AAD = {
  pair: (session: string) => `nano-pair-v1|${session}`,
  pairReply: (session: string, id: string) => `nano-pair-reply-v1|${session}|${id}`,
  pairKey: (session: string, device: string) => `nano-pair-key-v2|${session}|${device}`,
  rpc: (device: string) => `nano-rpc-v1|${device}`,
  rpcReply: (device: string, id: string) => `nano-rpc-reply-v1|${device}|${id}`,
};
