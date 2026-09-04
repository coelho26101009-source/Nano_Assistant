/**
 * The top-right AI pill, and the mode / provider / model selector behind it.
 *
 * The pill used to be a label that navigated to the status page. It showed
 * "Groq · AUTO" and did nothing with it, which made the most prominent piece of
 * runtime state in the whole window read as decoration.
 *
 * It is now the fastest way to change how Nano thinks. Choosing a mode calls
 * the SAME `set_provider_mode` the Settings page calls, choosing a provider the
 * same `set_preferred_cloud_provider`, and choosing a model the same
 * `set_cloud_model` — there is no second path and no local copy of the answer,
 * which is what keeps the pill and Settings → IA from disagreeing.
 *
 * `set_cloud_model` and not `update_setting`, deliberately. Writing the setting
 * directly is what the Groq model select used to do, and it stored ids the
 * account did not have; the endpoint validates against the account first. Both render `providers.*` straight from the backend payload, so
 * whichever one the user touched, the other is correct on the next poll (and
 * immediately, because the caller refreshes).
 *
 * WHY THE MODEL LIST IS SAFE TO PUT HERE. Every entry comes from
 * `provider.models`, which is what the ACCOUNT reported when the backend listed
 * it. Nothing in this file names a model, so a menu can never offer something
 * that does not exist — the failure that once had Nano calling a decommissioned
 * model on every message.
 *
 * WHAT THE LABEL MAY SAY. Only what the backend measured. `route.provider` is
 * the provider that would actually answer right now, and the model name is the
 * one the provider really reports — nothing here assumes "qwen3" because qwen3
 * happens to be installed on the development machine. When AUTO has fallen
 * back, the fallback stays visible: `core/providers.py` treats "you can see
 * that it fell back" as a property of the design, not a detail.
 */
import React, { useState } from "react";

import type { CloudProviderKey, ProviderInfo, ProviderPayload } from "../lib/backend";
import { Popover, StatusIndicator } from "./ui";

export type ProviderMode = "AUTO" | "CLOUD" | "LOCAL";

export const MODES: { value: ProviderMode; label: string; hint: string }[] = [
  {
    value: "AUTO",
    label: "Automático",
    hint: "Provedor cloud preferido primeiro. Se falhar, o Nano continua no outro provedor cloud e, por fim, no modelo local.",
  },
  {
    value: "CLOUD",
    label: "Cloud",
    hint: "Apenas o provedor cloud escolhido. Se não estiver disponível, o Nano diz — nunca muda sozinho.",
  },
  {
    value: "LOCAL",
    label: "Local",
    hint: "Apenas Ollama. O texto das mensagens não sai do computador.",
  },
];

/** How many models the pill offers before deferring to Settings. */
const MODEL_LIMIT = 6;

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

/**
 * A cloud model id shortened for the pill: "models/gemini-x-flash" -> "Gemini X Flash".
 *
 * Same rule as the local label and for the same reason — the id is the source,
 * not a table this file would have to keep in step with someone else's catalogue.
 */
export function cloudModelLabel(model?: string | null): string {
  const raw = String(model ?? "").trim();
  if (!raw) return "";
  const tail = raw.split("/").pop()!;
  return tail
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => (/^\d/.test(part) ? part : part.charAt(0).toUpperCase() + part.slice(1)))
    .join(" ");
}

/** The text on the pill: "Groq · AUTO", "Gemini … · CLOUD", "Qwen3 · LOCAL". */
export function providerLabel(providers: ProviderPayload | null, offline = false): string {
  if (offline) return "Motor offline";
  const route = providers?.route;
  if (!route) return "A ligar…";
  const mode = providers?.mode ?? route.mode;
  if (!route.usable) return `Sem provedor · ${mode}`;
  // Groq is named by its BRAND and the others by their MODEL, which looks
  // inconsistent and is not. Groq's ids are vendor-prefixed paths
  // ("openai/gpt-oss-20b"), so the prettified model reads worse than the one
  // word everybody uses for it; Gemini and Ministral ids are already the name
  // a person would say. The rule is "the most recognisable true thing that
  // fits in a pill", not "always the same field".
  //
  // The final fallback is the provider's own NAME from the payload rather than
  // an em dash. That mattered: this returned "—" for every provider it did not
  // name, so a Gemini turn was labelled "—" in the top bar of the shell.
  const info = route.provider === "ollama"
    ? undefined
    : providers?.[route.provider as CloudProviderKey];
  const name =
    route.provider === "ollama" ? localModelLabel(route.model || providers?.ollama?.model)
    : route.provider === "groq" ? "Groq"
    : (cloudModelLabel(route.model) || info?.name || "—");
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

/**
 * One row of the menu. Kept local so every group looks identical.
 *
 * `data-group` is not decoration. The menu now holds three groups of
 * `role="menuitemradio"` items, so a test that counts every radio in the
 * popover is no longer measuring "the three modes" -- it is measuring
 * modes + providers + models and will drift every time a group is added.
 * The attribute lets a driven test address one group exactly.
 */
function MenuItem({
  group, checked, label, hint, disabled, title, onClick,
}: {
  group: "mode" | "provider" | "model";
  checked: boolean;
  label: string;
  hint?: string;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={checked}
      className="popover__item"
      data-group={group}
      disabled={disabled}
      title={title}
      onClick={onClick}
    >
      <span className="popover__item-check" aria-hidden="true">
        {checked ? <Glyph d={CHECK} size={14} /> : null}
      </span>
      <span className="popover__item-body">
        <span className="popover__item-label">{label}</span>
        {hint && <span className="popover__item-hint">{hint}</span>}
      </span>
    </button>
  );
}

/**
 * A provider's state in one short phrase.
 *
 * Only states the backend actually reported. There is no optimistic default:
 * an unknown state says so rather than implying the provider is fine.
 */
function providerHint(info?: ProviderInfo): string {
  if (!info) return "Estado desconhecido";
  if (info.temporarily_limited) {
    const seconds = Math.round(info.retry_in_seconds ?? 0);
    return seconds > 0 ? `Limite temporário — ~${seconds} s` : "Limite temporário atingido";
  }
  switch (info.state) {
    case "READY": return "Configurado";
    case "SETUP_REQUIRED": return "Falta a chave de API";
    case "MODEL_UNAVAILABLE": return "Modelo indisponível nesta conta";
    case "ERROR": return "Chave recusada";
    case "UNAVAILABLE": return "Sem ligação";
    case "DISABLED": return "Não usado neste modo";
    default: return info.state || "Estado desconhecido";
  }
}

export default function AiModeMenu({
  providers, agentState, healthLabel, offline, busy,
  onSetMode, onSetPreferredCloud, onSetCloudModel, onOpenAiSettings,
}: {
  providers: ProviderPayload | null;
  agentState: string;
  healthLabel: string;
  offline: boolean;
  busy: boolean;
  onSetMode: (mode: ProviderMode) => void;
  onSetPreferredCloud: (provider: CloudProviderKey) => void;
  onSetCloudModel: (provider: CloudProviderKey, model: string) => void;
  onOpenAiSettings: () => void;
}) {
  const [open, setOpen] = useState(false);

  const active = (providers?.mode ?? "AUTO") as ProviderMode;
  const label = providerLabel(providers, offline);
  const route = providers?.route;
  const fellBack = Boolean(route?.fallback);

  const preferred = (providers?.preferredCloud ?? "groq") as CloudProviderKey;
  const cloudKeys = (providers?.cloudProviders ?? []) as CloudProviderKey[];
  const preferredInfo: ProviderInfo | undefined = providers?.[preferred];
  // The conversation model, which is the one an ordinary message uses. The
  // complex tier is a different question and stays in Settings.
  const activeModel = preferredInfo?.tiers?.fast ?? preferredInfo?.model ?? "";
  const models = (preferredInfo?.models ?? []).slice(0, MODEL_LIMIT);

  const choose = (mode: ProviderMode) => {
    setOpen(false);
    // Selecting the mode already in force would still cost a backend round
    // trip and a toast, for no change.
    if (mode !== active) onSetMode(mode);
  };

  const chooseProvider = (key: CloudProviderKey) => {
    setOpen(false);
    if (key !== preferred) onSetPreferredCloud(key);
  };

  const chooseModel = (model: string) => {
    setOpen(false);
    if (model !== activeModel) onSetCloudModel(preferred, model);
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
      <p className="popover__label">Modo</p>
      {MODES.map((mode) => (
        <MenuItem
          key={mode.value}
          group="mode"
          checked={active === mode.value}
          label={mode.label}
          hint={mode.hint}
          disabled={busy}
          title={busy ? "A aplicar a alteração anterior…" : mode.hint}
          onClick={() => choose(mode.value)}
        />
      ))}

      {/* Provider, only when there is more than one to choose between. A menu
          group offering a single option is a control that cannot do anything. */}
      {cloudKeys.length > 1 && (
        <>
          <div className="popover__sep" role="separator" />
          <p className="popover__label">Provedor cloud</p>
          {cloudKeys.map((key) => (
            <MenuItem
              key={key}
              group="provider"
              checked={preferred === key}
              label={providers?.[key]?.name ?? key}
              hint={providerHint(providers?.[key])}
              disabled={busy}
              onClick={() => chooseProvider(key)}
            />
          ))}
        </>
      )}

      {/* Models, only when the backend actually listed some for this provider.
          An empty list means the account was never queried (no key, or LOCAL
          mode), and a select that can only offer the current value is a fake
          control. */}
      {models.length > 0 && (
        <>
          <div className="popover__sep" role="separator" />
          <p className="popover__label">
            Modelo de conversa · {preferredInfo?.name ?? preferred}
          </p>
          {models.map((model) => (
            <MenuItem
              key={model}
              group="model"
              checked={model === activeModel}
              label={model}
              disabled={busy}
              onClick={() => chooseModel(model)}
            />
          ))}
          {(preferredInfo?.models?.length ?? 0) > MODEL_LIMIT && (
            <p className="popover__note">
              Mais {(preferredInfo?.models?.length ?? 0) - MODEL_LIMIT} modelo(s) em
              Definições → IA.
            </p>
          )}
        </>
      )}

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
