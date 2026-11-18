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
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Icon from "./Icon";
import NanoLogo from "./NanoLogo";
import { Button, formatTime } from "./ui";
import { BRAND_NAME } from "../lib/brand";
import { t } from "../lib/i18n";
import {
  ACCEPT, MAX_FILES, formatSize, readAttachment,
  type AttachmentSummary, type PendingFile,
} from "../lib/attachments";

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
  google: t("Google Gemini"),
  groq: t("Groq"),
  mistral: t("Mistral"),
  ollama: t("Ollama (local)"),
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
  rate_limit: t("limite temporário atingido"),
  timeout: t("demorou demasiado a responder"),
  unavailable: t("não foi possível contactar"),
  provider_error: t("o serviço do provedor falhou"),
  auth: t("a chave de API foi recusada"),
  bad_request: t("o pedido foi recusado"),
  model_unavailable: t("o modelo não está disponível nesta conta"),
  cooldown: t("em pausa após uma falha recente"),
  setup_required: t("falta configurar a chave ou o modelo"),
  cancelled: t("o pedido foi cancelado"),
  no_cloud_available: t("nenhum provedor cloud estava disponível"),
  cloud_mode: t("modo Cloud: sem recurso ao modelo local"),
  partial_answer: t("a resposta foi interrompida a meio"),
  routing_bug: t("erro de encaminhamento interno"),
  other: t("motivo não classificado"),
};

/**
 * Reasons that mean "the cloud was never set up", not "the cloud failed".
 *
 * Someone who has only ever installed Ollama is not falling back to anything:
 * local IS their setup. Tagging every one of their answers "fallback" in amber
 * describes a failure that did not happen. A configured provider that failed
 * keeps the tag -- that is the case the tag exists for.
 */
export const EXPECTED_FALLBACK = new Set(["setup_required", "no_cloud_available"]);

export function reasonText(reason?: string): string {
  const key = String(reason ?? "").trim();
  if (!key) return "";
  return REASON_TEXT[key] ?? key;
}

/** An ambiguous PC target, offered as choices. See core/clarification.py. */
export type Clarification = {
  question: string;
  tool?: string;
  options: { label: string; reply: string }[];
  more?: number;
};

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  streaming?: boolean;
  tools?: ToolEvent[];
  error?: boolean;
  meta?: ResponseMeta;
  clarification?: Clarification;
  /** Text files sent with a user turn: names and sizes only. */
  attachments?: AttachmentSummary[];
}

/** The files on a sent message, or waiting in the composer. */
function FileChips({ files, onRemove }: {
  files: (AttachmentSummary & { id?: string })[];
  onRemove?: (index: number) => void;
}) {
  return (
    <ul className="file-chips" aria-label={t("Ficheiros anexados")}>
      {files.map((file, index) => (
        <li key={file.id ?? `${file.name}-${index}`} className="file-chip">
          <Icon name="attach" size={14} />
          <span className="file-chip__name" title={file.name}>{file.name}</span>
          <span className="file-chip__size">{formatSize(file.size)}</span>
          {file.truncated && (
            <span className="file-chip__note" title={t("Ficheiro longo: o NANO leu só o início.")}>
              {t("só o início")}
            </span>
          )}
          {onRemove && (
            <button
              type="button" className="file-chip__remove" onClick={() => onRemove(index)}
              aria-label={t("Remover {0}", { 0: file.name })} title={t("Remover")}
            >
              <Icon name="close" size={12} />
            </button>
          )}
        </li>
      ))}
    </ul>
  );
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

/* ── Code highlighting ────────────────────────────────────────────────── */

/**
 * A small, dependency-free highlighter: comments, strings, numbers, keywords.
 *
 * WHY NOT A LIBRARY. A full grammar engine is hundreds of kilobytes for what a
 * chat answer needs, and every dependency in this frontend is something the
 * security review has to keep reading. Four token classes cover what makes code
 * scannable at a glance; anything unrecognised is plain text, never wrong text.
 * React renders every token as a text node, so nothing here can inject markup.
 */
const KEYWORDS = new Set((
  "and as async await break case catch class const continue def default del do elif else enum " +
  "except export extends false finally fn for from func function go if impl import in interface " +
  "is lambda let match mod mut new nil none not null or package pass pub raise return self " +
  "static struct super switch this throw true try type typeof use var void while with yield " +
  "True False None echo fi then esac done local select where insert update delete create table"
).split(" "));

const TOKEN = new RegExp([
  String.raw`(\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/|--[^\n]*)`,          // 1 comment
  String.raw`("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|\x60(?:\\.|[^\x60\\])*\x60)`, // 2 string
  String.raw`(\b\d+(?:\.\d+)?\b)`,                                           // 3 number
  String.raw`([A-Za-z_][A-Za-z0-9_]*)`,                                     // 4 word
].join("|"), "g");

/** Languages where `#` starts a comment and `--` does not, and vice versa. */
const HASH_COMMENT = /^(py|python|sh|bash|shell|zsh|ps1|powershell|rb|ruby|yaml|yml|toml|r|perl)$/i;
const DASH_COMMENT = /^(sql|lua|haskell|hs)$/i;

export function highlight(code: string, lang?: string): React.ReactNode[] {
  const language = String(lang ?? "").trim();
  const out: React.ReactNode[] = [];
  let last = 0;
  let index = 0;
  let match: RegExpExecArray | null;
  TOKEN.lastIndex = 0;
  while ((match = TOKEN.exec(code)) !== null) {
    let kind = "";
    const [token, comment, str, num, word] = match;
    if (comment) {
      const hash = comment.startsWith("#");
      const dash = comment.startsWith("--");
      const slash = comment.startsWith("//") && HASH_COMMENT.test(language);
      if (slash || (hash && !HASH_COMMENT.test(language)) || (dash && !DASH_COMMENT.test(language))) {
        // Not a comment in this language: step one character and carry on.
        TOKEN.lastIndex = match.index + 1;
        continue;
      }
      kind = "comment";
    } else if (str) kind = "string";
    else if (num) kind = "number";
    else if (word && KEYWORDS.has(word)) kind = "keyword";
    if (!kind) continue;
    if (match.index > last) out.push(code.slice(last, match.index));
    out.push(<span key={index++} className={`tok-${kind}`}>{token}</span>);
    last = match.index + token.length;
  }
  if (last < code.length) out.push(code.slice(last));
  return out;
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
        <span>{lang || t("código")}</span>
        <span className="code-block__spacer" />
        <Button variant="ghost" size="sm" onClick={copy} aria-label={t("Copiar código")}>
          <Icon name={copied ? "check" : "copy"} size={14} />
          {copied ? t("Copiado") : t("Copiar")}
        </Button>
      </div>
      <pre><code>{highlight(code, lang)}</code></pre>
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

      // Block quote: consecutive "> " lines, rendered as one quote.
      if (/^\s*>\s?/.test(line)) {
        flushParagraph(paragraph);
        const quoted: string[] = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          quoted.push(lines[i].replace(/^\s*>\s?/, ""));
          i += 1;
        }
        out.push(
          <blockquote key={`q${key++}`}>{renderInline(quoted.join(" "), `q${key}`)}</blockquote>
        );
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
                  aria-expanded={open} aria-label={t("Detalhes técnicos")}>
            {open ? "ocultar" : "detalhes"}
          </button>
        )}
      </div>
      {open && event.detail && <div className="tool-card__details">{event.detail}</div>}
    </div>
  );
}

/* ── Clarification ────────────────────────────────────────────────────── */

/**
 * The candidates of an ambiguous PC target, as buttons.
 *
 * Pressing one sends its reply as an ordinary message: the choice goes through
 * the whole pipeline again (policy, permission, confirmation), now naming the
 * exact window or app. A button never runs anything by itself.
 */
function ClarificationChoices({
  clarification, onChoose,
}: { clarification: Clarification; onChoose: (reply: string) => void }) {
  const [chosen, setChosen] = useState<string | null>(null);
  return (
    <div className="msg__choices" role="group" aria-label={clarification.question || t("Escolhe uma opção")}>
      {clarification.options.map((option) => (
        <button
          key={option.reply} type="button" className="choice-chip"
          disabled={chosen !== null}
          aria-pressed={chosen === option.reply}
          title={option.label}
          onClick={() => { setChosen(option.reply); onChoose(option.reply); }}
        >
          {option.label}
        </button>
      ))}
      {clarification.more ? <span className="dim msg__choices-more">+{clarification.more}</span> : null}
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
  // Tagged only when something actually FAILED on the way: a provider attempt
  // that did not answer, or a reason other than "the cloud was never set up".
  // A local-only setup records fallback_used with nothing having failed.
  const failedHop = attempts.some((attempt) => attempt.outcome && attempt.outcome !== "ok");
  const rawReason = String(meta.fallback_reason ?? "");
  const surprising = fellBack && (failedHop || (Boolean(rawReason) && !EXPECTED_FALLBACK.has(rawReason)));
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
        <span>{t("Detalhes técnicos")}</span>
        {surprising && <span className="msg__meta-tag">{t("alternativa")}</span>}
      </button>

      <div id={panelId} className="msg__meta-panel" hidden={!open}>
        <dl className="kv">
          <dt>{t("Provedor")}</dt>
          <dd>
            {providerName(meta.provider)}
            {surprising ? t(" · fallback") : ""}
          </dd>
          <dt>{t("Modelo")}</dt><dd>{meta.model}</dd>
          <dt>{t("Modo")}</dt><dd>{[meta.mode, meta.tier].filter(Boolean).join(" · ")}</dd>
          {fellBack && from && (
            <>
              <dt>{t("Origem")}</dt>
              <dd>{providerName(from)} → {providerName(meta.provider)}</dd>
            </>
          )}
          {reason && (
            <>
              <dt>{t("Motivo")}</dt>
              <dd>
                {reason}
                {meta.retry_in_seconds ? ` · ~${Math.round(meta.retry_in_seconds)} s` : ""}
              </dd>
            </>
          )}
          <dt>{t("Pedido")}</dt><dd>{meta.task}</dd>
          <dt>{t("Ferramentas")}</dt>
          <dd>{t("{0} de {1}", { 0: meta.tools_offered ?? 0, 1: meta.tools_available ?? 0 })}</dd>
          {meta.prompt_tokens != null && (
            <>
              <dt>{t("Tokens")}</dt>
              <dd>{t("{0} entrada · {1} saída", { 0: meta.prompt_tokens, 1: meta.completion_tokens ?? 0 })}</dd>
            </>
          )}
          {meta.time_to_first_token_ms != null && (
            <>
              <dt>{t("1.º token")}</dt><dd>{meta.time_to_first_token_ms} ms</dd>
            </>
          )}
          {meta.total_latency_ms != null && (
            <>
              <dt>{t("Total")}</dt><dd>{meta.total_latency_ms} ms</dd>
            </>
          )}
        </dl>

        {crossed && (
          <ol className="meta-chain" aria-label={t("Provedores tentados nesta resposta")}>
            {attempts.map((attempt, index) => (
              <li key={`${attempt.provider}-${index}`}
                  className={`meta-chain__step${attempt.outcome === "ok" ? " is-ok" : ""}`}>
                <span className="meta-chain__provider">{providerName(attempt.provider)}</span>
                {attempt.model && <span className="meta-chain__model">{attempt.model}</span>}
                <span className="meta-chain__outcome">
                  {attempt.outcome === "ok" ? "respondeu" : reasonText(attempt.outcome) || t("sem resposta")}
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

/** Initials from the name the backend actually knows, or nothing at all.
 *  An invented "PA" would be a claim about the user that nothing measured. */
function initialsOf(name?: string | null): string | null {
  const parts = String(name ?? "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return null;
  return parts.slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

/**
 * One turn, laid out the way Chatbot UI lays out a message: a header with the
 * speaker's avatar and name, then the content, in the same reading column for
 * both sides.
 *
 * WHAT TELLS THE TWO APART. Nano's turns sit on a faint full-width band and
 * carry the N; the user's sit on the page itself with their initials. Nothing
 * else changes between them -- no bubble, no right alignment -- so a long
 * exchange scans as a steady rhythm of turns rather than as two columns.
 */
function MessageBubble({
  message, status, userName, onRetry, onEdit, onChoose, advanced = false,
}: {
  message: Message;
  advanced?: boolean;
  status?: string;
  userName?: string | null;
  /** Set only on the LAST answer of the open thread, and only when idle. */
  onRetry?: () => void;
  /** Set only on the LAST question of the open thread, and only when idle. */
  onEdit?: (text: string) => void;
  /** Only passed for the latest answer: older choices are history. */
  onChoose?: (reply: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const isUser = message.role === "user";
  const text = isUser ? message.content : cleanAssistantText(message.content);
  const initials = initialsOf(userName);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch { /* clipboard unavailable */ }
  };

  const startEdit = () => { setDraft(text); setEditing(true); };
  const saveEdit = () => {
    const next = draft.trim();
    setEditing(false);
    if (next && onEdit) onEdit(next);
  };

  // The actions row only exists for a finished turn with something in it.
  const showActions = Boolean(text) && !message.streaming && !editing;
  // The latest answer keeps its actions visible, as in every chat app; older
  // turns reveal theirs on hover and on keyboard focus.
  const pinned = Boolean(onRetry);

  return (
    <article className={`msg msg--${message.role}${message.error ? " msg--error" : ""}`}>
      <header className="msg__head">
        {isUser ? (
          <span className="msg__avatar msg__avatar--user" aria-hidden="true">
            {initials ?? <Icon name="user" size={14} />}
          </span>
        ) : (
          <NanoLogo size={22} className="msg__avatar" />
        )}
        <span className="msg__author">{isUser ? (userName || t("Tu")) : BRAND_NAME}</span>
        <span className="msg__time">{formatTime(message.timestamp)}</span>
      </header>

      {message.tools?.length ? (
        <div className="msg__tools">
          {message.tools.map((tool, index) => <ToolCard key={`${tool.name}-${index}`} event={tool} />)}
        </div>
      ) : null}

      {editing ? (
        <div className="msg__edit">
          <label className="sr-only" htmlFor={`edit-${message.id}`}>{t("Editar mensagem")}</label>
          <textarea
            id={`edit-${message.id}`} className="msg__edit-input" value={draft} autoFocus
            rows={Math.min(8, Math.max(2, draft.split("\n").length))}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); saveEdit(); }
              if (event.key === "Escape") { event.preventDefault(); setEditing(false); }
            }}
          />
          <div className="msg__edit-bar">
            <Button size="sm" onClick={() => setEditing(false)}>{t("Cancelar")}</Button>
            <Button size="sm" variant="primary" onClick={saveEdit} disabled={!draft.trim()}>{t("Enviar")}</Button>
          </div>
        </div>
      ) : (
        <div className="msg__body">
          {isUser && message.attachments?.length ? <FileChips files={message.attachments} /> : null}
          {isUser ? text : <Markdown text={text} />}
          {/* THE ONLY THINKING INDICATOR IN THE APP.
              There used to be a second one under the conversation, so a
              pending turn showed "O Nano está a pensar…" in the bubble AND
              "A pensar…" below it. The status line the other one carried is
              not lost: it is rendered HERE, so tool activity still narrates
              itself, in one place, inside the message it belongs to.

              No second logo: the turn's header already carries the mark, and
              a pending answer showed the N twice, one above the other. */}
          {message.streaming && !text && (
            <span className="thinking" role="status" aria-live="polite">
              <span className="thinking__text">{status?.trim() || t("A pensar…")}</span>
            </span>
          )}
          {message.streaming && text && <span className="caret" aria-hidden="true" />}
        </div>
      )}

      {showActions && (
        <div className={`msg__actions${pinned ? " msg__actions--pinned" : ""}`}>
          <button
            type="button" className="msg__action" onClick={copy}
            aria-label={copied ? t("Copiado") : isUser ? t("Copiar mensagem") : t("Copiar resposta")}
            title={copied ? t("Copiado") : t("Copiar")}
          >
            <Icon name={copied ? "check" : "copy"} size={15} />
          </button>
          {onEdit && (
            <button type="button" className="msg__action" onClick={startEdit}
                    aria-label={t("Editar e reenviar")} title={t("Editar")}>
              <Icon name="edit" size={15} />
            </button>
          )}
          {onRetry && (
            <button type="button" className="msg__action" onClick={onRetry}
                    aria-label={t("Tentar de novo")} title={t("Tentar de novo")}>
              <Icon name="refresh" size={15} />
            </button>
          )}
        </div>
      )}

      {onChoose && !isUser && !message.streaming && message.clarification?.options?.length ? (
        <ClarificationChoices clarification={message.clarification} onChoose={onChoose} />
      ) : null}

      {/* Technical details, collapsed by default so normal chat stays clean.
          Safe metadata only: provider, model, tokens and latency — and it is
          THIS message's, including for a message loaded from an older thread. */}
      {advanced && !isUser && !message.streaming && message.meta?.model && (
        <TechnicalDetails meta={message.meta} id={message.id} />
      )}
    </article>
  );
}

/**
 * Whether a standalone thinking indicator is needed.
 *
 * NEVER TWO, AND NEVER ZERO WHILE NOTHING IS VISIBLE. Human testing saw two at
 * once: "O Nano está a pensar…" inside the pending bubble and "A pensar…"
 * underneath it. The naive fix — delete the second — would have shown none at
 * all on a voice turn, which narrates "PROCESSING" before `on_stream_start`
 * inserts any bubble.
 *
 * So the rule has three cases and is stated once, here, rather than being spread
 * across the two places that render an indicator:
 *
 *   a streaming bubble with no text yet   the BUBBLE shows it → none here
 *   a streaming bubble with text          the caret shows activity → none here
 *   thinking with no streaming bubble     nothing else can → one here
 *
 * All three are verified by COUNTING LIVE INDICATORS in electron/test/
 * chat-drive.js — the production bundle, driven through a real turn in
 * Chromium — and reported as named steps by tests/test_chat_ui_contract.py.
 * That is deliberately not a unit test of this predicate: the defect being
 * guarded against was two elements on screen at once, and only the DOM can
 * answer how many there are.
 */
export function needsStandaloneThinking(messages: Message[], thinking: boolean): boolean {
  if (!thinking) return false;
  return !messages.some((message) => message.streaming);
}

/** "Boa tarde, Ana" by the local clock; no name when the backend has none. */
export function greeting(name?: string | null, now: Date = new Date()): string {
  const hour = now.getHours();
  const first = (name ?? "").trim().split(/\s+/)[0];
  const part = hour >= 5 && hour < 12 ? "morning" : hour >= 12 && hour < 20 ? "afternoon" : "evening";
  if (first) {
    if (part === "morning") return t("Bom dia, {0}", { 0: first });
    if (part === "afternoon") return t("Boa tarde, {0}", { 0: first });
    return t("Boa noite, {0}", { 0: first });
  }
  if (part === "morning") return t("Bom dia");
  if (part === "afternoon") return t("Boa tarde");
  return t("Boa noite");
}

export function Conversation({
  messages, status, thinking, userName, connecting = false, onRetry, onEdit, onChoose, advanced = false,
}: {
  messages: Message[];
  /** Modo avançado: show each answer's technical details. */
  advanced?: boolean;
  /** Ask the last question again. Offered on the last answer only. */
  onRetry?: () => void;
  /** Re-send the last question with new text. Offered on the last question only. */
  onEdit?: (text: string) => void;
  status: string;
  thinking: boolean;
  /** The user's name, only when the backend actually knows it. */
  userName?: string | null;
  /** The bridge has not answered yet (and has not given up either). */
  connecting?: boolean;
  /** Send a clarification choice as the next message. */
  onChoose?: (reply: string) => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  /* FOLLOW THE ANSWER ONLY WHILE THE READER IS AT THE END.
     Scrolling up to reread something used to be undone by the next streamed
     chunk. Now the view follows only when it was already at the bottom, and a
     button offers the way back down. Sending a message always goes down. */
  const [atEnd, setAtEnd] = useState(true);
  const atEndRef = useRef(true);
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    atEndRef.current = near;
    setAtEnd(near);
  };
  const stick = useCallback(() => {
    const el = scrollRef.current;
    // "instant", not the stylesheet's smooth scrolling: a smooth scroll is
    // still on its way down when the next chunk lands, its scroll events
    // report "not at the end", and following stopped half way.
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "instant" as ScrollBehavior });
  }, []);
  // A new question (or another thread opened) always starts at the end. The
  // question and its empty answer arrive together, so this keys on the last
  // QUESTION, not on the last row.
  const lastQuestion = [...messages].reverse().find((m) => m.role === "user")?.id;
  useEffect(() => {
    atEndRef.current = true;
    setAtEnd(true);
  }, [lastQuestion]);
  useEffect(() => {
    if (atEndRef.current) stick();
  }, [messages, status, stick]);
  // Content also grows with no new message: a code block or a table laid out
  // after the text arrived. Following the size, not only the messages, is
  // what keeps the end in view while the reader is there.
  const shown = messages.length > 0 || thinking;
  useEffect(() => {
    const inner = scrollRef.current?.firstElementChild;
    if (!inner || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => { if (atEndRef.current) stick(); });
    observer.observe(inner);
    return () => observer.disconnect();
  }, [shown, stick]);
  const toEnd = () => {
    atEndRef.current = true;
    setAtEnd(true);
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  };
  const standalone = needsStandaloneThinking(messages, thinking);
  const lastIndex = messages.length - 1;
  // A turn the user can ask again must exist as a question + answer pair. A
  // voice exchange is stored the same way, so it qualifies too.
  const retryable = lastIndex >= 1 && messages[lastIndex - 1]?.role === "user";

  if (!messages.length && !thinking) {
    return (
      <div className="conversation conversation--hero">
        {/* The home screen: the mark, one question, and the composer right
            below it (rendered by the shell). No dashboard and no cards: the
            one thing to do here is start talking. */}
        <div className="chat-hero">
          <NanoLogo size={56} className="chat-hero__mark" />
          <h1 className="chat-hero__title">{greeting(userName)}</h1>
          <p className="chat-hero__sub">{t("Como posso ajudar?")}</p>
          {connecting && (
            <p className="chat-hero__hint" role="status">{t("A ligar ao motor do {0}…", { 0: BRAND_NAME })}</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="conversation" role="log" aria-live="polite" aria-label={t("Conversa")}
         ref={scrollRef} onScroll={onScroll}>
      <div className="conversation__inner">
        {messages.map((message, index) => {
          const isLast = index === lastIndex;
          const isLastQuestion = message.role === "user" && index === lastIndex - 1
            && messages[lastIndex]?.role === "assistant";
          return (
            <MessageBubble
              key={message.id} message={message} status={status} userName={userName}
              advanced={advanced}
              onRetry={!thinking && isLast && message.role === "assistant" && retryable ? onRetry : undefined}
              onEdit={!thinking && isLastQuestion && retryable ? onEdit : undefined}
              onChoose={isLast && !thinking ? onChoose : undefined}
            />
          );
        })}
        {standalone && (
          <div className="thinking thinking--standalone" role="status" aria-live="polite">
            <span className="thinking__mark" aria-hidden="true"><NanoLogo size={22} /></span>
            <span className="thinking__text">{status?.trim() || t("A pensar…")}</span>
          </div>
        )}
        <div ref={endRef} />
      </div>
      {!atEnd && (
        <button type="button" className="conversation__to-end" onClick={toEnd}
                aria-label={t("Ir para o fim da conversa")} title={t("Ir para o fim da conversa")}>
          <Icon name="arrowDown" size={18} />
        </button>
      )}
    </div>
  );
}

/* ── Composer ─────────────────────────────────────────────────────────── */

/**
 * The message composer.
 *
 * Shaped like Chatbot UI's input: one rounded field with the text on top and a
 * quiet row of controls under it. The send button is the one solid object in
 * the field, in the neutral primary colour rather than a brand colour; the
 * microphone and the attachment slot are ghost controls beside it. The
 * suggestions sit under the field, where they read as an offer rather than as
 * part of the input.
 *
 * `readOnlyReason`, when set, means the user is looking at an older
 * conversation. The whole composer is disabled and says why — the Brain's
 * context holds only the live conversation, so a message typed here would be
 * answered against the wrong history.
 */
export function Composer({
  value, onChange, onSend, onStop, onVoice, onCancelVoice,
  thinking, disabled, voiceState, listening, suggestions, onSuggestion,
  readOnlyReason, files = [], onFiles, onRemoveFile, onFileError, onVoiceMode,
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
  readOnlyReason?: string;
  /** Files waiting to go with the next message. */
  files?: PendingFile[];
  onFiles?: (files: PendingFile[]) => void;
  onRemoveFile?: (index: number) => void;
  onFileError?: (message: string) => void;
  /** Open the full-screen voice mode. */
  onVoiceMode?: () => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const locked = disabled || Boolean(readOnlyReason);
  const canAttach = Boolean(onFiles) && !locked && files.length < MAX_FILES;

  /** Read what was picked or dropped; refuse what cannot be read, with why. */
  const addFiles = useCallback(async (list: FileList | null) => {
    if (!list || !onFiles) return;
    const room = MAX_FILES - files.length;
    const chosen = Array.from(list);
    if (chosen.length > room) onFileError?.(t("No máximo {0} ficheiros por mensagem.", { 0: MAX_FILES }));
    const read = await Promise.all(chosen.slice(0, Math.max(0, room)).map(readAttachment));
    const ok: PendingFile[] = [];
    for (const result of read) {
      if ("error" in result) onFileError?.(result.error);
      else ok.push(result);
    }
    if (ok.length) onFiles(ok);
    ref.current?.focus();
  }, [files.length, onFiles, onFileError]);

  useEffect(() => { if (!locked) ref.current?.focus(); }, [locked]);

  /* The height follows the content, and is re-measured when the field's WIDTH
     changes as well: the same text wraps onto more lines in a narrower field.
     Measured only on input, a field sized while the layout was still settling
     -- the first frame, before the sidebar has taken its real shape -- kept
     that height until the user typed. */
  const fit = useCallback(() => {
    const node = ref.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 220)}px`;
  }, []);
  useEffect(() => { fit(); }, [value, fit]);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    let width = node.clientWidth;
    // Only a change of width refits. Refitting changes the height, which this
    // observer also reports, and must not answer itself.
    const observer = new ResizeObserver(() => {
      if (node.clientWidth === width) return;
      width = node.clientWidth;
      fit();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [fit]);

  const voiceReady = voiceState === "READY";
  const voiceTitle = readOnlyReason
    ? readOnlyReason
    : listening
      ? t("A ouvir — clica para cancelar")
      : voiceReady ? t("Falar com o {0} (Ctrl+M)", { 0: BRAND_NAME })
        : t("Voz por configurar — abre Definições → Voz");

  // The placeholder and the line in the button bar are two halves of one
  // sentence, not the same sentence twice.
  const placeholder = readOnlyReason
    ? t("Conversa anterior — só leitura.")
    : disabled ? t("A aguardar o motor do {0}…", { 0: BRAND_NAME }) : t("Escreve uma mensagem ao {0}…", { 0: BRAND_NAME });

  return (
    <div className="composer-wrap">
      <div className="composer-inner">
        <div
          className={`composer${listening ? " composer--listening" : ""}${dragging ? " composer--drop" : ""}`}
          onDragOver={(event) => {
            if (!canAttach || !event.dataTransfer.types.includes("Files")) return;
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            setDragging(false);
            if (!canAttach) return;
            event.preventDefault();
            void addFiles(event.dataTransfer.files);
          }}
        >
          {files.length > 0 && <FileChips files={files} onRemove={onRemoveFile} />}
          <label className="sr-only" htmlFor="composer-input">{t("Mensagem para o {0}", { 0: BRAND_NAME })}</label>
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
            {/* Text files only; the window reads them and the backend fences
                their text as untrusted data. Dropping a file on the field
                works too. */}
            <input
              ref={picker} type="file" multiple accept={ACCEPT} hidden
              onChange={(event) => { void addFiles(event.target.files); event.target.value = ""; }}
            />
            <Button
              variant="ghost" icon size="sm" disabled={!canAttach}
              onClick={() => picker.current?.click()}
              title={files.length >= MAX_FILES
                ? t("No máximo {0} ficheiros por mensagem.", { 0: MAX_FILES })
                : t("Anexar ficheiro de texto")}
              aria-label={t("Anexar ficheiro de texto")}
            >
              <Icon name="attach" size={17} />
            </Button>

            {listening && (
              <span className="mic-indicator" role="status">
                <span className="mic-indicator__wave" aria-hidden="true"><i /><i /><i /></span>{t("A ouvir…")}</span>
            )}
            {/* No "voz" status chip here any more. Voice not being set up is
                not news to someone who types, and an amber dot that never
                goes away reads as a fault. The microphone itself is disabled
                and says why when hovered or focused. */}
            {readOnlyReason && (
              <span id="composer-readonly" className="dim" style={{ fontSize: 12 }}>
                {readOnlyReason}
              </span>
            )}

            <span className="composer__spacer" />

            {onVoiceMode && (
              <Button
                variant="ghost" icon size="sm" onClick={onVoiceMode}
                disabled={locked || (!voiceReady && !listening)}
                title={voiceReady ? t("Modo de voz (Ctrl+Shift+M)") : voiceTitle}
                aria-label={t("Modo de voz")}
              >
                <Icon name="waves" size={17} />
              </Button>
            )}

            <button
              type="button"
              className={`composer__mic${listening ? " composer__mic--live" : ""}`}
              onClick={listening ? onCancelVoice : onVoice}
              disabled={locked || thinking || (!voiceReady && !listening)}
              aria-label={voiceTitle} title={voiceTitle}
            >
              {listening ? <Icon name="stop" size={14} fill /> : <Icon name="mic" size={18} />}
            </button>

            {thinking ? (
              <button
                type="button" className="composer__send" onClick={onStop}
                aria-label={t("Parar a resposta")} title={t("Parar")}
              >
                <Icon name="stop" size={14} fill />
              </button>
            ) : (
              <button
                type="button" className="composer__send" onClick={onSend}
                disabled={locked || !value.trim()}
                aria-label={t("Enviar mensagem")}
                title={readOnlyReason ?? t("Enviar (Enter)")}
              >
                <Icon name="send" size={18} strokeWidth={2.2} />
              </button>
            )}
          </div>
        </div>

        {suggestions.length > 0 && !value && !readOnlyReason && (
          <div className="suggestions">
            {suggestions.map((text) => (
              <button key={text} type="button" className="suggestion" onClick={() => onSuggestion(text)}>
                {text}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
