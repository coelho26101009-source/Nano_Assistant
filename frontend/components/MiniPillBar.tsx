import React, { useState } from "react";

interface MiniPillBarProps {
  isThinking: boolean;
  statusText: string;
  onSend: (text: string) => void;
  onVoice: () => void;
  onStop: () => void;
  theme: "dark" | "light";
  onToggleTheme: () => void;
  eelReady: boolean;
}

export default function MiniPillBar({
  isThinking,
  statusText,
  onSend,
  onVoice,
  onStop,
  theme,
  onToggleTheme,
  eelReady
}: MiniPillBarProps) {
  const [miniInput, setMiniInput] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!miniInput.trim() || isThinking || !eelReady) return;
    onSend(miniInput.trim());
    setMiniInput("");
  };

  return (
    <div className="nano-mini-pill">
      <div className={`nano-mini-orb ${isThinking ? "active" : ""}`} />
      <span className="nano-mini-title">Nano</span>

      {statusText ? (
        <span className="nano-mini-status" title={statusText}>{statusText}</span>
      ) : null}

      <form onSubmit={handleSubmit} style={{ display: "flex", flex: 1, alignItems: "center", gap: 6 }}>
        <div className="nano-mini-input-box">
          <input
            type="text"
            className="nano-mini-input"
            value={miniInput}
            onChange={(e) => setMiniInput(e.target.value)}
            placeholder={statusText || (eelReady ? "Perguntar ao Nano..." : "A ligar...")}
            disabled={isThinking || !eelReady}
          />
        </div>
        <button
          type="submit"
          className="nano-mini-btn"
          disabled={!miniInput.trim() || isThinking || !eelReady}
          title="Enviar"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </form>

      {/* Botão de Parar de Falar quando estiver ativo */}
      {isThinking && (
        <button
          type="button"
          className="nano-mini-btn"
          onClick={onStop}
          title="Parar de Falar"
          style={{ color: "var(--red)" }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="6" width="12" height="12" rx="2" />
          </svg>
        </button>
      )}

      <button
        type="button"
        className="nano-mini-btn"
        onClick={onVoice}
        disabled={isThinking || !eelReady}
        title="Ouvir Voz"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
          <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
          <line x1="12" y1="19" x2="12" y2="23" />
          <line x1="8" y1="23" x2="16" y2="23" />
        </svg>
      </button>

      <button
        type="button"
        className="nano-mini-btn"
        onClick={onToggleTheme}
        title={theme === "dark" ? "Tema Claro" : "Tema Escuro"}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          {theme === "dark" ? (
            <>
              <circle cx="12" cy="12" r="5" />
              <line x1="12" y1="1" x2="12" y2="3" />
              <line x1="12" y1="21" x2="12" y2="23" />
              <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
              <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
              <line x1="1" y1="12" x2="3" y2="12" />
              <line x1="21" y1="12" x2="23" y2="12" />
            </>
          ) : (
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
          )}
        </svg>
      </button>
    </div>
  );
}
