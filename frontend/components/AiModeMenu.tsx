/**
 * The top-right AI pill, and the mode selector behind it.
 *
 * The pill used to be a label that navigated to the status page. It showed
 * "Groq · AUTO" and did nothing with it, which made the most prominent piece of
 * runtime state in the whole window read as decoration.
 *
 * It is now the fastest way to change how Nano thinks. Choosing a mode calls
 * the SAME `set_provider_mode` the Settings page calls — there is no second
 * path and no local copy of the answer, which is what keeps the pill and
 * Settings → IA from disagreeing. Both render `providers.mode` straight from
 * the backend payload, so whichever one the user touched, the other is correct
 * on the next poll (and immediately, because the caller refreshes).
 *
 * WHAT THE LABEL MAY SAY. Only what the backend measured. `route.provider` is
 * the provider that would actually answer right now, and the local model name
 * is the one Ollama really reports — nothing here assumes "qwen3" because
 * qwen3 happens to be installed on the development machine. When AUTO has
 * fallen back, the fallback stays visible: `core/providers.py` treats "you can
 * see that it fell back" as a property of the design, not a detail.
 */
import React, { useState } from "react";

import type { ProviderPayload } from "../lib/backend";
import { Popover, StatusIndicator } from "./ui";

export type ProviderMode = "AUTO" | "CLOUD" | "LOCAL";

export const MODES: { value: ProviderMode; label: string; hint: string }[] = [
  {
    value: "AUTO",
    label: "Automático",
    hint: "Groq primeiro. Se falhar, o Nano continua no modelo local.",
  },
  {
    value: "CLOUD",
    label: "Cloud",
    hint: "Apenas Groq. Se não estiver disponível, o Nano diz — nunca muda sozinho.",
  },
  {
    value: "LOCAL",
    label: "Local",
    hint: "Apenas Ollama. O texto das mensagens não sai do computador.",
  },
];

/**
 * A model id, as a person would say it: "qwen3:8b" -> "Qwen3".
 *
 * Derived from the real id rather than looked up in a table, so a model nobody
 * anticipated still gets a sensible name instead of falling back to "Local".
 */
export function localModelLabel(model?: string | null): string {
  const raw = String(model ?? "").trim();
  if (!raw) return "Local";
  // "library/qwen3:8b-instruct" -> "qwen3"
  const family = raw.split("/").pop()!.split(":")[0];
  if (!family) return "Local";
  return family.charAt(0).toUpperCase() + family.slice(1);
}

/** The text on the pill: "Groq · AUTO", "Qwen3 · LOCAL". */
export function providerLabel(providers: ProviderPayload | null, offline = false): string {
  if (offline) return "Motor offline";
  const route = providers?.route;
  if (!route) return "A ligar…";
  const mode = providers?.mode ?? route.mode;
  if (!route.usable) return `Sem provedor · ${mode}`;
  const name =
    route.provider === "groq" ? "Groq"
    : route.provider === "ollama" ? localModelLabel(route.model || providers?.ollama?.model)
    : "—";
  return `${name} · ${mode}`;
}

const CHECK = "M20 6 9 17l-5-5";
const SLIDERS = "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6";

const Glyph = ({ d, size = 15 }: { d: string; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);

export default function AiModeMenu({
  providers, agentState, healthLabel, offline, busy, onSetMode, onOpenAiSettings,
}: {
  providers: ProviderPayload | null;
  agentState: string;
  healthLabel: string;
  offline: boolean;
  busy: boolean;
  onSetMode: (mode: ProviderMode) => void;
  onOpenAiSettings: () => void;
}) {
  const [open, setOpen] = useState(false);

  const active = (providers?.mode ?? "AUTO") as ProviderMode;
  const label = providerLabel(providers, offline);
  const route = providers?.route;
  const fellBack = Boolean(route?.fallback);

  const choose = (mode: ProviderMode) => {
    setOpen(false);
    // Selecting the mode already in force would still cost a backend round
    // trip and a toast, for no change.
    if (mode !== active) onSetMode(mode);
  };

  return (
    <Popover
      open={open}
      onClose={() => setOpen(false)}
      label="Modo de inteligência"
      trigger={(props) => (
        <button
          {...props}
          type="button"
          className="status-pill status-pill--menu"
          onClick={() => setOpen((v) => !v)}
          title={`${healthLabel} — mudar o modo de IA`}
          data-mode={active}
        >
          <StatusIndicator state={agentState} label="" />
          <span className="status-pill__text">{label}</span>
          {fellBack && <span className="status-pill__tag">fallback</span>}
          <span className="status-pill__caret" aria-hidden="true">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="m6 9 6 6 6-6" />
            </svg>
          </span>
        </button>
      )}
    >
      {MODES.map((mode) => (
        <button
          key={mode.value}
          type="button"
          role="menuitemradio"
          aria-checked={active === mode.value}
          className="popover__item"
          disabled={busy}
          title={busy ? "A aplicar a alteração anterior…" : mode.hint}
          onClick={() => choose(mode.value)}
        >
          <span className="popover__item-check" aria-hidden="true">
            {active === mode.value ? <Glyph d={CHECK} size={14} /> : null}
          </span>
          <span className="popover__item-body">
            <span className="popover__item-label">{mode.label}</span>
            <span className="popover__item-hint">{mode.hint}</span>
          </span>
        </button>
      ))}

      {/* Why the pill says what it says, in the backend's own words. */}
      {route?.reason && (
        <p className="popover__note">{route.reason}</p>
      )}

      <div className="popover__sep" role="separator" />

      <button
        type="button"
        role="menuitem"
        className="popover__item popover__item--action"
        onClick={() => { setOpen(false); onOpenAiSettings(); }}
      >
        <span className="popover__item-check" aria-hidden="true"><Glyph d={SLIDERS} size={14} /></span>
        <span className="popover__item-body">
          <span className="popover__item-label">Abrir definições de IA</span>
        </span>
      </button>
    </Popover>
  );
}
