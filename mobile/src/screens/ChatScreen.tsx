/**
 * The chat: the conversation the PC has open, a composer with the microphone,
 * and approval cards. Rows look like the PC's (avatar, name, one reading
 * column, every turn on the page background, only a failed turn on a red band).
 */
import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import Icon from "../components/Icon";
import Markdown from "../components/Markdown";
import { NanoMark } from "../components/Brand";
import CameraSheet from "../components/CameraSheet";
import { cameraSupported, type Photo } from "../lib/camera";
import type { Approval, CameraRequest, Chat, Message } from "../lib/chat";
import { t } from "../lib/i18n";
import { listen, speak, speechAvailable, stopSpeaking, tap, type Listening } from "../lib/native";

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

/** Where a photo will be seen, in words, and that nothing is recorded. */
function seenNote(seenBy?: { local: boolean; provider: string }): string {
  if (!seenBy || seenBy.local) return t("A foto é vista no teu PC e não é guardada.");
  const names: Record<string, string> = { groq: "Groq", mistral: "Mistral", google: "Google Gemini" };
  return t("A foto é vista por {0}, o serviço de IA escolhido no teu PC, e não é guardada.", { 0: names[seenBy.provider] ?? seenBy.provider });
}

function CameraRequestCard({ request, onAllow, onDecline }: {
  request: CameraRequest; onAllow: () => void; onDecline: () => void;
}) {
  return (
    <section className="confirm confirm--camera" role="alertdialog" aria-label={t("O NANO quer ver pela câmara")}>
      <div className="confirm__head"><Icon name="camera" size={18} />{t("O NANO quer ver pela câmara")}</div>
      {request.question && <p className="confirm__message">{request.question}</p>}
      <p className="confirm__note">{seenNote(request.seenBy)} {t("Só tu abres a câmara e tiras a foto, e só desta vez.")}</p>
      <div className="confirm__actions">
        <button className="btn" onClick={onDecline}>{t("Recusar")}</button>
        <button className="btn btn--primary" onClick={onAllow}>{t("Abrir a câmara")}</button>
      </div>
    </section>
  );
}

function Row({ message, last, onRetry, initials, reading, onRead }: {
  message: Message; last: boolean; onRetry: () => void; initials: string;
  /** This answer is being read aloud right now. */
  reading: boolean;
  /** Read this answer aloud, or stop if it is being read. */
  onRead: () => void;
}) {
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
            <button className={`icon-btn${reading ? " icon-btn--on" : ""}`} onClick={onRead}
              aria-label={reading ? t("Parar a leitura") : t("Ler em voz alta")} aria-pressed={reading}>
              <Icon name={reading ? "stop" : "speaker"} size={18} />
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
  chat, online, displayName, onVoiceTurn, cameraReady,
}: {
  chat: Chat;
  online: boolean;
  displayName: string;
  /** The PC understands photos from the camera (its "hello" says so). */
  cameraReady: boolean;
  /** Called before a spoken message is sent, so its answer is read aloud. */
  onVoiceTurn: () => void;
}) {
  const [text, setText] = useState("");
  const [listening, setListening] = useState<Listening | null>(null);
  const [heard, setHeard] = useState("");
  const [canSpeak, setCanSpeak] = useState(false);
  const [micError, setMicError] = useState("");
  /** The answer being read aloud with the phone's own voice, if any. */
  const [readingId, setReadingId] = useState<string | null>(null);
  /** The camera is open: for the person's own photo, or to answer NANO's request. */
  const [camera, setCamera] = useState<{ kind: "own" } | { kind: "request"; request: CameraRequest } | null>(null);
  /** A photo the person took, waiting to go with the next message. */
  const [photo, setPhoto] = useState<Photo | null>(null);
  const canCamera = cameraReady && cameraSupported();
  const reading = useRef<string | null>(null);
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
    const sending = photo;
    // A photo with no words asks the obvious question.
    if (!value.trim() && sending) value = t("O que é isto?");
    if (!value.trim() || chat.busy) return;
    if (spoken) onVoiceTurn();
    stick.current = true;
    setText("");
    setPhoto(null);
    void tap();
    const ok = await chat.send(value, sending);
    if (!ok) { setText(value); setPhoto(sending); }
  };

  /* "Ler em voz alta" uses the phone's own voice, so it works with the PC
     asleep too. One answer at a time; pressing it again stops it. */
  const readAloud = async (message: Message) => {
    const again = reading.current === message.id;
    reading.current = null;
    setReadingId(null);
    await stopSpeaking();
    if (again) return;
    reading.current = message.id;
    setReadingId(message.id);
    await speak(message.content);
    if (reading.current === message.id) { reading.current = null; setReadingId(null); }
  };

  const toggleMic = async () => {
    setMicError("");
    reading.current = null;
    setReadingId(null);
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
            <Row key={m.id} message={m} last={m === lastAssistant} onRetry={() => void chat.retry()} initials={initials}
              reading={readingId === m.id} onRead={() => void readAloud(m)} />
          ))
        )}
        {chat.cameraRequests.map((r) => (
          <CameraRequestCard key={r.requestId} request={r}
            onAllow={() => { void tap(); setCamera({ kind: "request", request: r }); }}
            onDecline={() => { void tap(); void chat.answerCamera(r.requestId, null); }} />
        ))}
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
        {photo && (
          <div className="photo-chip">
            <img src={`data:${photo.mime};base64,${photo.data}`} alt={t("Foto para o NANO")} />
            <span>{t("Foto pronta")}</span>
            <button className="icon-btn" onClick={() => setPhoto(null)} aria-label={t("Tirar a foto da mensagem")}><Icon name="close" size={18} /></button>
          </div>
        )}
        <div className={`composer ${listening ? "composer--listening" : ""}`}>
          <label htmlFor="composer" className="sr-only">{t("Mensagem para o NANO")}</label>
          <textarea id="composer" ref={field} rows={1} value={text} enterKeyHint="send"
            placeholder={online ? t("Escreve ao NANO…") : t("Sem ligação ao PC")}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(); } }} />
          {canCamera && (
            <button className="icon-btn" onClick={() => { void tap(); setCamera({ kind: "own" }); }}
              disabled={!online || chat.busy || Boolean(listening)} aria-label={t("Mostrar ao NANO com a câmara")}>
              <Icon name="camera" />
            </button>
          )}
          {canSpeak && (
            <button className={`icon-btn ${listening ? "mic-btn--live" : ""}`} onClick={() => void toggleMic()}
              disabled={!online || chat.busy} aria-pressed={Boolean(listening)}
              aria-label={listening ? t("Parar de ouvir") : t("Falar com o NANO")}>
              <Icon name={listening ? "stop" : "mic"} />
            </button>
          )}
          <button className="send-btn" onClick={() => void submit()} disabled={(!text.trim() && !photo) || chat.busy || !online}
            aria-label={t("Enviar")}>
            <Icon name="send" size={20} strokeWidth={2.2} />
          </button>
        </div>
      </div>
      {camera && (
        <CameraSheet
          title={camera.kind === "request" ? (camera.request.question || t("O NANO quer ver pela câmara")) : t("Mostra ao NANO")}
          note={camera.kind === "request" ? seenNote(camera.request.seenBy) : t("A foto vai cifrada para o teu PC e não é guardada.")}
          onPhoto={(taken) => {
            if (camera.kind === "request") void chat.answerCamera(camera.request.requestId, taken);
            else { setPhoto(taken); field.current?.focus(); }
            setCamera(null);
          }}
          onCancel={() => {
            // Closing the camera on NANO's request is a no; the card goes.
            if (camera.kind === "request") void chat.answerCamera(camera.request.requestId, null);
            setCamera(null);
          }} />
      )}
    </div>
  );
}
