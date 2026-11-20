/**
 * Agenda: the user's reminders and calendar events, in one place.
 *
 * Until this page existed, reminders and events could only be created, listed
 * or deleted by asking Nano in the chat. Everything shown here comes from the
 * backend (get_agenda); every change goes back through it and the page is then
 * re-read, so the list never shows something the backend did not confirm.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { call } from "../lib/backend";
import { Badge, Button, ConfirmDialog, EmptyState, Field, Panel, Skeleton, Tabs } from "./ui";
import { intlLocale, t } from "../lib/i18n";
import { tj } from "../lib/tj";

export type AgendaReminder = { id: number; text: string; due_at: string; fired?: boolean; recurrence?: string | null; recurrence_label?: string | null };
const REPEAT_OPTIONS = [
  { value: "", label: t("Não repetir") },
  { value: "daily", label: t("Todos os dias") },
  { value: "weekdays", label: t("Dias úteis") },
  { value: "weekly", label: t("Todas as semanas") },
  { value: "monthly", label: t("Todos os meses") },
];
/** How a stored recurrence reads after "repete": from the code, in the
 *  interface language, not the backend's Portuguese label. */
const REPEAT_LABEL: Record<string, string> = {
  daily: t("todos os dias"), weekdays: t("nos dias úteis"),
  weekly: t("todas as semanas"), monthly: t("todos os meses"),
};

export type AgendaEvent = {
  id: number | string; title: string; start: string; end?: string | null;
  location?: string | null; source?: string;
};
export type AgendaPayload = {
  ok: boolean;
  windowDays: number;
  available: { reminders: boolean; calendar: boolean };
  reminders: AgendaReminder[];
  events: AgendaEvent[];
  remindersError?: string;
  eventsError?: string;
  googleError?: string;
};

type Result = { ok: boolean; message?: string } | null;
type Notify = (text: string, tone?: "default" | "error" | "success") => void;
type Window = "7" | "30" | "90";
type PendingDelete = { kind: "reminder" | "event"; id: number; label: string } | null;


function parseLocal(value?: string | null): Date | null {
  if (!value) return null;
  // A time with its own offset ("…T09:00:00Z", "…+01:00", as Google sends it)
  // is an instant: shown in this PC's time zone. Without one it is already
  // local wall time, which is how NANO stores its own events and reminders.
  if (/T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) {
    const instant = new Date(value);
    if (!Number.isNaN(instant.getTime())) return instant;
  }
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(value);
  if (!match) return null;
  const [, y, mo, d, h = "0", mi = "0"] = match;
  return new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi));
}

/** A calendar date with no time: an all-day event (Google's `start.date`). */
function isAllDay(value?: string | null): boolean {
  return Boolean(value && /^\d{4}-\d{2}-\d{2}$/.test(value.trim()));
}

const pad = (n: number) => String(n).padStart(2, "0");

/** "hoje", "amanhã" or "quinta, 12 nov": the day heading of a group. */
export function dayLabel(date: Date, now: Date = new Date()): string {
  const start = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = Math.round((start(date) - start(now)) / 86400000);
  if (diff === 0) return t("Hoje");
  if (diff === 1) return t("Amanhã");
  // The weekday and month come from the browser's own calendar data, in the
  // interface language: "quinta, 12 nov" or "Thursday, Nov 12".
  return new Intl.DateTimeFormat(intlLocale(), {
    weekday: "long", day: "numeric", month: "short",
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  }).format(date);
}

function timeLabel(date: Date | null): string {
  return date ? `${pad(date.getHours())}:${pad(date.getMinutes())}` : "—";
}

function sourceLabel(source?: string): string | null {
  if (source === "google") return "Google";
  if (source === "ics") return t("importado");
  return null;
}

export default function AgendaPage({ enabled, notify }: { enabled: boolean; notify: Notify }) {
  const [windowDays, setWindowDays] = useState<Window>("30");
  const [data, setData] = useState<AgendaPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<PendingDelete>(null);
  // A failed read is not an empty agenda. It used to show "Sem lembretes
  // pendentes" and "Nada marcado", which is a statement the page could not make.
  const [failed, setFailed] = useState(false);
  const requestSeq = useRef(0);

  const [reminderText, setReminderText] = useState("");
  const [reminderWhen, setReminderWhen] = useState("");
  const [reminderRepeat, setReminderRepeat] = useState("");
  const [eventTitle, setEventTitle] = useState("");
  const [eventStart, setEventStart] = useState("");
  const [eventEnd, setEventEnd] = useState("");
  const [eventLocation, setEventLocation] = useState("");

  const refresh = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    const value = await call<AgendaPayload>("get_agenda", Number(windowDays));
    // Switching 7 → 30 → 90 days quickly sends three reads; an older, slower
    // answer must not replace the one that was asked for last.
    if (seq !== requestSeq.current) return;
    if (value && value.ok !== false) {
      setData(value);
      setFailed(false);
    } else {
      setFailed(true);
    }
    setLoading(false);
  }, [windowDays]);

  useEffect(() => { if (enabled) refresh(); }, [enabled, refresh]);

  const run = useCallback(async (name: string, args: any[], success: string, after?: () => void) => {
    setBusy(true);
    const result = await call<Result>(name, ...args);
    setBusy(false);
    if (result?.ok) {
      notify(result.message || success, "success");
      after?.();
      refresh();
    } else {
      notify(result?.message || t("Não foi possível concluir."), "error");
    }
  }, [notify, refresh]);

  const addReminder = (event: React.FormEvent) => {
    event.preventDefault();
    run("agenda_create_reminder", [reminderText, reminderWhen, reminderRepeat || null], t("Lembrete criado."), () => {
      setReminderText(""); setReminderWhen(""); setReminderRepeat("");
    });
  };

  const addEvent = (event: React.FormEvent) => {
    event.preventDefault();
    run("agenda_create_event", [eventTitle, eventStart, eventEnd || null, eventLocation], t("Evento criado."), () => {
      setEventTitle(""); setEventStart(""); setEventEnd(""); setEventLocation("");
    });
  };

  const confirmDelete = () => {
    if (!pendingDelete) return;
    const { kind, id } = pendingDelete;
    setPendingDelete(null);
    if (kind === "reminder") run("agenda_delete_reminder", [id], t("Lembrete apagado."));
    else run("agenda_delete_event", [id], t("Evento apagado."));
  };

  const eventGroups = useMemo(() => {
    const groups: { label: string; events: AgendaEvent[] }[] = [];
    for (const item of data?.events ?? []) {
      const start = parseLocal(item.start);
      const label = start ? dayLabel(start) : t("Sem data");
      const last = groups[groups.length - 1];
      if (last && last.label === label) last.events.push(item);
      else groups.push({ label, events: [item] });
    }
    return groups;
  }, [data]);

  const reminders = data?.reminders ?? [];
  const remindersOff = data && !data.available.reminders;
  const calendarOff = data && !data.available.calendar;
  const unknown = failed && !data;
  const readError = failed && (
    <div className="inline" role="alert" style={{ gap: 8, marginBottom: 8 }}>
      <span className="field__error">{t("Não foi possível ler a agenda.")}</span>
      <Button size="sm" variant="ghost" disabled={loading} onClick={() => { void refresh(); }}>{t("Tentar de novo")}</Button>
    </div>
  );

  return (
    <div className="page__inner page__inner--wide agenda">
      <div className="agenda__grid">
        <Panel title={t("Lembretes")} action={<Badge>{reminders.length}</Badge>}>
          {remindersOff ? (
            <EmptyState title={t("Os lembretes estão desligados")}
                        hint={t("Liga reminders.enabled nas definições para os usar.")} />
          ) : (
            <>
              <form className="agenda__form" onSubmit={addReminder}>
                <Field label={t("Lembrar-me de")} htmlFor="agenda-reminder-text">
                  <input id="agenda-reminder-text" className="input" value={reminderText} maxLength={300}
                         placeholder={t("Ligar ao dentista")} onChange={(e) => setReminderText(e.target.value)} />
                </Field>
                <Field label={t("Quando")} htmlFor="agenda-reminder-when"
                       hint={t("Por exemplo: 10 minutos, 18:30, amanhã às 9 ou sexta às 15h")}>
                  <input id="agenda-reminder-when" className="input" value={reminderWhen} maxLength={64}
                         placeholder="18:30" onChange={(e) => setReminderWhen(e.target.value)} />
                </Field>
                <Field label={t("Repetir")} htmlFor="agenda-reminder-repeat">
                  <select id="agenda-reminder-repeat" className="input input--select" value={reminderRepeat}
                          onChange={(e) => setReminderRepeat(e.target.value)}>
                    {REPEAT_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </Field>
                <div className="agenda__form-actions">
                  <Button type="submit" variant="primary"
                          disabled={busy || !reminderText.trim() || !reminderWhen.trim()}>
                    {t("Criar lembrete")}
                  </Button>
                </div>
              </form>
              {data?.remindersError && <p className="field__error">{data.remindersError}</p>}
              {readError}
              {loading && !data ? (
                <div className="stack stack--tight"><Skeleton height={44} /><Skeleton height={44} /></div>
              ) : unknown ? null : reminders.length === 0 ? (
                <EmptyState title={t("Sem lembretes pendentes")}
                            hint={t("Cria um aqui ou pede ao NANO: “lembra-me de beber água daqui a 1 hora”.")} />
              ) : (
                <ul className="stack stack--tight agenda__list" aria-label={t("Lembretes pendentes")}>
                  {reminders.map((item) => {
                    const due = parseLocal(item.due_at);
                    return (
                      <li key={item.id} className="row-item">
                        <div className="row-item__main">
                          <div className="row-item__title" title={item.text}>{item.text}</div>
                          <div className="row-item__meta">
                            {due ? `${dayLabel(due)} · ${timeLabel(due)}` : item.due_at}
                            {item.recurrence
                              ? ` · ${t("repete {0}", { 0: REPEAT_LABEL[item.recurrence] ?? item.recurrence_label ?? item.recurrence })}`
                              : ""}
                          </div>
                        </div>
                        <div className="row-item__actions">
                          <Button size="sm" variant="ghost" disabled={busy}
                                  aria-label={t("Apagar o lembrete {0}", { 0: item.text })}
                                  onClick={() => setPendingDelete({ kind: "reminder", id: item.id, label: item.text })}>
                            {t("Apagar")}
                          </Button>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </>
          )}
        </Panel>

        <Panel title={t("Eventos")} action={
          <Tabs<Window> value={windowDays} onChange={setWindowDays} tabs={[
            { value: "7", label: t("{0} dias", { 0: 7 }) }, { value: "30", label: t("{0} dias", { 0: 30 }) },
            { value: "90", label: t("{0} dias", { 0: 90 }) },
          ]} />
        }>
          {calendarOff ? (
            <EmptyState title={t("O calendário não está disponível")} />
          ) : (
            <>
              <form className="agenda__form" onSubmit={addEvent}>
                <Field label={t("Título")} htmlFor="agenda-event-title">
                  <input id="agenda-event-title" className="input" value={eventTitle} maxLength={200}
                         placeholder={t("Reunião de equipa")} onChange={(e) => setEventTitle(e.target.value)} />
                </Field>
                <div className="agenda__form-row">
                  <Field label={t("Início")} htmlFor="agenda-event-start">
                    <input id="agenda-event-start" className="input" type="datetime-local" value={eventStart}
                           onChange={(e) => setEventStart(e.target.value)} />
                  </Field>
                  <Field label={t("Fim (opcional)")} htmlFor="agenda-event-end">
                    <input id="agenda-event-end" className="input" type="datetime-local" value={eventEnd}
                           min={eventStart || undefined} onChange={(e) => setEventEnd(e.target.value)} />
                  </Field>
                </div>
                <Field label={t("Local (opcional)")} htmlFor="agenda-event-location">
                  <input id="agenda-event-location" className="input" value={eventLocation} maxLength={200}
                         onChange={(e) => setEventLocation(e.target.value)} />
                </Field>
                <div className="agenda__form-actions">
                  <Button type="submit" variant="primary" disabled={busy || !eventTitle.trim() || !eventStart}>
                    {t("Criar evento")}
                  </Button>
                </div>
              </form>
              {data?.eventsError && <p className="field__error">{data.eventsError}</p>}
              {data?.googleError && (
                <p className="field__hint">{t("O Google Calendar não respondeu: {0}", { 0: data.googleError })}</p>
              )}
              {readError}
              {loading && !data ? (
                <div className="stack stack--tight"><Skeleton height={44} /><Skeleton height={44} /></div>
              ) : unknown ? null : eventGroups.length === 0 ? (
                <EmptyState title={t("Nada marcado nos próximos {0} dias", { 0: windowDays })}
                            hint={t("Cria um evento aqui, importa um ficheiro .ics pelo chat ou liga o Google Calendar.")} />
              ) : (
                <div className="stack agenda__list">
                  {eventGroups.map((group) => (
                    <section key={group.label} aria-label={group.label}>
                      <div className="section-label agenda__day">{group.label}</div>
                      <ul className="stack stack--tight">
                        {group.events.map((item) => {
                          const start = parseLocal(item.start);
                          const end = parseLocal(item.end);
                          const source = sourceLabel(item.source);
                          const deletable = item.source !== "google" && typeof item.id === "number";
                          return (
                            <li key={`${item.source}-${item.id}`} className="row-item">
                              <div className="row-item__main">
                                <div className="row-item__title" title={item.title}>{item.title}</div>
                                <div className="row-item__meta">
                                  {isAllDay(item.start)
                                    ? t("Todo o dia")
                                    : `${timeLabel(start)}${end ? `–${timeLabel(end)}` : ""}`}
                                  {item.location ? ` · ${item.location}` : ""}
                                </div>
                              </div>
                              <div className="row-item__actions">
                                {source && <Badge tone="info">{source}</Badge>}
                                {deletable && (
                                  <Button size="sm" variant="ghost" disabled={busy}
                                          aria-label={t("Apagar o evento {0}", { 0: item.title })}
                                          onClick={() => setPendingDelete({ kind: "event", id: item.id as number, label: item.title })}>
                                    {t("Apagar")}
                                  </Button>
                                )}
                              </div>
                            </li>
                          );
                        })}
                      </ul>
                    </section>
                  ))}
                </div>
              )}
            </>
          )}
        </Panel>
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title={pendingDelete?.kind === "event" ? t("Apagar evento") : t("Apagar lembrete")}
        message={tj("Vais apagar {0}. Isto não pode ser desfeito.", { 0: <strong>{pendingDelete?.label}</strong> })}
        confirmLabel={t("Apagar")} danger
        onConfirm={confirmDelete} onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
