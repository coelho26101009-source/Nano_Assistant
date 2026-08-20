/**
 * Nano design system primitives.
 *
 * Every visual decision lives here or in globals.css. Components compose these
 * rather than carrying their own inline styles, so a change to a button or a
 * status colour lands everywhere at once.
 */
import React, { useEffect, useRef, useCallback } from "react";

/* ── Readiness vocabulary ─────────────────────────────────────────────────
 * These are the only states the UI may display. There is deliberately no
 * "online" fallback: a subsystem the backend has not confirmed reads UNKNOWN.
 */
export type ReadinessState =
  | "READY" | "WORKING" | "WAITING" | "APPROVAL_REQUIRED"
  | "SETUP_REQUIRED" | "MODEL_MISSING" | "MODEL_LOADING" | "PROVIDER_READY"
  | "DISABLED" | "OFFLINE" | "ERROR" | "EXPERIMENTAL" | "NOT_AVAILABLE" | "UNKNOWN"
  | "LISTENING" | "STT_UNAVAILABLE" | "MIC_UNAVAILABLE"
  | "OLLAMA_UNAVAILABLE" | "OLLAMA_NOT_INSTALLED" | "MODEL_UNAVAILABLE"
  | "BACKEND_OFFLINE" | "PROCESSING";

const STATE_TONE: Record<string, string> = {
  READY: "ready",
  WORKING: "working",
  MODEL_LOADING: "working",
  LISTENING: "working",
  WAITING: "waiting",
  APPROVAL_REQUIRED: "approval",
  SETUP_REQUIRED: "setup",
  MODEL_MISSING: "setup",
  PROVIDER_READY: "setup",
  STT_UNAVAILABLE: "setup",
  MIC_UNAVAILABLE: "setup",
  OLLAMA_UNAVAILABLE: "setup",
  OLLAMA_NOT_INSTALLED: "setup",
  MODEL_UNAVAILABLE: "setup",
  PROCESSING: "working",
  BACKEND_OFFLINE: "error",
  DISABLED: "offline",
  OFFLINE: "offline",
  NOT_AVAILABLE: "offline",
  UNKNOWN: "offline",
  ERROR: "error",
  EXPERIMENTAL: "experimental",
};

const STATE_LABEL: Record<string, string> = {
  READY: "Ready",
  WORKING: "Working",
  LISTENING: "Listening",
  WAITING: "Waiting",
  APPROVAL_REQUIRED: "Approval required",
  SETUP_REQUIRED: "Setup required",
  MODEL_MISSING: "Model missing",
  MODEL_LOADING: "Model loading",
  PROVIDER_READY: "Provider ready",
  STT_UNAVAILABLE: "Speech-to-text unavailable",
  MIC_UNAVAILABLE: "Microphone unavailable",
  OLLAMA_UNAVAILABLE: "Ollama unavailable",
  OLLAMA_NOT_INSTALLED: "Ollama not installed",
  MODEL_UNAVAILABLE: "Model not installed",
  PROCESSING: "Processing",
  BACKEND_OFFLINE: "Backend offline",
  DISABLED: "Disabled",
  OFFLINE: "Offline",
  NOT_AVAILABLE: "Not available",
  UNKNOWN: "Unknown",
  ERROR: "Error",
  EXPERIMENTAL: "Experimental",
};

export function normalizeState(value: unknown): ReadinessState {
  const key = String(value ?? "").toUpperCase().replace(/[\s-]/g, "_");
  return (key in STATE_LABEL ? key : "UNKNOWN") as ReadinessState;
}

export function stateLabel(value: unknown): string {
  return STATE_LABEL[normalizeState(value)];
}

export function StatusIndicator({
  state,
  label,
  title,
}: {
  state: unknown;
  label?: string;
  title?: string;
}) {
  const normalized = normalizeState(state);
  const tone = STATE_TONE[normalized] ?? "offline";
  return (
    <span className={`status status--${tone}`} title={title ?? STATE_LABEL[normalized]}>
      <span className="status-dot" aria-hidden="true" />
      <span>{label ?? STATE_LABEL[normalized]}</span>
    </span>
  );
}

/* ── Button ───────────────────────────────────────────────────────────── */
type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "ghost" | "danger" | "allow-once" | "allow-task";
  size?: "md" | "sm";
  icon?: boolean;
  block?: boolean;
};

export function Button({
  variant = "default",
  size = "md",
  icon = false,
  block = false,
  className = "",
  children,
  ...rest
}: ButtonProps) {
  const classes = [
    "btn",
    variant !== "default" ? `btn--${variant}` : "",
    size === "sm" ? "btn--sm" : "",
    icon ? "btn--icon" : "",
    block ? "btn--block" : "",
    className,
  ].filter(Boolean).join(" ");
  return <button type="button" className={classes} {...rest}>{children}</button>;
}

/* ── Badge ────────────────────────────────────────────────────────────── */
export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "accent" | "info";
  children: React.ReactNode;
}) {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}

export function RiskBadge({ risk }: { risk?: string }) {
  const level = String(risk ?? "medium").toLowerCase();
  const known = ["low", "medium", "high", "critical"].includes(level) ? level : "medium";
  return <span className={`badge badge--risk-${known}`}>{known}</span>;
}

export function ToolChip({ name, muted = false }: { name: string; muted?: boolean }) {
  return <span className={`tool-chip${muted ? " tool-chip--muted" : ""}`}>{name}</span>;
}

/* ── Panel ────────────────────────────────────────────────────────────── */
export function Panel({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="panel">
      <header className="panel-head">
        <span>{title}</span>
        <span className="panel-head-spacer" />
        {action}
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function MetricRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="metric-row">
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
    </div>
  );
}

export function Meter({ value, tone = "accent" }: { value: number; tone?: "accent" | "info" }) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    <div className={`meter${tone === "info" ? " meter--info" : ""}`} role="progressbar"
         aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <i style={{ width: `${pct}%` }} />
    </div>
  );
}

/* ── States ───────────────────────────────────────────────────────────── */
export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty-state">
      <p className="empty-state-title">{title}</p>
      {hint && <p className="empty-state-hint">{hint}</p>}
    </div>
  );
}

export function Skeleton({ height = 14, width = "100%" }: { height?: number; width?: string }) {
  return <div className="skeleton" style={{ height, width }} aria-hidden="true" />;
}

export function ErrorState({ message }: { message: string }) {
  return <div className="error-state" role="alert">{message}</div>;
}

/* ── Modal with focus management ──────────────────────────────────────── */
export function Modal({
  open,
  onClose,
  eyebrow,
  title,
  footer,
  width = "default",
  children,
}: {
  open: boolean;
  onClose: () => void;
  eyebrow?: string;
  title: string;
  footer?: React.ReactNode;
  width?: "default" | "wide" | "narrow";
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const restoreTo = useRef<HTMLElement | null>(null);

  const trapFocus = useCallback((event: KeyboardEvent) => {
    if (event.key === "Escape") { onClose(); return; }
    if (event.key !== "Tab" || !ref.current) return;
    const focusables = ref.current.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    restoreTo.current = document.activeElement as HTMLElement;
    document.addEventListener("keydown", trapFocus);
    const timer = window.setTimeout(() => {
      ref.current?.querySelector<HTMLElement>("button, input, textarea")?.focus();
    }, 30);
    return () => {
      document.removeEventListener("keydown", trapFocus);
      window.clearTimeout(timer);
      restoreTo.current?.focus?.();
    };
  }, [open, trapFocus]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div
        className={`modal${width === "wide" ? " modal--wide" : width === "narrow" ? " modal--narrow" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={ref}
      >
        <header className="modal-header">
          <div>
            {eyebrow && <div className="modal-eyebrow">{eyebrow}</div>}
            <h2 className="modal-title">{title}</h2>
          </div>
          <Button variant="ghost" icon aria-label="Fechar" onClick={onClose}>✕</Button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-footer">{footer}</footer>}
      </div>
    </div>
  );
}

/* ── Helpers ──────────────────────────────────────────────────────────── */
const SECRETISH = /secret|token|password|passwd|key|credential|api[_-]?key/i;

/** Never render a value whose key looks like a secret. */
export function sanitizeArgs(args?: Record<string, any>, limit = 8) {
  if (!args) return [] as { key: string; value: string }[];
  return Object.entries(args)
    .filter(([key]) => !key.startsWith("_") && !SECRETISH.test(key))
    .slice(0, limit)
    .map(([key, value]) => ({ key, value: String(value ?? "").slice(0, 220) }));
}

export function formatTime(value?: string | number | Date) {
  if (!value) return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
