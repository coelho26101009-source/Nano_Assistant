/**
 * POST a JSON body to the PC.
 *
 * On the phone this goes through Android's own HTTP stack (CapacitorHttp), so
 * the WebView's mixed-content and CORS rules do not apply; in a browser
 * preview it is an ordinary fetch. Either way the body is already sealed (see
 * crypto.ts): this layer never sees anything readable.
 */
import { Capacitor, CapacitorHttp } from "@capacitor/core";

export class HttpError extends Error {
  /** status 0 means the PC could not be reached at all. */
  constructor(public status: number, public code: string) { super(code || `http_${status}`); }
}

export async function httpPost(url: string, body: unknown, timeoutMs: number): Promise<unknown> {
  if (Capacitor.isNativePlatform()) {
    let response;
    try {
      response = await CapacitorHttp.post({
        url,
        headers: { "Content-Type": "application/json" },
        data: body,
        connectTimeout: Math.min(timeoutMs, 6000),
        readTimeout: timeoutMs,
        responseType: "json",
      });
    } catch {
      throw new HttpError(0, "unreachable");
    }
    let data: any = response.data;
    if (typeof data === "string") {
      try { data = JSON.parse(data); } catch { data = {}; }
    }
    if (response.status !== 200) throw new HttpError(response.status, data?.error || "");
    return data;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch {
    throw new HttpError(0, "unreachable");
  } finally {
    clearTimeout(timer);
  }
  let data: any = {};
  try { data = await response.json(); } catch { /* empty body */ }
  if (response.status !== 200) throw new HttpError(response.status, data?.error || "");
  return data;
}
