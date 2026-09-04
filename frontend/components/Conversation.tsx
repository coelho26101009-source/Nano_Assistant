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
import NanoLogo, { NanoAvatar, NanoWordmark } from "./NanoLogo";
import { Button, StatusIndicator, formatTime } from "./ui";

export type ToolEvent = {
  name: string;
  state: "running" | "ok" | "error" | "approval";
  summary?: string;
  detail?: string;
};

/** One provider this turn asked, and what came back. See core/response_meta.py. */
export type ProviderAttempt = {
  provider: string;
  model?: string;
  /** "ok", or one of the reason categories below. */
  outcome?: string;
};

/**
 * Safe per-response diagnostics. Never contains prompts, arguments or keys.
 *
 * THIS DESCRIBES ONE MESSAGE, NOT THE CURRENT SETTINGS. The top-bar pill reads
 * `providers.route`, which answers "who would reply if you asked right now".
 * These fields answer "who DID reply to this one", and the two disagree
 * legitimately — after a failover, and after the user changes model between
 * turns. Rendering the live route here would rewrite history, so nothing in
 * this panel may come from anywhere but the message's own metadata.
 *
 * Shaped by an allow-list in Python, which is also what the row on disk holds,
 * so a reopened thread shows exactly the same panel.
 */
export interface ResponseMeta {
  provider?: string;
  model?: string;
  mode?: string;
  tier?: string;
  task?: string;
  fallback_used?: boolean;
  /** The provider that was asked FIRST, when it is not the one that answered. */
  fallback_from?: string;
  /** Machine-readable category from core/response_meta.REASON_CATEGORIES. */
  fallback_reason?: string;
  /** Every hop this turn made, in order. */
  provider_attempts?: ProviderAttempt[];
  attempted_provider?: string;
  attempted_model?: string;
  local_model?: string;
  retry_in_seconds?: number;
  tools_offered?: number;
  tools_available?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  time_to_first_token_ms?: number;
  total_latency_ms?: number;
}

/** Provider ids as a person says them. Unknown ids print as they arrived. */
const PROVIDER_LABEL: Record<string, string> = {
  google: "Google Gemini",
  groq: "Groq",
  mistral: "Mistral",
  ollama: "Ollama (local)",
};

export function providerName(id?: string): string {
  const key = String(id ?? "").trim().toLowerCase();
  if (!key) return "—";
  return PROVIDER_LABEL[key] ?? key;
}

/**
 * Why a turn did not go to the provider the user picked, in one plain sentence.
 *
 * The backend sends a category, never a provider's own error string: "UNAVAILABLE
 * This model is currently experiencing high demand" is a raw backend exception
 * and does not belong on screen. The sentences live here because they are UI
 * copy; the vocabulary lives in Python because routing decides it.
 */
const REASON_TEXT: Record<string, string> = {
  rate_limit: "limite temporário atingido",
  timeout: "demorou demasiado a responder",
  unavailable: "não foi possível contactar",
  provider_error: "o serviço do provedor falhou",
  auth: "a chave de API foi recusada",
  bad_request: "o pedido foi recusado",
  model_unavailable: "o modelo não está disponível nesta conta",
  cooldown: "em pausa após uma falha recente",
  setup_required: "falta configurar a chave ou o modelo",
  cancelled: "o pedido foi cancelado",
  no_cloud_available: "nenhum provedor cloud estava disponível",
  cloud_mode: "modo Cloud: sem recurso ao modelo local",
  partial_answer: "a resposta foi interrompida a meio",
  routing_bug: "erro de encaminhamento interno",
  other: "motivo não classificado",
};

export function reasonText(reason?: string): string {
  const key = String(reason ?? "").trim();
  if (!key) return "";
  return REASON_TEXT[key] ?? key;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  streaming?: boolean;
  tools?: ToolEvent[];
  error?: boolean;
  meta?: ResponseMeta;
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

/* ── Technical details ────────────────────────────────────────────────── */

const CHEVRON = "m6 9 6 6 6-6";

/**
 * The per-message diagnostics disclosure.
 *
 * WHY IT IS NOT A `<details>` ANY MORE. The native element gave a summary with
 * a text-glyph caret, no hover surface and no focus ring worth the name — a
 * control that read as a caption. It is now a real button with `aria-expanded`
 * pointing at the region it opens, which is what a screen reader needs and what
 * lets the caret rotate instead of being swapped for a different character.
 *
 * It stays SMALL on purpose. This is secondary information sitting under an
 * answer; a full-width button would compete with the thing the user came to
 * read. The affordance comes from a hairline surface that only fills in on
 * hover, focus and open — not from size.
 */
function TechnicalDetails({ meta, id }: { meta: ResponseMeta; id: string }) {
  const [open, setOpen] = useState(false);
  const panelId = `meta-${id}`;

  const attempts = meta.provider_attempts ?? [];
  // Only worth drawing when the turn actually crossed a provider. One hop is
  // already stated by "Provedor" above it, and repeating it as a chain would be
  // a diagram of nothing.
  const crossed = attempts.length > 1;
  const reason = reasonText(meta.fallback_reason);
  const fellBack = Boolean(meta.fallback_used);
  const from = meta.fallback_from ?? meta.attempted_provider;

  return (
    <div className="msg__meta">
      <button
        type="button"
        className="msg__meta-toggle"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="msg__meta-caret" aria-hidden="true">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d={CHEVRON} />
          </svg>
        </span>
        <span>Detalhes técnicos</span>
        {fellBack && <span className="msg__meta-tag">fallback</span>}
      </button>

      <div id={panelId} className="msg__meta-panel" hidden={!open}>
        <dl className="kv">
          <dt>Provedor</dt>
          <dd>
            {providerName(meta.provider)}
            {fellBack ? " · fallback" : ""}
          </dd>
          <dt>Modelo</dt><dd>{meta.model}</dd>
          <dt>Modo</dt><dd>{[meta.mode, meta.tier].filter(Boolean).join(" · ")}</dd>
          {fellBack && from && (
            <>
              <dt>Origem</dt>
              <dd>{providerName(from)} → {providerName(meta.provider)}</dd>
            </>
          )}
          {reason && (
            <>
              <dt>Motivo</dt>
              <dd>
                {reason}
                {meta.retry_in_seconds ? ` · ~${Math.round(meta.retry_in_seconds)} s` : ""}
              </dd>
            </>
          )}
          <dt>Pedido</dt><dd>{meta.task}</dd>
          <dt>Ferramentas</dt>
          <dd>{meta.tools_offered ?? 0} de {meta.tools_available ?? 0}</dd>
          {meta.prompt_tokens != null && (
            <>
              <dt>Tokens</dt>
              <dd>{meta.prompt_tokens} entrada · {meta.completion_tokens ?? 0} saída</dd>
            </>
          )}
          {meta.time_to_first_token_ms != null && (
            <>
              <dt>1.º token</dt><dd>{meta.time_to_first_token_ms} ms</dd>
            </>
          )}
          {meta.total_latency_ms != null && (
            <>
              <dt>Total</dt><dd>{meta.total_latency_ms} ms</dd>
            </>
          )}
        </dl>

        {crossed && (
          <ol className="meta-chain" aria-label="Provedores tentados nesta resposta">
            {attempts.map((attempt, index) => (
              <li key={`${attempt.provider}-${index}`}
                  className={`meta-chain__step${attempt.outcome === "ok" ? " is-ok" : ""}`}>
                <span className="meta-chain__provider">{providerName(attempt.provider)}</span>
                {attempt.model && <span className="meta-chain__model">{attempt.model}</span>}
                <span className="meta-chain__outcome">
                  {attempt.outcome === "ok" ? "respondeu" : reasonText(attempt.outcome) || "sem resposta"}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>
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
    <article className={`msg msg--${message.role}${message.error ? " msg--error" : ""}`}>
      {/* The Nano mark IS the assistant's avatar. The user has no avatar: the
          bubble's own colour and its right alignment already say whose it is,
          and a second disc on the right only adds noise. */}
      {!isUser && (
        <NanoAvatar className="msg__avatar" size={34} active={message.streaming} title="Nano" />
      )}

      <div className="msg__main">
        {message.tools?.length ? (
          <div style={{ width: "100%" }}>
            {message.tools.map((tool, index) => <ToolCard key={`${tool.name}-${index}`} event={tool} />)}
          </div>
        ) : null}

        <div className="msg__bubble">
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

        <div className="msg__foot">
          <span className="msg__author">{isUser ? "Você" : "Nano"}</span>
          <span className="msg__time">{formatTime(message.timestamp)}</span>
          {!isUser && text && !message.streaming && (
            <span className="msg__actions">
              <Button variant="ghost" size="sm" onClick={copy} aria-label="Copiar resposta">
                {copied ? "Copiado" : "Copiar"}
              </Button>
            </span>
          )}
        </div>

        {/* Technical details, collapsed by default so normal chat stays clean.
            Safe metadata only: provider, model, tokens and latency — and it is
            THIS message's, including for a message loaded from an older thread. */}
        {!isUser && !message.streaming && message.meta?.model && (
          <TechnicalDetails meta={message.meta} id={message.id} />
        )}
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
      <div className="conversation conversation--hero">
        {/* The one place the full wordmark earns its size: an empty screen is
            the moment the product introduces itself. Everywhere else the mark
            alone carries the identity. */}
        <div className="chat-hero">
          <NanoLogo size={72} className="chat-hero__mark" title="Nano" />
          <NanoWordmark height={30} />
          <p className="chat-hero__hint">
            Pergunta alguma coisa, ou diz “Ei Nano”. Ações sensíveis pedem sempre a tua
            autorização antes de acontecerem.
          </p>
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

const ICON = {
  attach: "M21.4 11.05 12.25 20.2a5.5 5.5 0 0 1-7.78-7.78l9.19-9.19a3.67 3.67 0 0 1 5.19 5.19l-9.2 9.19a1.83 1.83 0 0 1-2.59-2.59l8.49-8.48",
  plus: "M12 5v14M5 12h14",
  mic: "M12 2a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3ZM19 10v1a7 7 0 0 1-14 0v-1M12 18v4",
  send: "M12 19V5M5 12l7-7 7 7",
  stop: "M7 7h10v10H7z",
} as const;

const Icon = ({ d, size = 17, fill = false }: { d: string; size?: number; fill?: boolean }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={fill ? "currentColor" : "none"}
       stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
       aria-hidden="true">
    <path d={d} />
  </svg>
);

/**
 * The message composer.
 *
 * The two round controls on the right are the reference's, and they are
 * deliberately different weights: the microphone is quiet glass, the send
 * button is the one saturated red object on the screen. Secondary actions sit
 * on the left as ghost buttons so they are available without competing.
 *
 * `readOnlyReason`, when set, means the user is looking at an older
 * conversation. The whole composer is disabled and says why — the Brain's
 * context holds only the live conversation, so a message typed here would be
 * answered against the wrong history.
 */
export function Composer({
  value, onChange, onSend, onStop, onVoice, onCancelVoice, onNew,
  thinking, disabled, voiceState, listening, suggestions, onSuggestion,
  readOnlyReason,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onVoice: () => void;
  onCancelVoice: () => void;
  onNew: () => void;
  thinking: boolean;
  disabled: boolean;
  voiceState: string;
  listening: boolean;
  suggestions: string[];
  onSuggestion: (text: string) => void;
  readOnlyReason?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const locked = disabled || Boolean(readOnlyReason);

  useEffect(() => { if (!locked) ref.current?.focus(); }, [locked]);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 220)}px`;
  }, [value]);

  const voiceReady = voiceState === "READY";
  const voiceTitle = readOnlyReason
    ? readOnlyReason
    : listening
      ? "A ouvir — clica para cancelar"
      : voiceReady ? "Falar com o Nano (Ctrl+M)" : "Voz indisponível";

  // The placeholder and the line in the button bar are two halves of one
  // sentence, not the same sentence twice.
  const placeholder = readOnlyReason
    ? "Conversa anterior — só leitura."
    : disabled ? "A aguardar o motor do Nano…" : "Envie uma mensagem para o Nano…";

  return (
    <div className="composer-wrap">
      <div className="composer-inner">
        {suggestions.length > 0 && !value && !readOnlyReason && (
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
            value={value} disabled={locked}
            placeholder={placeholder}
            aria-describedby={readOnlyReason ? "composer-readonly" : undefined}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); onSend(); }
            }}
          />

          <div className="composer__bar">
            <Button
              variant="ghost" icon size="sm" onClick={onNew}
              aria-label="Nova conversa" title="Nova conversa (Ctrl+N)"
            >
              <Icon d={ICON.plus} size={17} />
            </Button>

            {/* Attachments are not supported by the backend yet. Shown disabled
                with the reason rather than silently doing nothing when clicked. */}
            <Button
              variant="ghost" icon size="sm" disabled
              title="Anexos: brevemente" aria-label="Anexar ficheiro (brevemente)"
            >
              <Icon d={ICON.attach} size={16} />
            </Button>

            {listening && (
              <span className="mic-indicator" role="status">
                <span className="mic-indicator__wave" aria-hidden="true"><i /><i /><i /></span>
                A ouvir…
              </span>
            )}
            {!listening && !voiceReady && !readOnlyReason && (
              <StatusIndicator state={voiceState} label="voz" />
            )}
            {readOnlyReason && (
              <span id="composer-readonly" className="dim" style={{ fontSize: 12 }}>
                {readOnlyReason}
              </span>
            )}

            <span className="composer__spacer" />
            {!locked && <span className="composer__hint"><kbd>Enter</kbd> enviar</span>}

            <button
              type="button"
              className={`composer__mic${listening ? " composer__mic--live" : ""}`}
              onClick={listening ? onCancelVoice : onVoice}
              disabled={locked || thinking || (!voiceReady && !listening)}
              aria-label={voiceTitle} title={voiceTitle}
            >
              {listening ? <Icon d={ICON.stop} size={15} fill /> : <Icon d={ICON.mic} size={17} />}
            </button>

            {thinking ? (
              <button
                type="button" className="composer__send" onClick={onStop}
                aria-label="Parar a resposta" title="Parar"
              >
                <Icon d={ICON.stop} size={16} fill />
              </button>
            ) : (
              <button
                type="button" className="composer__send" onClick={onSend}
                disabled={locked || !value.trim()}
                aria-label="Enviar mensagem"
                title={readOnlyReason ?? "Enviar (Enter)"}
              >
                <Icon d={ICON.send} size={19} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
