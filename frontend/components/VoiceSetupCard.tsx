/**
 * The speech pack in one click (core/voice_setup.py).
 *
 * NANO already speaks after installing; to LISTEN it needs the local
 * transcriber (faster-whisper) and its model. One button installs both, with
 * progress. When it is done, the card ASKS whether to switch on the wake
 * phrase: nothing starts listening on its own.
 */
import React, { useCallback, useEffect, useState } from "react";
import { call } from "../lib/backend";
import { Badge, Button, Meter } from "./ui";
import { t } from "../lib/i18n";

export type VoiceSetup = {
  ok: boolean;
  ready?: boolean;
  packageInstalled?: boolean;
  modelCached?: boolean;
  model?: string;
  sizeMb?: number;
  lockAvailable?: boolean;
  wakePhrase?: string;
  wakePhraseEnabled?: boolean;
  install?: { state: string; message?: string; percent?: number; completedMb?: number; totalMb?: number };
};

export default function VoiceSetupCard({ compact = false, onWakePhrase }: {
  /** In the first-run guide: no heading, smaller text. */
  compact?: boolean;
  /** Turn the wake phrase on (the normal settings path). */
  onWakePhrase?: (enabled: boolean) => void;
}) {
  const [setup, setSetup] = useState<VoiceSetup | null>(null);
  const [error, setError] = useState("");
  const [wakeAnswered, setWakeAnswered] = useState(false);

  const refresh = useCallback(async () => {
    const next = await call<VoiceSetup>("get_voice_setup");
    if (next?.ok) setSetup(next);
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const state = setup?.install?.state ?? "idle";
  const working = state === "installing" || state === "downloading";
  useEffect(() => {
    if (!working) return;
    const timer = window.setInterval(() => { void refresh(); }, 1500);
    return () => window.clearInterval(timer);
  }, [working, refresh]);

  if (!setup) return null;

  const start = async () => {
    setError("");
    const result = await call<{ ok: boolean; detail?: string }>("start_voice_setup");
    if (!result?.ok) setError(result?.detail || t("Não foi possível começar a instalação da voz."));
    void refresh();
  };
  const phrase = setup.wakePhrase || "ei nano";

  return (
    <div className="voice-setup" style={{ fontSize: compact ? 12 : 13 }}>
      {setup.ready ? (
        <>
          <p className="muted" style={{ marginBottom: 8 }}>
            <Badge tone="info">{t("Pronta")}</Badge>{" "}
            {t("A voz está pronta: o NANO ouve-te com o atalho Ctrl + Shift + Space e responde em voz alta, a começar logo na primeira frase.")}
          </p>
          {!setup.wakePhraseEnabled && !wakeAnswered && onWakePhrase && (
            <div className="card" style={{ padding: 12, marginTop: 8 }}>
              <p style={{ marginBottom: 8 }}>{t("Queres ligar a frase de ativação “{0}”? O NANO fica à escuta no teu PC, sem enviar nada, e gasta um pouco de processador.", { 0: phrase })}</p>
              <div className="inline">
                <Button size="sm" variant="primary" onClick={() => { onWakePhrase(true); setWakeAnswered(true); void refresh(); }}>{t("Sim, ligar")}</Button>
                <Button size="sm" variant="ghost" onClick={() => setWakeAnswered(true)}>{t("Agora não")}</Button>
              </div>
            </div>
          )}
        </>
      ) : working ? (
        <div>
          <p className="muted" style={{ marginBottom: 6 }}>{setup.install?.message}</p>
          {state === "downloading" ? (
            <>
              <Meter value={setup.install?.percent ?? 0} tone="info" />
              <p className="dim" style={{ fontSize: 11, marginTop: 4 }}>
                {setup.install?.totalMb ? `${setup.install?.completedMb ?? 0} / ${setup.install.totalMb} MB` : ""}
              </p>
            </>
          ) : (
            <p className="dim" style={{ fontSize: 11 }}>{t("Pode demorar alguns minutos. Podes continuar a usar o NANO.")}</p>
          )}
        </div>
      ) : (
        <>
          <p className="muted" style={{ marginBottom: 8 }}>{t("Para o NANO te ouvir é preciso o pacote de fala: o reconhecimento de voz local e o seu modelo (cerca de {0} MB). Fica no teu PC; o que dizes não sai do computador para ser transcrito.", { 0: setup.sizeMb ?? 600 })}</p>
          <Button size="sm" variant="primary" onClick={() => { void start(); }}
                  disabled={setup.lockAvailable === false && !setup.packageInstalled}>
            {t("Preparar a voz")}
          </Button>
        </>
      )}
      {state === "error" && <p className="dim" role="alert" style={{ fontSize: 12, marginTop: 6 }}>{setup.install?.message}</p>}
      {error && <p className="dim" role="alert" style={{ fontSize: 12, marginTop: 6 }}>{error}</p>}
    </div>
  );
}
