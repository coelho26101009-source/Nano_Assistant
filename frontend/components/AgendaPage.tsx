/**
 * Agenda: the user's reminders and calendar events, in one place.
 *
 * Until this page existed, reminders and events could only be created, listed
 * or deleted by asking Nano in the chat. Everything shown here comes from the
 * backend (get_agenda); every change goes back through it and the page is then
 * re-read, so the list never shows something the backend did not confirm.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { call } from "../lib/backend";
import { Badge, Button, ConfirmDialog, EmptyState, Field, Panel, Skeleton, Tabs } from "./ui";

export type AgendaReminder = { id: number; text: string; due_at: string; fired?: boolean; recurrence?: string | null };
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

const WEEKDAYS = ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"];
const MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];

function parseLocal(value?: string | null): Date | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(value);
  if (!match) return null;
  const [, y, mo, d, h = "0", mi = "0"] = match;
  return new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi));
}

const pad = (n: number) => String(n).padStart(2, "0");

/** "hoje", "amanhã" or "quinta, 12 nov": the day heading of a group. */
export function dayLabel(date: Date, now: Date = new Date()): string {
  const start = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diff = Math.round((start(date) - start(now)) / 86400000);
  if (diff === 0) return "Hoje";
  if (diff === 1) return "Amanhã";
  const base = `${WEEKDAYS[date.getDay()]}, ${date.getDate()} ${MONTHS[date.getMonth()]}`;
  return date.getFullYear() === now.getFullYear() ? base : `${base} ${date.getFullYear()}`;
}

function timeLabel(date: Date | null): string {
  return date ? `${pad(date.getHours())}:${pad(date.getMinutes())}` : "—";
}

function sourceLabel(source?: string): string | null {
  if (source === "google") return "Google";
  if (source === "ics") return "importado";
  return null;
}

export default function AgendaPage({ enabled, notify }: { enabled: boolean; notify: Notify }) {
  const [windowDays, setWindowDays] = useState<Window>("30");
  const [data, setData] = useState<AgendaPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<PendingDelete>(null);

  const [reminderText, setReminderText] = useState("");
  const [reminderWhen, setReminderWhen] = useState("");
  const [eventTitle, setEventTitle] = useState("");
  const [eventStart, setEventStart] = useState("");
  const [eventEnd, setEventEnd] = useState("");
  const [eventLocation, setEventLocation] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    const value = await call<AgendaPayload>("get_agenda", Number(windowDays));
    setData(value);
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
      notify(result?.message || "Não foi possível concluir.", "error");
    }
  }, [notify, refresh]);

  const addReminder = (event: React.FormEvent) => {
    event.preventDefault();
    run("agenda_create_reminder", [reminderText, reminderWhen], "Lembrete criado.", () => {
      setReminderText(""); setReminderWhen("");
    });
  };

  const addEvent = (event: React.FormEvent) => {
    event.preventDefault();
    run("agenda_create_event", [eventTitle, eventStart, eventEnd || null, eventLocation], "Evento criado.", () => {
      setEventTitle(""); setEventStart(""); setEventEnd(""); setEventLocation("");
    });
  };

  const confirmDelete = () => {
    if (!pendingDelete) return;
    const { kind, id } = pendingDelete;
    setPendingDelete(null);
    if (kind === "reminder") run("agenda_delete_reminder", [id], "Lembrete apagado.");
    else run("agenda_delete_event", [id], "Evento apagado.");
  };

  const eventGroups = useMemo(() => {
    const groups: { label: string; events: AgendaEvent[] }[] = [];
    for (const item of data?.events ?? []) {
      const start = parseLocal(item.start);
      const label = start ? dayLabel(start) : "Sem data";
      const last = groups[groups.length - 1];
      if (last && last.label === label) last.events.push(item);
      else groups.push({ label, events: [item] });
    }
    return groups;
  }, [data]);

  const reminders = data?.reminders ?? [];
  const remindersOff = data && !data.available.reminders;
  const calendarOff = data && !data.available.calendar;

  return (
    <div className="page__inner page__inner--wide agenda">
      <div className="agenda__grid">
        <Panel title="Lembretes" action={<Badge>{reminders.length}</Badge>}>
          {remindersOff ? (
            <EmptyState title="Os lembretes estão desligados"
                        hint="Liga reminders.enabled nas definições para os usar." />
          ) : (
            <>
              <form className="agenda__form" onSubmit={addReminder}>
                <Field label="Lembrar-me de" htmlFor="agenda-reminder-text">
                  <input id="agenda-reminder-text" className="input" value={reminderText} maxLength={300}
                         placeholder="Ligar ao dentista" onChange={(e) => setReminderText(e.target.value)} />
                </Field>
                <Field label="Quando" htmlFor="agenda-reminder-when"
                       hint="Por exemplo: 10 minutos, 18:30, 25/12 09:00 ou 2026-12-25 09:00">
                  <input id="agenda-reminder-when" className="input" value={reminderWhen} maxLength={64}
                         placeholder="18:30" onChange={(e) => setReminderWhen(e.target.value)} />
                </Field>
                <div className="agenda__form-actions">
                  <Button type="submit" variant="primary"
                          disabled={busy || !reminderText.trim() || !reminderWhen.trim()}>
                    Criar lembrete
                  </Button>
                </div>
              </form>
              {data?.remindersError && <p className="field__error">{data.remindersError}</p>}
              {loading && !data ? (
                <div className="stack stack--tight"><Skeleton height={44} /><Skeleton height={44} /></div>
              ) : reminders.length === 0 ? (
                <EmptyState title="Sem lembretes pendentes"
                            hint="Cria um aqui ou pede ao NANO: “lembra-me de beber água daqui a 1 hora”." />
              ) : (
                <ul className="stack stack--tight agenda__list" aria-label="Lembretes pendentes">
                  {reminders.map((item) => {
                    const due = parseLocal(item.due_at);
                    return (
                      <li key={item.id} className="row-item">
                        <div className="row-item__main">
                          <div className="row-item__title" title={item.text}>{item.text}</div>
                          <div className="row-item__meta">
                            {due ? `${dayLabel(due)} · ${timeLabel(due)}` : item.due_at}
                            {item.recurrence ? ` · repete ${item.recurrence}` : ""}
                          </div>
                        </div>
                        <div className="row-item__actions">
                          <Button size="sm" variant="ghost" disabled={busy}
                                  aria-label={`Apagar o lembrete ${item.text}`}
                                  onClick={() => setPendingDelete({ kind: "reminder", id: item.id, label: item.text })}>
                            Apagar
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

        <Panel title="Eventos" action={
          <Tabs<Window> value={windowDays} onChange={setWindowDays} tabs={[
            { value: "7", label: "7 dias" }, { value: "30", label: "30 dias" }, { value: "90", label: "90 dias" },
          ]} />
        }>
          {calendarOff ? (
            <EmptyState title="O calendário não está disponível" />
          ) : (
            <>
              <form className="agenda__form" onSubmit={addEvent}>
                <Field label="Título" htmlFor="agenda-event-title">
                  <input id="agenda-event-title" className="input" value={eventTitle} maxLength={200}
                         placeholder="Reunião de equipa" onChange={(e) => setEventTitle(e.target.value)} />
                </Field>
                <div className="agenda__form-row">
                  <Field label="Início" htmlFor="agenda-event-start">
                    <input id="agenda-event-start" className="input" type="datetime-local" value={eventStart}
                           onChange={(e) => setEventStart(e.target.value)} />
                  </Field>
                  <Field label="Fim (opcional)" htmlFor="agenda-event-end">
                    <input id="agenda-event-end" className="input" type="datetime-local" value={eventEnd}
                           min={eventStart || undefined} onChange={(e) => setEventEnd(e.target.value)} />
                  </Field>
                </div>
                <Field label="Local (opcional)" htmlFor="agenda-event-location">
                  <input id="agenda-event-location" className="input" value={eventLocation} maxLength={200}
                         onChange={(e) => setEventLocation(e.target.value)} />
                </Field>
                <div className="agenda__form-actions">
                  <Button type="submit" variant="primary" disabled={busy || !eventTitle.trim() || !eventStart}>
                    Criar evento
                  </Button>
                </div>
              </form>
              {data?.eventsError && <p className="field__error">{data.eventsError}</p>}
              {data?.googleError && (
                <p className="field__hint">O Google Calendar não respondeu: {data.googleError}</p>
              )}
              {loading && !data ? (
                <div className="stack stack--tight"><Skeleton height={44} /><Skeleton height={44} /></div>
              ) : eventGroups.length === 0 ? (
                <EmptyState title={`Nada marcado nos próximos ${windowDays} dias`}
                            hint="Cria um evento aqui, importa um ficheiro .ics pelo chat ou liga o Google Calendar." />
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
                                  {timeLabel(start)}{end ? `–${timeLabel(end)}` : ""}
                                  {item.location ? ` · ${item.location}` : ""}
                                </div>
                              </div>
                              <div className="row-item__actions">
                                {source && <Badge tone="info">{source}</Badge>}
                                {deletable && (
                                  <Button size="sm" variant="ghost" disabled={busy}
                                          aria-label={`Apagar o evento ${item.title}`}
                                          onClick={() => setPendingDelete({ kind: "event", id: item.id as number, label: item.title })}>
                                    Apagar
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
        title={pendingDelete?.kind === "event" ? "Apagar evento" : "Apagar lembrete"}
        message={<>Vais apagar <strong>{pendingDelete?.label}</strong>. Isto não pode ser desfeito.</>}
        confirmLabel="Apagar" danger
        onConfirm={confirmDelete} onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
