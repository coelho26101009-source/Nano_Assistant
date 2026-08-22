/**
 * The UI's view of the Electron desktop shell.
 *
 * CAPABILITY DETECTION, NOT ENVIRONMENT SNIFFING. Nothing here asks "am I in
 * Electron?" by inspecting the user agent. It asks whether the specific
 * function it is about to call exists, and does nothing if it does not. That is
 * what lets the identical bundle run in a browser during development: the
 * native controls simply are not rendered, and every call below becomes a
 * no-op instead of a TypeError that blanks the page.
 *
 * The preload (electron/preload.js) exposes a small, frozen, named API. This
 * file is its typed mirror; if the two ever disagree, the guards here mean the
 * UI degrades rather than crashes.
 */
import { useCallback, useEffect, useState } from "react";

export type WindowState = {
  maximized: boolean;
  fullScreen: boolean;
  focused: boolean;
  platform: string;
};

export type AutoLaunchState = {
  enabled: boolean;
  /** False in development, where a login item would point at electron.exe. */
  supported: boolean;
  reason: string | null;
};

export type DesktopStatus = {
  isDesktop: true;
  version: string;
  /** The accelerator as registered, already human-readable ("Ctrl + Shift + Space"). */
  shortcut: string;
  /** Measured with globalShortcut.isRegistered — never assumed. */
  shortcutRegistered: boolean;
  shortcutError: string | null;
  overlayEnabled: boolean;
  autoLaunch: AutoLaunchState;
  dataDir: string;
  packaged: boolean;
};

type NanoAppApi = {
  isDesktop: true;
  minimize: () => void;
  toggleMaximize: () => void;
  hide: () => void;
  quit: () => Promise<boolean>;
  getWindowState: () => Promise<WindowState>;
  onWindowState: (handler: (state: WindowState) => void) => () => void;
  getDesktopStatus: () => Promise<DesktopStatus>;
  retryShortcut: () => Promise<{ registered: boolean; error: string | null }>;
  setOverlayEnabled: (enabled: boolean) => Promise<boolean>;
  setAutoLaunch: (enabled: boolean) => Promise<AutoLaunchState>;
};

/** The shell's API, or null in a plain browser. Never throws. */
export function desktop(): NanoAppApi | null {
  if (typeof window === "undefined") return null;
  const api = (window as any).nanoApp;
  return api && api.isDesktop === true ? (api as NanoAppApi) : null;
}

/**
 * Whether the desktop shell is present.
 *
 * Resolved in an effect rather than during render: the server-rendered export
 * and the first client render must agree, or React logs a hydration mismatch
 * and the title bar flickers in.
 */
export function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(false);
  useEffect(() => { setIsDesktop(desktop() !== null); }, []);
  return isDesktop;
}

/** Live maximised/focused state, so the title bar draws the right glyph. */
export function useWindowState(): WindowState | null {
  const [state, setState] = useState<WindowState | null>(null);

  useEffect(() => {
    const api = desktop();
    if (!api) return;
    let active = true;
    api.getWindowState().then((value) => { if (active) setState(value); }).catch(() => {});
    const unsubscribe = api.onWindowState((value) => { if (active) setState(value); });
    return () => { active = false; unsubscribe(); };
  }, []);

  return state;
}

/**
 * Desktop/activation status for the Settings page.
 *
 * Everything is measured by the main process. When there is no shell this
 * stays null and the UI says so, rather than showing a key combination that
 * does nothing.
 */
export function useDesktopStatus() {
  const [status, setStatus] = useState<DesktopStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const api = desktop();
    if (!api) { setStatus(null); setLoading(false); return; }
    try {
      setStatus(await api.getDesktopStatus());
    } catch {
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return { status, loading, refresh };
}

/* ── Actions. Each one is a no-op outside the desktop shell. ─────────────── */

export const minimizeWindow = () => desktop()?.minimize();
export const toggleMaximizeWindow = () => desktop()?.toggleMaximize();
/** The close button hides to the tray so the global shortcut keeps working. */
export const hideWindow = () => desktop()?.hide();
export const quitNano = () => desktop()?.quit();
export const retryShortcut = () => desktop()?.retryShortcut() ?? Promise.resolve(null);
export const setOverlayEnabled = (enabled: boolean) =>
  desktop()?.setOverlayEnabled(enabled) ?? Promise.resolve(null);
export const setAutoLaunch = (enabled: boolean) =>
  desktop()?.setAutoLaunch(enabled) ?? Promise.resolve(null);
