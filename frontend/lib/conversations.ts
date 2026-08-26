/**
 * Turning the stored message log into a list of conversations.
 *
 * WHY THIS IS DERIVED AND NOT STORED. The backend keeps ONE rolling message log
 * (core/memory.py: a flat `messages` table) and one live context window in the
 * Brain. There is no thread table, no thread id and no API to reopen a thread —
 * so a sidebar that listed "conversations" from a store would be listing
 * something that does not exist.
 *
 * What DOES exist is a real timestamp on every real message. A conversation is
 * recovered from that: a run of messages with no long silence in it. The titles
 * are the user's own first sentence, the times are the times the messages were
 * actually saved, and the groups are real calendar buckets. Nothing here is
 * invented; if the log holds one conversation, the list shows one conversation.
 *
 * The consequence is stated in the UI rather than hidden: only the most recent
 * conversation is live, because only its messages are in the Brain's context.
 * Older ones open read-only. See Rail.tsx and the shell's `readingSession`.
 */

/** A message exactly as core/memory.get_recent_messages returns it. */
export type HistoryMessage = {
  role: string;
  content: string;
  timestamp: string;
};

export type Session = {
  /** Stable across refetches: the timestamp of the session's first message. */
  id: string;
  title: string;
  startedAt: Date;
  updatedAt: Date;
  messages: HistoryMessage[];
};

/**
 * How long a silence has to be before it counts as a new conversation.
 *
 * Forty-five minutes is a judgement call, and it is the only one in this file.
 * Too short and a pause for coffee splits one conversation in two; too long and
 * a morning and an evening session merge. It is a constant rather than a
 * setting because a slider for it would be a worse product than a sensible
 * default.
 */
export const SESSION_GAP_MS = 45 * 60 * 1000;

const DISPLAY_ROLES = new Set(["user", "assistant"]);

function parseTime(value: string): Date | null {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** The first line of the user's first message, shortened to fit a row. */
function titleFor(messages: HistoryMessage[]): string {
  const first = messages.find((m) => m.role === "user" && m.content.trim());
  const source = (first ?? messages[0])?.content ?? "";
  const line = source.replace(/\s+/g, " ").trim();
  if (!line) return "Conversa sem título";
  return line.length > 52 ? `${line.slice(0, 51).trimEnd()}…` : line;
}

/**
 * Split the stored log into conversations, newest first.
 *
 * Tool and system messages are dropped: they are machinery, and a conversation
 * whose title came from a tool payload would be unreadable.
 *
 * `breaks` are the moments the user explicitly pressed "Nova conversa". They
 * matter because that is a real boundary the clock cannot see: starting a fresh
 * conversation and immediately typing produces a gap of seconds, which the
 * silence rule alone would merge straight back into the previous one.
 */
export function splitSessions(
  history: HistoryMessage[] | null | undefined,
  breaks: number[] = [],
): Session[] {
  if (!history?.length) return [];

  const usable = history
    .filter((m) => DISPLAY_ROLES.has(m.role) && (m.content ?? "").trim())
    .map((m) => ({ message: m, at: parseTime(m.timestamp) }))
    .filter((row): row is { message: HistoryMessage; at: Date } => row.at !== null)
    .sort((a, b) => a.at.getTime() - b.at.getTime());

  if (!usable.length) return [];

  const boundaries = [...breaks].sort((a, b) => a - b);

  const groups: { message: HistoryMessage; at: Date }[][] = [[usable[0]]];
  for (let i = 1; i < usable.length; i += 1) {
    const previous = usable[i - 1].at.getTime();
    const current = usable[i];
    const now = current.at.getTime();
    const brokenByUser = boundaries.some((at) => at > previous && at <= now);
    if (brokenByUser || now - previous > SESSION_GAP_MS) groups.push([current]);
    else groups[groups.length - 1].push(current);
  }

  return groups
    .map((rows) => ({
      id: rows[0].message.timestamp,
      title: titleFor(rows.map((r) => r.message)),
      startedAt: rows[0].at,
      updatedAt: rows[rows.length - 1].at,
      messages: rows.map((r) => r.message),
    }))
    .reverse();
}

export type GroupKey = "today" | "yesterday" | "week" | "older";

export const GROUP_LABEL: Record<GroupKey, string> = {
  today: "Hoje",
  yesterday: "Ontem",
  week: "Esta semana",
  older: "Mais antigas",
};

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** Which calendar bucket a moment falls in, relative to now. */
export function groupKeyFor(when: Date, now: Date = new Date()): GroupKey {
  const today = startOfDay(now);
  const day = startOfDay(when);
  if (day === today) return "today";
  if (day === today - 86_400_000) return "yesterday";
  if (day > today - 7 * 86_400_000) return "week";
  return "older";
}

/** Group sessions for the rail, dropping any bucket that has nothing in it. */
export function groupSessions(
  sessions: Session[], now: Date = new Date(),
): { key: GroupKey; label: string; sessions: Session[] }[] {
  const buckets = new Map<GroupKey, Session[]>();
  for (const session of sessions) {
    const key = groupKeyFor(session.updatedAt, now);
    const list = buckets.get(key);
    if (list) list.push(session);
    else buckets.set(key, [session]);
  }
  return (["today", "yesterday", "week", "older"] as GroupKey[])
    .filter((key) => buckets.get(key)?.length)
    .map((key) => ({ key, label: GROUP_LABEL[key], sessions: buckets.get(key)! }));
}

/**
 * The short time stamp shown on a row.
 *
 * Today gets a clock, this week gets a weekday, anything older gets a date —
 * the same rule every mail client uses, because it is the one that carries the
 * most information in the fewest characters.
 */
export function sessionStamp(session: Session, now: Date = new Date()): string {
  const key = groupKeyFor(session.updatedAt, now);
  if (key === "today") {
    return session.updatedAt.toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" });
  }
  if (key === "yesterday") return "Ontem";
  if (key === "week") {
    const weekday = session.updatedAt.toLocaleDateString("pt-PT", { weekday: "short" });
    return weekday.replace(".", "");
  }
  return session.updatedAt.toLocaleDateString("pt-PT", { day: "2-digit", month: "2-digit" });
}

/** Case- and accent-insensitive match over the title and the message bodies. */
export function matchesQuery(session: Session, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  const strip = (value: string) =>
    value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const target = strip(needle);
  if (strip(session.title).includes(target)) return true;
  return session.messages.some((m) => strip(m.content).includes(target));
}

/* ── Explicit conversation breaks ─────────────────────────────────
 * Pressing "Nova conversa" is a real event with a real moment, and it is the
 * only conversation boundary the timestamps cannot reveal on their own: start a
 * fresh conversation and type straight away and the gap is seconds, which the
 * silence rule would fold back into the previous one.
 *
 * IT IS DELIBERATELY NOT PERSISTED. The obvious home would be localStorage, and
 * the frontend is not allowed to touch browser storage at all: a blanket ban is
 * what keeps a provider key from ever being cached somewhere the page can read
 * it back, and tests/test_providers_and_secrets.py enforces it across every
 * frontend file. Narrowing that invariant to smuggle a list of timestamps
 * through would be a bad trade.
 *
 * The cost is small and bounded: the breaks live for the session, so after a
 * reload two conversations separated by hand less than SESSION_GAP_MS apart
 * appear in the rail as one row. Nothing is lost -- every message is still
 * there, in order -- and the next silence re-separates them.
 */
export function recordSessionBreak(breaks: number[], at: number = Date.now()): number[] {
  return [...breaks, at].slice(-40);
}
