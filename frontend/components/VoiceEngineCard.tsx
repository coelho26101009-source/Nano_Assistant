/**
 * "Voz do NANO": which voice reads the answers (core/speech_engines.py).
 *
 * The engine (automatic, Microsoft online, the offline voice on this PC,
 * ElevenLabs with the person's own key, or the basic Windows voice), the
 * voice itself, the speed, and an "Ouvir" button that reads a sample and says
 * which engine really spoke. The offline voice installs from here with one
 * click (core/voice_local_pack.py). The ElevenLabs key is write-only, like
 * every other key: the window only ever sees a masked hint.
 */
import React, { useCallback, useEffect, useState } from "react";
import { call, type SecretInfo } from "../lib/backend";
import { Badge, Button, Field, Meter, Panel, SecretField, SegmentedControl } from "./ui";
import { t } from "../lib/i18n";

type Option = { id: string; label: string };
type EngineId = "auto" | "edge" | "local" | "elevenlabs" | "windows";
type EngineStatus = { available: boolean; coolingSeconds: number; lastError: string };

type LocalPack = {
  ready: boolean;
  packageInstalled: boolean;
  modelReady: boolean;
  sizeMb: number;
  licence: string;
  install?: { state: string; message?: string; percent?: number; completedMb?: number; totalMb?: number };
};

export type SpeechSettings = {
  ok: boolean;
  language: string;
  engine: EngineId;
  engines: { id: EngineId; label: string; available: boolean }[];
  edgeVoice: string;
  edgeVoices: Option[];
  localVoice: string;
  localVoices: Option[];
  elevenlabs: { key: SecretInfo; voice: string; model: string; models: string[] };
  speed: number;
  localMode: boolean;
  localPack: LocalPack;
  status: { engines: Record<string, EngineStatus>; lastEngine: string; lastVoice: string; fallbackReason: string };
};

type Preview = { ok: boolean; engine?: string; voice?: string; fellBack?: boolean; detail?: string };

const ENGINE_NAMES: Record<string, string> = {
  edge: "Microsoft", local: "Offline", elevenlabs: "ElevenLabs", windows: "Windows",
};

function engineName(id: string): string {
  return id === "local" ? t("Voz offline") : (ENGINE_NAMES[id] ?? id);
}

export default function VoiceEngineCard() {
  const [data, setData] = useState<SpeechSettings | null>(null);
  const [speed, setSpeed] = useState(0);
  const [elVoices, setElVoices] = useState<Option[]>([]);
  const [message, setMessage] = useState("");
  const [previewing, setPreviewing] = useState(false);

  const refresh = useCallback(async () => {
    const next = await call<SpeechSettings>("get_speech_settings");
    if (next?.ok) {
      setData(next);
      setSpeed(next.speed);
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const installState = data?.localPack.install?.state ?? "idle";
  const installing = installState === "installing" || installState === "downloading" || installState === "extracting";
  useEffect(() => {
    if (!installing) return;
    const timer = window.setInterval(() => { void refresh(); }, 1500);
    return () => window.clearInterval(timer);
  }, [installing, refresh]);

  const keyConfigured = Boolean(data?.elevenlabs.key.configured);
  const loadElevenLabsVoices = useCallback(async () => {
    const result = await call<{ ok: boolean; voices: Option[]; detail?: string }>("list_elevenlabs_voices");
    if (result?.ok) setElVoices(result.voices);
    else if (result?.detail) setMessage(result.detail);
  }, []);
  useEffect(() => {
    if (data?.engine === "elevenlabs" && keyConfigured && elVoices.length === 0) void loadElevenLabsVoices();
  }, [data?.engine, keyConfigured, elVoices.length, loadElevenLabsVoices]);

  if (!data) return null;

  const update = async (key: string, value: unknown) => {
    setMessage("");
    await call("update_setting", key, value);
    await refresh();
  };

  const preview = async () => {
    setPreviewing(true);
    setMessage("");
    try {
      const result = await call<Preview>("preview_voice", data.engine);
      if (!result) return;
      if (!result.ok) setMessage(result.detail || t("Nenhuma voz conseguiu falar."));
      else if (result.fellBack) setMessage(t("A voz escolhida não respondeu, por isso falou: {0}.", { 0: `${engineName(result.engine ?? "")} · ${result.voice ?? ""}` }));
      else setMessage(t("Ouviste: {0}.", { 0: `${engineName(result.engine ?? "")} · ${result.voice ?? ""}` }));
    } finally {
      setPreviewing(false);
      void refresh();
    }
  };

  const installLocal = async () => {
    setMessage("");
    const result = await call<{ ok: boolean; detail?: string }>("start_local_voice_setup");
    if (!result?.ok) setMessage(result?.detail || t("Não foi possível começar a instalação da voz offline."));
    void refresh();
  };

  const saveKey = async (key: string) => {
    const result = await call<{ ok: boolean; voices?: Option[]; detail?: string }>("set_elevenlabs_api_key", key);
    if (result?.voices?.length) setElVoices(result.voices);
    setMessage(result?.detail || (result?.ok ? t("Chave guardada.") : t("Não consegui guardar a chave.")));
    await refresh();
  };
  const removeKey = async () => {
    await call("remove_elevenlabs_api_key");
    setElVoices([]);
    await refresh();
  };

  const engine = data.engine;
  const pack = data.localPack;
  const last = data.status.lastEngine;
  const showEdge = engine === "auto" || engine === "edge";
  const showLocal = engine === "auto" || engine === "local";

  return (
    <Panel title={t("Voz do NANO")}>
      <p className="muted" style={{ fontSize: 13, marginBottom: 10 }}>
        {t("Se a voz escolhida falhar (sem internet, sem créditos), o NANO passa logo para a seguinte. Nunca fica calado.")}
      </p>
      {last && (
        <p className="dim" style={{ fontSize: 12, marginBottom: 10 }}>
          <Badge tone="info">{t("A falar")}</Badge>{" "}
          {engineName(last)}{data.status.lastVoice ? ` · ${data.status.lastVoice}` : ""}
          {data.status.fallbackReason ? ` ${t("(a voz principal falhou)")}` : ""}
        </p>
      )}

      <Field label={t("Motor de voz")}
             hint={engine === "auto"
               ? (data.localMode && pack.ready
                 ? t("Modo local: usa a voz offline primeiro, para nada sair do PC.")
                 : t("Microsoft online primeiro; sem internet, a voz offline (se instalada) e depois a do Windows."))
               : undefined}>
        <select className="select" value={engine}
                onChange={(e) => { void update("tts_engine", e.target.value); }}>
          {data.engines.map((option) => (
            <option key={option.id} value={option.id}
                    disabled={!option.available && option.id !== "local" && option.id !== "elevenlabs"}>
              {option.label}
            </option>
          ))}
        </select>
      </Field>

      {showEdge && data.edgeVoices.length > 0 && (
        <Field label={t("Voz online")}>
          <select className="select" value={data.edgeVoice}
                  onChange={(e) => { void update("tts_voice", e.target.value); }}>
            <option value="">{t("Automática (segue a língua)")}</option>
            {data.edgeVoices.map((voice) => <option key={voice.id} value={voice.id}>{voice.label}</option>)}
          </select>
        </Field>
      )}

      {showLocal && (
        pack.ready ? (
          <Field label={t("Voz offline")} hint={t("Corre no teu PC, sem internet. O texto nunca sai do computador.")}>
            <select className="select" value={data.localVoice}
                    onChange={(e) => { void update("tts_local_voice", e.target.value); }}>
              {data.localVoices.map((voice) => <option key={voice.id} value={voice.id}>{voice.label}</option>)}
            </select>
          </Field>
        ) : installing ? (
          <div style={{ marginBottom: 12 }}>
            <p className="muted" style={{ fontSize: 13, marginBottom: 6 }}>{pack.install?.message}</p>
            {installState === "downloading" && (
              <>
                <Meter value={pack.install?.percent ?? 0} tone="info" />
                <p className="dim" style={{ fontSize: 11, marginTop: 4 }}>
                  {pack.install?.totalMb ? `${pack.install?.completedMb ?? 0} / ${pack.install.totalMb} MB` : ""}
                </p>
              </>
            )}
          </div>
        ) : (
          <div style={{ marginBottom: 12 }}>
            <p className="muted" style={{ fontSize: 13, marginBottom: 8 }}>
              {t("Voz offline natural: o NANO fala mesmo sem internet, no teu PC, só com o processador. Descarrega cerca de {0} MB uma vez.", { 0: pack.sizeMb })}
            </p>
            <Button size="sm" onClick={() => { void installLocal(); }}>{t("Instalar voz offline")}</Button>
            {installState === "error" && <p className="dim" role="alert" style={{ fontSize: 12, marginTop: 6 }}>{pack.install?.message}</p>}
          </div>
        )
      )}

      {engine === "elevenlabs" && (
        <>
          <SecretField
            label={t("Chave da ElevenLabs")}
            masked={data.elevenlabs.key.masked}
            configured={keyConfigured}
            onSave={saveKey}
            onRemove={removeKey}
            onTest={async () => { await loadElevenLabsVoices(); await preview(); }}
            placeholder="sk_…"
            hint={t("As vozes mais humanas que existem. Pagas pela tua conta ElevenLabs (o plano grátis dá para uns minutos por mês). Sem créditos, o NANO volta sozinho à voz grátis.")}
          />
          {keyConfigured && (
            <>
              <Field label={t("Voz da ElevenLabs")}>
                <select className="select" value={data.elevenlabs.voice}
                        onChange={(e) => { void update("tts_elevenlabs_voice", e.target.value); }}>
                  <option value="">{t("Daniel (britânico, calmo)")}</option>
                  {elVoices.map((voice) => <option key={voice.id} value={voice.id}>{voice.label}</option>)}
                </select>
              </Field>
              <Field label={t("Qualidade")}>
                <SegmentedControl<string>
                  label={t("Qualidade")} value={data.elevenlabs.model}
                  onChange={(value) => { void update("tts_elevenlabs_model", value); }}
                  options={[
                    { value: "eleven_flash_v2_5", label: t("Rápida"), hint: t("Começa a falar mais depressa e gasta metade dos créditos.") },
                    { value: "eleven_multilingual_v2", label: t("Máxima"), hint: t("A mais natural, um pouco mais lenta.") },
                  ]}
                />
              </Field>
            </>
          )}
        </>
      )}

      <Field label={t("Velocidade: {0}%", { 0: speed > 0 ? `+${speed}` : String(speed) })}>
        <input type="range" min={-30} max={30} step={5} value={speed}
               aria-label={t("Velocidade da voz")}
               onChange={(e) => setSpeed(Number(e.target.value))}
               onPointerUp={() => { if (speed !== data.speed) void update("tts_speed", speed); }}
               onKeyUp={() => { if (speed !== data.speed) void update("tts_speed", speed); }} />
      </Field>

      <div className="inline">
        <Button size="sm" variant="primary" onClick={() => { void preview(); }} disabled={previewing}>
          {previewing ? t("A falar…") : t("Ouvir")}
        </Button>
      </div>
      {message && <p className="dim" role="status" style={{ fontSize: 12, marginTop: 8 }}>{message}</p>}
    </Panel>
  );
}
