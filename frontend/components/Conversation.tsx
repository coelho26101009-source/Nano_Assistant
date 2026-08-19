/** Conversation view and composer. Streaming, tool activity, voice, cancel. */
import React, { useEffect, useRef } from "react";
import { Button, EmptyState, StatusIndicator, ToolChip } from "./ui";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  streaming?: boolean;
  tools?: string[];
}

export function Conversation({
  messages,
  status,
  thinking,
}: {
  messages: Message[];
  status: string;
  thinking: boolean;
}) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, status]);

  if (!messages.length && !thinking) {
    return (
      <div className="conversation">
        <div className="conversation-inner">
          <EmptyState
            title="Pronto quando quiseres"
            hint="Pede ao Nano para inspecionar o projeto, correr os testes ou procurar algo. Ações sensíveis pedem sempre autorização."
          />
        </div>
      </div>
    );
  }

  return (
    <div className="conversation" role="log" aria-live="polite" aria-label="Conversa">
      <div className="conversation-inner">
        {messages.map((message) => (
          <article key={message.id} className={`msg msg--${message.role}`}>
            <header className="msg-role">
              <span className="msg-avatar" aria-hidden="true">{message.role === "user" ? "S" : "N"}</span>
              <span>{message.role === "user" ? "Tu" : "Nano"}</span>
            </header>
            <div className="msg-body">
              {message.content}
              {message.streaming && <span className="caret" aria-hidden="true" />}
            </div>
            {message.tools?.length ? (
              <div className="msg-tools">
                {message.tools.map((tool) => <ToolChip key={tool} name={tool} />)}
              </div>
            ) : null}
          </article>
        ))}

        {thinking && status && (
          <div className="activity-line">
            <span className="spinner" aria-hidden="true" />
            <span>{status}</span>
          </div>
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}

const SUGGESTIONS = [
  "Resume o estado do projeto",
  "Corre os testes do projeto",
  "Lista os ficheiros do workspace",
];

export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  onVoice,
  thinking,
  disabled,
  voiceState,
  showSuggestions,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onVoice: () => void;
  thinking: boolean;
  disabled: boolean;
  voiceState: string;
  showSuggestions: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!disabled) ref.current?.focus();
  }, [disabled]);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 200)}px`;
  }, [value]);

  const voiceReady = voiceState === "READY";

  return (
    <div className="composer-wrap">
      <div className="composer-inner">
        {showSuggestions && !value && (
          <div className="suggestions">
            {SUGGESTIONS.map((text) => (
              <button key={text} type="button" className="suggestion" onClick={() => onChange(text)}>
                {text}
              </button>
            ))}
          </div>
        )}

        <div className="composer">
          <label className="sr-only" htmlFor="composer-input">Mensagem para o Nano</label>
          <textarea
            id="composer-input"
            ref={ref}
            className="composer-textarea"
            rows={1}
            value={value}
            disabled={disabled}
            placeholder={disabled ? "A aguardar o motor do Nano…" : "Pergunta ou pede alguma coisa ao Nano…"}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                onSend();
              }
            }}
          />
          <div className="composer-bar">
            <Button
              variant="ghost"
              size="sm"
              icon
              onClick={onVoice}
              disabled={disabled || thinking || !voiceReady}
              aria-label={voiceReady ? "Falar com o Nano" : `Voz indisponível: ${voiceState}`}
              title={voiceReady ? "Falar (Ctrl+M)" : `Voz: ${voiceState}`}
            >
              ⏺
            </Button>
            {!voiceReady && <StatusIndicator state={voiceState} label="voz" />}
            <span className="composer-spacer" />
            <span className="composer-hint"><kbd>Enter</kbd> enviar · <kbd>Shift+Enter</kbd> linha</span>
            {thinking ? (
              <Button variant="danger" size="sm" onClick={onStop}>Parar</Button>
            ) : (
              <Button variant="primary" size="sm" onClick={onSend} disabled={disabled || !value.trim()}>
                Enviar
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
