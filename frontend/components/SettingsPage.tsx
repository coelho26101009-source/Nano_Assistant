/**
 * Settings.
 *
 * The Groq API key flow is the important part: paste → validated against the
 * live Groq API → stored OS-encrypted by the backend. The key never round-trips
 * to the browser, is never written to localStorage, never appears in a log, and
 * the user never has to open .env.
 */
import React, { useState } from "react";
import type {
  CloudProviderKey, ProviderInfo, ProviderPayload, SettingsPayload, VoiceDiagnostics,
} from "../lib/backend";
import { call } from "../lib/backend";
import {
  retryShortcut, setAutoLaunch, setOverlayEnabled, useDesktopStatus,
} from "../lib/desktop";
import {
  AboutSection, EraseAllDataPanel, MemorySection, PcControlSection,
} from "./SettingsSections";
import {
  Badge, Button, ConfirmDialog, EmptyState, ErrorState, Field, MetricRow,
  Panel, SecretField, SegmentedControl, StatusIndicator, Toggle,
} from "./ui";
import { LOCALES, locale, normaliseLocale, reloadInLocale, t } from "../lib/i18n";
import type { Locale } from "../lib/i18n";
import { tj } from "../lib/tj";

/**
 * The seven Settings categories.
 *
 * "appearance" and "advanced" are gone as top-level categories, and neither
 * lost anything: theme and motion are appearance-of-the-app and sit in Geral
 * beside the other window behaviour, while the emergency stop, the logs and the
 * diagnostics command are all statements about data and safety and sit in
 * Privacidade. The result is that every category answers a question a user
 * would actually ask, instead of "advanced" meaning "the rest".
 */
export type Section = "general" | "ai" | "voice" | "pccontrol" | "memory" | "privacy" | "about";

const SECTIONS: { value: Section; label: string; hint: string }[] = [
  { value: "general", label: t("Geral"), hint: t("Arranque, janela e aparência") },
  { value: "ai", label: t("IA"), hint: t("Modo, provedores e modelos") },
  { value: "voice", label: t("Voz"), hint: t("Microfone, atalho e resposta falada") },
  { value: "pccontrol", label: t("PC Control"), hint: t("O que o NANO pode fazer no computador") },
  { value: "memory", label: t("Memória"), hint: t("O que o NANO guarda sobre ti") },
  { value: "privacy", label: t("Privacidade"), hint: t("Onde correm os teus dados") },
  { value: "about", label: t("Sobre"), hint: t("Versão e projeto") },
];

type WakeTest = {
  phrase: string;
  matched: boolean;
  transcript?: string;
  normalized?: string;
  gate?: boolean;
  rms?: number;
  threshold?: number;
  detail?: string;
  error?: string;
};

/** Candidate wake phrases, all Portuguese: the transcriber runs in Portuguese,
 *  which is precisely why the English "Hey Nano" was heard as "Ei, não.". */
const WAKE_CANDIDATES = ["ei nano", "olá nano", "acorda nano"];

/**
 * Desktop mode and global activation.
 *
 * Every value here is MEASURED by the Electron main process -- the accelerator
 * it actually holds, whether globalShortcut.isRegistered() agrees, whether the
 * login item really exists. Nothing is assumed: if another application owns
 * Ctrl+Shift+Space, this says so and offers to try again, rather than showing
 * a key combination that quietly does nothing.
 *
 * Outside the desktop shell it renders an honest explanation instead of
 * pretending the feature exists.
 */
function DesktopPanel() {
  const { status, loading, refresh } = useDesktopStatus();
  const [busy, setBusy] = useState(false);

  if (loading) return null;

  if (!status) {
    return (
      <Panel title={t("Modo desktop")}>
        <p className="muted" style={{ fontSize: 13 }}>{tj("Esta janela está a correr no {0}, não na aplicação de ambiente de trabalho. O atalho global, o tabuleiro do sistema e o painel de voz só existem no NANO Desktop.", { 0: <strong>{t("navegador")}</strong> })}</p>
        <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>{tj("Abre o NANO com {0} para os ativar.", { 0: <code>NANO_DESKTOP.bat</code> })}</p>
      </Panel>
    );
  }

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try { await action(); } finally { setBusy(false); refresh(); }
  };

  return (
    <Panel
      title={t("Modo desktop")}
      action={<Badge tone="accent">{t("Ativo")}</Badge>}
    >
      <MetricRow label={t("Atalho global")} value={<code>{status.shortcut}</code>} />
      <MetricRow
        label={t("Estado do atalho")}
        value={
          <StatusIndicator
            state={status.shortcutRegistered ? "READY" : "ERROR"}
            label={status.shortcutRegistered ? t("Registado") : t("Em conflito")}
            title={status.shortcutError ?? undefined}
          />
        }
      />
      {!status.shortcutRegistered && (
        <>
          <p className="field__error" style={{ marginTop: 8 }}>
            {status.shortcutError ?? t("Não foi possível registar o atalho.")}
          </p>
          <p className="dim" style={{ fontSize: 11, marginTop: 6 }}>{t("Outra aplicação está a usar esta combinação. Fecha-a e tenta novamente — o resto do NANO continua a funcionar, incluindo o botão de microfone.")}</p>
          <div className="inline" style={{ marginTop: 10 }}>
            <Button size="sm" disabled={busy} onClick={() => run(retryShortcut)}>{t("Tentar registar outra vez")}</Button>
          </div>
        </>
      )}

      <div style={{ height: 12 }} />

      <Toggle
        label={t("Mostrar o painel de voz")}
        hint={t("Uma pequena janela sobre as outras aplicações a dizer o que o NANO está a fazer durante um turno de voz.")}
        checked={status.overlayEnabled}
        onChange={(value) => run(() => setOverlayEnabled(value))}
      />
      <Toggle
        label={t("Iniciar com o Windows")}
        hint={
          status.autoLaunch.supported
            ? t("O NANO arranca minimizado no tabuleiro, pronto para o atalho global.")
            : t("Disponível apenas na aplicação instalada — em desenvolvimento o atalho de arranque apontaria para o Electron, não para o NANO.")
        }
        checked={status.autoLaunch.enabled}
        disabled={!status.autoLaunch.supported || busy}
        disabledReason={status.autoLaunch.reason ?? undefined}
        onChange={(value) => run(() => setAutoLaunch(value))}
      />

      <div style={{ height: 12 }} />
      <p className="dim" style={{ fontSize: 11, lineHeight: 1.6 }}>{tj("Fechar a janela esconde o NANO no tabuleiro para que o atalho continue a funcionar. Para o encerrar mesmo, usa {0} no menu do tabuleiro.", { 0: <strong>{t("Sair do Nano")}</strong> })}</p>
    </Panel>
  );
}

/**
 * Try a wake phrase out loud without waking Nano.
 *
 * A wake that does not fire is otherwise invisible: you say the phrase,
 * nothing happens, and there is no way to tell whether the microphone, the
 * transcriber or the matcher is at fault. This runs the same provider, the
 * same STT settings and the same matcher as the live detector, and shows the
 * transcript verbatim. It never reaches the Brain and stores no audio.
 */
function WakePhraseTester({ phrase }: { phrase: string }) {
  const [active, setActive] = useState<string | null>(null);
  const [history, setHistory] = useState<WakeTest[]>([]);

  const run = async (candidate: string) => {
    setActive(candidate);
    try {
      const result = await call<WakeTest>("test_wake_phrase", candidate, 3);
      if (result) setHistory((prev) => [result, ...prev].slice(0, 8));
    } finally {
      setActive(null);
    }
  };

  return (
    <Panel title={t("Testar frase de ativação")}>
      <p className="dim" style={{ fontSize: 12, marginBottom: 10 }}>{t("Carrega numa frase e di-la em voz alta. O NANO mostra o que ouviu, sem acordar nem responder. Repete algumas vezes para veres se é fiável.")}</p>
      <div className="inline" style={{ flexWrap: "wrap", gap: 8 }}>
        {WAKE_CANDIDATES.map((candidate) => (
          <Button
            key={candidate}
            size="sm"
            variant={candidate === phrase ? "primary" : "default"}
            disabled={active !== null}
            onClick={() => run(candidate)}
          >
            {active === candidate ? t("A ouvir…") : candidate}
          </Button>
        ))}
      </div>

      {history.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {history.map((entry, index) => (
            <div
              key={index}
              style={{
                display: "flex", gap: 8, alignItems: "baseline",
                padding: "5px 0", borderTop: index ? "1px solid var(--border)" : "none",
                fontSize: 12,
              }}
            >
              <Badge tone={entry.matched ? "accent" : "neutral"}>
                {entry.matched ? "MATCH" : t("NO MATCH")}
              </Badge>
              <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>
                <span className="dim">{entry.phrase} → </span>
                <span style={{ fontFamily: "var(--font-mono)" }}>
                  {entry.transcript ? `"${entry.transcript}"` : (entry.detail ?? "—")}
                </span>
                {entry.gate === false && entry.rms != null && (
                  <span className="dim">{t("· nível {0} / limiar {1}", { 0: entry.rms, 1: entry.threshold })}</span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

/**
 * A provider's state in one short phrase, for the preferred-provider control.
 *
 * Only what the backend reported. There is no optimistic default: an unknown
 * state says so instead of implying the provider is fine.
 */
function cloudStateHint(info?: ProviderInfo): string {
  if (!info) return t("Estado desconhecido");
  if (info.temporarily_limited) return t("Limite temporário atingido");
  switch (info.state) {
    case "READY": return t("Configurado");
    // SETUP_REQUIRED NO LONGER MEANS ONE THING. It is "no key" and also "a
    // valid key whose account offers nothing Nano can use" -- two different
    // problems with two different fixes. Reading secret.configured is what
    // stops Nano telling somebody to paste a key they already pasted.
    case "SETUP_REQUIRED":
      return info.secret?.configured ? t("Sem modelo utilizável") : t("Falta a chave");
    case "MODEL_UNAVAILABLE": return t("Modelo indisponível");
    case "ERROR": return t("Chave recusada");
    case "UNAVAILABLE": return t("Sem ligação");
    case "DISABLED": return t("Não usado neste modo");
    default: return info.state;
  }
}

/**
 * What the selected model can and cannot do, from the account's own metadata.
 *
 * Rendered only when the provider actually published a record for it. Nano
 * never guesses a capability: telling somebody a model supports tool calling
 * when it does not is how "abre o Spotify" becomes a paragraph of advice.
 */
function modelCapabilityNote(info: ProviderInfo | undefined, model: string) {
  const record = info?.records?.find((entry) => entry.id === model);
  if (!record) return null;
  const facts: string[] = [];
  if (record.tool_calling === false) facts.push(t("não suporta chamadas de ferramentas (sem PC Control)"));
  if (record.thinking) facts.push(t("suporta raciocínio configurável"));
  if (record.input_tokens) facts.push(t("contexto até {0} tokens", { 0: record.input_tokens.toLocaleString("pt-PT") }));
  if (!facts.length) return null;
  return (
    <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
      {record.display_name}: {facts.join(" · ")}.
    </p>
  );
}

/**
 * Per-provider presentation. ONLY what cannot be derived from the payload.
 *
 * The title, the state and the model list all come from the backend, so this
 * holds the two things it cannot know: what a key for this vendor looks like,
 * and the one-line reminder of what the provider is for. A provider missing
 * from here still renders -- with the name the backend reported -- because a
 * provider the UI cannot show is a provider the user cannot configure.
 */
const CLOUD_PRESENTATION: Record<string, { title: string; placeholder: string }> = {
  google: { title: t("Google · Gemini"), placeholder: "AIza..." },
  groq: { title: t("Groq · Cloud"), placeholder: "gsk_..." },
  mistral: { title: t("Mistral · Cloud"), placeholder: t("Chave de API do Mistral") },
};

/**
 * One cloud provider: state, key, and the two model tiers.
 *
 * ONE component for every cloud provider rather than one panel each. The
 * Google and Groq panels used to be near-identical copies, and they had
 * already drifted -- Google's model select wrote through the validating
 * endpoint while Groq's wrote the setting directly, so a Groq model that did
 * not exist on the account could be stored and then 404 on every message.
 * Rendering all of them from the payload removes the place that drift lives,
 * and the next provider is an entry in CLOUD_PRESENTATION.
 */
function CloudProviderPanel({
  providerKey, info, preferred, busy, secretsEncrypted,
  onSaveKey, onRemoveKey, onTest, onSetModel,
}: {
  providerKey: CloudProviderKey;
  info?: ProviderInfo;
  preferred: string;
  busy: boolean;
  secretsEncrypted: boolean;
  onSaveKey: (provider: CloudProviderKey, key: string) => Promise<void>;
  onRemoveKey: (provider: CloudProviderKey) => void;
  onTest: (provider: CloudProviderKey) => void;
  onSetModel: (provider: CloudProviderKey, model: string, tier: "fast" | "complex") => void;
}) {
  const presentation = CLOUD_PRESENTATION[providerKey];
  const title = presentation?.title ?? info?.name ?? providerKey;
  const models = info?.models ?? [];

  const tierSelect = (tier: "fast" | "complex") => {
    const current = (tier === "complex" ? info?.tiers?.complex : info?.tiers?.fast) ?? info?.model ?? "";
    return (
      <select className="select" value={current}
              onChange={(e) => onSetModel(providerKey, e.target.value, tier)}
              disabled={busy}>
        {/* A configured model the account does not have is SHOWN, and shown as
            missing. Hiding it would make the select silently display someone
            else's choice. */}
        {!models.includes(current) && (
          <option value={current}>{t("{0} (indisponível)", { 0: current || "—" })}</option>
        )}
        {models.map((model) => <option key={model} value={model}>{model}</option>)}
      </select>
    );
  };

  return (
    <Panel
      title={title}
      action={<Badge tone={preferred === providerKey ? "accent" : "neutral"}>
        {preferred === providerKey ? "preferido" : "cloud"}
      </Badge>}
    >
      <MetricRow label={t("Estado")} value={<StatusIndicator state={info?.state} />} />
      {info?.detail && <p className="dim" style={{ fontSize: 12 }}>{info.detail}</p>}

      <div style={{ height: 8 }} />
      <SecretField
        label={t("Chave de API")}
        masked={info?.secret.masked ?? ""}
        configured={Boolean(info?.secret.configured)}
        onSave={(key) => onSaveKey(providerKey, key)}
        onRemove={() => onRemoveKey(providerKey)}
        onTest={() => onTest(providerKey)}
        placeholder={presentation?.placeholder ?? t("Chave de API")}
        hint={
          secretsEncrypted
            ? t("Guardada encriptada pelo Windows (DPAPI). Nunca é enviada para o navegador nem escrita em ficheiros do projeto.")
            : t("Guardada localmente com permissões restritas. Nunca é enviada para o navegador.")
        }
      />

      {/* The list is what the ACCOUNT reported, never a table in this file: a
          menu that can offer a model the account does not have is a 404 on
          every message. With no list there is no select at all, because a
          dropdown that can only offer its current value is a fake control. */}
      {models.length ? (
        <>
          <div style={{ height: 12 }} />
          {/* Two tiers. Conversation must never pay for the big model, so they
              are configured -- and shown -- separately. */}
          <Field label={t("Modelo de conversa")}
                 hint={t("Usado em conversa normal e em voz. Deve ser o mais rápido.")}>
            {tierSelect("fast")}
          </Field>
          <div style={{ height: 8 }} />
          <Field label={t("Modelo complexo")}
                 hint={t("Só para análise, código e raciocínio. Nunca por a mensagem ser comprida.")}>
            {tierSelect("complex")}
          </Field>
          {info?.tiers_ok && info.tiers_ok.complex === false && (
            <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>{t("O modelo complexo configurado não existe nesta conta; os pedidos complexos usam o modelo de conversa.")}</p>
          )}
          {modelCapabilityNote(info, info?.tiers?.fast ?? info?.model ?? "")}
        </>
      ) : null}
    </Panel>
  );
}

/**
 * Interface language. "Seguir o sistema" is the default: the backend resolves it
 * from the operating system's language, English when NANO does not speak it.
 * Saving reloads the page in the new language, because every string is
 * translated once, when the page loads.
 */
/** The name NANO greets the user by. Saved on Enter or when the field is left. */
function NameField({ value, onSave }: { value: string; onSave: (value: string) => void }) {
  const [draft, setDraft] = useState(value);
  React.useEffect(() => { setDraft(value); }, [value]);
  const commit = () => { if (draft.trim() !== value) onSave(draft.trim()); };
  return (
    <Field label={t("Como te chamas?")} hint={t("O NANO usa-o para te cumprimentar. Fica só neste computador.")}>
      <input
        className="input" value={draft} maxLength={40} placeholder={t("O teu nome")}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); commit(); } }}
      />
    </Field>
  );
}

function LanguagePanel({ language }: { language?: SettingsPayload["language"] }) {
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const choice = language?.setting ?? "system";
  const systemLabel = LOCALES.find((entry) => entry.id === normaliseLocale(language?.system))?.label
    ?? LOCALES[0].label;

  const choose = async (value: string) => {
    setSaving(true);
    setFailed(false);
    const result = await call<{ ok: boolean; resolved: string }>("set_language", value);
    const next = normaliseLocale(result?.resolved);
    if (result?.ok && next) {
      if (next !== locale()) { reloadInLocale(next as Locale); return; }
    } else {
      setFailed(true);
    }
    setSaving(false);
  };

  return (
    <Panel title={t("Idioma")}>
      <Field label={t("Língua da interface")}
             hint={t("As respostas do NANO seguem a língua em que escreves, seja qual for a escolhida aqui.")}>
        <select className="select" value={choice} disabled={saving}
                onChange={(e) => { void choose(e.target.value); }}>
          <option value="system">{t("Seguir o sistema ({0})", { 0: systemLabel })}</option>
          {LOCALES.map((entry) => <option key={entry.id} value={entry.id}>{entry.label}</option>)}
        </select>
      </Field>
      {failed && <p className="dim" role="alert" style={{ fontSize: 11, marginTop: 6 }}>{t("Não foi possível mudar a língua.")}</p>}
    </Panel>
  );
}

/**
 * NANO speaks first (core/proactive.py): a morning briefing and a heads-up
 * before events. Built locally from the agenda; no model, nothing sent.
 */
function ProactivePanel({ proactive, onUpdate, onShowBriefing }: {
  proactive?: SettingsPayload["proactive"];
  onUpdate: (key: string, value: any) => void;
  onShowBriefing: () => void;
}) {
  const briefing = proactive?.briefing ?? true;
  const alerts = proactive?.eventAlerts ?? true;
  return (
    <Panel title={t("O NANO fala primeiro")}>
      <Toggle
        label={t("Resumo da manhã")}
        hint={t("Uma vez por dia, à hora escolhida (ou quando ligas o PC depois dela), o NANO diz bom dia e mostra a agenda e os lembretes de hoje. É feito no teu PC, sem IA na nuvem.")}
        checked={briefing}
        onChange={(value) => onUpdate("proactive_briefing", value)}
      />
      {briefing && (
        <Field label={t("Hora do resumo")}>
          <input className="input" type="time" value={proactive?.briefingTime ?? "08:00"}
                 onChange={(e) => { if (/^\d{2}:\d{2}$/.test(e.target.value)) onUpdate("proactive_briefing_time", e.target.value); }} />
        </Field>
      )}
      <Toggle
        label={t("Avisar antes dos eventos")}
        hint={t("Uns minutos antes de um evento da agenda começar, o NANO avisa. Os lembretes já avisam à hora certa.")}
        checked={alerts}
        onChange={(value) => onUpdate("proactive_event_alerts", value)}
      />
      {alerts && (
        <Field label={t("Avisar com antecedência de")}>
          <select className="select" value={proactive?.leadMinutes ?? 15}
                  onChange={(e) => onUpdate("proactive_event_lead_minutes", Number(e.target.value))}>
            {[5, 10, 15, 30, 60].map((minutes) => (
              <option key={minutes} value={minutes}>{t("{0} minutos", { 0: minutes })}</option>
            ))}
          </select>
        </Field>
      )}
      <div style={{ marginTop: 8 }}>
        <Button size="sm" onClick={onShowBriefing}>{t("Mostrar o resumo agora")}</Button>
      </div>
    </Panel>
  );
}

export default function SettingsPage({
  settings, providers, diagnostics, loading, busy, onSetMode, onSetPreferredCloud,
  onSaveCloudKey, onRemoveCloudKey, onTestCloud, onSetCloudModel,
  onSetLocalModel, onUpdate, onTestSpeaker, onTestMicrophone,
  onToggleEmergencyStop, onClearConversation, onForgetAllMemory, onEraseAllData,
  onNavigate, onOpenFirstRun,
  section, onSection,
  theme, onTheme, reduceMotion, onReduceMotion,
  advancedMode, onAdvancedMode, displayName, onDisplayName,
}: {
  settings: SettingsPayload | null;
  providers: ProviderPayload | null;
  /** Live microphone/wake numbers, polled once a second on their own cheap
   *  endpoint. get_settings() carries the same fields but is slow and cached,
   *  so anything that must be live is read from here first. */
  diagnostics: VoiceDiagnostics | null;
  loading: boolean;
  busy: boolean;
  onSetMode: (mode: "AUTO" | "CLOUD" | "LOCAL") => void;
  onSetPreferredCloud: (provider: CloudProviderKey) => void;
  /* One set of handlers for EVERY cloud provider, taking the provider as an
     argument. Three copies of the same four callbacks is how the Google and
     Groq flows drifted apart in the first place. */
  onSaveCloudKey: (provider: CloudProviderKey, key: string) => Promise<void>;
  onRemoveCloudKey: (provider: CloudProviderKey) => void;
  onTestCloud: (provider: CloudProviderKey) => void;
  onSetCloudModel: (provider: CloudProviderKey, model: string, tier: "fast" | "complex") => void;
  onSetLocalModel: (model: string) => void;
  onUpdate: (key: string, value: any) => void;
  onTestSpeaker: () => void;
  onTestMicrophone: () => void;
  onToggleEmergencyStop: (enabled: boolean) => void;
  onClearConversation: () => void;
  onForgetAllMemory: () => void;
  /** Erase every personal store. Resolves to whether it worked. */
  onEraseAllData: (confirmation: string) => Promise<boolean>;
  /** Jump to a top-level page (Permissões, Memória, Capacidades). */
  onNavigate: (view: "permissions" | "memory" | "capabilities") => void;
  onOpenFirstRun: () => void;
  /** The open category. Owned by the shell so the AI pill's "Abrir definições
   *  de IA" can land directly on IA rather than on whatever was open last. */
  section: Section;
  onSection: (section: Section) => void;
  theme: "system" | "dark" | "light";
  onTheme: (theme: "system" | "dark" | "light") => void;
  advancedMode: boolean;
  onAdvancedMode: (value: boolean) => void;
  /** What NANO calls the user; empty when they have not said. */
  displayName: string;
  onDisplayName: (value: string) => void;
  reduceMotion: boolean;
  onReduceMotion: (value: boolean) => void;
}) {
  const [confirmClear, setConfirmClear] = useState(false);

  if (loading && !settings) {
    return <div className="page__inner"><EmptyState title={t("A carregar definições…")} /></div>;
  }
  if (!settings) {
    return (
      <div className="page__inner">
        <ErrorState error={{ message: t("Não foi possível ler as definições."), component: "backend" }} />
      </div>
    );
  }

  // Live fields come from the fast diagnostics poll; everything else (the
  // toggles, the configured phrase, the timeouts) comes from the settings
  // snapshot. Falling back to the snapshot keeps the panel correct on the
  // first render, before the first diagnostics tick has landed.
  const voice = {
    ...settings.voice,
    state: diagnostics?.state ?? settings.voice.state,
    explain: diagnostics?.explain ?? settings.voice.explain,
    audio: diagnostics?.audio ?? settings.voice.audio,
    counters: diagnostics?.counters ?? settings.voice.counters,
    lastTranscript: diagnostics?.lastTranscript ?? settings.voice.lastTranscript,
    recentTranscripts: diagnostics?.recentTranscripts ?? settings.voice.recentTranscripts,
  };
  const ollama = providers?.ollama;
  const preferred = providers?.preferredCloud ?? "groq";
  const cloudKeys = providers?.cloudProviders ?? [];

  const activeSection = SECTIONS.find((entry) => entry.value === section) ?? SECTIONS[0];

  return (
    <div className="settings-layout">
      {/* A rail rather than a tab strip. Seven categories do not fit on one row
          at 940px, and a horizontally scrolling tab strip hides the categories
          a first-time user most needs to discover. The rail collapses to a
          scrollable chip row under the narrow breakpoint -- see globals.css. */}
      <nav className="settings-rail" aria-label={t("Categorias de definições")}>
        {SECTIONS.map((entry) => (
          <button
            key={entry.value}
            type="button"
            className="settings-rail__item"
            aria-current={section === entry.value ? "page" : undefined}
            onClick={() => onSection(entry.value)}
          >
            <span className="settings-rail__label">{entry.label}</span>
            <span className="settings-rail__hint">{entry.hint}</span>
          </button>
        ))}
      </nav>

      <div className="settings-body">
        <header className="settings-body__head">
          <h2 className="page-title" style={{ margin: 0 }}>{activeSection.label}</h2>
          <p className="dim" style={{ fontSize: 12, margin: "2px 0 0" }}>{activeSection.hint}</p>
        </header>

        {/* ── AI ───────────────────────────────────────────────────────── */}
        {section === "ai" && (
          <div className="stack">
            <Panel title={t("Modo")}>
              <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>{t("A cloud é o cérebro normal do NANO: é rápida e não consome RAM local. O Ollama fica como alternativa local e de privacidade.")}</p>
              <SegmentedControl<"AUTO" | "CLOUD" | "LOCAL">
                label={t("Modo de provedor")}
                value={(providers?.mode ?? "AUTO") as any}
                onChange={onSetMode}
                options={[
                  { value: "AUTO", label: t("Automático"), hint: t("Provedor preferido, depois o outro, depois o Ollama") },
                  { value: "CLOUD", label: t("Cloud"), hint: t("Apenas o provedor cloud escolhido") },
                  { value: "LOCAL", label: t("Local"), hint: t("Apenas Ollama; o texto das mensagens não sai do computador") },
                ]}
              />

              {/* MODE and PROVIDER are two different questions, so they are two
                  different controls. Choosing Google does not leave Local mode,
                  and choosing Local does not forget which provider is preferred. */}
              {cloudKeys.length > 1 && (
                <>
                  <div style={{ height: 14 }} />
                  <SegmentedControl<CloudProviderKey>
                    label={t("Provedor preferido")}
                    value={preferred as CloudProviderKey}
                    onChange={onSetPreferredCloud}
                    options={cloudKeys.map((key) => ({
                      value: key,
                      // The NAME the backend reported, not a table here: a
                      // label kept in the renderer is a label that goes stale
                      // the day a provider is added.
                      label: providers?.[key]?.name ?? key,
                      hint: cloudStateHint(providers?.[key]),
                    }))}
                  />
                </>
              )}

              {providers?.route && (
                <div className="route-reason" style={{ marginTop: 10 }}>
                  {providers.route.reason}
                </div>
              )}
            </Panel>

            {/* Every cloud provider the BACKEND says exists, in its order.
                A provider added server-side appears here without a frontend
                change, which is the point of rendering from the payload. */}
            {cloudKeys.map((key) => (
              <CloudProviderPanel
                key={key}
                providerKey={key}
                info={providers?.[key]}
                preferred={preferred}
                busy={busy}
                secretsEncrypted={settings.security.secretsEncrypted}
                onSaveKey={onSaveCloudKey}
                onRemoveKey={onRemoveCloudKey}
                onTest={onTestCloud}
                onSetModel={onSetCloudModel}
              />
            ))}

            <Panel title={t("Ollama · Local")} action={<Badge tone="info">{t("alternativa")}</Badge>}>
              <MetricRow label={t("Estado")} value={<StatusIndicator state={ollama?.state} />} />
              <MetricRow label="API" value={ollama?.url || "—"} />

              {/* The list is what Ollama really reports as installed. When it
                  is empty the select is not rendered at all: a dropdown that
                  can only offer the current value is a fake control. */}
              {ollama?.models?.length ? (
                <>
                  <div style={{ height: 10 }} />
                  <Field label={t("Modelo local")}
                         hint={t("Apenas modelos já instalados. O NANO não descarrega modelos por ti.")}>
                    <select className="select" value={ollama.model}
                            onChange={(e) => onSetLocalModel(e.target.value)}
                            disabled={busy}>
                      {!ollama.models.includes(ollama.model) && (
                        <option value={ollama.model}>{t("{0} (não instalado)", { 0: ollama.model })}</option>
                      )}
                      {ollama.models.map((model) => <option key={model} value={model}>{model}</option>)}
                    </select>
                  </Field>
                </>
              ) : (
                <MetricRow label={t("Modelo")} value={ollama?.model || "—"} />
              )}

              {ollama?.detail && <p className="dim" style={{ fontSize: 12, marginTop: 6 }}>{ollama.detail}</p>}
              <p className="dim" style={{ fontSize: 11, marginTop: 10 }}>{t("O NANO arranca o servidor Ollama se ainda não estiver a correr, mas nunca carrega o modelo antecipadamente: só é carregado quando um pedido precisa mesmo dele.")}</p>
            </Panel>
          </div>
        )}

        {/* ── VOICE ────────────────────────────────────────────────────── */}
        {section === "voice" && (
          <div className="stack">
            <Panel title={t("Ativação por voz (experimental)")}
                   action={<Badge tone="neutral">{t("Experimental")}</Badge>}>
              <p className="muted" style={{ fontSize: 13, marginBottom: 10 }}>{tj("A forma normal de falar com o NANO é o atalho global {0}, que é instantâneo e não gasta nada enquanto não é usado.", { 0: <code>Ctrl + Shift + Space</code> })}</p>
              <p className="dim" style={{ fontSize: 11, marginBottom: 12, lineHeight: 1.6 }}>{tj("Esta alternativa mantém o microfone aberto e passa tudo o que ouve pelo Whisper local. Consome CPU continuamente — e agora mais ainda, porque a transcrição passou para o modelo {0}, que é bastante mais exato mas também mais pesado que o antigo {1}. Por isso vem desligada.", { 0: <code>small</code>, 1: <code>tiny</code> })}
              </p>
              <MetricRow label={t("Estado")} value={<StatusIndicator state={voice.state} />} />
              <MetricRow label={t("Frase")} value={`"${voice.wakePhrase}"`} />
              <div style={{ height: 8 }} />
              <Toggle
                label={t("Ativar \"{0}\"", { 0: voice.wakePhrase })}
                hint={t("O NANO ouve continuamente à espera da frase, localmente.")}
                checked={voice.wakePhraseEnabled}
                onChange={(v) => onUpdate("wake_phrase_enabled", v)}
              />
              <Toggle
                label={t("Aceitar apenas \"Nano\"")}
                hint={t("Desligado por omissão: \"Nano\" sozinho causava ativações falsas. Manter desligado torna a deteção muito mais fiável.")}
                checked={voice.allowNanoOnly}
                onChange={(v) => onUpdate("wake_phrase_allow_nano_only", v)}
              />
              <div style={{ height: 12 }} />
              <Field label={t("Tempo de espera pelo comando: {0}s", { 0: voice.commandTimeoutSeconds })}
                     hint={t("Depois do chime, quanto tempo o NANO espera por um comando antes de voltar a escutar.")}>
                <input type="range" min={3} max={15} step={1} value={voice.commandTimeoutSeconds}
                       onChange={(e) => onUpdate("wake_command_timeout_seconds", Number(e.target.value))}
                       aria-label={t("Tempo de espera pelo comando")} />
              </Field>
              <Field label={t("Intervalo entre ativações: {0}s", { 0: voice.cooldownSeconds })}
                     hint={t("Impede que a mesma frase active o NANO várias vezes seguidas.")}>
                <input type="range" min={1} max={10} step={0.5} value={voice.cooldownSeconds}
                       onChange={(e) => onUpdate("wake_phrase_cooldown_seconds", Number(e.target.value))}
                       aria-label={t("Intervalo entre ativações")} />
              </Field>
            </Panel>

            <Panel title={t("Dispositivos")}>
              <Field label={t("Microfone")} hint={t("Usado para a wake phrase e para os comandos de voz.")}>
                <select className="select"
                        value={String(settings.stored.input_device_index ?? "")}
                        onChange={(e) => onUpdate("input_device_index", e.target.value === "" ? null : Number(e.target.value))}>
                  <option value="">{t("Predefinido do sistema")}</option>
                  {settings.devices.inputs.map((device) => (
                    <option key={device.id} value={device.id}>{device.name}</option>
                  ))}
                </select>
              </Field>
              {settings.devices.error && (
                <p className="field__error">{settings.devices.error}</p>
              )}
              <div style={{ height: 12 }} />
              <div className="inline">
                <Button size="sm" onClick={onTestMicrophone}>{t("Testar microfone")}</Button>
                <Button size="sm" onClick={onTestSpeaker}>{t("Testar som")}</Button>
              </div>
            </Panel>

            <Panel title={t("Resposta falada")}>
              <Toggle
                label={t("Ler as respostas em voz alta")}
                hint={t("Interruptor geral. Desligado, o NANO nunca fala.")}
                checked={voice.ttsEnabled}
                onChange={(v) => onUpdate("tts_enabled", v)}
              />
              <Toggle
                label={t("Falar respostas do chat escrito")}
                hint={t("Desligado por omissão: escreveres no chat não faz o NANO falar.")}
                checked={voice.typedChatTts}
                onChange={(v) => onUpdate("typed_chat_tts", v)}
              />
              <Toggle
                label={t("Falar respostas às perguntas por voz")}
                hint={t("Se falaste com o NANO, ouves a resposta.")}
                checked={voice.voiceReplyTts}
                onChange={(v) => onUpdate("voice_reply_tts", v)}
              />
              <Toggle
                label={t("Voz ativada")}
                hint={t("Desligar suspende a wake phrase e os comandos de voz.")}
                checked={voice.enabled}
                onChange={(v) => onUpdate("voice_enabled", v)}
              />
            </Panel>

            <WakePhraseTester phrase={voice.wakePhrase} />

            {/* Honest microphone diagnostics: what Nano is really hearing. */}
            {voice.audio && (
              <Panel title={t("Diagnóstico do microfone")}>
                {voice.explain && (
                  <p className="dim" style={{ fontSize: 12, marginBottom: 10 }}>{voice.explain}</p>
                )}
                <MetricRow label={t("Ruído de fundo")} value={String(voice.audio.noise_floor ?? "—")} />
                <MetricRow label={t("Limiar de fala")} value={String(voice.audio.threshold ?? "—")} />
                <MetricRow label={t("Nível atual")} value={String(voice.audio.last_rms ?? "—")} />
                <MetricRow label={t("Pico observado")} value={String(voice.audio.peak_rms ?? "—")} />
                {voice.counters && (
                  <>
                    <MetricRow label={t("Blocos captados")} value={String(voice.counters.chunksCaptured ?? 0)} />
                    <MetricRow label={t("Com fala")} value={String(voice.counters.speechChunks ?? 0)} />
                    <MetricRow label={t("Em silêncio")} value={String(voice.counters.silentChunks ?? 0)} />
                    <MetricRow label={t("Transcrições")} value={String(voice.counters.transcriptsSeen ?? 0)} />
                    <MetricRow label={t("Ativações")} value={String(voice.counters.wakeMatches ?? 0)} />
                  </>
                )}
                {/* What the transcriber actually heard. A wake that does not
                    fire is otherwise invisible: this shows whether the phrase
                    was misheard or never reached the transcriber at all. */}
                {voice.recentTranscripts && voice.recentTranscripts.length > 0 && (
                  <>
                    <div style={{ height: 10 }} />
                    <p className="dim" style={{ fontSize: 11, marginBottom: 4 }}>{t("Últimas transcrições")}</p>
                    <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
                      {voice.recentTranscripts.map((line, i) => (
                        <li key={i} style={{ fontFamily: "var(--font-mono)", fontSize: 11,
                                             color: "var(--text-muted)", padding: "2px 0",
                                             overflowWrap: "anywhere" }}>
                          {line}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </Panel>
            )}
          </div>
        )}

        {/* ── GENERAL ──────────────────────────────────────────────────── */}
        {section === "general" && (
          <div className="stack">
            <Panel title={t("Personalização")}>
              <NameField value={displayName} onSave={onDisplayName} />
              <div style={{ height: 14 }} />
              <Field label={t("Tema")}>
                <SegmentedControl<"system" | "dark" | "light">
                  label={t("Tema")} value={theme} onChange={onTheme}
                  options={[
                    { value: "system", label: t("Seguir o sistema") },
                    { value: "dark", label: t("Escuro") },
                    { value: "light", label: t("Claro") },
                  ]}
                />
              </Field>
              <div style={{ height: 6 }} />
              <Toggle
                label={t("Reduzir animações")}
                hint={t("Desliga transições e indicadores animados. Também respeita a definição do sistema.")}
                checked={reduceMotion}
                onChange={onReduceMotion}
              />
              <Toggle
                label={t("Modo avançado")}
                hint={t("Mostra os detalhes técnicos de cada resposta e as páginas de agentes e componentes.")}
                checked={advancedMode}
                onChange={onAdvancedMode}
              />
            </Panel>
            <LanguagePanel language={settings.language} />
            <ProactivePanel proactive={settings.proactive} onUpdate={onUpdate}
                            onShowBriefing={() => { void call("get_briefing_now"); }} />
            <DesktopPanel />
            {advancedMode && (
              <Panel title={t("Arranque")}>
                <p className="muted" style={{ fontSize: 13 }}>{tj("O NANO Desktop arranca com o {0}: o Electron valida o Python, arranca o motor, espera que fique realmente pronto e só então abre a janela. Nenhum separador do navegador é aberto.", { 0: <code>NANO_DESKTOP.bat</code> })}</p>
                <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
                  {tj("O {0} continua a existir para desenvolvimento e como alternativa: abre a mesma interface no navegador, sem atalho global, tabuleiro nem painel de voz.", { 0: <code>NANO.bat</code> })}</p>
              </Panel>
            )}
          </div>
        )}

        {/* ── PC CONTROL ───────────────────────────────────────────────── */}
        {section === "pccontrol" && (
          <PcControlSection
            settings={settings}
            enabled
            onOpenPermissions={() => onNavigate("permissions")}
            onOpenCapabilities={() => onNavigate("capabilities")}
          />
        )}

        {/* ── MEMÓRIA ──────────────────────────────────────────────────── */}
        {section === "memory" && (
          <MemorySection
            settings={settings}
            onUpdate={onUpdate}
            onOpenMemory={() => onNavigate("memory")}
            onForgetAll={onForgetAllMemory}
          />
        )}

        {/* ── SOBRE ────────────────────────────────────────────────────── */}
        {section === "about" && <AboutSection settings={settings} onOpenFirstRun={onOpenFirstRun} />}

        {/* ── PRIVACY ──────────────────────────────────────────────────── */}
        {section === "privacy" && (
          <div className="stack">
            <Panel title={t("Onde correm os teus dados")}>
              <p className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>{tj("A memória, o histórico de conversa, a transcrição de voz e a deteção da wake phrase acontecem {0}.", { 0: <strong>{t("sempre neste computador")}</strong> })}
                <br /><br />{t("No modo Automático ou Cloud, o texto das tuas mensagens é enviado ao Groq para gerar a resposta. No modo Local, o texto das mensagens não sai do computador.")}</p>
            </Panel>

            {/* Stated separately and plainly because it is the one thing that
                leaves the machine REGARDLESS of provider mode. Saying "no modo
                Local nada sai do computador" without this would be false. */}
            <Panel title={t("Resposta falada")} action={<Badge tone="info">{t("sai do computador")}</Badge>}>
              <p className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>{tj("Quando o NANO lê uma resposta em voz alta, usa o serviço de voz da Microsoft ({0}). O {1} é enviado à Microsoft para gerar o áudio — {2}.", { 0: <code>edge-tts</code>, 1: <strong>{t("texto que vai ser lido")}</strong>, 2: <strong>{t("em qualquer modo, incluindo Local")}</strong> })}
              </p>
              <div style={{ height: 8 }} />
              <MetricRow
                label={t("Estado")}
                value={
                  <StatusIndicator
                    state={voice.ttsEnabled ? "READY" : "DISABLED"}
                    label={voice.ttsEnabled ? t("ativa — o texto lido é enviado") : t("desligada — nada é enviado")}
                  />
                }
              />
              <p className="dim" style={{ fontSize: 11, marginTop: 8, lineHeight: 1.6 }}>{t("Para que nada saia do computador, desliga a resposta falada em Definições → Voz. A transcrição do que dizes (Whisper) continua a ser feita localmente e nunca é enviada.")}</p>
            </Panel>

            {/* Off by default. The text it reads goes to the model, which is
                why it is a Privacy decision and not a PC Control one. */}
            <Panel title={t("Ler texto do ecrã")} action={<Badge tone="info">local</Badge>}>
              <Toggle
                label={t("Permitir que o NANO leia o texto do ecrã")}
                hint={t("Quando pedes (\u201co que diz esta janela?\u201d), o NANO usa o OCR do próprio Windows para extrair o texto da janela, monitor ou região. Pede sempre confirmação, nunca lê gestores de palavras-passe, oculta chaves, palavras-passe, cartões e IBANs, e não guarda a imagem. O texto lido é enviado ao modelo como qualquer mensagem.")}
                checked={Boolean(settings.screenText?.enabled)}
                onChange={(value) => onUpdate("screen_text_enabled", value)}
              />
              {settings.screenText && !settings.screenText.available && (
                <p className="dim" style={{ fontSize: 12, marginTop: 8, lineHeight: 1.6 }}>
                  {settings.screenText.message}
                </p>
              )}
            </Panel>

            {/* Off by default. Unlike OCR the picture itself goes to a vision
                model -- in the cloud unless the provider mode is LOCAL. */}
            <Panel title={t("Ver o ecrã com IA")} action={<Badge tone="info">{t("opcional")}</Badge>}>
              <Toggle
                label={t("Permitir que o NANO veja o ecrã com um modelo de visão")}
                hint={t("Quando pedes (\u201co que estou a ver?\u201d, \u201cajuda-me com este erro\u201d), o NANO tira uma imagem da janela, monitor ou região e pede a um modelo de visão que a descreva. Pede sempre confirmação, nunca olha para gestores de palavras-passe e não guarda a imagem. No modo AUTO ou CLOUD a imagem vai ao fornecedor na nuvem; no modo LOCAL só usa um modelo de visão do Ollama e nada sai do PC.")}
                checked={Boolean(settings.screenVision?.enabled)}
                onChange={(value) => onUpdate("screen_vision_enabled", value)}
              />
            </Panel>

            <Panel title={t("Permissões")}>
              <MetricRow label={t("Modo de autonomia")} value={settings.security.autonomyMode} />
              <MetricRow
                label={t("Autorização permanente")}
                value={<StatusIndicator state="DISABLED" label={t("desativada por desenho")} />}
              />
              <p className="dim" style={{ fontSize: 12, marginTop: 8, lineHeight: 1.6 }}>{t("Nenhuma capability pode ser autorizada para sempre. Cada decisão vale para uma execução, ou para uma tarefa com um alvo concreto. Ações destrutivas, capturas de ecrã e envios para o exterior pedem confirmação. O modelo não tem acesso a shell ou terminal.")}</p>
            </Panel>

            <Panel title={t("Segredos")}>
              <MetricRow
                label={t("Armazenamento")}
                value={<StatusIndicator
                  state={settings.security.secretsEncrypted ? "READY" : "SETUP_REQUIRED"}
                  label={settings.security.secretsEncrypted ? t("encriptado pelo Windows") : t("ficheiro protegido")} />}
              />
              <p className="dim" style={{ fontSize: 12, marginTop: 8 }}>{t("As chaves de API ficam apenas no backend. Nunca são enviadas para o navegador, guardadas no armazenamento do browser, nem escritas em ficheiros do projeto.")}</p>
            </Panel>

            <Panel title={t("Limpar dados")}>
              <Button variant="danger" onClick={() => setConfirmClear(true)}>{t("Limpar conversa atual")}</Button>
              <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>{t("Limpa apenas a conversa em memória. Os factos guardados são geridos na página Memória.")}</p>
            </Panel>

            <EraseAllDataPanel onErase={onEraseAllData} />

            {/* The former "Avançado" section is gone. Its three panels were all
                statements about safety and data, so they live here with the rest
                of that story rather than in a category named after its own
                obscurity. */}
            <Panel title={t("Paragem de emergência")}>
              <div className="inline">
                <StatusIndicator
                  state={settings.security.emergencyStop ? "OFFLINE" : "READY"}
                  label={settings.security.emergencyStop ? t("Execução bloqueada") : t("Execução permitida")}
                />
                <span style={{ flex: 1 }} />
                {settings.security.emergencyStop ? (
                  <Button variant="primary" onClick={() => onToggleEmergencyStop(false)}>{t("Retomar")}</Button>
                ) : (
                  <Button variant="danger" onClick={() => onToggleEmergencyStop(true)}>{t("Parar tudo")}</Button>
                )}
              </div>
            </Panel>

            <Panel title={t("Diagnóstico")}>
              <p className="muted" style={{ fontSize: 13, marginBottom: 10 }}>{t("Para diagnosticar a deteção de voz, corre no terminal:")}</p>
              <div className="perm-card__args">{t("python -m core.wake_phrase_debug")}</div>
              <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>{t("Mostra cada transcrição ouvida e diz se o problema é o microfone, a transcrição ou a correspondência da frase.")}</p>
            </Panel>

            <Panel title={t("Registos")}>
              <p className="muted" style={{ fontSize: 13 }}>{tj("O registo completo é escrito em {0}, com rotação automática.", { 0: <code>logs/nano.log</code> })}</p>
            </Panel>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmClear} danger
        title={t("Limpar a conversa?")}
        confirmLabel={t("Limpar")}
        message={t("A conversa atual é apagada da memória do NANO. Isto não apaga os factos guardados.")}
        onConfirm={() => { setConfirmClear(false); onClearConversation(); }}
        onCancel={() => setConfirmClear(false)}
      />
    </div>
  );
}
