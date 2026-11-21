/**
 * The phone's camera, for one still photo at a time.
 *
 * WHY THE WEBVIEW'S CAMERA (getUserMedia) AND NOT A CAMERA APP
 * The picture stays inside NANO: a live preview in our own screen, with the
 * red "Câmara ligada" sign over it, and one photo taken by our own shutter.
 * Nothing is written to the gallery or to any file; the photo lives in memory
 * until it is sent (sealed, see crypto.ts) to the PC, and is then dropped.
 * Android asks for the camera permission the first time (and shows its own
 * green camera dot while it is on).
 *
 * The camera is open only while CameraSheet is on screen. Every way out of it
 * (photo taken, cancel, back button, app sent to the background) stops every
 * track, which turns the camera off.
 */

/** The longest side of a photo sent to the PC. Vision models look at about
 *  this much anyway, and it is enough to read a label. */
export const MAX_SIDE = 1024;
/** The largest photo sent, as bytes of JPEG. Sealed, it is base64 twice over
 *  (about 1.8x), and one request may be 256 KB at most, on the Wi-Fi and
 *  through NANO's relay away from home (core/mobile_server.py). */
export const MAX_PHOTO_BYTES = 120 * 1024;

export type Photo = { mime: "image/jpeg"; data: string; width: number; height: number };

/** Scale (w, h) down so the longest side is at most `max`. Never scales up. */
export function fitSize(width: number, height: number, max = MAX_SIDE): { width: number; height: number } {
  if (!(width > 0) || !(height > 0)) return { width: 0, height: 0 };
  const scale = Math.min(1, max / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

/** "data:image/jpeg;base64,AAAA" -> "AAAA" (or "" when it is not a JPEG data URL). */
export function jpegBase64(dataUrl: string): string {
  const prefix = "data:image/jpeg;base64,";
  return dataUrl.startsWith(prefix) ? dataUrl.slice(prefix.length) : "";
}

/** Bytes a base64 string decodes to. */
export function base64Bytes(data: string): number {
  const padding = data.endsWith("==") ? 2 : data.endsWith("=") ? 1 : 0;
  return Math.max(0, Math.floor((data.length * 3) / 4) - padding);
}

export function cameraSupported(): boolean {
  return typeof navigator !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia);
}

/** Open the camera ("environment" = the back one). Throws Error("camera_denied") or Error("camera_unavailable"). */
export async function openCamera(facing: "environment" | "user" = "environment"): Promise<MediaStream> {
  if (!cameraSupported()) throw new Error("camera_unavailable");
  try {
    return await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: facing }, width: { ideal: 1920 }, height: { ideal: 1080 } },
    });
  } catch (error) {
    const name = (error as { name?: string })?.name ?? "";
    throw new Error(name === "NotAllowedError" || name === "SecurityError" ? "camera_denied" : "camera_unavailable");
  }
}

/** Turn the camera off. Safe to call more than once. */
export function closeCamera(stream: MediaStream | null | undefined): void {
  stream?.getTracks().forEach((track) => { try { track.stop(); } catch { /* already stopped */ } });
}

/** One still from the live preview, as a JPEG that fits MAX_SIDE and MAX_PHOTO_BYTES. */
export function capture(video: HTMLVideoElement): Photo | null {
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (!context) return null;
  // Smaller sides only when the larger one cannot fit MAX_PHOTO_BYTES.
  for (const side of [MAX_SIDE, 768, 512]) {
    const size = fitSize(video.videoWidth, video.videoHeight, side);
    if (!size.width) return null;
    canvas.width = size.width;
    canvas.height = size.height;
    context.drawImage(video, 0, 0, size.width, size.height);
    for (const quality of [0.8, 0.68, 0.55, 0.42]) {
      const data = jpegBase64(canvas.toDataURL("image/jpeg", quality));
      if (data && base64Bytes(data) <= MAX_PHOTO_BYTES) {
        canvas.width = canvas.height = 0;
        return { mime: "image/jpeg", data, width: size.width, height: size.height };
      }
    }
  }
  // The canvas is dropped with this function; nothing else holds the pixels.
  canvas.width = canvas.height = 0;
  return null;
}
