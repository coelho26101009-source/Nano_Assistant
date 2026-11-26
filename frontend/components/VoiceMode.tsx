/**
 * Full-screen voice mode: talk to NANO without the chat in the way.
 *
 * It is a view over the SAME voice turn the microphone button and the wake
 * phrase drive (start_voice_turn_from_ui, on_voice_phase, on_voice_exchange).
 * It adds no audio path of its own and opens the microphone only when the
 * person presses the button or Space.
 *
 * WHAT THE RING SHOWS, AND WHAT IT DOES NOT. The ring follows the turn's PHASE
 * (listening, transcribing, thinking, speaking). It is never driven by the
 * microphone level: the backend does not send one, and a ring that seemed to
 * react to a voice it was not measuring would be a fake. Listening is the
 * information colour, because an open microphone is a privacy state, and it
 * is never carried by colour alone: the label says "A ouvir…" too.
 *
 * The exchange shown is the last one in the conversation, so leaving voice mode
 * finds the same turns in the chat, already saved.
 */
import React, { useEffect, useRef } from "react";

import Icon from "./Icon";
import NanoLogo from "./NanoLogo";
import Nubi, { type NubiMood } from "./Nubi";
import { useMascot } from "../lib/desktop";
import { t } from "../lib/i18n";
import { BRAND_NAME } from "../lib/brand";

/** The phases core/voice.py emits, in the interface language. */
const PHASE_LABEL: Record<string, string> = {
  WAKE_DETECTED: t("A acordar…"),
  COMMAND_LISTENING: t("A ouvir…"),
  TRANSCRIBING: t("A transcrever…"),
  PROCESSING: t("A pensar…"),
  SPEAKING: t("A falar…"),
};

export type VoiceModeState = "idle" | "listening" | "working" | "speaking";

/** The ring's state, from the phase. Anything unknown is idle. */
export function voiceModeState(phase: string | undefined, listening: boolean): VoiceModeState {
  if (listening || phase === "COMMAND_LISTENING" || phase === "WAKE_DETECTED") return "listening";
  if (phase === "TRANSCRIBING" || phase === "PROCESSING") return "working";
  if (phase === "SPEAKING") return "speaking";
  return "idle";
}

/** Nubi's face for each state: she listens, thinks, and smiles while she speaks. */
export const NUBI_MOOD: Record<VoiceModeState, NubiMood> = {
  idle: "calm", listening: "listening", working: "thinking", speaking: "happy",
};

export default function VoiceMode({
  open, phase, listening, voiceReady, lastQuestion, lastAnswer,
  onTalk, onStop, onClose,
}: {
  open: boolean;
  phase?: string;
  listening: boolean;
  /** Whether a voice turn can start at all (voice set up and not busy). */
  voiceReady: boolean;
  lastQuestion?: string;
  lastAnswer?: string;
  onTalk: () => void;
  /** Cancel listening, or stop the answer being spoken. */
  onStop: () => void;
  onClose: () => void;
}) {
  const state = voiceModeState(phase, listening);
  const mascot = useMascot();
  const talkRef = useRef<HTMLButtonElement>(null);

  useEffect(() => { if (open) talkRef.current?.focus(); }, [open]);

  // Space talks (or stops), Esc leaves. Space is ignored while a button other
  // than the main one has focus, so it still presses that button.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); return; }
      if (event.key === " " && (event.target === document.body || event.target === talkRef.current)) {
        event.preventDefault();
        if (state === "idle") { if (voiceReady) onTalk(); } else onStop();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, state, voiceReady, onTalk, onStop, onClose]);

  if (!open) return null;

  const label = state === "idle"
    ? (voiceReady ? t("Carrega no botão ou em Espaço para falar") : t("A voz não está pronta. Abre Definições → Voz."))
    : PHASE_LABEL[phase ?? ""] ?? t("A ouvir…");
  const busy = state !== "idle";

  return (
    <div className="voice-mode" role="dialog" aria-modal="true" aria-label={t("Modo de voz")}>
      <button type="button" className="voice-mode__close" onClick={onClose}
              aria-label={t("Sair do modo de voz (Esc)")} title={t("Sair do modo de voz (Esc)")}>
        <Icon name="close" size={20} />
      </button>

      <div className="voice-mode__stage">
        <div className="voice-mode__orb" data-state={state} aria-hidden="true">
          <span className="voice-mode__ring" />
          {mascot ? <Nubi size={150} mood={NUBI_MOOD[state]} /> : <NanoLogo size={88} />}
        </div>
        <p className="voice-mode__status" role="status" aria-live="polite">{label}</p>

        {(lastQuestion || lastAnswer) && (
          <div className="voice-mode__exchange">
            {lastQuestion && <p className="voice-mode__you"><span className="sr-only">{t("Tu")}: </span>{lastQuestion}</p>}
            {lastAnswer && (
              <p className="voice-mode__answer">
                <span className="sr-only">{BRAND_NAME}: </span>{lastAnswer}
              </p>
            )}
          </div>
        )}
      </div>

      <div className="voice-mode__controls">
        <button
          ref={talkRef} type="button"
          className={`voice-mode__talk${busy ? " voice-mode__talk--busy" : ""}`}
          data-state={state}
          disabled={!busy && !voiceReady}
          onClick={busy ? onStop : onTalk}
          aria-label={state === "listening" ? t("Cancelar a escuta")
            : state === "speaking" || state === "working" ? t("Parar")
              : t("Falar com o {0}", { 0: BRAND_NAME })}
        >
          {busy ? <Icon name="stop" size={22} fill /> : <Icon name="mic" size={28} />}
        </button>
      </div>
    </div>
  );
}
