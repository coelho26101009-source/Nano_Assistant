/**
 * The single typed surface between the Nano UI and the Python backend.
 *
 * Everything the UI renders comes through here. There is no mock data and no
 * default-optimistic value: when the bridge is down, callers get null and the
 * UI shows an explicit state rather than inventing one.
 *
 * POLLING BUDGET (this matters — aggressive polling caused real bugs)
 * Readiness used to be polled every 3 s, and each poll enumerated audio devices
 * and probed Ollama. That raced the wake-phrase thread over PortAudio and
 * crashed the process. Intervals here are deliberately conservative, the page
 * pauses polling when hidden, and expensive calls are shared through one
 * subscription rather than duplicated per component.
 */
import { useCallback, useEffect, useRef, useState } from "react";

/* ── Payload types ────────────────────────────────────────────────────── */

export type SecretInfo = {
  configured: boolean;
  masked: string;
  source: "none" | "environment" | "encrypted_store";
  encrypted: boolean;
};

/** Every provider Nano can route to. Matches core.providers.ProviderId. */
export type ProviderKey = "google" | "groq" | "mistral" | "ollama";

/** The cloud providers only — the ones a preference can point at. */
export type CloudProviderKey = "google" | "groq" | "mistral";

/** One model as the ACCOUNT reports it. Discovered, never hardcoded.
 *  Fields a given provider does not publish are simply absent, and the UI
 *  renders nothing for them rather than guessing a capability. */
export type ModelRecord = {
  id: string;
  display_name: string;
  description?: string;
  input_tokens?: number | null;
  output_tokens?: number | null;
  streaming?: boolean;
  tool_calling?: boolean;
  thinking?: boolean;
  vision?: boolean;
  deprecated?: boolean;
};

export type ProviderInfo = {
  id: ProviderKey;
  name: string;
  kind: "cloud" | "local";
  role: string;
  state: string;
  model: string;
  models: string[];
  /** Cloud providers that publish per-model metadata (Google and Mistral). */
  records?: ModelRecord[];
  secret: SecretInfo;
  detail: string;
  url?: string;
  /** Cloud only: the conversation model and the complex-work model. */
  tiers?: { fast: string; complex: string };
  /** Cloud only: whether each configured tier actually exists on the account. */
  tiers_ok?: { fast: boolean; complex: boolean };
  /** Live circuit-breaker state, read from memory rather than re-probed. */
  temporarily_limited?: boolean;
  retry_in_seconds?: number | null;
};

export type CooldownInfo = {
  provider: string;
  temporarily_limited: boolean;
  retry_in_seconds: number | null;
  consecutive_failures: number;
  failure_type?: string;
};

export type ProviderPayload = {
  mode: "AUTO" | "CLOUD" | "LOCAL";
  modes: string[];
  /** WHICH cloud provider AUTO/CLOUD use first. Mode and provider are separate
   *  questions, and this is the single canonical answer to the second one —
   *  the header pill and Settings both render it rather than keeping copies. */
  preferredCloud: CloudProviderKey;
  cloudProviders: CloudProviderKey[];
  google: ProviderInfo;
  groq: ProviderInfo;
  mistral: ProviderInfo;
  ollama: ProviderInfo;
  /** The Groq breaker, kept for panels that already read it. */
  cooldown?: CooldownInfo;
  cooldowns?: Record<string, CooldownInfo>;
  route: {
    provider: ProviderKey | "none";
    model: string;
    usable: boolean;
    fallback: boolean;
    mode: string;
    reason: string;
    /** Other cloud providers that were ready when the decision was made, in
     *  the order this turn would try them. */
    alternatives?: CloudProviderKey[];
  };
};

export type ReadinessPayload = {
  agent: { state: string; pending_permissions: number };
  voice: { state: string; blockers: string[]; enabled: boolean };
  wakeWord: { state: string; phrase?: string; modelStatus: string; error?: string };
  wakePhrase: {
    state: string; turnState?: string; phrase?: string;
    allowNanoOnly?: boolean; cooldownSeconds?: number; error?: string;
  };
  model: {
    state: string; detail?: string; installed?: string[];
    local: { model: string; online: boolean; modelReady: boolean; enabled: boolean; url?: string };
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

export type TaskRow = {
  id: string; title: string; status: string; progress: number;
  task_type: string; priority: number; retries: number;
  created_at: string; started_at?: string; finished_at?: string;
  updated_at: string; last_event?: string; error?: string;
  has_result?: boolean; task_kind?: string;
};

export type TaskCounts = {
  active: number; attention: number; badge: number; total: number;
  byStatus: Record<string, number>;
};

/**
 * The PC page's read-only picture of the machine.
 *
 * Every section is nullable ON PURPOSE. The backend probes each one
 * independently and reports null when it could not read it, so the UI can say
 * "não consegui ler" instead of rendering a plausible zero. A volume of 0 and
 * an unknown volume are different facts.
 */
export type PcSnapshot = {
  platform: "windows" | "unsupported";
  system: {
    os: string; cpu?: string | null; cpu_percent: number;
    ram_used_gb: number; ram_total_gb: number; ram_percent: number;
    uptime_hours?: number; gpu?: string;
  } | null;
  volume: { level: number; muted: boolean } | null;
  storage: { free_gb: number; total_gb: number; count: number } | null;
  network: { connected: boolean | null; connection_type: string | null } | null;
  monitors: { number: number; primary: boolean; width: number; height: number }[] | null;
  activeWindow: { window_id: number; title: string; process: string | null; state: string } | null;
  windowCount: number | null;
  applications: { process: string; windows: number }[] | null;
  recentActions: {
    action: string; target: string; capability: string;
    decision: string; risk: string; at: string;
  }[];
  unavailable?: Record<string, string>;
};

export type ActivityEvent = {
  event: string;
  payload: Record<string, any>;
  timestamp: string;
};

/**
 * One row of PC -> Atividade: a real, already-authorised action Nano took on
 * this computer, read from the permission audit trail.
 *
 * Deliberately narrower than ActivityEvent above. This is scoped to `pc.*`
 * capabilities only, so it can never show a task's lifecycle -- that is
 * Tarefas' own data -- and every field is already redacted server-side:
 * `target` never carries clipboard contents, typed text, or a raw argument
 * blob, because those were never written to the audit trail in the first
 * place.
 */
export type PcActivityEntry = {
  action: string;
  target: string;
  capability: string;
  decision: "executed" | "allow_once" | "deny" | "failed";
  risk: string;
  requiresConfirmation: boolean;
  at: string;
};

export type PcActivityCategory = "all" | "acoes" | "permissoes" | "erros";

export type CommandCenterPayload = {
  worker: { running: boolean; queue_size: number; poll_interval: number };
  system: Record<string, number>;
  task_summary: Record<string, number>;
  current_task: TaskRow | null;
  tasks: TaskRow[];
  activities: ActivityEvent[];
  permissions: any[];
  agents: { agents: any[]; selected: any[] };
  health: Record<string, any>;
  emergency_stop: boolean;
  autonomy_mode: string;
};

/** Live wake/microphone numbers. Cheap enough to poll once a second. */
export type VoiceDiagnostics = {
  state?: string;
  turnState?: string;
  explain?: string;
  phrase?: string;
  error?: string | null;
  lastTranscript?: string | null;
  recentTranscripts?: string[];
  audio?: Record<string, number> | null;
  voiceTurn?: { active: boolean; source: string | null; phase: string; elapsed_seconds: number | null };
  counters?: {
    chunksCaptured?: number; silentChunks?: number; speechChunks?: number;
    transcriptsSeen?: number; wakeMatches?: number;
  };
};

export type SettingsPayload = {
  providers: ProviderPayload;
  voice: {
    enabled: boolean; ttsEnabled: boolean; wakePhrase: string;
    wakePhraseEnabled: boolean; allowNanoOnly: boolean;
    cooldownSeconds: number; commandTimeoutSeconds: number; state: string;
    // Typing and talking are separate conversations.
    typedChatTts: boolean; voiceReplyTts: boolean;
    // Honest microphone diagnostics: what Nano is really hearing.
    explain?: string;
    lastTranscript?: string | null;
    recentTranscripts?: string[];
    audio?: {
      calibrated?: boolean; noise_floor?: number; threshold?: number;
      last_rms?: number; peak_rms?: number;
      chunks_seen?: number; speech_chunks?: number; silent_chunks?: number;
    };
    counters?: {
      chunksCaptured?: number; silentChunks?: number; speechChunks?: number;
      transcriptsSeen?: number; wakeMatches?: number;
    };
  };
  devices: { inputs: { id: number; name: string }[]; outputs: { id: number; name: string }[]; error?: string };
  security: {
    autonomyMode: string; emergencyStop: boolean;
    persistentAllowDisabled: boolean; secretsEncrypted: boolean;
  };
  /** Memory behaviour, read from the Brain rather than from the config file, so
   *  the switch shows what the running conversation actually does. */
  memory: {
    factsEnabled: boolean;
    ragEnabled: boolean;
    /** Whether the SQLite build has FTS5. False means retrieval has degraded to
     *  simple text matching -- the UI says which, and never claims the better
     *  one. It used to be hardcoded false with a note blaming a missing
     *  chromadb, which hid a feature that had always worked. */
    ragSupported: boolean;
    ragNote: string;
    /** Whether anything may be carried between conversations at all. */
    longTermEnabled: boolean;
    /** Whether Nano may PROPOSE memories on its own. */
    captureEnabled: boolean;
    ready: boolean;
    retrieval: { mode: string; engine: string; entries: number; byKind: Record<string, number> };
    stats: Record<string, any>;
    knowledge: Record<string, any>;
  };
  stored: Record<string, any>;
  runtime: Record<string, any>;
};

/* ── Bridge ───────────────────────────────────────────────────────────── */

function bridge(): any | null {
  if (typeof window === "undefined") return null;
  return (window as any).eel ?? null;
}

/** Promise wrapper over eel's callback style, with a settle guarantee. */
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
    // The socket can drop a call mid-flight; never leave the UI awaiting a
    // promise that will not settle.
    window.setTimeout(() => { if (!settled) { settled = true; resolve(null); } }, 25000);
  });
}

/**
 * Register a handler for a Python -> UI callback.
 *
 * The actual registration lives in /public/nano_bridge.js and must stay there:
 * eel text-scans served JS for its registration token, which the production
 * minifier destroys if the call sits inside this bundle.
 */
export function expose(fn: (...args: any[]) => void, name: string) {
  if (typeof window === "undefined") return;
  const w = window as any;
  w.__nanoHandlers = w.__nanoHandlers || {};
  w.__nanoHandlers[name] = fn;

  const pending: any[][] | undefined = w.__nanoPending?.[name];
  if (pending?.length) {
    w.__nanoPending[name] = [];
    for (const args of pending) {
      try { fn(...args); } catch (err) { console.error("[nano] replay failed:", name, err); }
    }
  }
}

export function bridgeCallbacksReady(): boolean {
  if (typeof window === "undefined") return false;
  return Boolean((window as any).__nanoHandlers);
}

export function useBridgeReady() {
  const [ready, setReady] = useState(false);
  const [gaveUp, setGaveUp] = useState(false);

  useEffect(() => {
    if (bridge()) { setReady(true); return; }
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      if (bridge()) { window.clearInterval(timer); setReady(true); }
      else if (attempts > 120) { window.clearInterval(timer); setGaveUp(true); }
    }, 100);
    return () => window.clearInterval(timer);
  }, []);

  return { ready, gaveUp };
}

/* ── Polling ──────────────────────────────────────────────────────────── */

/** Deliberate intervals. Readiness probes Ollama, so it stays slow. */
export const POLL = {
  commandCenter: 4000,
  readiness: 10000,
  taskCounts: 8000,
  page: 6000,
  /**
   * Live microphone levels only. Fast BECAUSE it is cheap: get_voice_diagnostics
   * reads in-memory counters and touches no network, database or audio device.
   * Never point a 1 s poll at get_settings() again -- that endpoint describes
   * both providers, and describing Groq is a blocking HTTPS request.
   */
  voiceDiagnostics: 1000,
} as const;

/**
 * Poll one backend function. Skips ticks while the tab is hidden and never
 * overlaps requests, so a slow backend cannot queue up work.
 */
export function usePolled<T>(name: string, intervalMs: number, enabled: boolean, ...args: any[]) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const argsKey = JSON.stringify(args);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const value = await call<T>(name, ...JSON.parse(argsKey));
      if (!mounted.current) return;
      setData(value);
      setError(value === null ? "Sem resposta do motor do Nano." : null);
    } finally {
      inFlight.current = false;
      if (mounted.current) setLoading(false);
    }
  }, [name, argsKey]);

  useEffect(() => {
    mounted.current = true;
    if (!enabled) return () => { mounted.current = false; };
    refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, intervalMs);
    return () => { mounted.current = false; window.clearInterval(timer); };
  }, [enabled, intervalMs, refresh]);

  return { data, loading, error, refresh };
}

/** One-shot fetch that reloads when `deps` change. For page-scoped data. */
export function useFetch<T>(name: string, enabled: boolean, deps: any[] = [], ...args: any[]) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const mounted = useRef(true);
  const argsKey = JSON.stringify(args);
  const depsKey = JSON.stringify(deps);

  const refresh = useCallback(async () => {
    setLoading(true);
    const value = await call<T>(name, ...JSON.parse(argsKey));
    if (!mounted.current) return;
    setData(value);
    setLoading(false);
  }, [name, argsKey]);

  useEffect(() => {
    mounted.current = true;
    if (!enabled) return () => { mounted.current = false; };
    refresh();
    return () => { mounted.current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, depsKey, refresh]);

  return { data, loading, refresh };
}
