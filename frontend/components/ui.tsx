/**
 * Nano design system primitives.
 *
 * Every visual decision lives here or in globals.css. Components compose these
 * rather than carrying inline styles, so a change to a button, a status colour
 * or a focus ring lands everywhere at once.
 */
import React, { useCallback, useEffect, useId, useRef, useState } from "react";

/* ── Readiness vocabulary ─────────────────────────────────────────────────
 * The only states the UI may display. There is deliberately no "online"
 * fallback: a subsystem the backend has not confirmed reads UNKNOWN.
 */
export type ReadinessState =
  | "READY" | "WORKING" | "WAITING" | "APPROVAL_REQUIRED" | "PROCESSING"
  | "SETUP_REQUIRED" | "MODEL_MISSING" | "MODEL_LOADING" | "MODEL_UNAVAILABLE"
  | "PROVIDER_READY" | "LISTENING" | "STT_UNAVAILABLE" | "MIC_UNAVAILABLE"
  | "OLLAMA_UNAVAILABLE" | "OLLAMA_NOT_INSTALLED" | "UNAVAILABLE" | "NOT_INSTALLED"
  | "BACKEND_OFFLINE" | "DISABLED" | "OFFLINE" | "ERROR" | "EXPERIMENTAL"
  | "NOT_AVAILABLE" | "UNKNOWN"
  // Task statuses reach StatusIndicator directly from task rows. Without them
  // every task in the list rendered as "Desconhecido", because an unmapped key
  // normalises to UNKNOWN.
  | "QUEUED" | "PLANNING" | "RUNNING" | "RETRYING" | "RECOVERABLE"
  | "WAITING_FOR_PERMISSION" | "NEEDS_ATTENTION"
  | "COMPLETED" | "CANCELLED" | "FAILED";

const STATE_TONE: Record<string, string> = {
  READY: "ready",
  WORKING: "working", PROCESSING: "working", MODEL_LOADING: "working", LISTENING: "working",
  WAITING: "waiting",
  APPROVAL_REQUIRED: "approval",
  SETUP_REQUIRED: "setup", MODEL_MISSING: "setup", MODEL_UNAVAILABLE: "setup",
  PROVIDER_READY: "setup", STT_UNAVAILABLE: "setup", MIC_UNAVAILABLE: "setup",
  OLLAMA_UNAVAILABLE: "setup", OLLAMA_NOT_INSTALLED: "setup",
  UNAVAILABLE: "setup", NOT_INSTALLED: "setup",
  DISABLED: "offline", OFFLINE: "offline", NOT_AVAILABLE: "offline", UNKNOWN: "offline",
  ERROR: "error", BACKEND_OFFLINE: "error",
  EXPERIMENTAL: "experimental",
  // Task statuses.
  QUEUED: "waiting", RETRYING: "waiting", RECOVERABLE: "waiting",
  WAITING_FOR_PERMISSION: "approval",
  PLANNING: "working", RUNNING: "working",
  NEEDS_ATTENTION: "approval",
  COMPLETED: "ready", CANCELLED: "offline", FAILED: "error",
};

const STATE_LABEL: Record<string, string> = {
  READY: "Pronto",
  WORKING: "A trabalhar",
  PROCESSING: "A processar",
  LISTENING: "A ouvir",
  WAITING: "Em espera",
  APPROVAL_REQUIRED: "Precisa de autorização",
  SETUP_REQUIRED: "Configuração necessária",
  MODEL_MISSING: "Modelo em falta",
  MODEL_UNAVAILABLE: "Modelo indisponível",
  MODEL_LOADING: "A carregar modelo",
  PROVIDER_READY: "Provedor pronto",
  STT_UNAVAILABLE: "Transcrição indisponível",
  MIC_UNAVAILABLE: "Microfone indisponível",
  OLLAMA_UNAVAILABLE: "Ollama indisponível",
  OLLAMA_NOT_INSTALLED: "Ollama não instalado",
  UNAVAILABLE: "Indisponível",
  NOT_INSTALLED: "Não instalado",
  BACKEND_OFFLINE: "Motor offline",
  DISABLED: "Desativado",
  OFFLINE: "Offline",
  NOT_AVAILABLE: "Não disponível",
  UNKNOWN: "Desconhecido",
  ERROR: "Erro",
  EXPERIMENTAL: "Experimental",
  // Task statuses.
  QUEUED: "Em fila",
  PLANNING: "A planear",
  RUNNING: "A executar",
  RETRYING: "A repetir",
  RECOVERABLE: "Recuperável",
  WAITING_FOR_PERMISSION: "Precisa de autorização",
  NEEDS_ATTENTION: "Precisa de atenção",
  COMPLETED: "Concluída",
  CANCELLED: "Cancelada",
  FAILED: "Falhou",
};

export function normalizeState(value: unknown): ReadinessState {
  const key = String(value ?? "").toUpperCase().replace(/[\s-]/g, "_");
  return (key in STATE_LABEL ? key : "UNKNOWN") as ReadinessState;
}

export function stateLabel(value: unknown): string {
  return STATE_LABEL[normalizeState(value)];
}

/** Status is never conveyed by colour alone: the dot always carries a label. */
export function StatusIndicator({
  state, label, title,
}: { state: unknown; label?: string; title?: string }) {
  const normalized = normalizeState(state);
  const tone = STATE_TONE[normalized] ?? "offline";
  const text = label ?? STATE_LABEL[normalized];
  return (
    <span className={`status status--${tone}`} title={title ?? text}>
      <span className="status-dot" aria-hidden="true" />
      <span className="status__label">{text}</span>
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
  variant = "default", size = "md", icon = false, block = false,
  className = "", children, ...rest
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

/* ── Badges ───────────────────────────────────────────────────────────── */
export function Badge({
  tone = "neutral", children,
}: { tone?: "neutral" | "accent" | "info"; children: React.ReactNode }) {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}

export function RiskBadge({ risk }: { risk?: string }) {
  const level = String(risk ?? "medium").toLowerCase();
  const known = ["low", "medium", "high", "critical"].includes(level) ? level : "medium";
  const labels: Record<string, string> = { low: "baixo", medium: "médio", high: "alto", critical: "crítico" };
  return <span className={`badge badge--risk-${known}`}>{labels[known]}</span>;
}

export function ToolChip({ name, muted = false }: { name: string; muted?: boolean }) {
  return <span className={`tool-chip${muted ? " tool-chip--muted" : ""}`} title={name}>{name}</span>;
}

/* ── Layout pieces ────────────────────────────────────────────────────── */
export function Panel({
  title, action, children,
}: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="panel">
      <header className="panel-head">
        <span className="panel-head__title">{title}</span>
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

export function Meter({
  value, tone = "accent",
}: { value: number; tone?: "accent" | "info" | "warn" | "danger" }) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    <div className={`meter${tone !== "accent" ? ` meter--${tone}` : ""}`}
         role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <i style={{ width: `${pct}%` }} />
    </div>
  );
}

/** Colour a usage meter by severity so it is readable without reading numbers. */
export function usageTone(pct: number): "accent" | "warn" | "danger" {
  if (pct >= 90) return "danger";
  if (pct >= 75) return "warn";
  return "accent";
}

/* ── Form controls ────────────────────────────────────────────────────── */
export function Field({
  label, hint, error, htmlFor, children,
}: { label: string; hint?: string; error?: string; htmlFor?: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <label className="field__label" htmlFor={htmlFor}>{label}</label>
      {children}
      {error ? <span className="field__error">{error}</span>
             : hint ? <span className="field__hint">{hint}</span> : null}
    </div>
  );
}

export function Toggle({
  checked, onChange, label, hint, disabled, disabledReason,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
  disabledReason?: string;
}) {
  return (
    <div className="toggle-row">
      <div className="toggle-row__text">
        <div className="toggle-row__title">{label}</div>
        {(disabled && disabledReason) ? <div className="toggle-row__hint">{disabledReason}</div>
          : hint ? <div className="toggle-row__hint">{hint}</div> : null}
      </div>
      <button
        type="button" role="switch" className="toggle"
        aria-checked={checked} aria-label={label}
        disabled={disabled} title={disabled ? disabledReason : undefined}
        onClick={() => onChange(!checked)}
      />
    </div>
  );
}

export function SegmentedControl<T extends string>({
  options, value, onChange, label,
}: {
  options: { value: T; label: string; hint?: string }[];
  value: T;
  onChange: (next: T) => void;
  label?: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value} type="button" className="segmented__option"
          aria-pressed={value === option.value} title={option.hint}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/**
 * A write-only credential input.
 *
 * The stored value never reaches the browser: the backend sends only a masked
 * hint. Typing replaces it wholesale; there is no "reveal", because there is
 * nothing here to reveal.
 */
export function SecretField({
  label, masked, configured, onSave, onRemove, onTest, hint, placeholder,
}: {
  label: string;
  masked: string;
  configured: boolean;
  onSave: (value: string) => Promise<void> | void;
  onRemove: () => Promise<void> | void;
  onTest: () => Promise<void> | void;
  hint?: string;
  placeholder?: string;
}) {
  const inputId = useId();
  const [value, setValue] = useState("");
  const [editing, setEditing] = useState(!configured);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (configured) { setEditing(false); setValue(""); } }, [configured]);

  const save = async () => {
    if (!value.trim()) return;
    setBusy(true);
    try { await onSave(value.trim()); setValue(""); } finally { setBusy(false); }
  };

  return (
    <Field label={label} hint={hint} htmlFor={inputId}>
      {!editing && configured ? (
        <div className="field__row">
          <span className="input input--mono" style={{ flex: 1, minWidth: 0 }} aria-label={`${label} guardada`}>
            {masked || "••••••••"}
          </span>
          <Button size="sm" onClick={() => setEditing(true)}>Alterar</Button>
          <Button size="sm" onClick={() => onTest()}>Testar</Button>
          <Button size="sm" variant="danger" onClick={() => onRemove()}>Remover</Button>
        </div>
      ) : (
        <div className="field__row">
          <input
            id={inputId} type="password" className="input input--mono"
            style={{ flex: 1, minWidth: 0 }}
            autoComplete="off" spellCheck={false}
            placeholder={placeholder ?? "Cola aqui a tua chave"}
            value={value} disabled={busy}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); save(); } }}
          />
          <Button size="sm" variant="primary" onClick={save} disabled={busy || !value.trim()}>
            {busy ? "A validar…" : "Guardar"}
          </Button>
          {configured && <Button size="sm" onClick={() => { setEditing(false); setValue(""); }}>Cancelar</Button>}
        </div>
      )}
    </Field>
  );
}

/* ── Tabs ─────────────────────────────────────────────────────────────── */
export function Tabs<T extends string>({
  tabs, value, onChange,
}: {
  tabs: { value: T; label: string; count?: number }[];
  value: T;
  onChange: (next: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.value} type="button" role="tab" className="tab"
          aria-selected={value === tab.value}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
          {typeof tab.count === "number" && tab.count > 0 && <span className="tab__count">{tab.count}</span>}
        </button>
      ))}
    </div>
  );
}

/* ── States ───────────────────────────────────────────────────────────── */
export function EmptyState({
  title, hint, icon, action,
}: { title: string; hint?: string; icon?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-state__icon" aria-hidden="true">{icon}</div>}
      <p className="empty-state-title">{title}</p>
      {hint && <p className="empty-state-hint">{hint}</p>}
      {action}
    </div>
  );
}

export function Skeleton({ height = 14, width = "100%" }: { height?: number; width?: string }) {
  return <div className="skeleton" style={{ height, width }} aria-hidden="true" />;
}

export type NanoError = {
  message: string;
  component?: string;
  code?: string;
  detail?: string;
  timestamp?: string;
};

/**
 * The one error presentation in the app.
 *
 * A bare "Error" tells the user nothing and gives them nowhere to go, so every
 * error carries a reason, an optional retry, and optional technical detail
 * kept behind a disclosure rather than dumped on screen.
 */
export function ErrorState({
  error, onRetry, compact,
}: { error: NanoError | string; onRetry?: () => void; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const normalized: NanoError = typeof error === "string" ? { message: error } : error;
  const hasDetail = Boolean(normalized.detail || normalized.code || normalized.component);

  return (
    <div className="error-state" role="alert">
      <div className="error-state__title">
        <span aria-hidden="true">⚠</span>
        <span>{compact ? normalized.message : "Algo correu mal"}</span>
      </div>
      {!compact && <div className="error-state__body">{normalized.message}</div>}
      {(onRetry || hasDetail) && (
        <div className="error-state__actions">
          {onRetry && <Button size="sm" onClick={onRetry}>Tentar novamente</Button>}
          {hasDetail && (
            <Button size="sm" variant="ghost" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
              {open ? "Ocultar detalhes" : "Detalhes"}
            </Button>
          )}
        </div>
      )}
      {open && hasDetail && (
        <div className="error-state__details">
          {normalized.component && <div>componente: {normalized.component}</div>}
          {normalized.code && <div>código: {normalized.code}</div>}
          {normalized.timestamp && <div>quando: {normalized.timestamp}</div>}
          {normalized.detail && <div style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{normalized.detail}</div>}
        </div>
      )}
    </div>
  );
}

/* ── Modal, Drawer, ConfirmDialog ─────────────────────────────────────── */
function useFocusTrap(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  const restoreTo = useRef<HTMLElement | null>(null);

  const onKeyDown = useCallback((event: KeyboardEvent) => {
    if (event.key === "Escape") { onClose(); return; }
    if (event.key !== "Tab" || !ref.current) return;
    const focusables = ref.current.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
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
    document.addEventListener("keydown", onKeyDown);
    const timer = window.setTimeout(() => {
      ref.current?.querySelector<HTMLElement>("input, textarea, button")?.focus();
    }, 30);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      window.clearTimeout(timer);
      restoreTo.current?.focus?.();
    };
  }, [open, onKeyDown]);

  return ref;
}

export function Modal({
  open, onClose, eyebrow, title, footer, width = "default", children,
}: {
  open: boolean; onClose: () => void; eyebrow?: string; title: string;
  footer?: React.ReactNode; width?: "default" | "wide" | "narrow"; children: React.ReactNode;
}) {
  const ref = useFocusTrap(open, onClose);
  if (!open) return null;
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div
        className={`modal${width === "wide" ? " modal--wide" : width === "narrow" ? " modal--narrow" : ""}`}
        role="dialog" aria-modal="true" aria-label={title} ref={ref}
      >
        <header className="modal-header">
          <div style={{ minWidth: 0 }}>
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

export function ConfirmDialog({
  open, title, message, confirmLabel = "Confirmar", danger, onConfirm, onCancel,
}: {
  open: boolean; title: string; message: React.ReactNode; confirmLabel?: string;
  danger?: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <Modal
      open={open} onClose={onCancel} title={title} width="narrow"
      footer={
        <>
          <Button onClick={onCancel}>Cancelar</Button>
          <Button variant={danger ? "danger" : "primary"} onClick={onConfirm}>{confirmLabel}</Button>
        </>
      }
    >
      <div className="muted" style={{ fontSize: 14, lineHeight: 1.6 }}>{message}</div>
    </Modal>
  );
}

/* ── Toasts ───────────────────────────────────────────────────────────── */
export type Toast = { id: string; text: string; tone?: "default" | "error" | "success" };

export function ToastStack({ toasts }: { toasts: Toast[] }) {
  if (!toasts.length) return null;
  return (
    <div className="toast-stack" aria-live="polite" aria-atomic="false">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast${toast.tone && toast.tone !== "default" ? ` toast--${toast.tone}` : ""}`} role="status">
          {toast.text}
        </div>
      ))}
    </div>
  );
}

export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef<Record<string, number>>({});

  const notify = useCallback((text: string, tone: Toast["tone"] = "default") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((prev) => [...prev.slice(-2), { id, text, tone }]);
    timers.current[id] = window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
      delete timers.current[id];
    }, tone === "error" ? 5200 : 2800);
  }, []);

  useEffect(() => () => { Object.values(timers.current).forEach(window.clearTimeout); }, []);
  return { toasts, notify };
}

/* ── Helpers ──────────────────────────────────────────────────────────── */
const SECRETISH = /secret|token|password|passwd|key|credential|api[_-]?key|authorization/i;

/** Never render a value whose key looks like a secret. */
export function sanitizeArgs(args?: Record<string, any>, limit = 8) {
  if (!args) return [] as { key: string; value: string }[];
  return Object.entries(args)
    .filter(([key]) => !key.startsWith("_") && !SECRETISH.test(key))
    .slice(0, limit)
    .map(([key, value]) => ({ key, value: String(value ?? "").slice(0, 240) }));
}

export function formatTime(value?: string | number | Date) {
  if (!value) return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" });
}

export function formatDateTime(value?: string | number | Date) {
  if (!value) return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("pt-PT", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** Elapsed time as a compact mm:ss / h m string. Returns null when unknown. */
export function elapsedSince(iso?: string): string | null {
  if (!iso) return null;
  const started = new Date(iso).getTime();
  if (Number.isNaN(started)) return null;
  const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
