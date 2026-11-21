/**
 * The phone's own abilities, behind small functions that degrade quietly in a
 * browser preview: speech-to-text, text-to-speech, reminders as local
 * notifications, the QR scanner, a light vibration.
 */
import { Capacitor, type PluginListenerHandle } from "@capacitor/core";
import { Haptics, ImpactStyle } from "@capacitor/haptics";
import { LocalNotifications } from "@capacitor/local-notifications";
import { SpeechRecognition } from "@capacitor-community/speech-recognition";
import { TextToSpeech } from "@capacitor-community/text-to-speech";
import { BarcodeScanner, BarcodeFormat } from "@capacitor-mlkit/barcode-scanning";
import { speechTag, t } from "./i18n";

export const isNative = () => Capacitor.isNativePlatform();

/** "Pixel 8", "SM-S921B"… read from the WebView's user agent; "Android phone" otherwise. */
export function deviceName(): string {
  const ua = typeof navigator !== "undefined" ? navigator.userAgent : "";
  const match = ua.match(/Android [\d.]+; ([^;)]+?)(?: Build|\))/);
  const model = match?.[1]?.trim();
  if (model && model !== "K" && model.length <= 40) return model;
  return isNative() ? t("Telemóvel Android") : t("Navegador");
}

export async function tap() {
  if (!isNative()) return;
  try { await Haptics.impact({ style: ImpactStyle.Light }); } catch { /* no vibrator */ }
}

// ── speech to text ─────────────────────────────────────────────────────────

export async function speechAvailable(): Promise<boolean> {
  if (!isNative()) return false;
  try { return (await SpeechRecognition.available()).available; } catch { return false; }
}

export type Listening = { stop: () => Promise<string> };

/**
 * Start listening. `onPartial` gets the words as they are recognised; the
 * promise of `stop()` gives the final text. Android ends a turn on its own
 * after a pause, and then `onEnd` fires with the text.
 */
export async function listen(onPartial: (text: string) => void, onEnd: (text: string) => void): Promise<Listening | null> {
  if (!isNative()) return null;
  const permission = await SpeechRecognition.requestPermissions();
  if (permission.speechRecognition !== "granted") throw new Error("microphone_denied");
  let latest = "";
  let ended = false;
  const handles: PluginListenerHandle[] = [];
  const finish = async () => {
    if (ended) return latest;
    ended = true;
    for (const handle of handles) await handle.remove();
    return latest;
  };
  handles.push(await SpeechRecognition.addListener("partialResults", (data) => {
    latest = (data.matches?.[0] ?? latest).trim();
    onPartial(latest);
  }));
  handles.push(await SpeechRecognition.addListener("listeningState", (data) => {
    if (data.status === "stopped") void finish().then(onEnd);
  }));
  await SpeechRecognition.start({ language: speechTag(), maxResults: 1, partialResults: true, popup: false });
  return {
    stop: async () => {
      try { await SpeechRecognition.stop(); } catch { /* already stopped */ }
      return finish();
    },
  };
}

// ── text to speech ─────────────────────────────────────────────────────────

/** What an answer sounds like: no markdown, code, links, [n] citations or the
 *  list of sources under a research report. */
export function spokenText(text: string): string {
  return text
    .replace(/(?:^|\n)\*\*(?:Fontes|Sources)(?::[^\n]*)?\*\*\n[\s\S]*$/, "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/[*_#>|~]+/g, " ")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/https?:\/\/\S+/g, " ")
    .replace(/\s?\[\d{1,2}(?:\s*[,;–-]\s*\d{1,2})*\]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 3000);
}

/** Speak an answer. Markdown marks and code are left out of what is said. */
export async function speak(text: string) {
  const spoken = spokenText(text);
  if (!spoken) return;
  if (isNative()) {
    try { await TextToSpeech.speak({ text: spoken, lang: speechTag(), rate: 1.0, pitch: 1.0, volume: 1.0 }); } catch { /* no engine */ }
    return;
  }
  if ("speechSynthesis" in window) {
    const utterance = new SpeechSynthesisUtterance(spoken);
    utterance.lang = speechTag();
    window.speechSynthesis.speak(utterance);
  }
}

export async function stopSpeaking() {
  if (isNative()) { try { await TextToSpeech.stop(); } catch { /* idle */ } return; }
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
}

// ── notifications ──────────────────────────────────────────────────────────

const REMINDER_CHANNEL = "reminders";
const NOTICE_CHANNEL = "notices";

export async function notificationsAllowed(): Promise<boolean> {
  if (!isNative()) return false;
  try { return (await LocalNotifications.checkPermissions()).display === "granted"; } catch { return false; }
}

export async function askNotifications(): Promise<boolean> {
  if (!isNative()) return false;
  try {
    const result = await LocalNotifications.requestPermissions();
    if (result.display !== "granted") return false;
    await LocalNotifications.createChannel({
      id: REMINDER_CHANNEL, name: t("Lembretes"), description: t("Os teus lembretes do NANO"),
      importance: 5, visibility: 0, vibration: true,
    });
    await LocalNotifications.createChannel({
      id: NOTICE_CHANNEL, name: t("Avisos do NANO"), description: t("Quando o NANO te quer dizer alguma coisa"),
      importance: 3, visibility: 0,
    });
    return true;
  } catch {
    return false;
  }
}

export type PhoneReminder = { id: number; text: string; at: Date };

/** Reminder ids from the PC are kept under 1,000,000; notices use the range above. */
const NOTICE_BASE = 1_000_000;

/**
 * Make the phone's scheduled reminders match the PC's list: schedule the new
 * ones, cancel the ones that are gone. Runs after every sync, so a reminder
 * rings on the phone even when the app is closed or the PC is off.
 */
export async function syncReminders(reminders: PhoneReminder[]): Promise<number> {
  if (!isNative() || !(await notificationsAllowed())) return 0;
  const now = Date.now();
  const wanted = reminders.filter((r) => r.at.getTime() > now + 5000 && r.id > 0 && r.id < NOTICE_BASE).slice(0, 60);
  const pending = await LocalNotifications.getPending();
  const ours = pending.notifications.filter((n) => n.id < NOTICE_BASE);
  const wantedIds = new Set(wanted.map((r) => r.id));
  const stale = ours.filter((n) => !wantedIds.has(n.id));
  if (stale.length) await LocalNotifications.cancel({ notifications: stale.map((n) => ({ id: n.id })) });
  if (wanted.length) {
    await LocalNotifications.schedule({
      notifications: wanted.map((r) => ({
        id: r.id,
        title: t("Lembrete"),
        body: r.text,
        schedule: { at: r.at, allowWhileIdle: true },
        channelId: REMINDER_CHANNEL,
        smallIcon: "ic_stat_nano",
      })),
    });
  }
  return wanted.length;
}

let noticeId = NOTICE_BASE;

/** Show something NANO said while the app is in the background. */
export async function notify(title: string, body: string) {
  if (!isNative() || !(await notificationsAllowed())) return;
  noticeId = noticeId >= NOTICE_BASE + 100_000 ? NOTICE_BASE : noticeId + 1;
  await LocalNotifications.schedule({
    notifications: [{ id: noticeId, title, body: body.slice(0, 400), channelId: NOTICE_CHANNEL, smallIcon: "ic_stat_nano" }],
  });
}

// ── QR scanner ─────────────────────────────────────────────────────────────

/**
 * Scan a QR code with Google's code scanner. It runs in its own screen and
 * hands back only the code it read: the app never gets the camera, so it asks
 * for no camera permission.
 */
export async function scanQr(): Promise<string | null> {
  if (!isNative()) return null;
  const { available } = await BarcodeScanner.isGoogleBarcodeScannerModuleAvailable();
  if (!available) {
    await new Promise<void>((resolve, reject) => {
      let handle: PluginListenerHandle | null = null;
      const timer = setTimeout(() => { void handle?.remove(); reject(new Error("scanner_install_timeout")); }, 60000);
      void BarcodeScanner.addListener("googleBarcodeScannerModuleInstallProgress", (event) => {
        if (event.state === 4 /* COMPLETED */) { clearTimeout(timer); void handle?.remove(); resolve(); }
        if (event.state === 5 /* FAILED */ || event.state === 3 /* CANCELED */) {
          clearTimeout(timer); void handle?.remove(); reject(new Error("scanner_install_failed"));
        }
      }).then((h) => { handle = h; });
      void BarcodeScanner.installGoogleBarcodeScannerModule().catch(reject);
    });
  }
  const { barcodes } = await BarcodeScanner.scan({ formats: [BarcodeFormat.QrCode] });
  return barcodes[0]?.rawValue ?? null;
}
