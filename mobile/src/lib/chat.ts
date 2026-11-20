/**
 * The conversation on the phone: the same thread the PC has open, kept in step
 * with the PC's event feed. Mirrors the PC window's handling of the stream
 * events (frontend/pages/index.tsx): exactly one user bubble per turn, a chunk
 * never creates a bubble, and the final text replaces the streamed one.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { randomId } from "./crypto";
import { t } from "./i18n";
import type { NanoEvent, NanoLink } from "./link";
import { RpcError } from "./link";

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  error?: boolean;
  status?: string;
  notice?: boolean;
  /** Never reached the PC: "again" sends it anew instead of rewinding the
   *  PC's last stored turn, which would be the exchange BEFORE this one. */
  unsent?: boolean;
};

export type Approval = {
  requestId: string;
  message: string;
  meta: { action?: string; target?: string; scope?: string; tool?: string; args?: Record<string, string> };
};

export type HistoryRow = { id: number | string; role: string; content: string; timestamp?: string };

const userId = (turn: string) => `u:${turn}`;

function friendlyStatus(text: string): string {
  const match = /⚙️\s*(.+?)\.\.\./.exec(text || "");
  if (match) {
    const names = match[1].split(",").map((n) => n.trim()).filter(Boolean);
    return names.length === 1 ? t("A usar {0}…", { 0: names[0] }) : t("A usar {0} ferramentas…", { 0: names.length });
  }
  return "";
}

export type ChatHooks = {
  onAnswer?: (text: string, msgId: string) => void;
  onNotice?: (title: string, text: string) => void;
  onReminder?: (text: string) => void;
  onTurnDone?: () => void;
};

export function useChat(link: NanoLink, hooks: ChatHooks) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const hooksRef = useRef(hooks);
  hooksRef.current = hooks;

  const showHistory = useCallback((rows: HistoryRow[], active: string | null) => {
    setMessages(rows.filter((r) => r.role === "user" || r.role === "assistant").map((r) => ({
      id: `stored:${r.id}`, role: r.role as "user" | "assistant", content: r.content,
    })));
    setActiveId(active);
    setBusy(false);
    setLoaded(true);
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const result = await link.call<{ activeId: string | null; messages: HistoryRow[] }>("chat.history");
      showHistory(result.messages, result.activeId);
    } catch { /* offline: keep what is on screen */ }
  }, [link, showHistory]);

  useEffect(() => link.onEvent((event: NanoEvent) => {
    const [a, b, c] = event.data ?? [];
    switch (event.name) {
      case "resync":
        void loadHistory();
        break;
      case "on_stream_start": {
        const msgId = String(a);
        const text = typeof b === "string" ? b : "";
        setBusy(true);
        setMessages((prev) => {
          const next = [...prev];
          if (text && !next.some((m) => m.id === userId(msgId))) next.push({ id: userId(msgId), role: "user", content: text });
          if (!next.some((m) => m.id === msgId)) next.push({ id: msgId, role: "assistant", content: "", streaming: true });
          return next;
        });
        break;
      }
      case "on_stream_status":
        setMessages((prev) => prev.map((m) => m.id === String(a) ? { ...m, status: friendlyStatus(String(b ?? "")) } : m));
        break;
      case "on_stream_chunk":
        setMessages((prev) => prev.some((m) => m.id === String(a))
          ? prev.map((m) => m.id === String(a) ? { ...m, content: m.content + String(b ?? ""), streaming: true } : m)
          : prev);
        break;
      case "on_stream_end": {
        const final = (b ?? {}) as { text?: string; error?: string; ok?: boolean };
        const text = final.text ?? final.error;
        setBusy(false);
        let shown = "";
        setMessages((prev) => prev.map((m) => {
          if (m.id !== String(a)) return m;
          shown = text ?? m.content;
          return { ...m, content: shown, streaming: false, status: "", error: final.ok === false };
        }));
        if (final.ok !== false && text) hooksRef.current.onAnswer?.(text, String(a));
        hooksRef.current.onTurnDone?.();
        break;
      }
      case "on_stream_error": {
        const error = (b ?? {}) as { detail?: string; code?: string };
        setBusy(false);
        setMessages((prev) => prev.map((m) => m.id === String(a)
          ? { ...m, streaming: false, error: true, status: "",
              content: m.content || t("**Não foi possível responder.** {0}", { 0: error.detail ?? error.code ?? "" }) }
          : m));
        break;
      }
      case "on_rate_limited": {
        const info = (b ?? {}) as { message?: string };
        setBusy(false);
        setMessages((prev) => prev.map((m) => m.id === String(a) && m.streaming
          ? { ...m, streaming: false, error: true, content: m.content || info.message || t("Limite temporário atingido.") }
          : m));
        break;
      }
      case "on_voice_exchange":
        setMessages((prev) => prev.some((m) => m.id === String(a)) ? prev : [
          ...prev,
          { id: userId(String(a)), role: "user", content: String(b ?? "") },
          { id: String(a), role: "assistant", content: String(c ?? "") },
        ]);
        break;
      case "on_confirm_request": {
        const approval: Approval = { requestId: String(a), message: String(b ?? ""), meta: (c ?? {}) as Approval["meta"] };
        setApprovals((prev) => prev.some((p) => p.requestId === approval.requestId) ? prev : [...prev, approval]);
        hooksRef.current.onNotice?.(t("O NANO pede autorização"), approval.message);
        break;
      }
      case "on_confirm_resolved":
        setApprovals((prev) => prev.filter((p) => p.requestId !== String(a)));
        break;
      case "on_conversation_changed":
        // The PC window opened another conversation. There is one active
        // conversation for both screens, so the phone shows that one too.
        if (b !== "phone") void loadHistory();
        break;
      case "on_proactive_notice": {
        const notice = (a ?? {}) as { title?: string; text?: string };
        const text = String(notice.text ?? "").trim();
        if (!text) break;
        const title = String(notice.title ?? "").trim();
        setMessages((prev) => [...prev, { id: `notice:${randomId()}`, role: "assistant", notice: true,
          content: title ? `**${title}**\n\n${text}` : text }]);
        hooksRef.current.onNotice?.(title || "NANO", text);
        break;
      }
      case "on_reminder_due": {
        const reminder = (a ?? {}) as { text?: string };
        if (reminder.text) hooksRef.current.onReminder?.(String(reminder.text));
        break;
      }
      default:
        break;
    }
  }), [link, loadHistory]);

  const send = useCallback(async (text: string) => {
    const clean = text.trim();
    if (!clean) return false;
    const msgId = randomId();
    setBusy(true);
    setMessages((prev) => [...prev,
      { id: userId(msgId), role: "user", content: clean },
      { id: msgId, role: "assistant", content: "", streaming: true }]);
    try {
      const ack = await link.call<{ ok: boolean; error?: string }>("chat.send", { text: clean, msgId });
      if (!ack.ok) throw new RpcError(ack.error || "rejected");
      return true;
    } catch (error) {
      const code = error instanceof RpcError ? error.code : "error";
      // The PC got it on another address; the answer arrives on the feed.
      if (code === "already_sent") return true;
      setBusy(false);
      setMessages((prev) => prev.map((m) => m.id === msgId ? {
        ...m, streaming: false, error: true,
        content: code === "unreachable" ? t("Não consegui chegar ao teu PC. A mensagem não foi enviada.")
          : t("A mensagem não foi aceite ({0}).", { 0: code }),
      } : m.id === userId(msgId) ? { ...m, unsent: true } : m));
      return false;
    }
  }, [link]);

  /** Ask the last question again. The PC rewinds the turn and streams it anew. */
  const retry = useCallback(async () => {
    const index = messages.map((m) => m.role).lastIndexOf("user");
    if (index < 0) return;
    if (messages[index].unsent) {
      // Nothing of it is stored on the PC: send it as a new message.
      const text = messages[index].content;
      setMessages(messages.slice(0, index));
      await send(text);
      return;
    }
    const before = messages;
    setMessages(messages.slice(0, index));
    setBusy(true);
    try {
      const ack = await link.call<{ ok: boolean }>("chat.retry", { text: "", msgId: randomId() });
      if (!ack.ok) throw new RpcError("rejected");
    } catch {
      setBusy(false);
      setMessages(before);
    }
  }, [link, messages, send]);

  const answer = useCallback(async (requestId: string, confirmed: boolean) => {
    setApprovals((prev) => prev.filter((p) => p.requestId !== requestId));
    try {
      const result = await link.call<{ ok: boolean }>("confirm.respond", { requestId, confirmed });
      return result.ok;
    } catch {
      return false;
    }
  }, [link]);

  return { messages, activeId, approvals, busy, loaded, send, retry, answer, loadHistory, showHistory, setActiveId };
}

export type Chat = ReturnType<typeof useChat>;
