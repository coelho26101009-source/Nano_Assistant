/**
 * NANO application shell.
 *
 * Layout: sidebar · main column (top bar, then the stage).
 *
 * Follows the Chatbot UI layout. The sidebar carries the brand, a new
 * conversation, search, the five sections (Chat, Ferramentas, PC, Memória,
 * Definições) and the list of real conversations; it collapses to a column of
 * icons, and becomes a drawer at narrow widths. The top bar names the open page
 * and holds the AI selector and, in the desktop shell, the window caption. The
 * stage holds the conversation or the page of whichever section is open. There
 * is no fixed inspector column: its panels live in PC › Estado, where they
 * answer a question instead of permanently occupying the main screen.
 *
 * All state shown here is read from the Python backend through lib/backend.
 * Nothing is mocked and nothing defaults to "ready".
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Head from "next/head";

import CommandPalette, { Command } from "../components/CommandPalette";
import ShortcutsDialog from "../components/ShortcutsDialog";
import VoiceMode from "../components/VoiceMode";
import ConfirmModal, { ConfirmCard } from "../components/Confirm";
import PluginCodeModal from "../components/PluginCodeModal";
import Rail, { type RailMode } from "../components/Rail";
import CapabilitiesPage from "../components/CapabilitiesPage";
import AgendaPage from "../components/AgendaPage";
import WorkModesPage, { type WorkModeResult } from "../components/WorkModesPage";
import SettingsPage, { type Section as SettingsSection } from "../components/SettingsPage";
import TaskDetailModal from "../components/TaskDetailModal";
import TopBar from "../components/TopBar";
import Icon from "../components/Icon";
import { SECTIONS, ViewId, sectionEntry, sectionOf } from "../lib/navigation";
import { BRAND_NAME } from "../lib/brand";
import FirstRunGuide from "../components/FirstRunGuide";
import DiagnosticsPanel from "../components/DiagnosticsPanel";
import {
  Composer, Conversation, Message, SidePanel, ToolEvent, cleanAssistantText, type PanelDoc,
  type NoticeAction, type NoticeInfo,
} from "../components/Conversation";
import {
  ActivityPage, AgentsPage, IntegrationsPage,
  PermissionsPage, StatusPage, TaskScope, TasksPage, pcActivityMatches,
} from "../components/Pages";
import { Button, ErrorState, ToastStack, stateLabel, useToasts } from "../components/ui";
import {
  CloudProviderKey, CommandCenterPayload, PcActivityCategory, PcActivityEntry, PcSnapshot,
  POLL, ProviderPayload, ReadinessPayload,
  SettingsPayload, TaskCounts, TaskRow, VoiceDiagnostics,
  call, expose, useBridgeReady, useFetch, usePolled,
} from "../lib/backend";
import { MemoriesPage, GraphPage, KnowledgePage, NodeDetailModal, RecapsPage } from "../components/MemoryPages";
import { Thread, ThreadMessage } from "../lib/conversations";
import type {
  KnowledgeGraphPayload, KnowledgeNode, MemoryOverview, RecapsPayload,
} from "../lib/memory";
import { useIsDesktop } from "../lib/desktop";
// The product version comes from version.json, which core/version.py and the
// Electron main process read too. It used to be a literal here, which is how
// the UI said "v1.0" while the backend reported "8.1.0".
import { APP_VERSION } from "../lib/version";
import { locale, normaliseLocale, reloadInLocale, t } from "../lib/i18n";
import { toPayload, type PendingFile } from "../lib/attachments";


export interface ConfirmRequest {
  requestId: string;
  message: string;
  meta: Record<string, any>;
}

/** The id of the user bubble belonging to a turn.
 *
 * One authoritative insertion per user turn, keyed by the request id rather
 * than by comparing message text: the same text may legitimately be sent
 * twice, so text equality can never be the identity of a turn. */
const userMessageId = (requestId: string) => `user:${requestId}`;

/**
 * The backend's narration of a turn, reduced to what a person needs to read.
 *
 * Routing narrates itself in full ("Cloud indisponível — a usar o Ollama
 * local. Adiciona uma chave de API do Groq… do Mistral… do Google… — a usar
 * qwen3:8b local…"), which is the right thing for the log and the wrong thing
 * for the line under a pending answer: it was a paragraph about API keys on
 * every single turn. Provider changes and local fallbacks become the generic
 * "A pensar…" (the per-message technical details still say exactly which
 * provider answered and why); anything else keeps its own short text.
 */
/** Tools whose raw name says little: a sentence for the status line and card. */
const TOOL_SUMMARY: Record<string, () => string> = {
  deep_research: () => t("A investigar a fundo: várias pesquisas e sítios…"),
};

function toolStatus(name: string): string {
  return TOOL_SUMMARY[name]?.() ?? t("A usar {0}…", { 0: name });
}

function friendlyStatus(raw: string): string {
  const text = String(raw ?? "").replace(/^_thinking_:?\s*/, "").trim();
  // 🧠 local fallback, 🔄 switching provider: routing, not activity.
  if (/^[🧠🔄]/u.test(text) || /^\\?🔄/u.test(text)) return "";
  const clean = text.replace(/^[⚙️]+\s*/u, "").trim();
  // A long sentence is an explanation, not a status. The details panel has it.
  return clean.length > 80 ? "" : clean;
}

/**
 * Whether the window matches a width query -- narrow enough that the sidebar
 * starts collapsed and expands as a drawer, or so narrow that it leaves the
 * layout entirely.
 *
 * Resolved in an effect, never during render: the statically exported HTML and
 * the first client render must agree or React logs a hydration mismatch.
 */
function useNarrow(query = "(max-width: 1024px)"): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const media = window.matchMedia(query);
    const sync = () => setNarrow(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, [query]);
  return narrow;
}

type ThemePref = "system" | "dark" | "light";

/** Pages for developers, hidden until Modo avançado is on. */
const ADVANCED_VIEWS: ReadonlySet<ViewId> = new Set<ViewId>(["agents", "integrations"]);

/** A media query's live answer, with a fallback until it is known. */
function useMediaQuery(query: string, fallback: boolean): boolean {
  const [matches, setMatches] = useState(fallback);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const media = window.matchMedia(query);
    const sync = () => setMatches(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, [query]);
  return matches;
}

export default function Home() {
  const { ready, gaveUp } = useBridgeReady();
  const { toasts, notify } = useToasts();
  // PC -> Modos: which mode is running, and how the last one went.
  const [modeRunning, setModeRunning] = useState<string | null>(null);
  const [modeResult, setModeResult] = useState<WorkModeResult | null>(null);
  // Capability detection, resolved after mount. In a browser this stays false
  // and the window controls are simply never rendered.
  const isDesktop = useIsDesktop();
  // Read by backend callbacks registered once on mount.
  const isDesktopRef = useRef(isDesktop);
  isDesktopRef.current = isDesktop;
  const narrow = useNarrow();
  // Below this the collapsed icon column no longer fits either; the sidebar
  // leaves the layout and the top bar carries the button that opens it.
  const mobile = useNarrow("(max-width: 640px)");

  /* ── Shell state ────────────────────────────────────────────────────── */
  const [view, setView] = useState<ViewId>("chat");
  /* Which Settings category is open. Lifted out of SettingsPage so "Abrir
     definições de IA" in the AI pill can land on IA directly instead of on
     whichever category happened to be open last. */
  const [settingsSection, setSettingsSection] = useState<SettingsSection>("general");
  /* Two sidebar states, because they answer two different questions. At
     narrow widths the sidebar is a drawer, and `railOpen` is whether it is
     open over the page right now -- it closes again as soon as something in it
     is chosen. At desktop widths it is docked, and `railCollapsed` is whether
     the user folded it down to its icon column, a choice that holds for the
     session. */
  const [railOpen, setRailOpen] = useState(false);
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [voiceModeOpen, setVoiceModeOpen] = useState(false);
  /** A long answer or code block open beside the chat. */
  const [panelDoc, setPanelDoc] = useState<PanelDoc | null>(null);
  /** Text files waiting in the composer for the next message. */
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  // Appearance and personalisation live in the backend's settings (the
  // frontend uses no browser storage). "system" follows the OS theme.
  const [themePref, setThemePref] = useState<ThemePref>("system");
  const systemDark = useMediaQuery("(prefers-color-scheme: dark)", true);
  const theme: "dark" | "light" = themePref === "system" ? (systemDark ? "dark" : "light") : themePref;
  const [reduceMotion, setReduceMotionState] = useState(false);
  const [advancedMode, setAdvancedModeState] = useState(false);
  const [displayName, setDisplayNameState] = useState("");

  /* ── Conversation ───────────────────────────────────────────────────── */
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const [status, setStatus] = useState("");
  const [listening, setListening] = useState(false);
  // Rate limiting is a temporary, self-clearing state with a known wait, not
  // an error message. Kept separate so the UI can count it down.
  const [rateLimit, setRateLimit] =
    useState<{ message: string; waitSeconds: number; provider: string } | null>(null);
  const [voicePhase, setVoicePhase] = useState<{ phase: string; detail: string } | null>(null);

  /* ── Conversation threads ───────────────────────────────────────────────
   * `threads` is the real list from the backend and `activeThreadId` is the one
   * the Brain currently holds. There is no `reading` state any more: every
   * thread is writable, because opening one rebuilds the model's context from
   * it. That was the single biggest limitation of the derived-session model. */
  const [threads, setThreads] = useState<Thread[] | null>(null);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [chatQuery, setChatQuery] = useState("");
  const [profileName, setProfileName] = useState<string | null>(null);
  const [memoryReady, setMemoryReady] = useState(true);
  const [showFirstRun, setShowFirstRun] = useState(false);
  const [savingFirstRun, setSavingFirstRun] = useState(false);
  const firstRunChecked = useRef(false);

  /* ── Memória / Second Brain ─────────────────────────────────────────── */
  const [nodeDetail, setNodeDetail] = useState<any | null>(null);
  const [nodeOpen, setNodeOpen] = useState(false);
  const [graphType, setGraphType] = useState("");

  /* ── Dialogs ────────────────────────────────────────────────────────── */
  // A QUEUE, not one slot. Two approvals can be pending at once (a plan step
  // and a phone request, two tools in one turn); with one slot the second
  // replaced the first, which then waited, invisible, until it timed out.
  const [confirmQueue, setConfirmQueue] = useState<ConfirmRequest[]>([]);
  const confirmReq = confirmQueue[0] ?? null;
  const answerConfirm = useCallback((requestId: string, approved: boolean) => {
    call("confirm_action", requestId, approved);
    setConfirmQueue((queue) => queue.filter((item) => item.requestId !== requestId));
  }, []);
  const [taskDetail, setTaskDetail] = useState<any>(null);
  const [taskDetailEvents, setTaskDetailEvents] = useState<any[]>([]);
  const [taskDetailOpen, setTaskDetailOpen] = useState(false);
  const [pluginCode, setPluginCode] = useState<any>(null);
  const [resolving, setResolving] = useState(false);
  const [busy, setBusy] = useState(false);

  /* ── Page-scoped state ──────────────────────────────────────────────── */
  const [taskScope, setTaskScope] = useState<TaskScope>("active");
  const [taskQuery, setTaskQuery] = useState("");
  const [activityCategory, setActivityCategory] = useState<PcActivityCategory>("all");
  const [plugins, setPlugins] = useState<Record<string, string[]>>({});
  const [policies, setPolicies] = useState<any[]>([]);

  /* ── Backend data ───────────────────────────────────────────────────── */
  const { data: commandCenter, loading: ccLoading, refresh: refreshCC } =
    usePolled<CommandCenterPayload>("get_command_center_state", POLL.commandCenter, ready);
  const { data: readiness, refresh: refreshReadiness } =
    usePolled<ReadinessPayload>("get_system_readiness", POLL.readiness, ready);
  const { data: providers, refresh: refreshProviders } =
    usePolled<ProviderPayload>("get_providers", POLL.readiness, ready);
  const { data: counts, refresh: refreshCounts } =
    usePolled<TaskCounts>("get_task_counts", POLL.taskCounts, ready);

  const { data: tasks, loading: tasksLoading, refresh: refreshTasks } =
    useFetch<TaskRow[]>("list_tasks_filtered", ready && view === "tasks", [taskScope], taskScope, 100);
  /* PC -> Atividade. Fetched unfiltered once per page visit -- the category
     tabs are a client-side filter over this one list (see
     Pages.tsx:pcActivityMatches), not a re-fetch per tab, since the backend
     already returns a small bounded set. */
  const { data: pcActivity, loading: pcActivityLoading, refresh: refreshActivity } =
    useFetch<PcActivityEntry[]>("get_pc_activity", ready && view === "activity", [], "all", 100);
  /* Memória is three views over two payloads. The overview is fetched for any
     of them (the retrieval footer and the stats are shared); the node list and
     the graph slice are fetched only by the view that draws them, because both
     are bounded server-side reads that nothing else needs. */
  const inMemorySection = view === "memory" || view === "recaps" || view === "knowledge" || view === "graph";
  const { data: memoryData, loading: memoryLoading, refresh: refreshMemory } =
    useFetch<MemoryOverview>("get_memory_overview", ready && inMemorySection, [view]);
  /* Memory M3: Memória -> Conversas antigas. */
  const { data: recapsData, loading: recapsLoading, refresh: refreshRecaps } =
    useFetch<RecapsPayload>("list_conversation_recaps", ready && view === "recaps", []);
  const { data: knowledgeData, loading: knowledgeLoading, refresh: refreshKnowledge } =
    useFetch<any>("list_knowledge_nodes", ready && view === "knowledge", []);
  const { data: graphData, loading: graphLoading, refresh: refreshGraph } =
    useFetch<KnowledgeGraphPayload>("get_knowledge_graph", ready && view === "graph",
      [graphType], graphType, "", 160);
  const { data: agents } = useFetch<any[]>("get_agents_detail", ready && view === "agents", []);
  /* Fetched when the PC page opens, and on demand. NOT polled: every line of
     it is a real Windows call, and a timer would spend the machine's time
     describing itself. */
  const { data: pcSnapshot, loading: pcLoading, refresh: refreshPc } =
    useFetch<PcSnapshot>("get_pc_snapshot", ready && view === "status", []);
  const { data: settings, loading: settingsLoading, refresh: refreshSettings } =
    useFetch<SettingsPayload>("get_settings", ready && view === "settings", []);

  // The microphone diagnostics are only useful live. Fetched once at page
  // load they showed a snapshot from before the user spoke -- which read as
  // "the wake listener hears nothing" when it simply had not looked yet.
  //
  // This used to poll get_settings() once a second, which ALSO describes both
  // providers -- and describing Groq is a blocking HTTPS request. That was one
  // call to api.groq.com every second the Settings page was open.
  // get_voice_diagnostics() reads only in-memory counters: no network, no
  // database, no PortAudio. Provider state keeps the slow readiness cadence.
  const { data: voiceDiag } = usePolled<VoiceDiagnostics>(
    "get_voice_diagnostics", POLL.voiceDiagnostics, ready && view === "settings");

  // The backend owns this preference: the desktop's localhost port may
  // change between launches, so browser localStorage is not durable here.
  useEffect(() => {
    if (!ready || threads === null || firstRunChecked.current || !memoryReady) return;
    firstRunChecked.current = true;
    if (threads.some((thread) => thread.messageCount > 0)) return;
    call<{ completed: boolean }>("get_onboarding_status").then((result) => {
      if (result?.completed === false) { firstRunAuto.current = true; setShowFirstRun(true); }
    });
  }, [ready, threads, memoryReady]);

  // The page guessed its language from the browser before the bridge was up.
  // The backend's resolved choice wins: if they differ, reload once in the
  // backend's language. Loop-safe, because the reload carries ?lang= and the
  // next load then matches.
  const languageChecked = useRef(false);
  useEffect(() => {
    if (!ready || languageChecked.current) return;
    languageChecked.current = true;
    call<{ resolved: string }>("get_language").then((result) => {
      const resolved = normaliseLocale(result?.resolved);
      if (resolved && resolved !== locale()) reloadInLocale(resolved);
    });
  }, [ready]);

  useEffect(() => { document.documentElement.lang = locale(); }, []);

  /** Opened by the app itself on a first run (not reopened from Sobre). */
  const firstRunAuto = useRef(false);
  const finishFirstRun = useCallback(async () => {
    setSavingFirstRun(true);
    const result = await call<{ ok: boolean }>("update_setting", "onboarding_completed", true);
    setSavingFirstRun(false);
    if (result?.ok) setShowFirstRun(false);
    else notify(t("Não foi possível guardar o progresso. Tenta novamente."), "error");
  }, [notify]);

  useEffect(() => {
    if (!ready) return;
    call<{ theme: ThemePref; reduceMotion: boolean; displayName: string; advancedMode: boolean }>("get_preferences")
      .then((prefs) => {
        if (!prefs) return;
        setThemePref(prefs.theme ?? "system");
        setReduceMotionState(prefs.reduceMotion === true);
        setDisplayNameState(prefs.displayName ?? "");
        setAdvancedModeState(prefs.advancedMode === true);
      });
  }, [ready]);

  const savePreference = useCallback((key: string, value: unknown) => {
    call<{ ok: boolean }>("update_setting", key, value).then((result) => {
      if (!result?.ok) notify(t("Não foi possível guardar a preferência."), "error");
    });
  }, [notify]);
  const setTheme = useCallback((next: ThemePref) => { setThemePref(next); savePreference("theme", next); }, [savePreference]);
  const setReduceMotion = useCallback((next: boolean) => { setReduceMotionState(next); savePreference("reduce_motion", next); }, [savePreference]);
  const setAdvancedMode = useCallback((next: boolean) => { setAdvancedModeState(next); savePreference("advanced_mode", next); }, [savePreference]);
  const setDisplayName = useCallback((next: string) => { setDisplayNameState(next.trim()); savePreference("display_name", next.trim()); }, [savePreference]);
  /** The name NANO greets: the one typed in Settings, else what memory knows. */
  const userName = displayName || profileName;

  // A provider that already works means there is nothing to set up: the
  // automatic first-run guide steps aside (it stays reachable from Sobre).
  useEffect(() => {
    if (showFirstRun && firstRunAuto.current && providers?.route?.usable === true) {
      firstRunAuto.current = false;
      void finishFirstRun();
    }
  }, [showFirstRun, providers, finishFirstRun]);

  useEffect(() => { document.documentElement.setAttribute("data-theme", theme); }, [theme]);
  // Stamped on the root so rules that cannot see the React tree -- the drag
  // region on the top bar, the fixed rail drawer at narrow widths -- can tell
  // whether they are inside the desktop shell.
  useEffect(() => {
    document.documentElement.setAttribute("data-desktop", isDesktop ? "true" : "false");
  }, [isDesktop]);
  useEffect(() => {
    document.documentElement.setAttribute("data-reduce-motion", reduceMotion ? "true" : "false");
  }, [reduceMotion]);

  /* ── Sidebar ────────────────────────────────────────────────────────────
   * Folding the docked sidebar lasts for the session and the sidebar opens
   * expanded on the next launch. It is deliberately NOT kept in browser
   * storage: the frontend uses none at all (tests/test_providers_and_secrets.py
   * holds that line, so nothing a secret could reach is ever readable back from
   * the page), and the desktop's localhost port can change between launches
   * anyway, which would give the page a new, empty origin. Remembering it
   * across launches is a backend setting's job. */

  // A drawer left open at one width must not pop open again at the next.
  useEffect(() => { setRailOpen(false); }, [narrow]);

  const toggleRail = useCallback(() => {
    if (narrow) setRailOpen((open) => !open);
    else setRailCollapsed((collapsed) => !collapsed);
  }, [narrow]);

  /* ── The thread list ────────────────────────────────────────────────────
   * Re-read after anything that appends to a thread, so the rail is never a
   * stale picture of what is on disk. It is a bounded, indexed query over the
   * conversations table -- title, timestamps and counts, never message bodies. */
  const reloadThreads = useCallback(async () => {
    const payload = await call<any>("list_conversations", "", 60, false);
    if (!payload) return;
    if (payload.ok === false) { setMemoryReady(false); setThreads([]); return; }
    setMemoryReady(true);
    setThreads(payload.conversations ?? []);
    if (payload.activeId) setActiveThreadId(payload.activeId);
  }, []);

  /** Render one thread's stored messages into the chat view.
   *
   * `row.meta` is what the backend recorded about the provider that produced
   * THAT message, months ago if need be. It is carried through verbatim so a
   * reopened conversation shows the same technical details it showed live —
   * rather than nothing, which is what happened while the column was written
   * and never read. */
  const showThreadMessages = useCallback((rows: ThreadMessage[] | null | undefined) => {
    // CHANGING WHAT IS ON SCREEN ENDS THE PENDING TURN, as far as this view is
    // concerned. `thinking` says "the conversation you are looking at is
    // waiting for an answer"; once a different transcript is shown, that is no
    // longer true, and leaving it set painted "O Nano está a pensar…" under a
    // conversation with nothing pending until the other turn happened to end.
    setThinking(false);
    setStatus("");
    setMessages((rows ?? []).map((row) => ({
      id: `stored:${row.id}`,
      role: row.role === "user" ? "user" : "assistant",
      content: row.content,
      timestamp: new Date(row.timestamp),
      meta: row.meta,
      attachments: row.attachments,
    })));
  }, []);

  /* ── Streaming events ───────────────────────────────────────────────── */
  useEffect(() => {
    if (!ready) return;

    expose((reqId: string, msg: string, meta: any) => {
      setConfirmQueue((queue) => (queue.some((item) => item.requestId === reqId)
        ? queue : [...queue, { requestId: reqId, message: msg, meta }]));
    }, "on_confirm_request");

    // Answered elsewhere (the paired phone) or timed out: take the card down.
    expose((reqId: string) => {
      setConfirmQueue((queue) => queue.filter((item) => item.requestId !== reqId));
    }, "on_confirm_resolved");

    // The conversation NANO holds changed on the phone. There is one active
    // conversation for both screens, so the window follows it: otherwise the
    // next message typed here would land in the thread the phone opened while
    // this screen still showed the old one. Changes made here are ignored --
    // the window already shows what it opened.
    expose((activeId: string, source?: string) => {
      if (!activeId || source === "pc") return;
      setActiveThreadId(activeId);
      call<ThreadMessage[]>("get_conversation_history").then(showThreadMessages);
      reloadThreads();
    }, "on_conversation_changed");

    // An answer read aloud is over (finished, stopped, or replaced by a voice turn).
    expose((messageId: string) => {
      setReadingId((current) => (current === messageId ? null : current));
    }, "on_read_aloud_end");

    expose(() => {
      setThinking(true);
      setListening(true);
      setStatus(t("Ouvi-te — diz o comando"));
      notify(t("Wake detetada"));
    }, "on_wake_detected");

    expose((msgId: string, userText?: string) => {
      setThinking(true);
      // NO PLACEHOLDER TEXT HERE. `status` is the SPECIFIC thing Nano is doing
      // ("A usar pc_volume_get…"), and the one indicator that renders it owns
      // its own generic wording. Seeding it with "A pensar…" meant the pending
      // bubble said something slightly different from the copy it is supposed
      // to show, for no reason other than the order the two were written in.
      setStatus("");
      setMessages((prev) => {
        const next = [...prev];
        // Exactly ONE user bubble per turn, identified by the turn's own id.
        //
        // This used to compare the text of the LAST message in the list. But
        // sendMessage appends [user, assistant], so the last entry was the
        // empty assistant bubble, the comparison never matched, and the user's
        // text was appended a second time -- after Nano's reply. Deriving the
        // id from the request id also survives the user legitimately sending
        // the same text twice, which text equality never could.
        const userId = userMessageId(msgId);
        if (userText && !next.some((m) => m.id === userId)) {
          next.push({ id: userId, role: "user", content: userText, timestamp: new Date() });
        }
        if (!next.some((m) => m.id === msgId)) {
          next.push({ id: msgId, role: "assistant", content: "", timestamp: new Date(), streaming: true });
        }
        return next;
      });
    }, "on_stream_start");

    expose((_msgId: string, text: string) => {
      // Tool activity arrives as a status line. Turn it into readable tool
      // cards rather than showing the raw backend string.
      const match = /⚙️\s*(.+?)\.\.\./.exec(text);
      if (match) {
        const names = match[1].split(",").map((name) => name.trim()).filter(Boolean);
        setStatus(names.length === 1 ? toolStatus(names[0]) : t("A usar {0} ferramentas…", { 0: names.length }));
        setMessages((prev) => {
          const next = [...prev];
          for (let i = next.length - 1; i >= 0; i -= 1) {
            if (next[i].role === "assistant") {
              const existing = next[i].tools ?? [];
              const merged: ToolEvent[] = [...existing];
              for (const name of names) {
                if (!merged.some((tool) => tool.name === name)) {
                  merged.push({ name, state: "running", summary: TOOL_SUMMARY[name]?.() ?? t("A executar {0}…", { 0: name }) });
                }
              }
              next[i] = { ...next[i], tools: merged };
              break;
            }
          }
          return next;
        });
      } else {
        setStatus(friendlyStatus(text));
      }
    }, "on_stream_status");

    expose((msgId: string, chunk: string) => {
      setMessages((prev) => {
        // A CHUNK NEVER CREATES A BUBBLE.
        //
        // The turn's bubble is created by on_stream_start, and the backend
        // always emits that before any chunk for the same id. So a chunk whose
        // bubble is not here means the turn is no longer on screen -- the user
        // opened another conversation while the answer was still arriving --
        // and the only correct thing to do with it is nothing.
        //
        // This branch used to append one instead, which wrote the answer to a
        // question asked in one conversation into the transcript of whichever
        // conversation the user had switched to.
        if (!prev.some((m) => m.id === msgId)) return prev;
        return prev.map((m) => (m.id === msgId ? { ...m, content: m.content + chunk, streaming: true } : m));
      });
    }, "on_stream_chunk");

    expose((msgId: string, final: any) => {
      setThinking(false);
      setListening(false);
      setStatus("");
      const text = final?.text ?? final?.error;
      const userId = userMessageId(msgId);
      setMessages((prev) => prev.map((m) => {
        // A PDF is read inside the turn, so only now is it known whether it
        // was cut; the question's bubble shows it like a cut text file.
        if (m.id === userId && Array.isArray(final?.attachments)) return { ...m, attachments: final.attachments };
        if (m.id !== msgId) return m;
        const tools = (m.tools ?? []).map((tool) => tool.state === "running" ? { ...tool, state: "ok" as const, summary: t("{0} concluída", { 0: tool.name }) } : tool);
        return {
          ...m,
          content: text ?? m.content,
          streaming: false,
          error: final?.ok === false,
          // Safe per-response diagnostics (provider, model, tokens, latency).
          meta: final?.meta ?? m.meta,
          tools,
          // An ambiguous PC target: the candidates become buttons.
          clarification: final?.clarification ?? undefined,
        };
      }));
      refreshCC();
      refreshCounts();
      // The turn is on disk now, so the rail can see it.
      reloadThreads();
    }, "on_stream_end");

    // A provider/model failure. Distinct from the bridge being unreachable:
    // the request WAS accepted, so this is never "Motor offline".
    expose((msgId: string, error: any) => {
      setThinking(false);
      setStatus("");
      setMessages((prev) => prev.map((m) => m.id === msgId
        ? { ...m, streaming: false, error: true,
            content: m.content || t("**Não foi possível responder.** {0}", { 0: error?.detail ?? error?.code ?? "" }) }
        : m));
      notify(t("Erro ao responder"), "error");
    }, "on_stream_error");

    // Rate limiting is a state with a known wait, not a generic error.
    expose((_msgId: string, info: any) => {
      const wait = Math.max(1, Math.round(Number(info?.wait_seconds ?? 0)));
      // The provider comes WITH the numbers, so the banner names the account
      // whose quota actually ran out instead of always saying "Groq". Only its
      // id is kept here: this handler is registered once, before the provider
      // list has loaded, so a display name looked up now is the raw id. The
      // name is resolved where it is shown -- see rateLimitProvider.
      setRateLimit({
        message: info?.message ?? t("Limite temporário atingido."),
        waitSeconds: wait,
        provider: String(info?.provider ?? ""),
      });
      setStatus("");
      setThinking(false);
      notify(info?.message ?? t("Limite temporário atingido."), "default");
    }, "on_rate_limited");

    // Which phase of a voice turn we are in, so the UI can narrate it.
    // Every trigger -- wake phrase, mic button, and the global hotkey -- drives
    // the same sequence, so this is the single place the composer learns that a
    // turn started and, crucially, that it ended.
    expose((phase: string, detail: string) => {
      setVoicePhase({ phase, detail });
      setListening(phase === "COMMAND_LISTENING");
      // A turn that ends without producing an answer (silence, no usable
      // command) still has to release the composer, or the UI sits on
      // "A ouvir…" forever after the microphone has already been let go.
      if (phase === "WAKE_LISTENING" || phase === "IDLE") {
        setThinking(false);
        setStatus("");
      } else if (phase === "PROCESSING" || phase === "SPEAKING") {
        setThinking(true);
      }
    }, "on_voice_phase");

    // A completed voice turn. Carries its own turn id so a retry or a repeated
    // phrase cannot insert the exchange twice.
    expose((turnId: string, userText: string, assistantText: string) => {
      setMessages((prev) => {
        if (prev.some((m) => m.id === turnId)) return prev;
        return [
          ...prev,
          { id: userMessageId(turnId), role: "user", content: userText, timestamp: new Date() },
          { id: turnId, role: "assistant", content: assistantText, timestamp: new Date() },
        ];
      });
      // A spoken turn is persisted into the same thread as a typed one, so the
      // rail has to be re-read here too.
      reloadThreads();
    }, "on_voice_exchange");

    // A reminder fell due. In the desktop shell Windows already shows a native
    // notification, so the in-app toast is only for the browser mode.
    expose((reminder: { text?: string; due_label?: string }) => {
      const text = String(reminder?.text ?? "").trim();
      if (!text || isDesktopRef.current) return;
      const when = String(reminder?.due_label ?? "").trim();
      notify(when ? `⏰ ${when} · ${text}` : `⏰ ${text}`, "default");
    }, "on_reminder_due");

    // NANO speaks first (core/proactive.py): the morning briefing or a heads-up
    // before an event. It lands in the conversation as NANO's own message, so
    // it reads like JARVIS talking, and is not stored: it is about today.
    expose((notice: { kind?: string; title?: string; text?: string; id?: string;
                      offer?: string; mutable?: boolean; silent?: boolean }) => {
      const text = String(notice?.text ?? "").trim();
      if (!text) return;
      const title = String(notice?.title ?? "").trim();
      const id = String(notice?.id ?? "");
      const offer = String(notice?.offer ?? "").trim();
      setMessages((prev) => [
        ...prev,
        { id: `notice-${crypto.randomUUID()}`, role: "assistant",
          content: title ? `**${title}**\n\n${text}` : text, timestamp: new Date(),
          // Buttons only for a notice the gate gave an id (a briefing shown
          // on demand has none and needs none).
          notice: id && (offer || notice?.mutable)
            ? { id, kind: String(notice?.kind ?? ""), offer: offer || undefined, mutable: !!notice?.mutable }
            : undefined },
      ]);
      if (!isDesktopRef.current && !notice?.silent) notify(title || text.split("\n")[0], "default");
    }, "on_proactive_notice");

    // A work mode finished. Each step went through the permission pipeline;
    // the result says what really happened to each one.
    expose((result: WorkModeResult) => {
      setModeRunning(null);
      setModeResult(result ?? null);
      notify(result?.message ?? t("O modo terminou."), result?.ok ? "success" : "error");
    }, "on_work_mode_done");

    // The thread list, and the messages of whichever thread the backend
    // resumed. The backend picks that thread (the most recently active one) so
    // the shell and the Brain cannot disagree about which conversation is open.
    reloadThreads();
    call<ThreadMessage[]>("get_conversation_history").then(showThreadMessages);

    // The user's own name, if the backend actually knows one. There is no
    // fallback initials: an invented "PA" in the corner would be a claim about
    // the user that nothing measured.
    call<any>("get_memory_overview").then((overview) => {
      if (!overview) return;
      setMemoryReady(overview.ready !== false);
      const profile = overview.profile ?? {};
      const raw = profile.name ?? profile.nome ?? profile.user_name;
      const value = raw && typeof raw === "object" ? raw.value : raw;
      if (typeof value === "string" && value.trim()) setProfileName(value.trim());
    });

    call<Record<string, string[]>>("get_loaded_plugins").then((v) => v && setPlugins(v));
    call<any[]>("list_permission_policies").then((v) => v && setPolicies(v));
  }, [ready, notify, refreshCC, refreshCounts, reloadThreads, showThreadMessages]);

  /* ── Rate-limit countdown ───────────────────────────────────────────── */
  // The wait comes from Groq's own retry-after header, so the banner clears
  // itself exactly when the limit does instead of lingering as a stale error.
  useEffect(() => {
    if (!rateLimit) return;
    if (rateLimit.waitSeconds <= 0) { setRateLimit(null); return; }
    const timer = setTimeout(
      () => setRateLimit((prev) => (prev ? { ...prev, waitSeconds: prev.waitSeconds - 1 } : null)),
      1000,
    );
    return () => clearTimeout(timer);
  }, [rateLimit]);

  /* ── Actions ────────────────────────────────────────────────────────── */
  const sendMessage = useCallback((override?: string) => {
    const text = (override ?? input).trim();
    if (!text || thinking || !ready) return;
    const msgId = crypto.randomUUID();
    // Suggestions and examples send their own text and never carry the files
    // waiting in the composer; only the composer's own send does.
    const files = override === undefined ? pendingFiles : [];
    const shown = files.map(({ name, size, kind }) => ({ name, size, kind }));
    // The user bubble's id is derived from the turn id, so on_stream_start can
    // recognise that this turn's message is already on screen and never append
    // a second copy.
    setMessages((prev) => [
      ...prev,
      { id: userMessageId(msgId), role: "user", content: text, timestamp: new Date(),
        attachments: shown.length ? shown : undefined },
      { id: msgId, role: "assistant", content: "", timestamp: new Date(), streaming: true },
    ]);
    if (override === undefined) { setInput(""); setPendingFiles([]); }
    setThinking(true);
    setStatus("");
    setView("chat");

    // send_message returns an ACK, never the answer: the reply arrives on the
    // stream events. Treating a slow turn's missing return value as "offline"
    // is what produced a false "Motor offline" while the backend was working.
    const payload = files.map(toPayload);
    call<any>("send_message", text, msgId, payload).then((ack) => {
      if (ack?.accepted) {
        // The backend says which files it cut to fit; the bubble shows it.
        if (ack.attachments) {
          setMessages((prev) => prev.map((m) => m.id === userMessageId(msgId)
            ? { ...m, attachments: ack.attachments } : m));
        }
        return;                                // healthy: the stream takes over
      }

      // Only a genuine transport failure lands here.
      setThinking(false);
      setStatus("");
      const transportDown = ack === null || ack === undefined;
      const answer = transportDown
        ? t("**Sem resposta do motor do NANO.** A ligação ao backend caiu ou expirou. Confirma que a janela do NANO continua aberta e recarrega a página.")
        : t("**O pedido não foi aceite.** {0}.", { 0: ack?.error ?? t("motivo desconhecido") });
      notify(transportDown ? t("Motor offline") : t("Pedido recusado"), "error");
      setMessages((prev) => prev.map((m) => m.id === msgId
        ? { ...m, content: answer, streaming: false, error: true,
            tools: (m.tools ?? []).map((tool) => tool.state === "running" ? { ...tool, state: "ok" as const } : tool) }
        : m.id === userMessageId(msgId) ? { ...m, unsent: true } : m));
    });
  }, [input, pendingFiles, thinking, ready, notify]);

  /** A button under something NANO said by itself (core/notice_gate.py).
   *  Accepting asks the backend for the sentence to send and sends it as the
   *  person's own message, so it meets every approval like anything typed. */
  const answerNotice = useCallback(async (notice: NoticeInfo, action: NoticeAction) => {
    if (action === "mute") {
      const res = await call<{ ok?: boolean }>("mute_notice_kind", notice.kind);
      if (res?.ok) notify(t("Não volto a avisar disto. Podes voltar a ligar em Definições → Geral."), "success");
      return;
    }
    const res = await call<{ ok?: boolean; send?: string; detail?: string }>("proactive_action", notice.id, action);
    if (action !== "accept") return;
    if (res?.ok && res.send) sendMessage(res.send);
    else notify(res?.detail ?? t("Esta sugestão já não está ativa."), "default");
  }, [notify, sendMessage]);

  /**
   * Ask the last question again, as it was or with new text.
   *
   * The backend takes the last turn out of the thread first, so the model never
   * sees the answer it is replacing; on screen the old pair is swapped for the
   * new one in place, exactly as sendMessage would draw a fresh turn.
   */
  const rerunLastTurn = useCallback((newText?: string) => {
    if (thinking || !ready) return;
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    const text = (newText ?? lastUser?.content ?? "").trim();
    if (!text) return;
    if (lastUser?.unsent) {
      // Never stored, so there is no turn to rewind: asking the backend to
      // rewind would take back the exchange BEFORE it. Send it as new instead.
      setMessages((prev) => {
        const cut = prev.findIndex((m) => m.id === lastUser.id);
        return cut >= 0 ? prev.slice(0, cut) : prev;
      });
      sendMessage(text);
      return;
    }
    const msgId = crypto.randomUUID();
    setMessages((prev) => {
      let cut = prev.length;
      for (let i = prev.length - 1; i >= 0; i -= 1) {
        if (prev[i].role === "user") { cut = i; break; }
      }
      return [
        ...prev.slice(0, cut),
        // The files go again with the question; the backend keeps them.
        { id: userMessageId(msgId), role: "user", content: text, timestamp: new Date(),
          attachments: lastUser?.attachments },
        { id: msgId, role: "assistant", content: "", timestamp: new Date(), streaming: true },
      ];
    });
    setThinking(true);
    setStatus("");
    call<any>("retry_last_turn", newText ?? "", msgId).then((ack) => {
      if (ack?.accepted) return;
      setThinking(false);
      setStatus("");
      notify(t("Não foi possível tentar de novo"), "error");
      setMessages((prev) => prev.map((m) => m.id === msgId
        ? { ...m, streaming: false, error: true,
            content: ack == null
              ? t("**Sem resposta do motor do NANO.** Recarrega a página e tenta outra vez.")
              : t("**O pedido não foi aceite.** {0}.", { 0: ack?.error ?? t("motivo desconhecido") }) }
        // Whatever the backend took back, this question is not on disk now.
        : m.id === userMessageId(msgId) ? { ...m, unsent: true } : m));
    });
  }, [thinking, ready, messages, notify, sendMessage]);

  /* "Ler em voz alta" under an answer. The backend reads it with NANO's voice
     and says when it is over (on_read_aloud_end), so the button never claims
     to be reading after the sound stopped. */
  const [readingId, setReadingId] = useState<string | null>(null);
  const readAnswer = useCallback((messageId: string, text: string) => {
    setReadingId(messageId);
    call<{ ok: boolean; detail?: string }>("read_aloud", messageId, text).then((result) => {
      if (result?.ok) return;
      setReadingId((current) => (current === messageId ? null : current));
      notify(result?.detail || t("Não foi possível ler a resposta."), "error");
    });
  }, [notify]);
  const stopReading = useCallback(() => {
    setReadingId(null);
    call("stop_voice");
  }, []);
  const readAloud = useMemo(() => (readiness?.voice.canSpeak
    ? { readingId, read: readAnswer, stop: stopReading } : null),
  [readiness?.voice.canSpeak, readingId, readAnswer, stopReading]);

  const stopWork = useCallback(() => {
    setReadingId(null);
    call("stop_voice");
    const current = commandCenter?.current_task;
    if (current && !["COMPLETED", "FAILED", "CANCELLED"].includes(current.status)) {
      call("cancel_agent_task", current.id).then(() => { notify(t("Tarefa cancelada")); refreshCC(); refreshCounts(); });
    }
    setThinking(false);
    setListening(false);
    setStatus("");
  }, [commandCenter, notify, refreshCC, refreshCounts]);

  const startVoice = useCallback(() => {
    if (!ready || thinking) return;
    if (readiness && readiness.voice.state !== "READY") {
      notify(t("Voz: {0}", { 0: stateLabel(readiness.voice.state) }), "error");
      return;
    }
    // One shared voice turn for every trigger. This returns an ACK, not the
    // answer: the turn narrates itself through on_voice_phase and delivers the
    // exchange through on_voice_exchange, exactly like the wake phrase does.
    // The old start_voice_listen blocked the whole bridge for the listen.
    call<any>("start_voice_turn_from_ui").then((result) => {
      if (result?.accepted) return;
      setListening(false);
      setThinking(false);
      setStatus("");
      if (result?.busy) notify(t("O NANO já está a ouvir"));
      else notify(t("Voz: {0}", { 0: t("não foi possível iniciar") }), "error");
    });
    setListening(true);
    setThinking(true);
    setStatus(t("A ouvir…"));
  }, [ready, thinking, readiness, notify]);

  const cancelVoice = useCallback(() => {
    call("stop_voice");
    setListening(false);
    setThinking(false);
    setStatus("");
    notify(t("Escuta cancelada"));
  }, [notify]);

  /**
   * Start a new conversation.
   *
   * This creates a REAL thread on the backend and makes the Brain hold it. The
   * previous conversation is not lost or merged: it keeps its row in the rail,
   * it can be reopened, and answering in it later continues it properly. Under
   * the derived-session model this button could only forget the in-memory
   * window and hope the timestamps would separate the two afterwards.
   */
  const newConversation = useCallback(() => {
    call<any>("create_conversation", "").then((result) => {
      setMessages([]);
      setThinking(false);   // same rule as showThreadMessages, different route
      setStatus("");
      setInput("");
      setView("chat");
      if (result?.conversation) setActiveThreadId(result.conversation.id);
      reloadThreads();
      // No "Nova conversa" toast: the empty home with its greeting already
      // says it, and the toast covered the voice view when it opened next.
    });
  }, [reloadThreads]);

  /** Open a thread and rebuild the model context from it.
   *
   * This is the operation that used to be impossible. Older conversations
   * opened read-only because the Brain held one rolling window over a flat log;
   * open_conversation rebuilds that window from the chosen thread, so the next
   * message is answered against the right history.
   */
  const openThread = useCallback((thread: Thread) => {
    // Picking the conversation already open must still close the drawer: on a
    // phone the drawer covers the chat, and tapping the open thread is how a
    // person says "take me back to it".
    if (thread.id === activeThreadId) { setView("chat"); if (narrow) setRailOpen(false); return; }
    call<any>("open_conversation", thread.id).then((result) => {
      if (!result?.ok) { notify(t("Não foi possível abrir a conversa"), "error"); return; }
      setActiveThreadId(result.conversation.id);
      showThreadMessages(result.messages);
      setInput("");
      setView("chat");
      if (narrow) setRailOpen(false);
    });
  }, [activeThreadId, notify, showThreadMessages, narrow]);

  /** Open a conversation by id: from a "Lembrei-me de" chip under an answer or
   *  from Memória -> Conversas antigas (Memory M3). */
  const openThreadById = useCallback((id: string) => {
    if (id === activeThreadId) { setView("chat"); return; }
    call<any>("open_conversation", id).then((result) => {
      if (!result?.ok) { notify(t("Esta conversa já não existe"), "error"); return; }
      setActiveThreadId(result.conversation.id);
      showThreadMessages(result.messages);
      setInput("");
      setView("chat");
      if (narrow) setRailOpen(false);
    });
  }, [activeThreadId, notify, showThreadMessages, narrow]);

  const renameThread = useCallback((thread: Thread, title: string) => {
    call<any>("rename_conversation", thread.id, title).then((result) => {
      if (result?.ok) { reloadThreads(); notify(t("Conversa renomeada")); }
      else notify(t("Não foi possível mudar o nome"), "error");
    });
  }, [notify, reloadThreads]);

  const pinThread = useCallback((thread: Thread, pinned: boolean) => {
    call<any>("pin_conversation", thread.id, pinned).then((result) => {
      if (result?.ok) { reloadThreads(); notify(pinned ? t("Conversa fixada") : t("Conversa desafixada")); }
      else notify(t("Não foi possível fixar"), "error");
    });
  }, [notify, reloadThreads]);

  const fileThread = useCallback((thread: Thread, project: string) => {
    call<any>("set_conversation_project", thread.id, project).then((result) => {
      if (!result?.ok) { notify(t("Não foi possível mover a conversa"), "error"); return; }
      reloadThreads();
      notify(project ? t("Movida para {0}", { 0: project }) : t("Tirada do projeto"));
    });
  }, [notify, reloadThreads]);

  const deleteThread = useCallback((thread: Thread) => {
    call<any>("delete_conversation", thread.id).then((result) => {
      if (!result?.ok) { notify(t("Não foi possível apagar"), "error"); return; }
      notify(t("Conversa apagada ({0} mensagens)", { 0: result.messages }));
      if (thread.id === activeThreadId) {
        // The backend has already moved the Brain to another thread; ask it
        // which one rather than guessing, so the two never disagree about
        // which conversation is open.
        call<ThreadMessage[]>("get_conversation_history").then(showThreadMessages);
      }
      reloadThreads();
    });
  }, [activeThreadId, notify, reloadThreads, showThreadMessages]);

  /**
   * Delete several conversations in ONE backend call.
   *
   * Not a loop over `delete_conversation`: forty round trips from the renderer
   * can be interrupted half way, and nothing would then know how far the batch
   * got. `delete_conversations` performs the whole set and reports per-id
   * outcomes, so a partial failure is REPORTED as one instead of being shown
   * as success — which is the only reason a bulk endpoint was worth adding.
   */
  const deleteThreads = useCallback((ids: string[]) => {
    if (!ids.length) return;
    const hadActive = activeThreadId != null && ids.includes(activeThreadId);
    call<any>("delete_conversations", ids).then((result) => {
      const removed = Number(result?.removed ?? 0);
      const failed = (result?.failed ?? []).length;
      if (!removed) {
        notify(t("Não foi possível apagar as conversas"), "error");
      } else if (failed) {
        // Say exactly what happened. "Apagadas" for a batch that partly failed
        // would leave rows on screen the user believes are gone.
        notify(t("{0} apagada(s), {1} falharam", { 0: removed, 1: failed }), "error");
      } else {
        notify(t("{0} conversa(s) apagada(s)", { 0: removed }));
      }
      if (hadActive) {
        call<ThreadMessage[]>("get_conversation_history").then(showThreadMessages);
      }
      reloadThreads();
    });
  }, [activeThreadId, notify, reloadThreads, showThreadMessages]);

  const copyConversation = useCallback(async () => {
    const text = messages
      .filter((m) => m.content.trim())
      .map((m) => `${m.role === "user" ? t("Tu") : "NANO"}: ${m.content}`)
      .join("\n\n");
    if (!text) { notify(t("Não há nada para copiar")); return; }
    try {
      await navigator.clipboard.writeText(text);
      notify(t("Conversa copiada"), "success");
    } catch {
      notify(t("A área de transferência está bloqueada"), "error");
    }
  }, [messages, notify]);

  const openTask = useCallback((taskId: string) => {
    call<any>("get_task_detail", taskId).then((result) => {
      if (result?.ok) {
        setTaskDetail(result.task);
        setTaskDetailEvents(result.events ?? []);
        setTaskDetailOpen(true);
      } else notify(t("Tarefa não encontrada"), "error");
    });
  }, [notify]);

  const cancelTask = useCallback((taskId: string) => {
    call<any>("cancel_agent_task", taskId).then((result) => {
      notify(result?.ok ? t("Tarefa cancelada") : t("Não foi possível cancelar"), result?.ok ? "default" : "error");
      refreshCC(); refreshCounts(); refreshTasks();
      setTaskDetailOpen(false);
    });
  }, [notify, refreshCC, refreshCounts, refreshTasks]);

  const archiveTasks = useCallback(() => {
    call<any>("archive_finished_tasks").then((result) => {
      notify(result?.ok ? t("{0} tarefa(s) arquivadas", { 0: result.removed }) : t("Falha ao arquivar"), result?.ok ? "success" : "error");
      refreshTasks(); refreshCounts(); refreshCC();
    });
  }, [notify, refreshTasks, refreshCounts, refreshCC]);

  const resolvePermission = useCallback((requestId: string, decision: "deny" | "allow_once" | "allow_for_task") => {
    setResolving(true);
    call<any>("resolve_permission", requestId, decision).then((result) => {
      setResolving(false);
      if (result?.ok) {
        notify(decision === "deny" ? t("Pedido recusado") : t("Autorização concedida"), "success");
        refreshCC(); refreshReadiness();
      } else notify(t("Recusado pela policy: {0}", { 0: result?.error ?? "erro" }), "error");
    });
  }, [notify, refreshCC, refreshReadiness]);

  const toggleEmergencyStop = useCallback((enabled: boolean) => {
    call<any>("set_emergency_stop", enabled).then(() => {
      notify(enabled ? t("Execução bloqueada") : t("Execução retomada"), enabled ? "error" : "success");
      refreshReadiness(); refreshCC(); refreshSettings();
    });
  }, [notify, refreshReadiness, refreshCC, refreshSettings]);

  const openPluginCode = useCallback((name: string) => {
    call<any>("get_plugin_code", name).then((result) => {
      if (result?.ok) setPluginCode(result); else notify(t("Não foi possível ler o componente"), "error");
    });
  }, [notify]);

  /* ── Provider actions ───────────────────────────────────────────────── */
  const setMode = useCallback((mode: "AUTO" | "CLOUD" | "LOCAL") => {
    setBusy(true);
    call<any>("set_provider_mode", mode).then((result) => {
      setBusy(false);
      if (result?.ok) { notify(t("Modo: {0}", { 0: mode }), "success"); refreshProviders(); refreshSettings(); refreshReadiness(); }
      else notify(t("Não foi possível mudar de modo"), "error");
    });
  }, [notify, refreshProviders, refreshSettings, refreshReadiness]);

  const setLocalModel = useCallback((model: string) => {
    setBusy(true);
    call<any>("set_local_model", model).then((result) => {
      setBusy(false);
      if (result?.ok) { notify(t("Modelo local: {0}", { 0: result.model }), "success"); refreshProviders(); refreshSettings(); }
      else notify(result?.detail ?? t("Não foi possível mudar o modelo local"), "error");
    });
  }, [notify, refreshProviders, refreshSettings]);

  const forgetAllMemory = useCallback(() => {
    call<any>("forget_all_memory_facts").then((result) => {
      notify(
        result?.ok ? t("{0} facto(s) esquecido(s)", { 0: result.removed }) : t("Não foi possível esquecer tudo"),
        result?.ok ? "success" : "error",
      );
      refreshMemory();
      refreshSettings();
    });
  }, [notify, refreshMemory, refreshSettings]);

  /**
   * Erase everything NANO holds about the user. The typed phrase is passed
   * through and checked by the backend too: the dialog is not the only gate.
   * Resolves to whether it worked, so the dialog can stay open on a failure.
   */
  const eraseAllData = useCallback(async (confirmation: string): Promise<boolean> => {
    const result = await call<any>("erase_all_data", confirmation);
    if (result?.ok || result?.activeId) {
      setMessages([]);
      setThinking(false);
      setStatus("");
      setInput("");
      setActiveThreadId(result.activeId ?? null);
    }
    reloadThreads();
    refreshMemory();
    refreshSettings();
    refreshCC();
    if (result?.ok) {
      notify(t("Todos os dados foram apagados"), "success");
      return true;
    }
    notify(
      result?.error === "tasks_not_cleared"
        ? t("Dados apagados, mas o histórico de tarefas não foi limpo")
        : result?.error === "purge_failed"
          ? t("Não foi possível apagar os dados. Nada foi alterado.")
          : t("Não foi possível apagar os dados."),
      "error",
    );
    return false;
  }, [notify, reloadThreads, refreshMemory, refreshSettings, refreshCC]);

  /** Open Settings on a specific category. Used by the AI pill. */
  const openSettingsSection = useCallback((section: SettingsSection) => {
    setSettingsSection(section);
    setView("settings");
  }, []);

  /* ── Cloud providers ─────────────────────────────────────────────────
     ONE set of handlers, taking the provider as an argument, for Google, Groq
     and Mistral alike. There used to be one copy per vendor and they had
     already drifted: the Groq model select wrote the setting directly while
     Google's went through the endpoint that validates the id against the
     account. Adding a third copy would have tripled a known defect.

     The key is sent ONCE, validated by the backend and stored encrypted.
     Nothing here ever reads a key back — the UI only ever receives a mask and
     a boolean. */
  const providerLabel = useCallback(
    (provider: CloudProviderKey) => providers?.[provider]?.name ?? provider,
    [providers]);

  const saveCloudKey = useCallback(async (provider: CloudProviderKey, key: string) => {
    setBusy(true);
    const result = await call<any>("set_cloud_api_key", provider, key);
    setBusy(false);
    if (result?.ok) { notify(t("Chave validada e guardada"), "success"); refreshProviders(); refreshSettings(); refreshReadiness(); }
    else notify(result?.detail ?? t("Chave recusada"), "error");
  }, [notify, refreshProviders, refreshSettings, refreshReadiness]);

  const removeCloudKey = useCallback((provider: CloudProviderKey) => {
    call<any>("remove_cloud_api_key", provider).then(() => {
      notify(t("Chave removida")); refreshProviders(); refreshSettings(); refreshReadiness();
    });
  }, [notify, refreshProviders, refreshSettings, refreshReadiness]);

  const testCloud = useCallback((provider: CloudProviderKey) => {
    setBusy(true);
    call<any>("test_cloud_connection", provider).then((result) => {
      setBusy(false);
      notify(result?.detail ?? t("Sem resposta"), result?.ok ? "success" : "error");
    });
  }, [notify]);

  const setCloudModelTier = useCallback(
    (provider: CloudProviderKey, model: string, tier: "fast" | "complex" = "fast") => {
      setBusy(true);
      call<any>("set_cloud_model", provider, model, tier).then((result) => {
        setBusy(false);
        if (result?.ok) { notify(t("Modelo: {0}", { 0: model }), "success"); refreshProviders(); refreshSettings(); }
        else notify(result?.detail ?? t("Modelo indisponível"), "error");
      });
    }, [notify, refreshProviders, refreshSettings]);

  const setPreferredCloud = useCallback((provider: CloudProviderKey) => {
    setBusy(true);
    call<any>("set_preferred_cloud_provider", provider).then((result) => {
      setBusy(false);
      if (result?.ok) { notify(t("Provedor: {0}", { 0: providerLabel(provider) }), "success"); refreshProviders(); refreshSettings(); refreshReadiness(); }
      else notify(result?.detail ?? t("Não foi possível mudar de provedor"), "error");
    });
  }, [notify, providerLabel, refreshProviders, refreshSettings, refreshReadiness]);

  /** The pill's model switcher. It writes through the SAME endpoint Settings
   *  uses, so there is exactly one authority for the stored choice. */
  const setCloudModel = useCallback((provider: CloudProviderKey, model: string) => {
    setCloudModelTier(provider, model, "fast");
  }, [setCloudModelTier]);

  const updateSetting = useCallback((key: string, value: any) => {
    call<any>("update_setting", key, value).then((result) => {
      if (result?.ok) { refreshSettings(); refreshReadiness(); }
      else notify(t("Não foi possível guardar: {0}", { 0: result?.error ?? "erro" }), "error");
    });
  }, [notify, refreshSettings, refreshReadiness]);

  const testSpeaker = useCallback(() => {
    call<any>("test_speaker").then((r) => notify(r?.detail ?? t("Sem resposta"), r?.ok ? "success" : "error"));
  }, [notify]);

  const testMicrophone = useCallback(() => {
    notify(t("A gravar 3 segundos…"));
    call<any>("test_microphone", 3).then((r) => notify(r?.detail ?? t("Sem resposta"), r?.ok && r?.speechDetected ? "success" : "error"));
  }, [notify]);

  /* ── Keyboard ───────────────────────────────────────────────────────── */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const mod = event.ctrlKey || event.metaKey;
      const target = event.target as HTMLElement | null;
      const typing = !!target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
      // "?" opens the shortcuts list, unless the person is typing a "?".
      if (!mod && event.key === "?" && !typing) { event.preventDefault(); setShortcutsOpen(true); return; }
      if (!mod) return;
      const key = event.key.toLowerCase();
      // Ctrl+Shift+O, the convention in other chat apps; Ctrl+N stays too.
      if (event.shiftKey && key === "o") { event.preventDefault(); newConversation(); return; }
      if (event.shiftKey && key === "m") { event.preventDefault(); setVoiceModeOpen(true); return; }
      if (key === "/") { event.preventDefault(); setShortcutsOpen(true); return; }
      if (key === "k") { event.preventDefault(); setPaletteOpen((v) => !v); }
      else if (key === "b") { event.preventDefault(); toggleRail(); }
      else if (key === "n") { event.preventDefault(); newConversation(); }
      else if (key === "m") { event.preventDefault(); startVoice(); }
      else if (key === ",") { event.preventDefault(); setView("settings"); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [newConversation, startVoice, toggleRail]);

  // The panel shows one message of the open thread; another thread closes it.
  useEffect(() => { setPanelDoc(null); }, [activeThreadId]);

  /* ── Derived ────────────────────────────────────────────────────────── */
  /** The last exchange, for the voice mode: plain text, the answer clipped. */
  const voiceLast = useMemo(() => {
    const question = [...messages].reverse().find((m) => m.role === "user")?.content;
    const last = messages[messages.length - 1];
    const raw = last?.role === "assistant" && !last.streaming ? cleanAssistantText(last.content) : "";
    const answer = raw.replace(/```[\s\S]*?```/g, "").replace(/[*_`#>|]/g, "").replace(/[-:]{3,}/g, "").replace(/\s+/g, " ").trim();
    return { question, answer: answer.length > 600 ? `${answer.slice(0, 600)}…` : answer };
  }, [messages]);
  const pendingCount = commandCenter?.permissions?.length ?? 0;
  const agentState = readiness?.agent.state ?? (gaveUp ? "BACKEND_OFFLINE" : "UNKNOWN");
  const section = sectionOf(view);
  const sectionDef = sectionEntry(section);
  /** Developer pages (agents, components) only in Modo avançado. */
  const visibleTabs = sectionDef.views.filter((entry) => advancedMode || !ADVANCED_VIEWS.has(entry.id));

  /** The thread currently on screen, from the real list. */
  const activeThread = useMemo(
    () => (threads ?? []).find((thread) => thread.id === activeThreadId) ?? null,
    [threads, activeThreadId],
  );

  /* ── Memória actions ────────────────────────────────────────────────── */
  const openNode = useCallback((nodeId: string) => {
    call<any>("get_knowledge_node", nodeId).then((result) => {
      if (result?.ok) { setNodeDetail(result); setNodeOpen(true); }
      else notify(t("Nó não encontrado"), "error");
    });
  }, [notify]);

  const createMemory = useCallback((text: string, kind: string, importance: number) => {
    call<any>("create_memory", text, kind, importance, []).then((result) => {
      if (result?.ok) { notify(t("Memória guardada"), "success"); refreshMemory(); }
      // A refusal is reported with its REASON. "Não guardei" alone would leave
      // the user guessing; "parece conter api_key" tells them what to change.
      else notify(result?.detail ?? t("Não foi possível guardar essa memória"), "error");
    });
  }, [notify, refreshMemory]);

  const updateMemory = useCallback((id: string, patch: Record<string, any>) => {
    call<any>("update_memory", id, patch.text ?? null, patch.kind ?? null,
      patch.importance ?? null, patch.pinned ?? null, patch.status ?? null,
      patch.tags ?? null).then((result) => {
      if (result?.ok) refreshMemory();
      else notify(result?.detail ?? t("Não foi possível atualizar"), "error");
    });
  }, [notify, refreshMemory]);

  const deleteMemory = useCallback((id: string) => {
    call<any>("delete_memory", id).then((result) => {
      notify(result?.ok ? t("Memória apagada") : t("Não foi possível apagar"),
             result?.ok ? "success" : "error");
      refreshMemory();
    });
  }, [notify, refreshMemory]);

  const clearMemories = useCallback(() => {
    call<any>("clear_memories").then((result) => {
      notify(result?.ok ? t("{0} memória(s) apagada(s)", { 0: result.removed }) : t("Falhou"),
             result?.ok ? "success" : "error");
      refreshMemory();
    });
  }, [notify, refreshMemory]);

  /* Memory M3: the recap of an older conversation -- edit, forget, restore. */
  const updateRecap = useCallback((id: string, summary: string, decisions: string[]) => {
    call<any>("update_conversation_recap", id, summary, decisions).then((result) => {
      if (result?.ok) { notify(t("Guardado. O NANO passa a lembrar a tua versão."), "success"); refreshRecaps(); }
      else if (result?.error === "secret") notify(t("Não guardei: parece conter uma palavra-passe ou chave."), "error");
      else notify(t("Não foi possível guardar"), "error");
    });
  }, [notify, refreshRecaps]);

  const forgetRecap = useCallback((id: string) => {
    call<any>("forget_conversation_recap", id).then((result) => {
      notify(result?.ok ? t("O NANO já não lembra esta conversa") : t("Falhou"),
             result?.ok ? "success" : "error");
      refreshRecaps();
    });
  }, [notify, refreshRecaps]);

  const restoreRecap = useCallback((id: string) => {
    call<any>("restore_conversation_recap", id).then((result) => {
      notify(result?.ok ? t("O NANO volta a lembrar esta conversa") : t("Falhou"),
             result?.ok ? "success" : "error");
      refreshRecaps();
    });
  }, [notify, refreshRecaps]);

  const forgetAllRecaps = useCallback(() => {
    call<any>("forget_all_conversation_recaps").then((result) => {
      notify(result?.ok ? t("{0} conversa(s) esquecida(s)", { 0: result.forgotten }) : t("Falhou"),
             result?.ok ? "success" : "error");
      refreshRecaps();
    });
  }, [notify, refreshRecaps]);

  const createNode = useCallback((title: string, type: string, summary: string) => {
    call<any>("create_knowledge_node", title, type, summary, "", []).then((result) => {
      if (result?.ok) { notify(t("Nó criado"), "success"); refreshKnowledge(); refreshGraph(); }
      else notify(t("Não foi possível criar o nó"), "error");
    });
  }, [notify, refreshKnowledge, refreshGraph]);

  const updateNode = useCallback((id: string, patch: Record<string, any>) => {
    call<any>("update_knowledge_node", id, patch.title ?? null, patch.node_type ?? null,
      patch.summary ?? null, patch.body ?? null, patch.tags ?? null,
      patch.pinned ?? null).then((result) => {
      if (result?.ok) { refreshKnowledge(); openNode(id); }
      else notify(result?.detail ?? t("Não foi possível atualizar o nó"), "error");
    });
  }, [notify, refreshKnowledge, openNode]);

  const deleteNode = useCallback((id: string) => {
    call<any>("delete_knowledge_node", id).then((result) => {
      if (result?.ok) {
        notify(t("Nó apagado ({0} ligações)", { 0: result.edges }), "success");
        setNodeOpen(false);
        setNodeDetail(null);
        refreshKnowledge();
        refreshGraph();
      } else notify(t("Não foi possível apagar o nó"), "error");
    });
  }, [notify, refreshKnowledge, refreshGraph]);

  const navCounts = useMemo(() => ({
    tasks: counts?.badge ?? 0,
    permissions: pendingCount,
  }), [counts, pendingCount]);

  /** The rate-limited provider as a person names it, from the live list. */
  const rateLimitProvider = rateLimit
    ? (providers?.[rateLimit.provider as CloudProviderKey]?.name ?? (rateLimit.provider || t("Cloud")))
    : "";

  const healthLabel = useMemo(() => {
    if (gaveUp) return t("Motor offline");
    if (rateLimit) return t("{0}: espera ~{1}s", { 0: rateLimitProvider, 1: rateLimit.waitSeconds });
    if (readiness?.emergencyStop) return t("Execução bloqueada");
    if (pendingCount > 0) return t("{0} por autorizar", { 0: pendingCount });
    if (providers?.route?.usable === false) return t("Sem provedor de IA");
    // The wake detector reports MIC_SILENT when chunks arrive with no energy.
    // Saying "operacional" then would be untrue.
    if (readiness?.wakePhrase?.state === "MIC_SILENT") return t("Microfone sem áudio");
    return t("Todos os serviços operacionais");
  }, [gaveUp, rateLimit, rateLimitProvider, readiness, pendingCount, providers]);

  /** What is actually answering, in the few words the top-bar pill can hold. */
  const routeLabel = useMemo(() => {
    if (gaveUp) return t("Motor offline");
    const route = providers?.route;
    if (!route) return t("A ligar…");
    if (!route.usable) return t("Sem provedor");
    // The provider's own name, from the payload. This read "Groq", "Local" or
    // an em dash, so every Gemini turn was labelled "—" in the top bar -- and
    // a third provider would have been too.
    const name = route.provider === "ollama"
      ? t("Local")
      : (providers?.[route.provider as CloudProviderKey]?.name ?? "—");
    return route.fallback ? t("{0} · fallback", { 0: name }) : `${name} · ${route.mode}`;
  }, [providers, gaveUp]);

  /** The Atividade list, filtered to the open category. See pcActivityMatches.
   *  Stays `null` while unloaded, distinct from an empty array once the
   *  backend has genuinely answered "nothing" -- ActivityPage tells the two
   *  apart to show a loading state rather than a premature empty one. */
  const pcActivityFiltered = useMemo<PcActivityEntry[] | null>(
    () => (pcActivity === null ? null : pcActivity.filter((entry) => pcActivityMatches(entry, activityCategory))),
    [pcActivity, activityCategory],
  );

  /** What Nano is doing right now, in one short line for the composer. */
  const activityLabel = useMemo(() => {
    if (rateLimit) return `${rateLimit.message}`;
    if (voicePhase && voicePhase.phase !== "WAKE_LISTENING") return voicePhase.detail;
    return status;
  }, [rateLimit, voicePhase, status]);

  const suggestions = useMemo(() => {
    if (messages.length > 0) return [];
    // What sets NANO apart is that it can act on this PC, so the offers lead
    // with that. Each is a request NANO really fulfils with a narrow tool.
    const base = [
      t("Quanto espaço livre tenho no disco?"),
      t("Põe as janelas lado a lado"),
      t("Lembra-me de fazer uma pausa daqui a 1 hora"),
      t("Ajuda-me a planear o dia"),
    ];
    if (providers?.route?.usable === false) return [t("Ajuda")];
    return base;
  }, [messages.length, providers]);

  const permissionAuditEvents = useMemo(
    () => (commandCenter?.activities ?? []).filter((e) => e.event.toLowerCase().includes("permission")),
    [commandCenter]
  );

  const commands: Command[] = useMemo(() => [
    { id: "new", label: t("Nova conversa"), hint: "Ctrl+Shift+O", run: newConversation },
    { id: "shortcuts", label: t("Atalhos de teclado"), hint: "?", run: () => setShortcutsOpen(true) },
    ...SECTIONS.flatMap((entry) => entry.views.filter((v) => advancedMode || !ADVANCED_VIEWS.has(v.id)).map((v) => ({
      id: `go-${v.id}`,
      label: entry.views.length > 1 ? t("Ir para {0} › {1}", { 0: entry.label, 1: v.label }) : t("Ir para {0}", { 0: v.label }),
      run: () => setView(v.id),
    }))),
    { id: "rail", label: t("Mostrar/ocultar barra lateral"), hint: "Ctrl+B", run: toggleRail },
    { id: "copy", label: t("Copiar conversa"), run: () => { copyConversation(); } },
    { id: "theme", label: t("Alternar tema"), run: () => setTheme(theme === "dark" ? "light" : "dark") },
    { id: "voice", label: t("Falar com o NANO"), hint: "Ctrl+M", run: startVoice },
    { id: "voice-mode", label: t("Modo de voz"), hint: "Ctrl+Shift+M", run: () => setVoiceModeOpen(true) },
    {
      id: "estop",
      label: readiness?.emergencyStop ? t("Retomar execução") : t("Paragem de emergência"),
      run: () => toggleEmergencyStop(!readiness?.emergencyStop),
    },
  ], [newConversation, startVoice, copyConversation, readiness, toggleEmergencyStop, toggleRail, advancedMode, theme, setTheme]);

  /* ── Chat presentation ──────────────────────────────────────────────── */
  const isChat = view === "chat";

  /* The sidebar's shape, from the width and the user's choice. See Rail.tsx.
     At the narrowest widths the collapsed column is hidden by the stylesheet
     and the top bar carries the button that opens the drawer instead. */
  const railMode: RailMode = narrow
    ? (railOpen ? "drawer" : "collapsed")
    : (railCollapsed ? "collapsed" : "expanded");

  /* The home screen: nothing said yet and nothing pending. It names itself
     ("Como posso ajudar?"), so the top bar carries no title there. */
  const approvalInline = view === "chat" && !showFirstRun && !voiceModeOpen;
  const hero = isChat && !showFirstRun && messages.length === 0 && !thinking && !confirmReq;

  /* Every thread is writable now, so there is exactly one set of messages on
     screen and no read-only mode to explain. `readOnlyReason` is gone with it. */
  const chatTitle = activeThread?.title ?? (messages.length ? t("Conversa") : "");
  const pageTitle = isChat ? (hero ? "" : chatTitle) : sectionDef.label;

  return (
    <>
      <Head>
        <title>{BRAND_NAME}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <meta name="application-name" content={BRAND_NAME} />
        {/* The page background of each theme, so the frame and the page agree. */}
        <meta name="theme-color" content={theme === "light" ? "#FFFFFF" : "#181818"} />
        <link rel="icon" type="image/png" href="/branding/nano-symbol.png" />
      </Head>

      <div className="shell">
        <div className="app" data-rail={railMode}>
          <Rail
            mode={railMode}
            onToggle={toggleRail}
            view={view}
            onView={(next) => { setView(next); if (narrow) setRailOpen(false); }}
            counts={navCounts}
            version={APP_VERSION}
            userName={userName}
            onHelp={() => setShortcutsOpen(true)}
            onProfile={() => { openSettingsSection("general"); if (narrow) setRailOpen(false); }}
            threads={threads ?? []}
            activeId={activeThreadId}
            query={chatQuery} onQuery={setChatQuery}
            onNew={() => { newConversation(); if (narrow) setRailOpen(false); }}
            onOpen={(id) => { setShowFirstRun(false); openThread(id); }}
            onRename={renameThread}
            onPin={pinThread}
            onProject={fileThread}
            onDelete={deleteThread}
            onDeleteMany={deleteThreads}
            loading={threads === null}
            unavailable={!memoryReady}
          />
          {railMode === "drawer" && (
            <div className="drawer-scrim" onClick={() => setRailOpen(false)} aria-hidden="true" />
          )}

          <div className="main">
            <TopBar
              // On a phone the bar holds menu, new chat, the AI selector and the bell;
              // a conversation title squeezed to "Ajuda-…" beside them helped nobody.
              title={mobile && isChat ? "" : pageTitle}
              actions={isChat && (messages.length > 0 || mobile) ? (
                <>
                  {messages.length > 0 && !mobile && (
                    <button
                      type="button" className="icon-btn" onClick={copyConversation}
                      aria-label={t("Copiar conversa")} title={t("Copiar a conversa para a área de transferência")}
                    >
                      <Icon name="copy" size={17} />
                    </button>
                  )}
                  {/* On a phone the sidebar is off screen, so "new chat" has to
                      live in the top bar, where every chat app puts it. */}
                  {mobile && (
                    <button
                      type="button" className="icon-btn" onClick={newConversation}
                      aria-label={t("Nova conversa")} title={t("Nova conversa")}
                    >
                      <Icon name="newChat" size={17} />
                    </button>
                  )}
                </>
              ) : undefined}
              showMenu={mobile}
              railOpen={railOpen}
              onToggleRail={toggleRail}
              providers={providers}
              agentState={agentState}
              healthLabel={healthLabel}
              offline={gaveUp}
              busy={busy}
              onSetMode={setMode}
              onSetPreferredCloud={setPreferredCloud}
              onSetCloudModel={setCloudModel}
              onOpenAiSettings={() => openSettingsSection("ai")}
              pendingCount={pendingCount}
              onOpenPermissions={() => setView("permissions")}
              isDesktop={isDesktop}
            />

            <main className="stage">
              {!isChat && visibleTabs.length > 1 && (
                <nav className="stage__tabs" aria-label={t("Secções de {0}", { 0: sectionDef.label })}>
                  {visibleTabs.map((entry) => (
                    <button
                      key={entry.id} type="button" className="subtab"
                      aria-current={view === entry.id ? "page" : undefined}
                      onClick={() => setView(entry.id)}
                      title={entry.hint}
                    >
                      {entry.label}
                      {(navCounts as Record<string, number>)[entry.id] > 0 && (
                        <span className="subtab__count">
                          {(navCounts as Record<string, number>)[entry.id]}
                        </span>
                      )}
                    </button>
                  ))}
                </nav>
              )}

              {gaveUp && (
                <div className="stage__offline">
                  <ErrorState
                    error={{
                      message: t("O motor do NANO não respondeu."),
                      component: t("ponte eel"),
                      detail: isDesktop
                        ? t("Fecha o NANO através do ícone junto ao relógio e volta a abri-lo. Se o problema continuar, copia o diagnóstico para o relatório de erro.")
                        : t("Verifica se o motor está a correr e recarrega esta página."),
                    }}
                    onRetry={() => window.location.reload()}
                  />
                  {isDesktop && <DiagnosticsPanel />}
                </div>
              )}

              {view === "chat" && (
                <div className={`chat-split${panelDoc ? " chat-split--panel" : ""}`}>
                <div className={`chat${hero ? " chat--hero" : ""}`}>
                  {showFirstRun ? (
                    <FirstRunGuide providers={providers} readiness={readiness}
                      displayName={displayName} onDisplayName={setDisplayName}
                      saving={savingFirstRun} onFinish={finishFirstRun}
                      onSettings={openSettingsSection}
                      onRefresh={() => { refreshProviders(); refreshReadiness(); }} />
                  ) : (
                    <Conversation
                      messages={messages} status={activityLabel} thinking={thinking}
                      userName={userName} connecting={!ready && !gaveUp}
                      advanced={advancedMode}
                      onRetry={() => rerunLastTurn()}
                      onEdit={(text) => rerunLastTurn(text)}
                      onChoose={(reply) => sendMessage(reply)}
                      onOpenPanel={setPanelDoc}
                      readAloud={readAloud}
                      onOpenConversation={openThreadById}
                      onNotice={answerNotice}
                    />
                  )}
                  {/* The provider's own wait, counted down, until it is over. It
                      used to reach the screen only as a tooltip on the AI
                      selector, so a limited turn said "limit" and never "when". */}
                  {!showFirstRun && rateLimit && (
                    <div className="chat-notice rate-limit" role="status">
                      <span>
                        <strong>{rateLimitProvider}:</strong> {rateLimit.message}{" "}{t("Tenta de novo dentro de ~{0} s.", { 0: rateLimit.waitSeconds })}
                      </span>
                    </div>
                  )}
                  {!showFirstRun && providers?.route?.usable === false && (
                    <div className="chat-notice setup-notice" role="status">
                      <span>{t("Configura um provedor cloud ou um modelo local para conversar.")}</span>
                      {/* Primary: with no provider this is the ONE action that
                          unblocks the app, and it was styled as a neutral
                          secondary button sitting beside the sentence explaining
                          the block. */}
                      <Button size="sm" variant="primary" onClick={() => openSettingsSection("ai")}>{t("Configurar IA")}</Button>
                    </div>
                  )}
                  {/* The approval sits in the conversation, where the request was
                      made, instead of covering it with a dialog. */}
                  {approvalInline && confirmReq && (
                    <div className="chat__approval">
                      <ConfirmCard
                        key={confirmReq.requestId} queued={confirmQueue.length - 1}
                        message={confirmReq.message} meta={confirmReq.meta}
                        onConfirm={() => answerConfirm(confirmReq.requestId, true)}
                        onCancel={() => answerConfirm(confirmReq.requestId, false)}
                      />
                    </div>
                  )}
                  {!showFirstRun && <Composer
                    value={input} onChange={setInput}
                    onSend={() => { setShowFirstRun(false); sendMessage(); }} onStop={stopWork}
                    onVoice={startVoice} onCancelVoice={cancelVoice}
                    onVoiceMode={() => setVoiceModeOpen(true)}
                    thinking={thinking} disabled={!ready}
                    voiceState={readiness?.voice.state ?? "UNKNOWN"}
                    listening={listening}
                    suggestions={suggestions}
                    onSuggestion={(text) => { setShowFirstRun(false); sendMessage(text); }}
                    files={pendingFiles}
                    onFiles={(added) => setPendingFiles((prev) => [...prev, ...added])}
                    onRemoveFile={(index) => setPendingFiles((prev) => prev.filter((_, i) => i !== index))}
                    onFileError={(text) => notify(text, "error")}
                  />}
                  {/* Weighs the home screen's group -- mark, headline, composer
                      -- slightly above the middle, where the eye lands. */}
                  {hero && <div className="chat__balance" aria-hidden="true" />}
                  <p className="stage__footer">{t("O {0} pode cometer erros. Ações sensíveis pedem sempre a tua autorização.", { 0: BRAND_NAME })}</p>
                </div>
                {panelDoc && <SidePanel doc={panelDoc} onClose={() => setPanelDoc(null)} />}
                </div>
              )}

              {view !== "chat" && (
                <div className="page-scroll">
                  {view === "tasks" && (
                    <TasksPage
                      tasks={tasks} counts={counts} scope={taskScope} onScope={setTaskScope}
                      loading={tasksLoading} onOpenTask={openTask} onCancelTask={cancelTask}
                      onArchive={archiveTasks} query={taskQuery} onQuery={setTaskQuery}
                    />
                  )}
                  {view === "activity" && (
                    <ActivityPage
                      entries={pcActivityFiltered}
                      category={activityCategory} onCategory={setActivityCategory}
                      loading={pcActivityLoading}
                      totalCount={pcActivity?.length ?? null}
                    />
                  )}
                  {view === "permissions" && (
                    <PermissionsPage
                      pending={commandCenter?.permissions ?? []} policies={policies}
                      auditEvents={permissionAuditEvents} onResolve={resolvePermission} busy={resolving}
                    />
                  )}
                  {view === "agents" && <AgentsPage agents={agents} />}
                  {view === "memory" && (
                    <MemoriesPage
                      overview={memoryData} loading={memoryLoading}
                      onCreate={createMemory} onUpdate={updateMemory}
                      onDelete={deleteMemory} onClearAll={clearMemories}
                      onOpenSettings={() => openSettingsSection("memory")}
                    />
                  )}
                  {view === "recaps" && (
                    <RecapsPage
                      payload={recapsData} loading={recapsLoading}
                      onUpdate={updateRecap} onForget={forgetRecap}
                      onRestore={restoreRecap} onForgetAll={forgetAllRecaps}
                      onOpenConversation={openThreadById}
                      onOpenSettings={() => openSettingsSection("memory")}
                    />
                  )}
                  {view === "knowledge" && (
                    <KnowledgePage
                      nodes={knowledgeData?.nodes ?? null}
                      types={knowledgeData?.types ?? []}
                      stats={knowledgeData?.stats ?? null}
                      loading={knowledgeLoading}
                      onOpenNode={openNode}
                      onCreate={createNode}
                      overview={memoryData}
                    />
                  )}
                  {view === "graph" && (
                    <GraphPage
                      graph={graphData}
                      types={graphData?.types ?? []}
                      loading={graphLoading}
                      onOpenNode={openNode}
                      onRefresh={setGraphType}
                      overview={memoryData}
                    />
                  )}
                  {view === "capabilities" && <CapabilitiesPage enabled={ready} onTry={(text) => { setView("chat"); setShowFirstRun(false); sendMessage(text); }} />}
                  {view === "agenda" && <AgendaPage enabled={ready} notify={notify} />}
                  {view === "modes" && (
                    <WorkModesPage
                      enabled={ready} running={modeRunning} result={modeResult}
                      onActivate={(modeId) => {
                        setModeRunning(modeId);
                        setModeResult(null);
                        call<any>("activate_work_mode", modeId).then((ack) => {
                          if (!ack?.accepted) {
                            setModeRunning(null);
                            notify(t("Não foi possível ativar o modo."), "error");
                          }
                        });
                      }}
                      onRunProtocol={(protocolId) => {
                        setModeRunning(`protocol:${protocolId}`);
                        setModeResult(null);
                        call<any>("run_protocol", protocolId).then((ack) => {
                          if (!ack?.accepted) {
                            setModeRunning(null);
                            notify(t("Não foi possível executar o protocolo."), "error");
                          }
                        });
                      }}
                    />
                  )}
                  {view === "integrations" && (
                    <IntegrationsPage
                      providers={providers} plugins={plugins} readiness={readiness}
                      onOpenSettings={() => setView("settings")} onOpenPlugin={openPluginCode}
                    />
                  )}
                  {view === "status" && (
                    <StatusPage readiness={readiness} providers={providers}
                                commandCenter={commandCenter} loading={ccLoading}
                                pcSnapshot={pcSnapshot} pcLoading={pcLoading}
                                onRefreshPc={refreshPc}
                                onToggleEmergencyStop={toggleEmergencyStop}
                                onOpenTask={openTask} onCancelTask={cancelTask}
                                onNavigate={setView} />
                  )}
                  {view === "settings" && (
                    <SettingsPage
                      settings={settings} providers={providers}
                      diagnostics={voiceDiag}
                      loading={settingsLoading} busy={busy}
                      onSetMode={setMode} onSetPreferredCloud={setPreferredCloud}
                      onSaveCloudKey={saveCloudKey} onRemoveCloudKey={removeCloudKey}
                      onTestCloud={testCloud} onSetCloudModel={setCloudModelTier}
                      onSetLocalModel={setLocalModel} onUpdate={updateSetting}
                      onTestSpeaker={testSpeaker} onTestMicrophone={testMicrophone}
                      onToggleEmergencyStop={toggleEmergencyStop}
                      onClearConversation={newConversation}
                      onForgetAllMemory={forgetAllMemory}
                      onEraseAllData={eraseAllData}
                      onNavigate={setView}
                      onOpenFirstRun={() => { firstRunAuto.current = false; setShowFirstRun(true); setView("chat"); }}
                      section={settingsSection} onSection={setSettingsSection}
                      theme={themePref} onTheme={setTheme}
                      reduceMotion={reduceMotion} onReduceMotion={setReduceMotion}
                      advancedMode={advancedMode} onAdvancedMode={setAdvancedMode}
                      displayName={displayName} onDisplayName={setDisplayName}
                    />
                  )}
                </div>
              )}
            </main>
          </div>
        </div>
      </div>

      <CommandPalette open={paletteOpen} commands={commands} onClose={() => setPaletteOpen(false)} />
      <ShortcutsDialog open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
      <VoiceMode
        open={voiceModeOpen}
        phase={voicePhase?.phase}
        listening={listening}
        voiceReady={ready && readiness?.voice.state === "READY" && !thinking}
        lastQuestion={voiceLast.question}
        lastAnswer={voiceLast.answer}
        onTalk={startVoice}
        onStop={listening ? cancelVoice : stopWork}
        onClose={() => setVoiceModeOpen(false)}
      />
      <ToastStack toasts={toasts} />

      {confirmReq && !approvalInline && (
        <ConfirmModal
          key={confirmReq.requestId} queued={confirmQueue.length - 1}
          message={confirmReq.message} meta={confirmReq.meta}
          onConfirm={() => answerConfirm(confirmReq.requestId, true)}
          onCancel={() => answerConfirm(confirmReq.requestId, false)}
        />
      )}

      <NodeDetailModal
        detail={nodeDetail} open={nodeOpen}
        onClose={() => setNodeOpen(false)}
        onOpenNode={openNode}
        onDelete={deleteNode}
        onUpdate={updateNode}
      />

      <TaskDetailModal
        visible={taskDetailOpen} task={taskDetail} events={taskDetailEvents}
        permissions={commandCenter?.permissions ?? []}
        onClose={() => setTaskDetailOpen(false)} onCancel={cancelTask}
      />

      {pluginCode && (
        <PluginCodeModal
          pluginName={pluginCode.name} code={pluginCode.code}
          tools={pluginCode.tools ?? []} filename={pluginCode.filename}
          onClose={() => setPluginCode(null)}
        />
      )}
    </>
  );
}
