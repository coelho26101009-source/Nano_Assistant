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
import { AboutSection, MemorySection, PcControlSection } from "./SettingsSections";
import {
  Badge, Button, ConfirmDialog, EmptyState, ErrorState, Field, MetricRow,
  Panel, SecretField, SegmentedControl, StatusIndicator, Toggle,
} from "./ui";

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
  { value: "general", label: "Geral", hint: "Arranque, janela e aparência" },
  { value: "ai", label: "IA", hint: "Modo, provedores e modelos" },
  { value: "voice", label: "Voz", hint: "Microfone, atalho e resposta falada" },
  { value: "pccontrol", label: "PC Control", hint: "O que o Nano pode fazer no computador" },
  { value: "memory", label: "Memória", hint: "O que o Nano guarda sobre ti" },
  { value: "privacy", label: "Privacidade", hint: "Onde correm os teus dados" },
  { value: "about", label: "Sobre", hint: "Versão e projeto" },
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
      <Panel title="Modo desktop">
        <p className="muted" style={{ fontSize: 13 }}>
          Esta janela está a correr no <strong>navegador</strong>, não na aplicação
          de ambiente de trabalho. O atalho global, o tabuleiro do sistema e o painel
          de voz só existem no Nano Desktop.
        </p>
        <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
          Abre o Nano com <code>NANO_DESKTOP.bat</code> para os ativar.
        </p>
      </Panel>
    );
  }

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try { await action(); } finally { setBusy(false); refresh(); }
  };

  return (
    <Panel
      title="Modo desktop"
      action={<Badge tone="accent">Ativo</Badge>}
    >
      <MetricRow label="Atalho global" value={<code>{status.shortcut}</code>} />
      <MetricRow
        label="Estado do atalho"
        value={
          <StatusIndicator
            state={status.shortcutRegistered ? "READY" : "ERROR"}
            label={status.shortcutRegistered ? "Registado" : "Em conflito"}
            title={status.shortcutError ?? undefined}
          />
        }
      />
      {!status.shortcutRegistered && (
        <>
          <p className="field__error" style={{ marginTop: 8 }}>
            {status.shortcutError ?? "Não foi possível registar o atalho."}
          </p>
          <p className="dim" style={{ fontSize: 11, marginTop: 6 }}>
            Outra aplicação está a usar esta combinação. Fecha-a e tenta novamente —
            o resto do Nano continua a funcionar, incluindo o botão de microfone.
          </p>
          <div className="inline" style={{ marginTop: 10 }}>
            <Button size="sm" disabled={busy} onClick={() => run(retryShortcut)}>
              Tentar registar outra vez
            </Button>
          </div>
        </>
      )}

      <div style={{ height: 12 }} />

      <Toggle
        label="Mostrar o painel de voz"
        hint="Uma pequena janela sobre as outras aplicações a dizer o que o Nano está a fazer durante um turno de voz."
        checked={status.overlayEnabled}
        onChange={(value) => run(() => setOverlayEnabled(value))}
      />
      <Toggle
        label="Iniciar com o Windows"
        hint={
          status.autoLaunch.supported
            ? "O Nano arranca minimizado no tabuleiro, pronto para o atalho global."
            : "Disponível apenas na aplicação instalada — em desenvolvimento o atalho de arranque apontaria para o Electron, não para o Nano."
        }
        checked={status.autoLaunch.enabled}
        disabled={!status.autoLaunch.supported || busy}
        disabledReason={status.autoLaunch.reason ?? undefined}
        onChange={(value) => run(() => setAutoLaunch(value))}
      />

      <div style={{ height: 12 }} />
      <p className="dim" style={{ fontSize: 11, lineHeight: 1.6 }}>
        Fechar a janela esconde o Nano no tabuleiro para que o atalho continue a
        funcionar. Para o encerrar mesmo, usa <strong>Sair do Nano</strong> no menu do
        tabuleiro.
      </p>
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
    <Panel title="Testar frase de ativação">
      <p className="dim" style={{ fontSize: 12, marginBottom: 10 }}>
        Carrega numa frase e di-la em voz alta. O Nano mostra o que ouviu, sem
        acordar nem responder. Repete algumas vezes para veres se é fiável.
      </p>
      <div className="inline" style={{ flexWrap: "wrap", gap: 8 }}>
        {WAKE_CANDIDATES.map((candidate) => (
          <Button
            key={candidate}
            size="sm"
            variant={candidate === phrase ? "primary" : "default"}
            disabled={active !== null}
            onClick={() => run(candidate)}
          >
            {active === candidate ? "A ouvir…" : candidate}
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
                {entry.matched ? "MATCH" : "NO MATCH"}
              </Badge>
              <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>
                <span className="dim">{entry.phrase} → </span>
                <span style={{ fontFamily: "var(--font-mono)" }}>
                  {entry.transcript ? `"${entry.transcript}"` : (entry.detail ?? "—")}
                </span>
                {entry.gate === false && entry.rms != null && (
                  <span className="dim"> · nível {entry.rms} / limiar {entry.threshold}</span>
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
  if (!info) return "Estado desconhecido";
  if (info.temporarily_limited) return "Limite temporário atingido";
  switch (info.state) {
    case "READY": return "Configurado";
    case "SETUP_REQUIRED": return "Falta a chave";
    case "MODEL_UNAVAILABLE": return "Modelo indisponível";
    case "ERROR": return "Chave recusada";
    case "UNAVAILABLE": return "Sem ligação";
    case "DISABLED": return "Não usado neste modo";
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
  if (record.tool_calling === false) facts.push("não suporta chamadas de ferramentas (sem PC Control)");
  if (record.thinking) facts.push("suporta raciocínio configurável");
  if (record.input_tokens) facts.push(`contexto até ${record.input_tokens.toLocaleString("pt-PT")} tokens`);
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
  google: { title: "Google · Gemini", placeholder: "AIza..." },
  groq: { title: "Groq · Cloud", placeholder: "gsk_..." },
  mistral: { title: "Mistral · Cloud", placeholder: "Chave de API do Mistral" },
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
          <option value={current}>{current || "—"} (indisponível)</option>
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
      <MetricRow label="Estado" value={<StatusIndicator state={info?.state} />} />
      {info?.detail && <p className="dim" style={{ fontSize: 12 }}>{info.detail}</p>}

      <div style={{ height: 8 }} />
      <SecretField
        label="Chave de API"
        masked={info?.secret.masked ?? ""}
        configured={Boolean(info?.secret.configured)}
        onSave={(key) => onSaveKey(providerKey, key)}
        onRemove={() => onRemoveKey(providerKey)}
        onTest={() => onTest(providerKey)}
        placeholder={presentation?.placeholder ?? "Chave de API"}
        hint={
          secretsEncrypted
            ? "Guardada encriptada pelo Windows (DPAPI). Nunca é enviada para o navegador nem escrita em ficheiros do projeto."
            : "Guardada localmente com permissões restritas. Nunca é enviada para o navegador."
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
          <Field label="Modelo de conversa"
                 hint="Usado em conversa normal e em voz. Deve ser o mais rápido.">
            {tierSelect("fast")}
          </Field>
          <div style={{ height: 8 }} />
          <Field label="Modelo complexo"
                 hint="Só para análise, código e raciocínio. Nunca por a mensagem ser comprida.">
            {tierSelect("complex")}
          </Field>
          {info?.tiers_ok && info.tiers_ok.complex === false && (
            <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
              O modelo complexo configurado não existe nesta conta; os pedidos
              complexos usam o modelo de conversa.
            </p>
          )}
          {modelCapabilityNote(info, info?.tiers?.fast ?? info?.model ?? "")}
        </>
      ) : null}
    </Panel>
  );
}

export default function SettingsPage({
  settings, providers, diagnostics, loading, busy, onSetMode, onSetPreferredCloud,
  onSaveCloudKey, onRemoveCloudKey, onTestCloud, onSetCloudModel,
  onSetLocalModel, onUpdate, onTestSpeaker, onTestMicrophone,
  onToggleEmergencyStop, onClearConversation, onForgetAllMemory, onNavigate,
  section, onSection,
  theme, onTheme, reduceMotion, onReduceMotion,
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
  /** Jump to a top-level page (Permissões, Memória, Capacidades). */
  onNavigate: (view: "permissions" | "memory" | "capabilities") => void;
  /** The open category. Owned by the shell so the AI pill's "Abrir definições
   *  de IA" can land directly on IA rather than on whatever was open last. */
  section: Section;
  onSection: (section: Section) => void;
  theme: "dark" | "light";
  onTheme: (theme: "dark" | "light") => void;
  reduceMotion: boolean;
  onReduceMotion: (value: boolean) => void;
}) {
  const [confirmClear, setConfirmClear] = useState(false);

  if (loading && !settings) {
    return <div className="page__inner"><EmptyState title="A carregar definições…" /></div>;
  }
  if (!settings) {
    return (
      <div className="page__inner">
        <ErrorState error={{ message: "Não foi possível ler as definições.", component: "backend" }} />
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
      <nav className="settings-rail" aria-label="Categorias de definições">
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
            <Panel title="Modo">
              <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
                A cloud é o cérebro normal do Nano: é rápida e não consome RAM local.
                O Ollama fica como alternativa local e de privacidade.
              </p>
              <SegmentedControl<"AUTO" | "CLOUD" | "LOCAL">
                label="Modo de provedor"
                value={(providers?.mode ?? "AUTO") as any}
                onChange={onSetMode}
                options={[
                  { value: "AUTO", label: "Automático", hint: "Provedor preferido, depois o outro, depois o Ollama" },
                  { value: "CLOUD", label: "Cloud", hint: "Apenas o provedor cloud escolhido" },
                  { value: "LOCAL", label: "Local", hint: "Apenas Ollama; o texto das mensagens não sai do computador" },
                ]}
              />

              {/* MODE and PROVIDER are two different questions, so they are two
                  different controls. Choosing Google does not leave Local mode,
                  and choosing Local does not forget which provider is preferred. */}
              {cloudKeys.length > 1 && (
                <>
                  <div style={{ height: 14 }} />
                  <SegmentedControl<CloudProviderKey>
                    label="Provedor preferido"
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
                <div className="tl-meta" style={{ whiteSpace: "normal", marginTop: 10 }}>
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

            <Panel title="Ollama · Local" action={<Badge tone="info">fallback</Badge>}>
              <MetricRow label="Estado" value={<StatusIndicator state={ollama?.state} />} />
              <MetricRow label="API" value={ollama?.url || "—"} />

              {/* The list is what Ollama really reports as installed. When it
                  is empty the select is not rendered at all: a dropdown that
                  can only offer the current value is a fake control. */}
              {ollama?.models?.length ? (
                <>
                  <div style={{ height: 10 }} />
                  <Field label="Modelo local"
                         hint="Apenas modelos já instalados. O Nano não descarrega modelos por ti.">
                    <select className="select" value={ollama.model}
                            onChange={(e) => onSetLocalModel(e.target.value)}
                            disabled={busy}>
                      {!ollama.models.includes(ollama.model) && (
                        <option value={ollama.model}>{ollama.model} (não instalado)</option>
                      )}
                      {ollama.models.map((model) => <option key={model} value={model}>{model}</option>)}
                    </select>
                  </Field>
                </>
              ) : (
                <MetricRow label="Modelo" value={ollama?.model || "—"} />
              )}

              {ollama?.detail && <p className="dim" style={{ fontSize: 12, marginTop: 6 }}>{ollama.detail}</p>}
              <p className="dim" style={{ fontSize: 11, marginTop: 10 }}>
                O Nano arranca o servidor Ollama se ainda não estiver a correr, mas nunca
                carrega o modelo antecipadamente: só é carregado quando um pedido precisa
                mesmo dele.
              </p>
            </Panel>
          </div>
        )}

        {/* ── VOICE ────────────────────────────────────────────────────── */}
        {section === "voice" && (
          <div className="stack">
            <Panel title="Ativação por voz (experimental)"
                   action={<Badge tone="neutral">Experimental</Badge>}>
              <p className="muted" style={{ fontSize: 13, marginBottom: 10 }}>
                A forma normal de falar com o Nano é o atalho global{" "}
                <code>Ctrl + Shift + Space</code>, que é instantâneo e não gasta nada
                enquanto não é usado.
              </p>
              <p className="dim" style={{ fontSize: 11, marginBottom: 12, lineHeight: 1.6 }}>
                Esta alternativa mantém o microfone aberto e passa tudo o que ouve pelo
                Whisper local. Consome CPU continuamente — e agora mais ainda, porque a
                transcrição passou para o modelo <code>small</code>, que é bastante mais
                exacto mas também mais pesado que o antigo <code>tiny</code>. Por isso
                vem desligada.
              </p>
              <MetricRow label="Estado" value={<StatusIndicator state={voice.state} />} />
              <MetricRow label="Frase" value={`"${voice.wakePhrase}"`} />
              <div style={{ height: 8 }} />
              <Toggle
                label={`Ativar "${voice.wakePhrase}"`}
                hint="O Nano ouve continuamente à espera da frase, localmente."
                checked={voice.wakePhraseEnabled}
                onChange={(v) => onUpdate("wake_phrase_enabled", v)}
              />
              <Toggle
                label='Aceitar apenas "Nano"'
                hint='Desligado por omissão: "Nano" sozinho causava activações falsas. Manter desligado torna a deteção muito mais fiável.'
                checked={voice.allowNanoOnly}
                onChange={(v) => onUpdate("wake_phrase_allow_nano_only", v)}
              />
              <div style={{ height: 12 }} />
              <Field label={`Tempo de espera pelo comando: ${voice.commandTimeoutSeconds}s`}
                     hint="Depois do chime, quanto tempo o Nano espera por um comando antes de voltar a escutar.">
                <input type="range" min={3} max={15} step={1} value={voice.commandTimeoutSeconds}
                       onChange={(e) => onUpdate("wake_command_timeout_seconds", Number(e.target.value))}
                       aria-label="Tempo de espera pelo comando" />
              </Field>
              <Field label={`Intervalo entre activações: ${voice.cooldownSeconds}s`}
                     hint="Impede que a mesma frase active o Nano várias vezes seguidas.">
                <input type="range" min={1} max={10} step={0.5} value={voice.cooldownSeconds}
                       onChange={(e) => onUpdate("wake_phrase_cooldown_seconds", Number(e.target.value))}
                       aria-label="Intervalo entre activações" />
              </Field>
            </Panel>

            <Panel title="Dispositivos">
              <Field label="Microfone" hint="Usado para a wake phrase e para os comandos de voz.">
                <select className="select"
                        value={String(settings.stored.input_device_index ?? "")}
                        onChange={(e) => onUpdate("input_device_index", e.target.value === "" ? null : Number(e.target.value))}>
                  <option value="">Predefinido do sistema</option>
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
                <Button size="sm" onClick={onTestMicrophone}>Testar microfone</Button>
                <Button size="sm" onClick={onTestSpeaker}>Testar som</Button>
              </div>
            </Panel>

            <Panel title="Resposta falada">
              <Toggle
                label="Ler as respostas em voz alta"
                hint="Interruptor geral. Desligado, o Nano nunca fala."
                checked={voice.ttsEnabled}
                onChange={(v) => onUpdate("tts_enabled", v)}
              />
              <Toggle
                label="Falar respostas do chat escrito"
                hint="Desligado por omissão: escreveres no chat não faz o Nano falar."
                checked={voice.typedChatTts}
                onChange={(v) => onUpdate("typed_chat_tts", v)}
              />
              <Toggle
                label="Falar respostas às perguntas por voz"
                hint="Se falaste com o Nano, ouves a resposta."
                checked={voice.voiceReplyTts}
                onChange={(v) => onUpdate("voice_reply_tts", v)}
              />
              <Toggle
                label="Voz ativada"
                hint="Desligar suspende a wake phrase e os comandos de voz."
                checked={voice.enabled}
                onChange={(v) => onUpdate("voice_enabled", v)}
              />
            </Panel>

            <WakePhraseTester phrase={voice.wakePhrase} />

            {/* Honest microphone diagnostics: what Nano is really hearing. */}
            {voice.audio && (
              <Panel title="Diagnóstico do microfone">
                {voice.explain && (
                  <p className="dim" style={{ fontSize: 12, marginBottom: 10 }}>{voice.explain}</p>
                )}
                <MetricRow label="Ruído de fundo" value={String(voice.audio.noise_floor ?? "—")} />
                <MetricRow label="Limiar de fala" value={String(voice.audio.threshold ?? "—")} />
                <MetricRow label="Nível atual" value={String(voice.audio.last_rms ?? "—")} />
                <MetricRow label="Pico observado" value={String(voice.audio.peak_rms ?? "—")} />
                {voice.counters && (
                  <>
                    <MetricRow label="Blocos captados" value={String(voice.counters.chunksCaptured ?? 0)} />
                    <MetricRow label="Com fala" value={String(voice.counters.speechChunks ?? 0)} />
                    <MetricRow label="Em silêncio" value={String(voice.counters.silentChunks ?? 0)} />
                    <MetricRow label="Transcrições" value={String(voice.counters.transcriptsSeen ?? 0)} />
                    <MetricRow label="Ativações" value={String(voice.counters.wakeMatches ?? 0)} />
                  </>
                )}
                {/* What the transcriber actually heard. A wake that does not
                    fire is otherwise invisible: this shows whether the phrase
                    was misheard or never reached the transcriber at all. */}
                {voice.recentTranscripts && voice.recentTranscripts.length > 0 && (
                  <>
                    <div style={{ height: 10 }} />
                    <p className="dim" style={{ fontSize: 11, marginBottom: 4 }}>
                      Últimas transcrições
                    </p>
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
            <DesktopPanel />
            <Panel title="Arranque">
              <p className="muted" style={{ fontSize: 13 }}>
                O Nano Desktop arranca com o <code>NANO_DESKTOP.bat</code>: o Electron
                valida o Python, arranca o motor, espera que fique realmente pronto e só
                então abre a janela. Nenhum separador do navegador é aberto.
              </p>
              <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
                O <code>NANO.bat</code> continua a existir para desenvolvimento e como
                alternativa: abre a mesma interface no navegador, sem atalho global,
                tabuleiro nem painel de voz.
              </p>
            </Panel>
            <Panel title="Idioma">
              <MetricRow label="Interface" value="Português (Portugal)" />
              <p className="dim" style={{ fontSize: 11, marginTop: 6 }}>
                Só está disponível uma língua nesta versão.
              </p>
            </Panel>
            <Panel title="Tema">
              <SegmentedControl<"dark" | "light">
                label="Tema" value={theme} onChange={onTheme}
                options={[{ value: "dark", label: "Escuro" }, { value: "light", label: "Claro" }]}
              />
              <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
                O Nano foi desenhado para o tema escuro; o claro é funcional mas secundário.
              </p>
            </Panel>
            <Panel title="Movimento">
              <Toggle
                label="Reduzir animações"
                hint="Desliga transições e indicadores animados. Também respeita a definição do sistema."
                checked={reduceMotion}
                onChange={onReduceMotion}
              />
            </Panel>
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
        {section === "about" && <AboutSection settings={settings} />}

        {/* ── PRIVACY ──────────────────────────────────────────────────── */}
        {section === "privacy" && (
          <div className="stack">
            <Panel title="Onde correm os teus dados">
              <p className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>
                A memória, o histórico de conversa, a transcrição de voz e a deteção da
                wake phrase acontecem <strong>sempre neste computador</strong>.
                <br /><br />
                No modo Automático ou Cloud, o texto das tuas mensagens é enviado ao Groq
                para gerar a resposta. No modo Local, o texto das mensagens não sai
                do computador.
              </p>
            </Panel>

            {/* Stated separately and plainly because it is the one thing that
                leaves the machine REGARDLESS of provider mode. Saying "no modo
                Local nada sai do computador" without this would be false. */}
            <Panel title="Resposta falada" action={<Badge tone="info">sai do computador</Badge>}>
              <p className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>
                Quando o Nano lê uma resposta em voz alta, usa o serviço de voz da
                Microsoft (<code>edge-tts</code>). O <strong>texto que vai ser lido</strong> é
                enviado à Microsoft para gerar o áudio — <strong>em qualquer modo,
                incluindo Local</strong>.
              </p>
              <div style={{ height: 8 }} />
              <MetricRow
                label="Estado"
                value={
                  <StatusIndicator
                    state={voice.ttsEnabled ? "READY" : "DISABLED"}
                    label={voice.ttsEnabled ? "ativa — o texto lido é enviado" : "desligada — nada é enviado"}
                  />
                }
              />
              <p className="dim" style={{ fontSize: 11, marginTop: 8, lineHeight: 1.6 }}>
                Para que nada saia do computador, desliga a resposta falada em
                Definições → Voz. A transcrição do que dizes (Whisper) continua a
                ser feita localmente e nunca é enviada.
              </p>
            </Panel>

            <Panel title="Permissões">
              <MetricRow label="Modo de autonomia" value={settings.security.autonomyMode} />
              <MetricRow
                label="Autorização permanente"
                value={<StatusIndicator state="DISABLED" label="desativada por desenho" />}
              />
              <p className="dim" style={{ fontSize: 12, marginTop: 8, lineHeight: 1.6 }}>
                Nenhuma capability pode ser autorizada para sempre. Cada decisão vale para
                uma execução, ou para uma tarefa com um alvo concreto. Ações destrutivas,
                shell e envios para o exterior pedem sempre confirmação.
              </p>
            </Panel>

            <Panel title="Segredos">
              <MetricRow
                label="Armazenamento"
                value={<StatusIndicator
                  state={settings.security.secretsEncrypted ? "READY" : "SETUP_REQUIRED"}
                  label={settings.security.secretsEncrypted ? "encriptado pelo Windows" : "ficheiro protegido"} />}
              />
              <p className="dim" style={{ fontSize: 12, marginTop: 8 }}>
                As chaves de API ficam apenas no backend. Nunca são enviadas para o
                navegador, guardadas no armazenamento do browser, nem escritas em ficheiros
                do projeto.
              </p>
            </Panel>

            <Panel title="Limpar dados">
              <Button variant="danger" onClick={() => setConfirmClear(true)}>
                Limpar conversa atual
              </Button>
              <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
                Limpa apenas a conversa em memória. Os factos guardados são geridos na
                página Memória.
              </p>
            </Panel>

            {/* The former "Avançado" section is gone. Its three panels were all
                statements about safety and data, so they live here with the rest
                of that story rather than in a category named after its own
                obscurity. */}
            <Panel title="Paragem de emergência">
              <div className="inline">
                <StatusIndicator
                  state={settings.security.emergencyStop ? "OFFLINE" : "READY"}
                  label={settings.security.emergencyStop ? "Execução bloqueada" : "Execução permitida"}
                />
                <span style={{ flex: 1 }} />
                {settings.security.emergencyStop ? (
                  <Button variant="primary" onClick={() => onToggleEmergencyStop(false)}>Retomar</Button>
                ) : (
                  <Button variant="danger" onClick={() => onToggleEmergencyStop(true)}>Parar tudo</Button>
                )}
              </div>
            </Panel>

            <Panel title="Diagnóstico">
              <p className="muted" style={{ fontSize: 13, marginBottom: 10 }}>
                Para diagnosticar a deteção de voz, corre no terminal:
              </p>
              <div className="perm-card__args">python -m core.wake_phrase_debug</div>
              <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
                Mostra cada transcrição ouvida e diz se o problema é o microfone, a
                transcrição ou a correspondência da frase.
              </p>
            </Panel>

            <Panel title="Registos">
              <p className="muted" style={{ fontSize: 13 }}>
                O registo completo é escrito em <code>logs/nano.log</code>, com rotação
                automática.
              </p>
            </Panel>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmClear} danger
        title="Limpar a conversa?"
        confirmLabel="Limpar"
        message="A conversa atual é apagada da memória do Nano. Isto não apaga os factos guardados."
        onConfirm={() => { setConfirmClear(false); onClearConversation(); }}
        onCancel={() => setConfirmClear(false)}
      />
    </div>
  );
}
