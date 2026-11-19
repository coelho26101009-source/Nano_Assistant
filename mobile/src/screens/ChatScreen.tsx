/**
 * The chat: the conversation the PC has open, a composer with the microphone,
 * and approval cards. Rows look like the PC's (avatar, name, one reading
 * column, every turn on the page background, only a failed turn on a red band).
 */
import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import Icon from "../components/Icon";
import Markdown from "../components/Markdown";
import { NanoMark } from "../components/Brand";
import type { Approval, Chat, Message } from "../lib/chat";
import { t } from "../lib/i18n";
import { listen, speechAvailable, stopSpeaking, tap, type Listening } from "../lib/native";

function greeting(name: string): string {
  const hour = new Date().getHours();
  const base = hour < 6 ? t("Boa noite") : hour < 13 ? t("Bom dia") : hour < 20 ? t("Boa tarde") : t("Boa noite");
  return name ? `${base}, ${name}` : base;
}

function ApprovalCard({ approval, onAnswer }: { approval: Approval; onAnswer: (id: string, ok: boolean) => void }) {
  const meta = approval.meta || {};
  const args = Object.entries(meta.args || {}).slice(0, 6);
  return (
    <section className="confirm" role="alertdialog" aria-label={t("O NANO pede autorização")}>
      <div className="confirm__head"><Icon name="shield" size={18} />{t("Autorização no PC")}</div>
      <p className="confirm__message">{approval.message}</p>
      {(meta.action || meta.target || meta.scope || args.length > 0) && (
        <dl>
          {meta.action && <><dt>{t("Ação")}</dt><dd>{meta.action}</dd></>}
          {meta.target && <><dt>{t("Alvo")}</dt><dd>{meta.target}</dd></>}
          {meta.scope && <><dt>{t("Âmbito")}</dt><dd>{meta.scope}</dd></>}
          {args.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
        </dl>
      )}
      <div className="confirm__actions">
        <button className="btn" onClick={() => onAnswer(approval.requestId, false)}>{t("Recusar")}</button>
        <button className="btn btn--primary" onClick={() => onAnswer(approval.requestId, true)}>{t("Permitir")}</button>
      </div>
    </section>
  );
}

function Row({ message, last, onRetry, initials }: { message: Message; last: boolean; onRetry: () => void; initials: string }) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";
  const copy = async () => {
    try { await navigator.clipboard.writeText(message.content); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* no clipboard */ }
  };
  return (
    <article className={`msg ${message.error ? "msg--error" : ""}`}>
      <div className={`msg__avatar ${isUser ? "" : "msg__avatar--nano"}`} aria-hidden="true">
        {isUser ? (initials || <Icon name="chat" size={16} />) : <NanoMark size={16} />}
      </div>
      <div style={{ minWidth: 0 }}>
        <div className="msg__name">{isUser ? t("Tu") : "NANO"}</div>
        <div className="msg__body">
          {message.streaming && !message.content
            ? <span className="thinking">{message.status || t("A pensar…")}</span>
            : isUser ? <p style={{ whiteSpace: "pre-wrap" }}>{message.content}</p> : <Markdown text={message.content} />}
        </div>
        {!isUser && !message.streaming && message.content && (
          <div className="msg__actions">
            <button className="icon-btn" onClick={() => void copy()} aria-label={copied ? t("Copiado") : t("Copiar")}>
              <Icon name={copied ? "check" : "copy"} size={18} />
            </button>
            {last && !message.notice && (
              <button className="icon-btn" onClick={onRetry} aria-label={t("Tentar de novo")}><Icon name="refresh" size={18} /></button>
            )}
          </div>
        )}
      </div>
    </article>
  );
}

export default function ChatScreen({
  chat, online, displayName, onVoiceTurn,
}: {
  chat: Chat;
  online: boolean;
  displayName: string;
  /** Called before a spoken message is sent, so its answer is read aloud. */
  onVoiceTurn: () => void;
}) {
  const [text, setText] = useState("");
  const [listening, setListening] = useState<Listening | null>(null);
  const [heard, setHeard] = useState("");
  const [canSpeak, setCanSpeak] = useState(false);
  const [micError, setMicError] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const stick = useRef(true);
  const initials = displayName.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("");

  useEffect(() => { void speechAvailable().then(setCanSpeak); }, []);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [chat.messages, chat.approvals]);

  useLayoutEffect(() => {
    const el = field.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [text]);

  const submit = async (value = text, spoken = false) => {
    if (!value.trim() || chat.busy) return;
    if (spoken) onVoiceTurn();
    stick.current = true;
    setText("");
    void tap();
    const ok = await chat.send(value);
    if (!ok) setText(value);
  };

  const toggleMic = async () => {
    setMicError("");
    if (listening) {
      const said = await listening.stop();
      setListening(null);
      setHeard("");
      if (said) await submit(said, true);
      return;
    }
    void stopSpeaking();
    try {
      const session = await listen(
        (partial) => setHeard(partial),
        (final) => {
          setListening(null);
          setHeard("");
          if (final) void submit(final, true);
        },
      );
      if (session) { void tap(); setListening(session); }
    } catch (error) {
      setMicError((error as Error).message === "microphone_denied"
        ? t("O NANO precisa de acesso ao microfone para te ouvir. Podes dar acesso nas definições do Android.")
        : t("Não consegui começar a ouvir."));
    }
  };

  const lastAssistant = [...chat.messages].reverse().find((m) => m.role === "assistant");
  const suggestions = [t("O que tenho hoje na agenda?"), t("Lembra-me de ligar à mãe às 18h"), t("Resume o que falámos ontem")];

  return (
    <div className="chat">
      <div />
      <div className="chat__scroll" ref={scroller}
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}>
        {chat.loaded && chat.messages.length === 0 ? (
          <div className="chat__home">
            <NanoMark size={48} />
            <h2>{greeting(displayName)}</h2>
            <p>{t("Como posso ajudar?")}</p>
            <div className="suggestions">
              {suggestions.map((s) => (
                <button key={s} className="suggestion" onClick={() => void submit(s)} disabled={!online}>{s}</button>
              ))}
            </div>
          </div>
        ) : (
          chat.messages.map((m) => (
            <Row key={m.id} message={m} last={m === lastAssistant} onRetry={() => void chat.retry()} initials={initials} />
          ))
        )}
        {chat.approvals.map((a) => (
          <ApprovalCard key={a.requestId} approval={a} onAnswer={(id, ok) => { void tap(); void chat.answer(id, ok); }} />
        ))}
      </div>
      <div className="composer-wrap">
        {listening && (
          <div className="listening-line" aria-live="polite">
            <span className="wave" aria-hidden="true"><i /><i /><i /></span>
            <span>{heard || t("A ouvir…")}</span>
          </div>
        )}
        {micError && <p className="error-text" style={{ padding: "0 6px 8px" }} role="alert">{micError}</p>}
        <div className={`composer ${listening ? "composer--listening" : ""}`}>
          <label htmlFor="composer" className="sr-only">{t("Mensagem para o NANO")}</label>
          <textarea id="composer" ref={field} rows={1} value={text} enterKeyHint="send"
            placeholder={online ? t("Escreve ao NANO…") : t("Sem ligação ao PC")}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(); } }} />
          {canSpeak && (
            <button className={`icon-btn ${listening ? "mic-btn--live" : ""}`} onClick={() => void toggleMic()}
              disabled={!online || chat.busy} aria-pressed={Boolean(listening)}
              aria-label={listening ? t("Parar de ouvir") : t("Falar com o NANO")}>
              <Icon name={listening ? "stop" : "mic"} />
            </button>
          )}
          <button className="send-btn" onClick={() => void submit()} disabled={!text.trim() || chat.busy || !online}
            aria-label={t("Enviar")}>
            <Icon name="send" size={20} strokeWidth={2.2} />
          </button>
        </div>
      </div>
    </div>
  );
}
