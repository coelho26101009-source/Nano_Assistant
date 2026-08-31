/**
 * Nano application shell.
 *
 * Layout: top bar · conversation rail · stage.
 *
 * The bar carries the five sections (Chat, Ferramentas, PC, Memória,
 * Definições) and, in the desktop shell, doubles as the window caption. The
 * rail on the left lists real conversations. The stage on the right holds the
 * conversation or the page of whichever section is open. There is no longer a
 * fixed inspector column: its panels moved to PC › Estado, where they answer a
 * question instead of permanently occupying the main screen.
 *
 * All state shown here is read from the Python backend through lib/backend.
 * Nothing is mocked and nothing defaults to "ready".
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Head from "next/head";

import CommandPalette, { Command } from "../components/CommandPalette";
import ConfirmModal from "../components/Confirm";
import PluginCodeModal from "../components/PluginCodeModal";
import Rail from "../components/Rail";
import CapabilitiesPage from "../components/CapabilitiesPage";
import SettingsPage, { type Section as SettingsSection } from "../components/SettingsPage";
import TaskDetailModal from "../components/TaskDetailModal";
import TopNav, { SECTIONS, ViewId, sectionEntry, sectionOf, viewEntry } from "../components/TopNav";
import NanoLogo from "../components/NanoLogo";
import { Composer, Conversation, Message, ToolEvent } from "../components/Conversation";
import {
  ActivityPage, AgentsPage, IntegrationsPage,
  PermissionsPage, StatusPage, TaskScope, TasksPage, pcActivityMatches,
} from "../components/Pages";
import { Button, ErrorState, ToastStack, stateLabel, useToasts } from "../components/ui";
import {
  CommandCenterPayload, PcActivityCategory, PcActivityEntry, PcSnapshot,
  POLL, ProviderPayload, ReadinessPayload,
  SettingsPayload, TaskCounts, TaskRow, VoiceDiagnostics,
  call, expose, useBridgeReady, useFetch, usePolled,
} from "../lib/backend";
import { MemoriesPage, GraphPage, KnowledgePage, NodeDetailModal } from "../components/MemoryPages";
import { Thread, ThreadMessage } from "../lib/conversations";
import type {
  KnowledgeGraphPayload, KnowledgeNode, MemoryOverview,
} from "../lib/memory";
import { useIsDesktop } from "../lib/desktop";
// The product version comes from version.json, which core/version.py and the
// Electron main process read too. It used to be a literal here, which is how
// the UI said "v1.0" while the backend reported "8.1.0".
import { APP_VERSION } from "../lib/version";


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
 * Whether the window is narrow enough that the rail has to become a drawer.
 *
 * Resolved in an effect, never during render: the statically exported HTML and
 * the first client render must agree or React logs a hydration mismatch.
 */
function useNarrow(query = "(max-width: 1080px)"): boolean {
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

export default function Home() {
  const { ready, gaveUp } = useBridgeReady();
  const { toasts, notify } = useToasts();
  // Capability detection, resolved after mount. In a browser this stays false
  // and the window controls are simply never rendered.
  const isDesktop = useIsDesktop();
  const narrow = useNarrow();

  /* ── Shell state ────────────────────────────────────────────────────── */
  const [view, setView] = useState<ViewId>("chat");
  /* Which Settings category is open. Lifted out of SettingsPage so "Abrir
     definições de IA" in the AI pill can land on IA directly instead of on
     whichever category happened to be open last. */
  const [settingsSection, setSettingsSection] = useState<SettingsSection>("general");
  const [railOpen, setRailOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [reduceMotion, setReduceMotion] = useState(false);

  /* ── Conversation ───────────────────────────────────────────────────── */
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const [status, setStatus] = useState("");
  const [listening, setListening] = useState(false);
  // Rate limiting is a temporary, self-clearing state with a known wait, not
  // an error message. Kept separate so the UI can count it down.
  const [rateLimit, setRateLimit] = useState<{ message: string; waitSeconds: number } | null>(null);
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
  const [messageCount, setMessageCount] = useState<number | null>(null);
  const [memoryReady, setMemoryReady] = useState(true);

  /* ── Memória / Second Brain ─────────────────────────────────────────── */
  const [nodeDetail, setNodeDetail] = useState<any | null>(null);
  const [nodeOpen, setNodeOpen] = useState(false);
  const [graphType, setGraphType] = useState("");

  /* ── Dialogs ────────────────────────────────────────────────────────── */
  const [confirmReq, setConfirmReq] = useState<ConfirmRequest | null>(null);
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
  const inMemorySection = view === "memory" || view === "knowledge" || view === "graph";
  const { data: memoryData, loading: memoryLoading, refresh: refreshMemory } =
    useFetch<MemoryOverview>("get_memory_overview", ready && inMemorySection, [view]);
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

  useEffect(() => { document.documentElement.setAttribute("data-theme", theme); }, [theme]);
  // Stamped on the root so rules that cannot see the React tree -- the drag
  // region on the top bar, the fixed rail drawer at narrow widths -- can tell
  // whether they are inside the desktop shell.
  useEffect(() => {
    document.documentElement.setAttribute("data-desktop", isDesktop ? "true" : "false");
  }, [isDesktop]);
  useEffect(() => {
    document.documentElement.style.setProperty("--transition", reduceMotion ? "0ms" : "140ms cubic-bezier(0.4, 0, 0.2, 1)");
  }, [reduceMotion]);

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
    setMessageCount(
      (payload.conversations ?? []).reduce(
        (total: number, thread: Thread) => total + (thread.messageCount ?? 0), 0));
  }, []);

  /** Render one thread's stored messages into the chat view. */
  const showThreadMessages = useCallback((rows: ThreadMessage[] | null | undefined) => {
    setMessages((rows ?? []).map((row) => ({
      id: `stored:${row.id}`,
      role: row.role === "user" ? "user" : "assistant",
      content: row.content,
      timestamp: new Date(row.timestamp),
    })));
  }, []);

  /* ── Streaming events ───────────────────────────────────────────────── */
  useEffect(() => {
    if (!ready) return;

    expose((reqId: string, msg: string, meta: any) => {
      setConfirmReq({ requestId: reqId, message: msg, meta });
    }, "on_confirm_request");

    expose(() => {
      setThinking(true);
      setListening(true);
      setStatus("Ouvi-te — diz o comando");
      notify("Wake detetada");
    }, "on_wake_detected");

    expose((msgId: string, userText?: string) => {
      setThinking(true);
      setStatus("A pensar…");
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
        const names = match[1].split(",").map((t) => t.trim()).filter(Boolean);
        setStatus(names.length === 1 ? `A usar ${names[0]}…` : `A usar ${names.length} ferramentas…`);
        setMessages((prev) => {
          const next = [...prev];
          for (let i = next.length - 1; i >= 0; i -= 1) {
            if (next[i].role === "assistant") {
              const existing = next[i].tools ?? [];
              const merged: ToolEvent[] = [...existing];
              for (const name of names) {
                if (!merged.some((t) => t.name === name)) {
                  merged.push({ name, state: "running", summary: `A executar ${name}…` });
                }
              }
              next[i] = { ...next[i], tools: merged };
              break;
            }
          }
          return next;
        });
      } else {
        setStatus(text.replace(/^_thinking_:?\s*/, "").replace(/^[🧠⚙️]\s*/u, "") || "A pensar…");
      }
    }, "on_stream_status");

    expose((msgId: string, chunk: string) => {
      setMessages((prev) => {
        if (!prev.some((m) => m.id === msgId)) {
          return [...prev, { id: msgId, role: "assistant", content: chunk, timestamp: new Date(), streaming: true }];
        }
        return prev.map((m) => (m.id === msgId ? { ...m, content: m.content + chunk, streaming: true } : m));
      });
    }, "on_stream_chunk");

    expose((msgId: string, final: any) => {
      setThinking(false);
      setListening(false);
      setStatus("");
      const text = final?.text ?? final?.error;
      setMessages((prev) => prev.map((m) => {
        if (m.id !== msgId) return m;
        const tools = (m.tools ?? []).map((t) => t.state === "running" ? { ...t, state: "ok" as const, summary: `${t.name} concluída` } : t);
        return {
          ...m,
          content: text ?? m.content,
          streaming: false,
          error: final?.ok === false,
          // Safe per-response diagnostics (provider, model, tokens, latency).
          meta: final?.meta ?? m.meta,
          tools,
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
            content: m.content || `**Não foi possível responder.** ${error?.detail ?? error?.code ?? ""}` }
        : m));
      notify("Erro ao responder", "error");
    }, "on_stream_error");

    // Rate limiting is a state with a known wait, not a generic error.
    expose((_msgId: string, info: any) => {
      const wait = Math.max(1, Math.round(Number(info?.wait_seconds ?? 0)));
      setRateLimit({ message: info?.message ?? "Limite temporário atingido.", waitSeconds: wait });
      setStatus("");
      setThinking(false);
      notify(info?.message ?? "Limite temporário da Groq atingido.", "default");
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
    // The user bubble's id is derived from the turn id, so on_stream_start can
    // recognise that this turn's message is already on screen and never append
    // a second copy.
    setMessages((prev) => [
      ...prev,
      { id: userMessageId(msgId), role: "user", content: text, timestamp: new Date() },
      { id: msgId, role: "assistant", content: "", timestamp: new Date(), streaming: true },
    ]);
    if (override === undefined) setInput("");
    setThinking(true);
    setStatus("A pensar…");
    setView("chat");

    // send_message returns an ACK, never the answer: the reply arrives on the
    // stream events. Treating a slow turn's missing return value as "offline"
    // is what produced a false "Motor offline" while the backend was working.
    call<any>("send_message", text, msgId).then((ack) => {
      if (ack?.accepted) return;               // healthy: the stream takes over

      // Only a genuine transport failure lands here.
      setThinking(false);
      setStatus("");
      const transportDown = ack === null || ack === undefined;
      const answer = transportDown
        ? "**Sem resposta do motor do Nano.** A ligação ao backend caiu ou expirou. Confirma que a janela do Nano continua aberta e recarrega a página."
        : `**O pedido não foi aceite.** ${ack?.error ?? "motivo desconhecido"}.`;
      notify(transportDown ? "Motor offline" : "Pedido recusado", "error");
      setMessages((prev) => prev.map((m) => m.id === msgId
        ? { ...m, content: answer, streaming: false, error: true,
            tools: (m.tools ?? []).map((t) => t.state === "running" ? { ...t, state: "ok" as const } : t) }
        : m));
    });
  }, [input, thinking, ready, notify]);

  const stopWork = useCallback(() => {
    call("stop_voice");
    const current = commandCenter?.current_task;
    if (current && !["COMPLETED", "FAILED", "CANCELLED"].includes(current.status)) {
      call("cancel_agent_task", current.id).then(() => { notify("Tarefa cancelada"); refreshCC(); refreshCounts(); });
    }
    setThinking(false);
    setListening(false);
    setStatus("");
  }, [commandCenter, notify, refreshCC, refreshCounts]);

  const startVoice = useCallback(() => {
    if (!ready || thinking) return;
    if (readiness && readiness.voice.state !== "READY") {
      notify(`Voz: ${stateLabel(readiness.voice.state)}`, "error");
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
      if (result?.busy) notify("O Nano já está a ouvir");
      else notify(`Voz: ${result?.error ?? "não foi possível iniciar"}`, "error");
    });
    setListening(true);
    setThinking(true);
    setStatus("A ouvir…");
  }, [ready, thinking, readiness, notify]);

  const cancelVoice = useCallback(() => {
    call("stop_voice");
    setListening(false);
    setThinking(false);
    setStatus("");
    notify("Escuta cancelada");
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
      setInput("");
      setView("chat");
      if (result?.conversation) setActiveThreadId(result.conversation.id);
      reloadThreads();
      notify("Nova conversa");
    });
  }, [notify, reloadThreads]);

  /** Open a thread and rebuild the model context from it.
   *
   * This is the operation that used to be impossible. Older conversations
   * opened read-only because the Brain held one rolling window over a flat log;
   * open_conversation rebuilds that window from the chosen thread, so the next
   * message is answered against the right history.
   */
  const openThread = useCallback((thread: Thread) => {
    if (thread.id === activeThreadId) { setView("chat"); return; }
    call<any>("open_conversation", thread.id).then((result) => {
      if (!result?.ok) { notify("Não foi possível abrir a conversa", "error"); return; }
      setActiveThreadId(result.conversation.id);
      showThreadMessages(result.messages);
      setInput("");
      setView("chat");
      if (narrow) setRailOpen(false);
    });
  }, [activeThreadId, notify, showThreadMessages, narrow]);

  const renameThread = useCallback((thread: Thread, title: string) => {
    call<any>("rename_conversation", thread.id, title).then((result) => {
      if (result?.ok) { reloadThreads(); notify("Conversa renomeada"); }
      else notify("Não foi possível mudar o nome", "error");
    });
  }, [notify, reloadThreads]);

  const deleteThread = useCallback((thread: Thread) => {
    call<any>("delete_conversation", thread.id).then((result) => {
      if (!result?.ok) { notify("Não foi possível apagar", "error"); return; }
      notify(`Conversa apagada (${result.messages} mensagens)`);
      if (thread.id === activeThreadId) {
        // The backend has already moved the Brain to another thread; ask it
        // which one rather than guessing, so the two never disagree about
        // which conversation is open.
        call<ThreadMessage[]>("get_conversation_history").then(showThreadMessages);
      }
      reloadThreads();
    });
  }, [activeThreadId, notify, reloadThreads, showThreadMessages]);

  const copyConversation = useCallback(async () => {
    const text = messages
      .filter((m) => m.content.trim())
      .map((m) => `${m.role === "user" ? "Você" : "Nano"}: ${m.content}`)
      .join("\n\n");
    if (!text) { notify("Não há nada para copiar"); return; }
    try {
      await navigator.clipboard.writeText(text);
      notify("Conversa copiada", "success");
    } catch {
      notify("A área de transferência está bloqueada", "error");
    }
  }, [messages, notify]);

  const openTask = useCallback((taskId: string) => {
    call<any>("get_task_detail", taskId).then((result) => {
      if (result?.ok) {
        setTaskDetail(result.task);
        setTaskDetailEvents(result.events ?? []);
        setTaskDetailOpen(true);
      } else notify("Tarefa não encontrada", "error");
    });
  }, [notify]);

  const cancelTask = useCallback((taskId: string) => {
    call<any>("cancel_agent_task", taskId).then((result) => {
      notify(result?.ok ? "Tarefa cancelada" : "Não foi possível cancelar", result?.ok ? "default" : "error");
      refreshCC(); refreshCounts(); refreshTasks();
      setTaskDetailOpen(false);
    });
  }, [notify, refreshCC, refreshCounts, refreshTasks]);

  const archiveTasks = useCallback(() => {
    call<any>("archive_finished_tasks").then((result) => {
      notify(result?.ok ? `${result.removed} tarefa(s) arquivadas` : "Falha ao arquivar", result?.ok ? "success" : "error");
      refreshTasks(); refreshCounts(); refreshCC();
    });
  }, [notify, refreshTasks, refreshCounts, refreshCC]);

  const resolvePermission = useCallback((requestId: string, decision: "deny" | "allow_once" | "allow_for_task") => {
    setResolving(true);
    call<any>("resolve_permission", requestId, decision).then((result) => {
      setResolving(false);
      if (result?.ok) {
        notify(decision === "deny" ? "Pedido recusado" : "Autorização concedida", "success");
        refreshCC(); refreshReadiness();
      } else notify(`Recusado pela policy: ${result?.error ?? "erro"}`, "error");
    });
  }, [notify, refreshCC, refreshReadiness]);

  const toggleEmergencyStop = useCallback((enabled: boolean) => {
    call<any>("set_emergency_stop", enabled).then(() => {
      notify(enabled ? "Execução bloqueada" : "Execução retomada", enabled ? "error" : "success");
      refreshReadiness(); refreshCC(); refreshSettings();
    });
  }, [notify, refreshReadiness, refreshCC, refreshSettings]);

  const openPluginCode = useCallback((name: string) => {
    call<any>("get_plugin_code", name).then((result) => {
      if (result?.ok) setPluginCode(result); else notify("Não foi possível ler o componente", "error");
    });
  }, [notify]);

  /* ── Provider actions ───────────────────────────────────────────────── */
  const setMode = useCallback((mode: "AUTO" | "CLOUD" | "LOCAL") => {
    setBusy(true);
    call<any>("set_provider_mode", mode).then((result) => {
      setBusy(false);
      if (result?.ok) { notify(`Modo: ${mode}`, "success"); refreshProviders(); refreshSettings(); refreshReadiness(); }
      else notify("Não foi possível mudar de modo", "error");
    });
  }, [notify, refreshProviders, refreshSettings, refreshReadiness]);

  const saveGroqKey = useCallback(async (key: string) => {
    setBusy(true);
    const result = await call<any>("set_groq_api_key", key);
    setBusy(false);
    if (result?.ok) { notify("Chave validada e guardada", "success"); refreshProviders(); refreshSettings(); refreshReadiness(); }
    else notify(result?.detail ?? "Chave recusada", "error");
  }, [notify, refreshProviders, refreshSettings, refreshReadiness]);

  const setLocalModel = useCallback((model: string) => {
    setBusy(true);
    call<any>("set_local_model", model).then((result) => {
      setBusy(false);
      if (result?.ok) { notify(`Modelo local: ${result.model}`, "success"); refreshProviders(); refreshSettings(); }
      else notify(result?.detail ?? "Não foi possível mudar o modelo local", "error");
    });
  }, [notify, refreshProviders, refreshSettings]);

  const forgetAllMemory = useCallback(() => {
    call<any>("forget_all_memory_facts").then((result) => {
      notify(
        result?.ok ? `${result.removed} facto(s) esquecido(s)` : "Não foi possível esquecer tudo",
        result?.ok ? "success" : "error",
      );
      refreshMemory();
      refreshSettings();
    });
  }, [notify, refreshMemory, refreshSettings]);

  /** Open Settings on a specific category. Used by the AI pill. */
  const openSettingsSection = useCallback((section: SettingsSection) => {
    setSettingsSection(section);
    setView("settings");
  }, []);

  const removeGroqKey = useCallback(() => {
    call<any>("remove_groq_api_key").then(() => {
      notify("Chave removida"); refreshProviders(); refreshSettings(); refreshReadiness();
    });
  }, [notify, refreshProviders, refreshSettings, refreshReadiness]);

  const testGroq = useCallback(() => {
    setBusy(true);
    call<any>("test_groq_connection").then((result) => {
      setBusy(false);
      notify(result?.detail ?? "Sem resposta", result?.ok ? "success" : "error");
    });
  }, [notify]);

  const setGroqModel = useCallback((model: string) => {
    setBusy(true);
    call<any>("set_groq_model", model).then((result) => {
      setBusy(false);
      if (result?.ok) { notify(`Modelo: ${model}`, "success"); refreshProviders(); refreshSettings(); }
      else notify(result?.detail ?? "Modelo indisponível", "error");
    });
  }, [notify, refreshProviders, refreshSettings]);

  const updateSetting = useCallback((key: string, value: any) => {
    call<any>("update_setting", key, value).then((result) => {
      if (result?.ok) { refreshSettings(); refreshReadiness(); }
      else notify(`Não foi possível guardar: ${result?.error ?? "erro"}`, "error");
    });
  }, [notify, refreshSettings, refreshReadiness]);

  const testSpeaker = useCallback(() => {
    call<any>("test_speaker").then((r) => notify(r?.detail ?? "Sem resposta", r?.ok ? "success" : "error"));
  }, [notify]);

  const testMicrophone = useCallback(() => {
    notify("A gravar 3 segundos…");
    call<any>("test_microphone", 3).then((r) => notify(r?.detail ?? "Sem resposta", r?.ok && r?.speechDetected ? "success" : "error"));
  }, [notify]);

  /* ── Keyboard ───────────────────────────────────────────────────────── */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const mod = event.ctrlKey || event.metaKey;
      if (!mod) return;
      const key = event.key.toLowerCase();
      if (key === "k") { event.preventDefault(); setPaletteOpen((v) => !v); }
      else if (key === "b") { event.preventDefault(); setRailOpen((v) => !v); }
      else if (key === "n") { event.preventDefault(); newConversation(); }
      else if (key === "m") { event.preventDefault(); startVoice(); }
      else if (key === ",") { event.preventDefault(); setView("settings"); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [newConversation, startVoice]);

  /* ── Derived ────────────────────────────────────────────────────────── */
  const pendingCount = commandCenter?.permissions?.length ?? 0;
  const agentState = readiness?.agent.state ?? (gaveUp ? "BACKEND_OFFLINE" : "UNKNOWN");
  const section = sectionOf(view);
  const sectionDef = sectionEntry(section);
  const meta = viewEntry(view);

  /** The thread currently on screen, from the real list. */
  const activeThread = useMemo(
    () => (threads ?? []).find((thread) => thread.id === activeThreadId) ?? null,
    [threads, activeThreadId],
  );

  /* ── Memória actions ────────────────────────────────────────────────── */
  const openNode = useCallback((nodeId: string) => {
    call<any>("get_knowledge_node", nodeId).then((result) => {
      if (result?.ok) { setNodeDetail(result); setNodeOpen(true); }
      else notify("Nó não encontrado", "error");
    });
  }, [notify]);

  const createMemory = useCallback((text: string, kind: string, importance: number) => {
    call<any>("create_memory", text, kind, importance, []).then((result) => {
      if (result?.ok) { notify("Memória guardada", "success"); refreshMemory(); }
      // A refusal is reported with its REASON. "Não guardei" alone would leave
      // the user guessing; "parece conter api_key" tells them what to change.
      else notify(result?.detail ?? "Não foi possível guardar essa memória", "error");
    });
  }, [notify, refreshMemory]);

  const updateMemory = useCallback((id: string, patch: Record<string, any>) => {
    call<any>("update_memory", id, patch.text ?? null, patch.kind ?? null,
      patch.importance ?? null, patch.pinned ?? null, patch.status ?? null,
      patch.tags ?? null).then((result) => {
      if (result?.ok) refreshMemory();
      else notify(result?.detail ?? "Não foi possível atualizar", "error");
    });
  }, [notify, refreshMemory]);

  const deleteMemory = useCallback((id: string) => {
    call<any>("delete_memory", id).then((result) => {
      notify(result?.ok ? "Memória apagada" : "Não foi possível apagar",
             result?.ok ? "success" : "error");
      refreshMemory();
    });
  }, [notify, refreshMemory]);

  const clearMemories = useCallback(() => {
    call<any>("clear_memories").then((result) => {
      notify(result?.ok ? `${result.removed} memória(s) apagada(s)` : "Falhou",
             result?.ok ? "success" : "error");
      refreshMemory();
    });
  }, [notify, refreshMemory]);

  const createNode = useCallback((title: string, type: string, summary: string) => {
    call<any>("create_knowledge_node", title, type, summary, "", []).then((result) => {
      if (result?.ok) { notify("Nó criado", "success"); refreshKnowledge(); refreshGraph(); }
      else notify("Não foi possível criar o nó", "error");
    });
  }, [notify, refreshKnowledge, refreshGraph]);

  const updateNode = useCallback((id: string, patch: Record<string, any>) => {
    call<any>("update_knowledge_node", id, patch.title ?? null, patch.node_type ?? null,
      patch.summary ?? null, patch.body ?? null, patch.tags ?? null,
      patch.pinned ?? null).then((result) => {
      if (result?.ok) { refreshKnowledge(); openNode(id); }
      else notify(result?.detail ?? "Não foi possível atualizar o nó", "error");
    });
  }, [notify, refreshKnowledge, openNode]);

  const deleteNode = useCallback((id: string) => {
    call<any>("delete_knowledge_node", id).then((result) => {
      if (result?.ok) {
        notify(`Nó apagado (${result.edges} ligações)`, "success");
        setNodeOpen(false);
        setNodeDetail(null);
        refreshKnowledge();
        refreshGraph();
      } else notify("Não foi possível apagar o nó", "error");
    });
  }, [notify, refreshKnowledge, refreshGraph]);

  const navCounts = useMemo(() => ({
    tasks: counts?.badge ?? 0,
    permissions: pendingCount,
  }), [counts, pendingCount]);

  const healthLabel = useMemo(() => {
    if (gaveUp) return "Motor offline";
    if (rateLimit) return `Groq: espera ~${rateLimit.waitSeconds}s`;
    if (readiness?.emergencyStop) return "Execução bloqueada";
    if (pendingCount > 0) return `${pendingCount} por autorizar`;
    if (providers?.route?.usable === false) return "Sem provedor de IA";
    // The wake detector reports MIC_SILENT when chunks arrive with no energy.
    // Saying "operacional" then would be untrue.
    if (readiness?.wakePhrase?.state === "MIC_SILENT") return "Microfone sem áudio";
    return "Todos os serviços operacionais";
  }, [gaveUp, rateLimit, readiness, pendingCount, providers]);

  /** What is actually answering, in the few words the top-bar pill can hold. */
  const routeLabel = useMemo(() => {
    if (gaveUp) return "Motor offline";
    const route = providers?.route;
    if (!route) return "A ligar…";
    if (!route.usable) return "Sem provedor";
    const name = route.provider === "groq" ? "Groq" : route.provider === "ollama" ? "Local" : "—";
    return route.fallback ? `${name} · fallback` : `${name} · ${route.mode}`;
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
    const base = ["Ver uso de RAM", "Listar processos pesados", "Ajuda"];
    if (providers?.route?.usable === false) return ["Ajuda"];
    return base;
  }, [messages.length, providers]);

  const permissionAuditEvents = useMemo(
    () => (commandCenter?.activities ?? []).filter((e) => e.event.toLowerCase().includes("permission")),
    [commandCenter]
  );

  const commands: Command[] = useMemo(() => [
    { id: "new", label: "Nova conversa", hint: "Ctrl+N", run: newConversation },
    ...SECTIONS.flatMap((entry) => entry.views.map((v) => ({
      id: `go-${v.id}`,
      label: entry.views.length > 1 ? `Ir para ${entry.label} › ${v.label}` : `Ir para ${v.label}`,
      run: () => setView(v.id),
    }))),
    { id: "rail", label: "Mostrar/ocultar conversas", hint: "Ctrl+B", run: () => setRailOpen((v) => !v) },
    { id: "copy", label: "Copiar conversa", run: () => { copyConversation(); } },
    { id: "theme", label: "Alternar tema", run: () => setTheme((t) => (t === "dark" ? "light" : "dark")) },
    { id: "voice", label: "Falar com o Nano", hint: "Ctrl+M", run: startVoice },
    {
      id: "estop",
      label: readiness?.emergencyStop ? "Retomar execução" : "Paragem de emergência",
      run: () => toggleEmergencyStop(!readiness?.emergencyStop),
    },
  ], [newConversation, startVoice, copyConversation, readiness, toggleEmergencyStop]);

  /* ── Chat presentation ──────────────────────────────────────────────── */
  const isChat = view === "chat";
  const railDocked = isChat && !narrow;
  const railVisible = isChat && (railDocked || railOpen);

  /* Every thread is writable now, so there is exactly one set of messages on
     screen and no read-only mode to explain. `readOnlyReason` is gone with it. */
  const chatTitle = activeThread?.title
    ?? (messages.length ? "Conversa" : "Nova conversa");

  const chatSubtitle = messages.length
    ? (activityLabel || `${messages.length} mensagens`)
    : "Escreve, ou diz “Ei Nano”";

  return (
    <>
      <Head>
        <title>Nano</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <meta name="theme-color" content="#050303" />
        {/* The real mark, so the taskbar and the tab carry the brand rather
            than a hand-drawn stand-in. */}
        <link rel="icon" href="/branding/nano-mark-alpha.png" />
      </Head>

      <div className="shell">
        <TopNav
          view={view} onView={(next) => { setView(next); if (narrow) setRailOpen(false); }}
          counts={navCounts}
          agentState={agentState}
          healthLabel={healthLabel}
          providers={providers}
          offline={gaveUp}
          busy={busy}
          onSetMode={setMode}
          onOpenAiSettings={() => openSettingsSection("ai")}
          pendingCount={pendingCount}
          profileName={profileName}
          isDesktop={isDesktop}
          railOpen={railOpen}
          onToggleRail={() => setRailOpen((v) => !v)}
          showRailToggle={isChat && narrow}
          version={APP_VERSION}
        />

        <div className="app" data-rail={railDocked ? "true" : "false"}>
          {railVisible && (
            <Rail
              threads={threads ?? []}
              activeId={activeThreadId}
              query={chatQuery} onQuery={setChatQuery}
              onNew={newConversation}
              onOpen={openThread}
              onRename={renameThread}
              onDelete={deleteThread}
              loading={threads === null}
              messageCount={messageCount}
              onOpenMemory={() => setView("memory")}
              drawer={railDocked ? "docked" : "open"}
              onCloseDrawer={() => setRailOpen(false)}
              unavailable={!memoryReady}
            />
          )}
          {railVisible && !railDocked && (
            <div className="drawer-scrim" onClick={() => setRailOpen(false)} aria-hidden="true" />
          )}

          <main className="stage surface-panel">
            <header className="stage__header">
              {isChat ? (
                <>
                  <span className="stage__title">
                    <NanoLogo size={22} />
                    <span className="stage__title-text">
                      <span className="stage__name">{chatTitle}</span>
                      <span className="stage__sub">{chatSubtitle}</span>
                    </span>
                  </span>
                  <span className="stage__spacer" />
                  <span className="stage__actions">
                    <Button variant="ghost" size="sm" onClick={copyConversation}
                            title="Copiar a conversa para a área de transferência">
                      Copiar
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setPaletteOpen(true)}
                            title="Paleta de comandos (Ctrl+K)">
                      ⌘K
                    </Button>
                  </span>
                </>
              ) : (
                <>
                  <span className="stage__title">
                    <span className="stage__title-text">
                      <span className="stage__name">{sectionDef.label}</span>
                      <span className="stage__sub">{meta.hint}</span>
                    </span>
                  </span>
                  {sectionDef.views.length > 1 && (
                    <nav className="stage__tabs" aria-label={`Secções de ${sectionDef.label}`}>
                      {sectionDef.views.map((entry) => (
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
                  <span className="stage__spacer" />
                </>
              )}
            </header>

            {gaveUp && (
              <div style={{ padding: 16 }}>
                <ErrorState
                  error={{
                    message: "O motor do Nano não respondeu.",
                    component: "ponte eel",
                    detail: "Verifica se a janela do NANO.bat continua aberta e recarrega esta página.",
                  }}
                  onRetry={() => window.location.reload()}
                />
              </div>
            )}

            {view === "chat" && (
              <>
                <Conversation messages={messages} status={activityLabel} thinking={thinking} />
                <Composer
                  value={input} onChange={setInput}
                  onSend={() => sendMessage()} onStop={stopWork}
                  onVoice={startVoice} onCancelVoice={cancelVoice}
                  onNew={newConversation}
                  thinking={thinking} disabled={!ready}
                  voiceState={readiness?.voice.state ?? "UNKNOWN"}
                  listening={listening}
                  suggestions={suggestions}
                  onSuggestion={(text) => sendMessage(text)}
                />
                <p className="stage__footer">
                  O Nano pode cometer erros. Ações sensíveis pedem sempre a tua autorização.
                </p>
              </>
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
                {view === "capabilities" && <CapabilitiesPage enabled={ready} />}
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
                    onSetMode={setMode} onSaveGroqKey={saveGroqKey} onRemoveGroqKey={removeGroqKey}
                    onTestGroq={testGroq} onSetGroqModel={setGroqModel}
                    onSetLocalModel={setLocalModel} onUpdate={updateSetting}
                    onTestSpeaker={testSpeaker} onTestMicrophone={testMicrophone}
                    onToggleEmergencyStop={toggleEmergencyStop}
                    onClearConversation={newConversation}
                    onForgetAllMemory={forgetAllMemory}
                    onNavigate={setView}
                    section={settingsSection} onSection={setSettingsSection}
                    theme={theme} onTheme={setTheme}
                    reduceMotion={reduceMotion} onReduceMotion={setReduceMotion}
                  />
                )}
              </div>
            )}
          </main>
        </div>
      </div>

      <CommandPalette open={paletteOpen} commands={commands} onClose={() => setPaletteOpen(false)} />
      <ToastStack toasts={toasts} />

      {confirmReq && (
        <ConfirmModal
          message={confirmReq.message} meta={confirmReq.meta}
          onConfirm={() => { call("confirm_action", confirmReq.requestId, true); setConfirmReq(null); }}
          onCancel={() => { call("confirm_action", confirmReq.requestId, false); setConfirmReq(null); }}
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
