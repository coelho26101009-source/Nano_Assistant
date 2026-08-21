/**
 * Conversation view, message rendering and composer.
 *
 * Two rules shape this file:
 *
 * 1. Internal model output never reaches the screen. Reasoning markers such as
 *    `/think`, `<think>` blocks and `_thinking_:` status lines are stripped
 *    before render — the user sees an answer, not the machinery.
 * 2. Tool activity is summarised as a readable line ("A ler ficheiro… ✓
 *    README.md"), with the raw payload behind a disclosure for anyone who
 *    wants it. Raw JSON is never dumped inline.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import NanoLogo from "./NanoLogo";
import { Button, EmptyState, StatusIndicator, formatTime } from "./ui";

export type ToolEvent = {
  name: string;
  state: "running" | "ok" | "error" | "approval";
  summary?: string;
  detail?: string;
};

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  streaming?: boolean;
  tools?: ToolEvent[];
  error?: boolean;
}

/* ── Cleaning model output ────────────────────────────────────────────── */

/** Strip reasoning artefacts some models emit into their visible answer. */
export function cleanAssistantText(raw: string): string {
  if (!raw) return "";
  let text = raw;
  text = text.replace(/<think>[\s\S]*?<\/think>/gi, "");
  text = text.replace(/<\|?(?:begin|end)_of_thought\|?>/gi, "");
  // Trailing control tokens like "/think" or "/no_think" that qwen-style
  // models append; only stripped at a boundary so real prose is untouched.
  text = text.replace(/(^|\s)\/(?:no_)?think\b/gi, "");
  text = text.replace(/^\s*(?:analysis|assistantfinal)\s*:?\s*/i, "");
  return text.trim();
}

/* ── Minimal markdown ─────────────────────────────────────────────────── */

function renderInline(text: string, keyBase: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\((https?:\/\/[^)\s]+)\))/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let index = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("`")) {
      nodes.push(<code key={`${keyBase}-c${index}`}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**")) {
      nodes.push(<strong key={`${keyBase}-b${index}`}>{token.slice(2, -2)}</strong>);
    } else {
      const label = token.slice(1, token.indexOf("]"));
      const href = match[4];
      nodes.push(
        <a key={`${keyBase}-a${index}`} href={href} target="_blank" rel="noopener noreferrer">{label}</a>
      );
    }
    last = match.index + token.length;
    index += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch { /* clipboard blocked; the code is still selectable */ }
  };
  return (
    <div className="code-block">
      <div className="code-block__head">
        <span>{lang || "código"}</span>
        <span className="code-block__spacer" />
        <Button variant="ghost" size="sm" onClick={copy} aria-label="Copiar código">
          {copied ? "Copiado" : "Copiar"}
        </Button>
      </div>
      <pre><code>{code}</code></pre>
    </div>
  );
}

/** Block-level markdown: fences, tables, lists, headings, paragraphs. */
function Markdown({ text }: { text: string }) {
  const blocks = useMemo(() => {
    const out: React.ReactNode[] = [];
    const lines = text.split("\n");
    let i = 0;
    let key = 0;

    const flushParagraph = (buffer: string[]) => {
      if (!buffer.length) return;
      out.push(<p key={`p${key++}`}>{renderInline(buffer.join(" "), `p${key}`)}</p>);
      buffer.length = 0;
    };

    let paragraph: string[] = [];

    while (i < lines.length) {
      const line = lines[i];

      // Fenced code
      if (line.trimStart().startsWith("```")) {
        flushParagraph(paragraph);
        const lang = line.trim().slice(3).trim();
        const body: string[] = [];
        i += 1;
        while (i < lines.length && !lines[i].trimStart().startsWith("```")) { body.push(lines[i]); i += 1; }
        i += 1;
        out.push(<CodeBlock key={`code${key++}`} code={body.join("\n")} lang={lang} />);
        continue;
      }

      // Table (header + separator + rows)
      if (line.includes("|") && i + 1 < lines.length && /^\s*\|?[\s:-]+\|[\s:|-]*$/.test(lines[i + 1])) {
        flushParagraph(paragraph);
        const cells = (row: string) => row.split("|").map((c) => c.trim()).filter((c, idx, arr) => !(c === "" && (idx === 0 || idx === arr.length - 1)));
        const header = cells(line);
        i += 2;
        const rows: string[][] = [];
        while (i < lines.length && lines[i].includes("|")) { rows.push(cells(lines[i])); i += 1; }
        out.push(
          <div className="md-table-wrap" key={`t${key++}`}>
            <table className="md-table">
              <thead><tr>{header.map((h, hi) => <th key={hi}>{renderInline(h, `th${hi}`)}</th>)}</tr></thead>
              <tbody>{rows.map((row, ri) => (
                <tr key={ri}>{row.map((cell, ci) => <td key={ci}>{renderInline(cell, `td${ri}${ci}`)}</td>)}</tr>
              ))}</tbody>
            </table>
          </div>
        );
        continue;
      }

      // Lists
      const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
      const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      if (bullet || numbered) {
        flushParagraph(paragraph);
        const ordered = Boolean(numbered);
        const items: string[] = [];
        while (i < lines.length) {
          const b = /^\s*[-*+]\s+(.*)$/.exec(lines[i]);
          const n = /^\s*\d+[.)]\s+(.*)$/.exec(lines[i]);
          if (ordered && n) items.push(n[1]);
          else if (!ordered && b) items.push(b[1]);
          else break;
          i += 1;
        }
        const content = items.map((item, ii) => <li key={ii}>{renderInline(item, `li${ii}`)}</li>);
        out.push(ordered ? <ol key={`l${key++}`}>{content}</ol> : <ul key={`l${key++}`}>{content}</ul>);
        continue;
      }

      // Heading
      const heading = /^(#{1,4})\s+(.*)$/.exec(line);
      if (heading) {
        flushParagraph(paragraph);
        out.push(<h3 key={`h${key++}`}>{renderInline(heading[2], `h${key}`)}</h3>);
        i += 1;
        continue;
      }

      if (!line.trim()) { flushParagraph(paragraph); i += 1; continue; }
      paragraph.push(line);
      i += 1;
    }
    flushParagraph(paragraph);
    return out;
  }, [text]);

  return <>{blocks}</>;
}

/* ── Tool activity ────────────────────────────────────────────────────── */

const TOOL_ICON: Record<ToolEvent["state"], string> = {
  running: "◌", ok: "✓", error: "✕", approval: "⛨",
};

function ToolCard({ event }: { event: ToolEvent }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`tool-card tool-card--${event.state}`}>
      <div className="tool-card__head">
        <span className="tool-card__icon" aria-hidden="true">{TOOL_ICON[event.state]}</span>
        <span className="tool-card__title">
          {event.summary || event.name}
        </span>
        {event.detail && (
          <button type="button" className="tool-card__toggle" onClick={() => setOpen((v) => !v)}
                  aria-expanded={open} aria-label="Detalhes técnicos">
            {open ? "ocultar" : "detalhes"}
          </button>
        )}
      </div>
      {open && event.detail && <div className="tool-card__details">{event.detail}</div>}
    </div>
  );
}

/* ── Messages ─────────────────────────────────────────────────────────── */

function MessageBubble({ message }: { message: Message }) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";
  const text = isUser ? message.content : cleanAssistantText(message.content);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch { /* clipboard unavailable */ }
  };

  return (
    <article className={`msg msg--${message.role}`}>
      <div className="msg__avatar" aria-hidden="true">
        {isUser ? "S" : <NanoLogo size={16} bare active={message.streaming} />}
      </div>
      <div className="msg__main">
        <header className="msg__head">
          <span className="msg__author">{isUser ? "Você" : "Nano"}</span>
          <span className="msg__time">{formatTime(message.timestamp)}</span>
          {!isUser && text && !message.streaming && (
            <span className="msg__actions">
              <Button variant="ghost" size="sm" onClick={copy} aria-label="Copiar resposta">
                {copied ? "Copiado" : "Copiar"}
              </Button>
            </span>
          )}
        </header>

        {message.tools?.length ? (
          <div>{message.tools.map((tool, index) => <ToolCard key={`${tool.name}-${index}`} event={tool} />)}</div>
        ) : null}

        <div className="msg__body">
          {isUser ? text : <Markdown text={text} />}
          {message.streaming && !text && (
            <span className="thinking">
              <span className="thinking__dots" aria-hidden="true"><i /><i /><i /></span>
              <span>O Nano está a pensar…</span>
            </span>
          )}
          {message.streaming && text && <span className="caret" aria-hidden="true" />}
        </div>
      </div>
    </article>
  );
}

export function Conversation({
  messages, status, thinking,
}: { messages: Message[]; status: string; thinking: boolean }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [messages, status]);

  if (!messages.length && !thinking) {
    return (
      <div className="conversation">
        <div className="conversation__inner">
          <EmptyState
            icon={<NanoLogo size={40} />}
            title="Pronto quando quiseres"
            hint="Pergunta alguma coisa, ou diz “Hey Nano”. Ações sensíveis pedem sempre a tua autorização antes de acontecerem."
          />
        </div>
      </div>
    );
  }

  return (
    <div className="conversation" role="log" aria-live="polite" aria-label="Conversa">
      <div className="conversation__inner">
        {messages.map((message) => <MessageBubble key={message.id} message={message} />)}
        {thinking && status && (
          <div className="thinking">
            <span className="thinking__dots" aria-hidden="true"><i /><i /><i /></span>
            <span>{status}</span>
          </div>
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}

/* ── Composer ─────────────────────────────────────────────────────────── */

export function Composer({
  value, onChange, onSend, onStop, onVoice, onCancelVoice,
  thinking, disabled, voiceState, listening, suggestions, onSuggestion,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onVoice: () => void;
  onCancelVoice: () => void;
  thinking: boolean;
  disabled: boolean;
  voiceState: string;
  listening: boolean;
  suggestions: string[];
  onSuggestion: (text: string) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { if (!disabled) ref.current?.focus(); }, [disabled]);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 220)}px`;
  }, [value]);

  const voiceReady = voiceState === "READY";
  const voiceTitle = listening
    ? "A ouvir — clica para cancelar"
    : voiceReady ? "Falar com o Nano (Ctrl+M)" : `Voz indisponível`;

  return (
    <div className="composer-wrap">
      <div className="composer-inner">
        {suggestions.length > 0 && !value && (
          <div className="suggestions">
            {suggestions.map((text) => (
              <button key={text} type="button" className="suggestion" onClick={() => onSuggestion(text)}>
                {text}
              </button>
            ))}
          </div>
        )}

        <div className={`composer${listening ? " composer--listening" : ""}`}>
          <label className="sr-only" htmlFor="composer-input">Mensagem para o Nano</label>
          <textarea
            id="composer-input" ref={ref} className="composer__textarea" rows={1}
            value={value} disabled={disabled}
            placeholder={disabled ? "A aguardar o motor do Nano…" : "Pergunte ou peça alguma coisa ao Nano..."}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); onSend(); }
            }}
          />
          <div className="composer__bar">
            {/* Attachments are not supported by the backend yet. The control is
                shown disabled with the reason rather than silently doing
                nothing when clicked. */}
            <Button variant="ghost" icon size="sm" disabled title="Anexos: brevemente" aria-label="Anexar ficheiro (brevemente)">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">
                <path d="M21.4 11.05 12.25 20.2a5.5 5.5 0 0 1-7.78-7.78l9.19-9.19a3.67 3.67 0 0 1 5.19 5.19l-9.2 9.19a1.83 1.83 0 0 1-2.59-2.59l8.49-8.48" />
              </svg>
            </Button>

            <Button
              variant="ghost" icon size="sm"
              onClick={listening ? onCancelVoice : onVoice}
              disabled={disabled || thinking || (!voiceReady && !listening)}
              aria-label={voiceTitle} title={voiceTitle}
            >
              {listening ? "■" : (
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">
                  <path d="M12 2a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3Z" />
                  <path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4" />
                </svg>
              )}
            </Button>

            {listening && (
              <span className="mic-indicator" role="status">
                <span className="mic-indicator__wave" aria-hidden="true"><i /><i /><i /></span>
                A ouvir…
              </span>
            )}
            {!listening && !voiceReady && <StatusIndicator state={voiceState} label="voz" />}

            <span className="composer__spacer" />
            <span className="composer__hint"><kbd>Enter</kbd> enviar</span>

            {thinking ? (
              <Button variant="danger" size="sm" onClick={onStop}>Parar</Button>
            ) : (
              <button
                type="button" className="composer__send" onClick={onSend}
                disabled={disabled || !value.trim()} aria-label="Enviar mensagem"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="m4 12 16-8-6 16-2-6-8-2Z" />
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
