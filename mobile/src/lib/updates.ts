/**
 * Updates for the test app (the APK installed by hand from GitHub).
 *
 * HOW IT WORKS
 *   1. Read the signed update feed next to the test APK on GitHub.
 *   2. Check its signature with the project's public key (WebCrypto, ECDSA
 *      P-256), and that it is for this app and points at this repository.
 *   3. If its build number is higher than this app's, offer it.
 *   4. On "Update", the native side (NanoUpdaterPlugin.java) downloads the APK,
 *      checks its size and SHA-256 against the signed feed, checks it is this
 *      app signed with the same key, and opens Android's installer. Android
 *      always asks the person to confirm, and refuses an APK signed by anyone
 *      else -- a second, independent check.
 *
 * The Play Store version has no updater at all (Google Play updates it, and
 * its rules forbid apps updating themselves): there the native side reports
 * `supported: false` and nothing here runs.
 *
 * The same feed format as the PC (electron/lib/update-feed.js); keys in
 * updateKeys.ts, which a PC test keeps identical to the PC's list.
 */
import { Capacitor, CapacitorHttp, registerPlugin, type PluginListenerHandle } from "@capacitor/core";
import { b64d } from "./crypto";
import { getPref, setPref } from "./storage";
import { UPDATE_KEYS } from "./updateKeys";

export const FEED_URL = "https://github.com/coelho26101009-source/Nano_Assistant/releases/download/android-test/nano-update.json";
const REPO_PATH = "/coelho26101009-source/Nano_Assistant/releases/download/";
const MAX_SIZE = 300 * 1024 * 1024;
const AUTO_INTERVAL_MS = 12 * 60 * 60 * 1000;

export type UpdateOffer = {
  versionCode: number;
  version: string;
  url: string;
  sha256: string;
  size: number;
};

export type UpdateCheck =
  | { status: "unsupported" }
  | { status: "none"; current: number }
  | { status: "available"; current: number; offer: UpdateOffer }
  | { status: "error"; code: string };

type NanoUpdaterPlugin = {
  status(): Promise<{ supported: boolean; allowed: boolean; versionCode: number }>;
  openInstallSettings(): Promise<void>;
  downloadAndInstall(options: { url: string; sha256: string; size: number }): Promise<{ status: string }>;
  addListener(event: "updateProgress", handler: (data: { progress: number }) => void): Promise<PluginListenerHandle>;
};

const NanoUpdater = registerPlugin<NanoUpdaterPlugin>("NanoUpdater");

export function isAllowedDownloadUrl(value: unknown): boolean {
  let url: URL;
  try { url = new URL(String(value)); } catch { return false; }
  return url.protocol === "https:" && url.hostname === "github.com"
    && !url.username && !url.password && !url.port
    && url.pathname.startsWith(REPO_PATH) && !url.pathname.includes("..")
    && !url.search && !url.hash;
}

/**
 * Check a feed. Resolves with the payload; rejects with an Error whose
 * message is a short code (never the input).
 */
export async function verifyFeed(
  text: string, keys: ReadonlyArray<{ id: string; spki: string }> = UPDATE_KEYS,
): Promise<UpdateOffer & { product: string }> {
  let envelope: any;
  try { envelope = JSON.parse(text); } catch { throw new Error("feed_not_json"); }
  if (!envelope || envelope.format !== 1 || typeof envelope.payload !== "string"
      || typeof envelope.signature !== "string") throw new Error("feed_format");
  const entry = keys.find((k) => k.id === envelope.keyId);
  if (!entry) throw new Error("feed_unknown_key");

  let payloadBytes: Uint8Array;
  let valid = false;
  try {
    payloadBytes = b64d(envelope.payload);
    const key = await crypto.subtle.importKey(
      "spki", b64d(entry.spki) as BufferSource, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    valid = await crypto.subtle.verify(
      { name: "ECDSA", hash: "SHA-256" }, key, b64d(envelope.signature) as BufferSource, payloadBytes as BufferSource);
  } catch {
    valid = false;
  }
  if (!valid) throw new Error("feed_bad_signature");

  let payload: any;
  try { payload = JSON.parse(new TextDecoder().decode(payloadBytes!)); } catch { throw new Error("feed_format"); }
  if (!payload || payload.product !== "nano-android") throw new Error("feed_wrong_product");
  if (!isAllowedDownloadUrl(payload.url)) throw new Error("feed_bad_url");
  if (typeof payload.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(payload.sha256)) throw new Error("feed_format");
  if (!Number.isSafeInteger(payload.size) || payload.size <= 0 || payload.size > MAX_SIZE) throw new Error("feed_format");
  if (!Number.isSafeInteger(payload.versionCode) || payload.versionCode <= 0) throw new Error("feed_format");
  if (typeof payload.version !== "string") throw new Error("feed_format");
  return payload;
}

/** Whether this build can update itself (the test APK, on a phone). */
async function nativeStatus() {
  if (!Capacitor.isNativePlatform()) return null;
  try {
    const status = await NanoUpdater.status();
    return status.supported ? status : null;
  } catch {
    return null; // an app built before the updater existed
  }
}

export async function checkForUpdate(): Promise<UpdateCheck> {
  const status = await nativeStatus();
  if (!status) return { status: "unsupported" };
  let response;
  try {
    response = await CapacitorHttp.get({ url: FEED_URL, responseType: "text", connectTimeout: 10000, readTimeout: 15000 });
  } catch {
    return { status: "error", code: "offline" };
  }
  await setPref("update.lastCheck", String(Date.now()));
  if (response.status === 404) return { status: "none", current: status.versionCode };
  if (response.status !== 200) return { status: "error", code: `feed_http_${response.status}` };
  const text = typeof response.data === "string" ? response.data : JSON.stringify(response.data);
  let offer;
  try {
    offer = await verifyFeed(text);
  } catch (error) {
    return { status: "error", code: error instanceof Error ? error.message : "feed_format" };
  }
  if (offer.versionCode <= status.versionCode) return { status: "none", current: status.versionCode };
  return { status: "available", current: status.versionCode, offer };
}

/** Automatic checks: on unless switched off, at most every 12 hours. */
export async function autoCheckEnabled(): Promise<boolean> {
  return (await getPref("update.auto")) !== "off";
}

export async function setAutoCheck(enabled: boolean): Promise<void> {
  await setPref("update.auto", enabled ? null : "off");
}

export async function autoCheckDue(now = Date.now()): Promise<boolean> {
  if (!(await autoCheckEnabled())) return false;
  const last = Number(await getPref("update.lastCheck")) || 0;
  return now - last >= AUTO_INTERVAL_MS;
}

/**
 * Download, verify and hand over to Android's installer.
 * "needs_permission": Android's "install unknown apps" switch for NANO is off;
 * its settings page has been opened, and the person can try again after.
 */
export async function installUpdate(
  offer: UpdateOffer, onProgress?: (progress: number) => void,
): Promise<"installer_opened" | "needs_permission"> {
  const status = await nativeStatus();
  if (!status) throw new Error("not_supported");
  if (!status.allowed) {
    await NanoUpdater.openInstallSettings();
    return "needs_permission";
  }
  const listener = onProgress
    ? await NanoUpdater.addListener("updateProgress", (data) => onProgress(Number(data.progress) || 0))
    : null;
  try {
    const result = await NanoUpdater.downloadAndInstall({ url: offer.url, sha256: offer.sha256, size: offer.size });
    return result.status === "needs_permission" ? "needs_permission" : "installer_opened";
  } catch (error: any) {
    throw new Error(typeof error?.code === "string" && error.code ? error.code : "download_failed");
  } finally {
    await listener?.remove();
  }
}
