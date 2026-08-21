/**
 * Settings.
 *
 * The Groq API key flow is the important part: paste → validated against the
 * live Groq API → stored OS-encrypted by the backend. The key never round-trips
 * to the browser, is never written to localStorage, never appears in a log, and
 * the user never has to open .env.
 */
import React, { useState } from "react";
import type { ProviderPayload, SettingsPayload } from "../lib/backend";
import {
  Badge, Button, ConfirmDialog, EmptyState, ErrorState, Field, MetricRow,
  Panel, SecretField, SegmentedControl, StatusIndicator, Tabs, Toggle,
} from "./ui";

type Section = "general" | "ai" | "voice" | "appearance" | "privacy" | "advanced";

export default function SettingsPage({
  settings, providers, loading, busy, onSetMode, onSaveGroqKey, onRemoveGroqKey,
  onTestGroq, onSetGroqModel, onUpdate, onTestSpeaker, onTestMicrophone,
  onToggleEmergencyStop, onClearConversation, theme, onTheme, reduceMotion, onReduceMotion,
}: {
  settings: SettingsPayload | null;
  providers: ProviderPayload | null;
  loading: boolean;
  busy: boolean;
  onSetMode: (mode: "AUTO" | "CLOUD" | "LOCAL") => void;
  onSaveGroqKey: (key: string) => Promise<void>;
  onRemoveGroqKey: () => void;
  onTestGroq: () => void;
  onSetGroqModel: (model: string) => void;
  onUpdate: (key: string, value: any) => void;
  onTestSpeaker: () => void;
  onTestMicrophone: () => void;
  onToggleEmergencyStop: (enabled: boolean) => void;
  onClearConversation: () => void;
  theme: "dark" | "light";
  onTheme: (theme: "dark" | "light") => void;
  reduceMotion: boolean;
  onReduceMotion: (value: boolean) => void;
}) {
  const [section, setSection] = useState<Section>("ai");
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

  const voice = settings.voice;
  const groq = providers?.groq;
  const ollama = providers?.ollama;

  return (
    <div className="page__inner">
      <Tabs<Section>
        value={section} onChange={setSection}
        tabs={[
          { value: "ai", label: "Inteligência artificial" },
          { value: "voice", label: "Voz" },
          { value: "general", label: "Geral" },
          { value: "appearance", label: "Aparência" },
          { value: "privacy", label: "Privacidade e segurança" },
          { value: "advanced", label: "Avançado" },
        ]}
      />

      <div style={{ marginTop: 20, maxWidth: 680 }}>
        {/* ── AI ───────────────────────────────────────────────────────── */}
        {section === "ai" && (
          <div className="stack">
            <Panel title="Modo">
              <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
                O Groq é o cérebro normal do Nano: é rápido e não consome RAM local.
                O Ollama fica como alternativa local e de privacidade.
              </p>
              <SegmentedControl<"AUTO" | "CLOUD" | "LOCAL">
                label="Modo de provedor"
                value={(providers?.mode ?? "AUTO") as any}
                onChange={onSetMode}
                options={[
                  { value: "AUTO", label: "Automático", hint: "Groq primeiro, Ollama se falhar" },
                  { value: "CLOUD", label: "Cloud", hint: "Apenas Groq" },
                  { value: "LOCAL", label: "Local", hint: "Apenas Ollama, nada sai do computador" },
                ]}
              />
              {providers?.route && (
                <div className="tl-meta" style={{ whiteSpace: "normal", marginTop: 10 }}>
                  {providers.route.reason}
                </div>
              )}
            </Panel>

            <Panel
              title="Groq · Cloud"
              action={<Badge tone="accent">principal</Badge>}
            >
              <MetricRow label="Estado" value={<StatusIndicator state={groq?.state} />} />
              {groq?.detail && <p className="dim" style={{ fontSize: 12 }}>{groq.detail}</p>}

              <div style={{ height: 8 }} />
              <SecretField
                label="Chave de API"
                masked={groq?.secret.masked ?? ""}
                configured={Boolean(groq?.secret.configured)}
                onSave={onSaveGroqKey}
                onRemove={onRemoveGroqKey}
                onTest={onTestGroq}
                placeholder="gsk_..."
                hint={
                  settings.security.secretsEncrypted
                    ? "Guardada encriptada pelo Windows (DPAPI). Nunca é enviada para o navegador nem escrita em ficheiros do projeto."
                    : "Guardada localmente com permissões restritas. Nunca é enviada para o navegador."
                }
              />

              {groq?.models?.length ? (
                <>
                  <div style={{ height: 12 }} />
                  <Field label="Modelo" hint="Apenas modelos que existem na tua conta Groq.">
                    <select className="select" value={groq.model}
                            onChange={(e) => onSetGroqModel(e.target.value)} disabled={busy}>
                      {!groq.models.includes(groq.model) && (
                        <option value={groq.model}>{groq.model} (indisponível)</option>
                      )}
                      {groq.models.map((model) => <option key={model} value={model}>{model}</option>)}
                    </select>
                  </Field>
                </>
              ) : null}
            </Panel>

            <Panel title="Ollama · Local" action={<Badge tone="info">fallback</Badge>}>
              <MetricRow label="Estado" value={<StatusIndicator state={ollama?.state} />} />
              <MetricRow label="Modelo" value={ollama?.model || "—"} />
              <MetricRow label="API" value={ollama?.url || "—"} />
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
            <Panel title="Wake phrase">
              <MetricRow label="Estado" value={<StatusIndicator state={voice.state} />} />
              <MetricRow label="Frase" value={`"${voice.wakePhrase}"`} />
              <div style={{ height: 8 }} />
              <Toggle
                label="Ativar wake phrase"
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
                hint="Usa a síntese de voz local. Requer ligação à internet para o edge-tts."
                checked={voice.ttsEnabled}
                onChange={(v) => onUpdate("tts_enabled", v)}
              />
              <Toggle
                label="Voz ativada"
                hint="Desligar suspende a wake phrase e os comandos de voz."
                checked={voice.enabled}
                onChange={(v) => onUpdate("voice_enabled", v)}
              />
            </Panel>
          </div>
        )}

        {/* ── GENERAL ──────────────────────────────────────────────────── */}
        {section === "general" && (
          <div className="stack">
            <Panel title="Arranque">
              <p className="muted" style={{ fontSize: 13 }}>
                O Nano arranca com o <code>NANO.bat</code>. O launcher valida o Python,
                arranca o servidor Ollama se for preciso, liga a escuta por voz e abre
                esta interface uma única vez.
              </p>
            </Panel>
            <Panel title="Idioma">
              <MetricRow label="Interface" value="Português (Portugal)" />
              <p className="dim" style={{ fontSize: 11, marginTop: 6 }}>
                Só está disponível uma língua nesta versão.
              </p>
            </Panel>
            <Panel title="Ambiente">
              <MetricRow label="Versão" value={settings.runtime?.version ?? "—"} />
              <MetricRow label="Python" value={settings.runtime?.python ?? "—"} />
              <MetricRow label="Plataforma" value={settings.runtime?.platform ?? "—"} />
              <MetricRow label="RAM total" value={`${settings.runtime?.ramTotalGb ?? "—"} GB`} />
            </Panel>
          </div>
        )}

        {/* ── APPEARANCE ───────────────────────────────────────────────── */}
        {section === "appearance" && (
          <div className="stack">
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

        {/* ── PRIVACY ──────────────────────────────────────────────────── */}
        {section === "privacy" && (
          <div className="stack">
            <Panel title="Onde correm os teus dados">
              <p className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>
                A memória, o histórico de conversa, a transcrição de voz e a deteção da
                wake phrase acontecem <strong>sempre neste computador</strong>.
                <br /><br />
                No modo Automático ou Cloud, o texto das tuas mensagens é enviado ao Groq
                para gerar a resposta. No modo Local, nada sai do computador.
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
          </div>
        )}

        {/* ── ADVANCED ─────────────────────────────────────────────────── */}
        {section === "advanced" && (
          <div className="stack">
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
