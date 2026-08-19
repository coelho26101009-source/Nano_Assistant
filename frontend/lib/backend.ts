/**
 * The single typed surface between the Nano UI and the Python backend.
 *
 * Everything the UI renders comes through here. There is no mock data and no
 * default-optimistic value: when the bridge is not up, callers get null and the
 * UI shows an explicit state rather than inventing one.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export type ReadinessPayload = {
  agent: { state: string; pending_permissions: number };
  voice: { state: string; blockers: string[]; enabled: boolean };
  wakeWord: { state: string; phrase?: string; modelStatus: string; error?: string };
  model: {
    state: string;
    local: { model: string; online: boolean; modelReady: boolean; enabled: boolean };
    cloud: { model: string; configured: boolean };
    provider: string;
  };
  worker: { state: string; running: boolean; queue_size: number; poll_interval: number };
  providers: Record<string, string>;
  emergencyStop: boolean;
  autonomyMode: string;
  browser: { state: string };
  vision: { state: string };
};

export type CommandCenterPayload = {
  worker: { running: boolean; queue_size: number; poll_interval: number };
  system: Record<string, number>;
  task_summary: Record<string, number>;
  current_task: any;
  tasks: any[];
  activities: { event: string; payload: Record<string, any>; timestamp: string }[];
  permissions: any[];
  agents: { agents: any[]; selected: any[] };
  health: Record<string, any>;
  emergency_stop: boolean;
  autonomy_mode: string;
};

function bridge(): any | null {
  if (typeof window === "undefined") return null;
  return (window as any).eel ?? null;
}

/** Promise wrapper over eel's callback style. */
export function call<T = any>(name: string, ...args: any[]): Promise<T | null> {
  const eel = bridge();
  if (!eel || typeof eel[name] !== "function") return Promise.resolve(null);
  return new Promise((resolve) => {
    let settled = false;
    const done = (value: T) => { if (!settled) { settled = true; resolve(value); } };
    try {
      eel[name](...args)(done);
    } catch {
      done(null as unknown as T);
    }
    // The bridge can drop a call if the socket closes mid-flight; never leave
    // the UI waiting forever on a promise that will not settle.
    window.setTimeout(() => { if (!settled) { settled = true; resolve(null); } }, 20000);
  });
}

export function expose(fn: (...args: any[]) => void, name: string) {
  const eel = bridge();
  if (!eel) return;
  try { eel.expose(fn, name); } catch { /* already exposed */ }
}

/** Resolves once the Eel runtime has attached, or gives up and reports offline. */
export function useBridgeReady() {
  const [ready, setReady] = useState(false);
  const [gaveUp, setGaveUp] = useState(false);

  useEffect(() => {
    if (bridge()) { setReady(true); return; }
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      if (bridge()) { window.clearInterval(timer); setReady(true); }
      else if (attempts > 100) { window.clearInterval(timer); setGaveUp(true); }
    }, 100);
    return () => window.clearInterval(timer);
  }, []);

  return { ready, gaveUp };
}

/** Poll a backend function on an interval, pausing when the tab is hidden. */
export function usePolled<T>(name: string, intervalMs: number, enabled: boolean) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    const value = await call<T>(name);
    if (!mounted.current) return;
    setData(value);
    setLoading(false);
  }, [name]);

  useEffect(() => {
    mounted.current = true;
    if (!enabled) return;
    refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, intervalMs);
    return () => { mounted.current = false; window.clearInterval(timer); };
  }, [enabled, intervalMs, refresh]);

  return { data, loading, refresh };
}
