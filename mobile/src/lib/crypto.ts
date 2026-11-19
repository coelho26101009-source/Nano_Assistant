/**
 * Sealing every message between the phone and the PC.
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

export function randomId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export const AAD = {
  pair: (session: string) => `nano-pair-v1|${session}`,
  pairReply: (session: string, id: string) => `nano-pair-reply-v1|${session}|${id}`,
  rpc: (device: string) => `nano-rpc-v1|${device}`,
  rpcReply: (device: string, id: string) => `nano-rpc-reply-v1|${device}|${id}`,
};
