/**
 * Conversation threads, as the backend actually stores them.
 *
 * WHAT CHANGED, AND WHY THE FILE KEPT ITS NAME
 * --------------------------------------------
 * This module used to DERIVE conversations from a flat message log: it split a
 * single rolling list on 45 minutes of silence, because the backend had no
 * thread table, no thread id and no way to reopen one. The comment at the top
 * said so honestly, and the consequences were real — only the newest
 * conversation could be continued, older ones opened read-only, and nothing
 * could be renamed or deleted because there was no object to rename or delete.
 *
 * There is now a `conversations` table. A thread has an id, a title the user
 * owns, its own messages and its own summary, and the backend rebuilds the
 * model's context from whichever thread is opened. So this file no longer
 * reconstructs anything: it types what the bridge returns and keeps the
 * presentation helpers — calendar grouping, the short timestamp, the search
 * predicate — which were always the good part.
 *
 * `splitSessions` and `recordSessionBreak` are gone with the guesswork they
 * supported. The 45-minute rule survives in exactly one place, `LEGACY_SESSION_GAP`
 * in core/memory_schema.py, where it is used ONCE to split the pre-existing flat
 * log into real threads during migration — so the conversations a user already
 * recognises come across with their titles and their order intact.
 */

/** A stored message, as core/conversation_store.messages returns it. */
export type ThreadMessage = {
  id: number;
  role: string;
  content: string;
  timestamp: string;
  trust?: string;
};

/** A conversation thread, as core/conversation_store returns it. */
export type Thread = {
  id: string;
  title: string;
  /** "auto" until the user renames it; then "user", and Nano stops retitling. */
  titleSource: "auto" | "user" | string;
  createdAt: string;
  updatedAt: string;
  lastMessageAt: string | null;
  messageCount: number;
  archived: boolean;
  metadata?: Record<string, unknown>;
};

export type ThreadSummary = {
  summary: string;
  coveredThrough: number;
  coveredMessages: number;
  generator: string;
  updatedAt: string;
};

export type GroupKey = "today" | "yesterday" | "week" | "older";

export const GROUP_LABEL: Record<GroupKey, string> = {
  today: "Hoje",
  yesterday: "Ontem",
  week: "Esta semana",
  older: "Mais antigas",
};

/** When a thread was last active. Falls back to its creation time. */
export function threadTime(thread: Thread): Date {
  const raw = thread.lastMessageAt ?? thread.updatedAt ?? thread.createdAt;
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? new Date(0) : date;
}

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

/** Group threads for the rail, dropping any bucket that has nothing in it. */
export function groupThreads(
  threads: Thread[], now: Date = new Date(),
): { key: GroupKey; label: string; threads: Thread[] }[] {
  const buckets = new Map<GroupKey, Thread[]>();
  for (const thread of threads) {
    const key = groupKeyFor(threadTime(thread), now);
    const list = buckets.get(key);
    if (list) list.push(thread);
    else buckets.set(key, [thread]);
  }
  return (["today", "yesterday", "week", "older"] as GroupKey[])
    .filter((key) => buckets.get(key)?.length)
    .map((key) => ({ key, label: GROUP_LABEL[key], threads: buckets.get(key)! }));
}

/**
 * The short timestamp shown on a row.
 *
 * Today gets a clock, this week gets a weekday, anything older gets a date —
 * the rule every mail client uses, because it carries the most information in
 * the fewest characters.
 */
export function threadStamp(thread: Thread, now: Date = new Date()): string {
  const when = threadTime(thread);
  const key = groupKeyFor(when, now);
  if (key === "today") {
    return when.toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" });
  }
  if (key === "yesterday") return "Ontem";
  if (key === "week") return when.toLocaleDateString("pt-PT", { weekday: "short" }).replace(".", "");
  return when.toLocaleDateString("pt-PT", { day: "2-digit", month: "2-digit" });
}

/** Case- and accent-insensitive match. Mirrors core/text_normalize.normalize. */
export function normalizeText(value: string): string {
  return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
}

/**
 * Whether a thread matches what the user typed in the rail search.
 *
 * TITLE ONLY, and deliberately. Message bodies are searched by the backend's
 * retrieval index, which is ranked and does not require every thread's text to
 * be in the browser. Filtering titles here keeps typing instant; finding a
 * thread by something said inside it is `search_memories`/`memory_search_history`,
 * a different question with a different cost.
 */
export function matchesThread(thread: Thread, query: string): boolean {
  const needle = normalizeText(query);
  if (!needle) return true;
  return normalizeText(thread.title).includes(needle);
}
