/**
 * The agenda: the PC's reminders and events, grouped by day. New reminders go
 * to the PC (which owns them) and are copied to the phone's own notifications
 * so they ring with the app closed.
 */
import { useCallback, useEffect, useState } from "react";
import Icon from "../components/Icon";
import { locale, t } from "../lib/i18n";
import type { NanoLink } from "../lib/link";
import { RpcError } from "../lib/link";

export type Reminder = { id: number; text: string; due_at: string; recurrence?: string | null; recurrence_label?: string | null };
export type CalendarEvent = { id: number | string; title: string; start: string; end?: string | null; location?: string | null; source?: string };
export type AgendaData = { reminders: Reminder[]; events: CalendarEvent[]; available?: { reminders: boolean; calendar: boolean } };

/** The PC writes local times without a zone ("2026-11-20T18:30"); read them as local. */
export function parseLocal(value: string): Date | null {
  if (!value) return null;
  let text = value.trim().replace(" ", "T");
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) text += "T00:00";
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : date;
}

function dayLabel(date: Date): string {
  const today = new Date();
  const start = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = Math.round((start(date) - start(today)) / 86400000);
  if (diff === 0) return t("Hoje");
  if (diff === 1) return t("Amanhã");
  if (diff === -1) return t("Ontem");
  return date.toLocaleDateString(locale() === "en" ? "en-GB" : "pt-PT", { weekday: "long", day: "numeric", month: "long" });
}

function timeLabel(date: Date): string {
  return date.toLocaleTimeString(locale() === "en" ? "en-GB" : "pt-PT", { hour: "2-digit", minute: "2-digit" });
}

type Item = { key: string; kind: "reminder" | "event"; at: Date; title: string; meta: string; id: number | string; deletable: boolean };

function toLocalInput(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export default function AgendaScreen({
  link, data, reload, online, toast,
}: {
  link: NanoLink;
  data: AgendaData | null;
  reload: () => Promise<void>;
  online: boolean;
  toast: (text: string) => void;
}) {
  const [adding, setAdding] = useState<"reminder" | "event" | null>(null);
  const [text, setText] = useState("");
  const [when, setWhen] = useState("");
  const [repeat, setRepeat] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { void reload(); }, [reload]);

  const open = useCallback((kind: "reminder" | "event") => {
    const soon = new Date(Date.now() + 60 * 60 * 1000);
    soon.setMinutes(0, 0, 0);
    setText(""); setWhen(toLocalInput(soon)); setRepeat(""); setError(""); setAdding(kind);
  }, []);

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const result = adding === "reminder"
        ? await link.call<{ ok: boolean; message?: string }>("agenda.addReminder", { text, when: when.replace("T", " "), repeat })
        : await link.call<{ ok: boolean; message?: string }>("agenda.addEvent", { title: text, start: when, end: "", location: "" });
      if (!result.ok) { setError(result.message || t("Não consegui guardar.")); return; }
      setAdding(null);
      toast(adding === "reminder" ? t("Lembrete criado.") : t("Evento criado."));
      await reload();
    } catch (err) {
      setError(err instanceof RpcError && err.code === "unreachable" ? t("Sem ligação ao PC.") : t("Não consegui guardar."));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (item: Item) => {
    try {
      const result = await link.call<{ ok: boolean }>(item.kind === "reminder" ? "agenda.deleteReminder" : "agenda.deleteEvent", { id: item.id });
      if (result.ok) { toast(t("Apagado.")); await reload(); }
    } catch {
      toast(t("Sem ligação ao PC."));
    }
  };

  const items: Item[] = [];
  for (const r of data?.reminders ?? []) {
    const at = parseLocal(r.due_at);
    if (at) items.push({ key: `r${r.id}`, kind: "reminder", at, title: r.text, id: r.id, deletable: true,
      meta: r.recurrence ? t("Lembrete · repete") : t("Lembrete") });
  }
  for (const e of data?.events ?? []) {
    const at = parseLocal(e.start);
    if (at) items.push({ key: `e${e.id}`, kind: "event", at, title: e.title, id: e.id,
      deletable: e.source !== "google" && typeof e.id === "number",
      meta: [e.location, e.source === "google" ? "Google" : ""].filter(Boolean).join(" · ") || t("Evento") });
  }
  items.sort((a, b) => a.at.getTime() - b.at.getTime());
  const groups: { label: string; items: Item[] }[] = [];
  for (const item of items) {
    const label = dayLabel(item.at);
    const group = groups[groups.length - 1];
    if (group && group.label === label) group.items.push(item);
    else groups.push({ label, items: [item] });
  }

  return (
    <div className="page__inner" style={{ paddingBottom: 96 }}>
      {data === null && <div className="empty"><div className="spinner" style={{ margin: "0 auto" }} /></div>}
      {data !== null && items.length === 0 && (
        <div className="empty">
          <h2>{t("Nada marcado")}</h2>
          <p>{t("Pede ao NANO \"lembra-me de…\" ou carrega em Novo.")}</p>
        </div>
      )}
      {groups.map((group) => (
        <section key={group.label}>
          <h2 className="day-title">{group.label}</h2>
          <div className="list">
            {group.items.map((item) => (
              <div className="row" key={item.key}>
                <span className="time-chip">{timeLabel(item.at)}</span>
                <div className="row__body">
                  <div className="row__title">{item.title}</div>
                  <div className="row__meta">{item.meta}</div>
                </div>
                {item.deletable && (
                  <button className="icon-btn" onClick={() => void remove(item)} disabled={!online}
                    aria-label={t("Apagar {0}", { 0: item.title })}><Icon name="trash" size={20} /></button>
                )}
              </div>
            ))}
          </div>
        </section>
      ))}

      <button className="fab" onClick={() => open("reminder")} disabled={!online}><Icon name="plus" />{t("Novo")}</button>

      {adding && (
        <>
          <div className="scrim" onClick={() => setAdding(null)} />
          <div className="sheet" role="dialog" aria-modal="true" aria-label={adding === "reminder" ? t("Novo lembrete") : t("Novo evento")}>
            <div className="sheet__grab" />
            <div className="chips" style={{ marginBottom: 16 }}>
              <button className="chip" aria-pressed={adding === "reminder"} onClick={() => setAdding("reminder")}>{t("Lembrete")}</button>
              <button className="chip" aria-pressed={adding === "event"} onClick={() => setAdding("event")}>{t("Evento")}</button>
            </div>
            <div className="stack">
              <div className="field">
                <label htmlFor="agenda-text">{adding === "reminder" ? t("Lembrar de") : t("Título")}</label>
                <input id="agenda-text" className="input" value={text} maxLength={300} autoFocus
                  placeholder={adding === "reminder" ? t("Ligar à mãe") : t("Jantar com a Ana")}
                  onChange={(e) => setText(e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="agenda-when">{t("Quando")}</label>
                <input id="agenda-when" className="input" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
              </div>
              {adding === "reminder" && (
                <div className="field">
                  <label htmlFor="agenda-repeat">{t("Repetir")}</label>
                  <select id="agenda-repeat" className="select" value={repeat} onChange={(e) => setRepeat(e.target.value)}>
                    <option value="">{t("Não repetir")}</option>
                    <option value="daily">{t("Todos os dias")}</option>
                    <option value="weekdays">{t("Nos dias úteis")}</option>
                    <option value="weekly">{t("Todas as semanas")}</option>
                    <option value="monthly">{t("Todos os meses")}</option>
                  </select>
                </div>
              )}
              {error && <p className="error-text" role="alert">{error}</p>}
              <button className="btn btn--primary btn--block" disabled={!text.trim() || !when || saving} onClick={() => void save()}>
                {saving ? t("A guardar…") : t("Guardar")}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
