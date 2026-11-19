/**
 * Where the phone keeps what it knows.
 *
 * The pairing -- above all the 256-bit key that opens the PC -- goes to the
 * Android Keystore through SecureStorage, never to plain preferences. Ordinary
 * choices (language, voice) go to Preferences. Android backup is turned off in
 * the manifest, so neither leaves the phone. In a browser preview, where there
 * is no Keystore, both fall back to localStorage: that is a developer tool,
 * not a place a real key ever lives.
 */
import { Capacitor } from "@capacitor/core";
import { Preferences } from "@capacitor/preferences";
import { SecureStorage } from "@aparajita/capacitor-secure-storage";

export type Pairing = {
  deviceId: string;
  key: string;
  pcName: string;
  hosts: string[];
  port: number;
  extraHosts: string[];
  pairedAt: number;
};

const PAIRING_KEY = "nano.pairing.v1";
const native = () => Capacitor.isNativePlatform();

function isPairing(value: any): value is Pairing {
  return value && typeof value.deviceId === "string" && typeof value.key === "string"
    && Array.isArray(value.hosts) && typeof value.port === "number";
}

export async function loadPairing(): Promise<Pairing | null> {
  try {
    let raw: string | null;
    if (native()) {
      raw = await SecureStorage.getItem(PAIRING_KEY);
    } else {
      raw = window.localStorage.getItem(PAIRING_KEY);
    }
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!isPairing(parsed)) return null;
    return { extraHosts: [], pcName: "NANO PC", pairedAt: 0, ...parsed };
  } catch {
    return null;
  }
}

export async function savePairing(pairing: Pairing): Promise<void> {
  const raw = JSON.stringify(pairing);
  if (native()) await SecureStorage.setItem(PAIRING_KEY, raw);
  else window.localStorage.setItem(PAIRING_KEY, raw);
}

export async function clearPairing(): Promise<void> {
  try {
    if (native()) await SecureStorage.removeItem(PAIRING_KEY);
    else window.localStorage.removeItem(PAIRING_KEY);
  } catch { /* nothing stored */ }
}

export async function getPref(key: string): Promise<string | null> {
  try {
    if (native()) return (await Preferences.get({ key: `nano.${key}` })).value;
    return window.localStorage.getItem(`nano.${key}`);
  } catch {
    return null;
  }
}

export async function setPref(key: string, value: string | null): Promise<void> {
  try {
    if (native()) {
      if (value === null) await Preferences.remove({ key: `nano.${key}` });
      else await Preferences.set({ key: `nano.${key}`, value });
    } else if (value === null) window.localStorage.removeItem(`nano.${key}`);
    else window.localStorage.setItem(`nano.${key}`, value);
  } catch { /* preferences are a convenience */ }
}
