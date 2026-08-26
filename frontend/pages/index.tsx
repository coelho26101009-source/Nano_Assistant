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
import SettingsPage from "../components/SettingsPage";
import TaskDetailModal from "../components/TaskDetailModal";
import TopNav, { SECTIONS, ViewId, sectionEntry, sectionOf, viewEntry } from "../components/TopNav";
import NanoLogo from "../components/NanoLogo";
import { Composer, Conversation, Message, ToolEvent } from "../components/Conversation";
import {
  ActivityFilter, ActivityPage, AgentsPage, IntegrationsPage, MemoryPage,
  PermissionsPage, StatusPage, TaskScope, TasksPage,
} from "../components/Pages";
import { Button, ErrorState, ToastStack, stateLabel, useToasts } from "../components/ui";
import {
  ActivityEvent, CommandCenterPayload, POLL, ProviderPayload, ReadinessPayload,
  SettingsPayload, TaskCounts, TaskRow, VoiceDiagnostics,
  call, expose, useBridgeReady, useFetch, usePolled,
} from "../lib/backend";
import {
  HistoryMessage, Session, recordSessionBreak, splitSessions,
} from "../lib/conversations";
import { useIsDesktop } from "../lib/desktop";

const APP_VERSION = "v1.0";

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

  /* ── Conversation list ──────────────────────────────────────────────────
   * `history` is the stored message log; the rail derives conversations from
   * it. `reading` is set only while the user is looking at an older one, which
   * is read-only because the Brain's context holds the live conversation and
   * nothing else. */
  const [history, setHistory] = useState<HistoryMessage[] | null>(null);
  const [sessionBreaks, setSessionBreaks] = useState<number[]>([]);
  const [reading, setReading] = useState<Session | null>(null);
  const [chatQuery, setChatQuery] = useState("");
  const [profileName, setProfileName] = useState<string | null>(null);
  const [messageCount, setMessageCount] = useState<number | null>(null);

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
  const [activityFilter, setActivityFilter] = useState<ActivityFilter>("all");
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
  const { data: activity, loading: activityLoading, refresh: refreshActivity } =
    useFetch<ActivityEvent[]>("get_activity", ready && view === "activity", [activityFilter], activityFilter, 120);
  const { data: memoryData, loading: memoryLoading, refresh: refreshMemory } =
    useFetch<any>("get_memory_overview", ready && view === "memory", []);
  const { data: agents } = useFetch<any[]>("get_agents_detail", ready && view === "agents", []);
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

  /* ── The stored conversation log ────────────────────────────────────────
   * Re-read after anything that appends to it, so the rail is never a stale
   * picture of what is on disk. */
  const reloadHistory = useCallback(async () => {
    const stored = await call<HistoryMessage[]>("get_conversation_history");
    if (stored) setHistory(stored);
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
      reloadHistory();
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
      reloadHistory();
    }, "on_voice_exchange");

    call<HistoryMessage[]>("get_conversation_history").then((stored) => {
      if (!stored?.length) { setHistory([]); return; }
      setHistory(stored);
      // The live conversation is the tail of the log: exactly the messages the
      // Brain still holds in its context window.
      const live = splitSessions(stored)[0];
      if (!live) return;
      setMessages(live.messages.map((m, index) => ({
        id: `stored:${m.timestamp}:${index}`,
        role: m.role === "user" ? "user" : "assistant",
        content: m.content,
        timestamp: new Date(m.timestamp),
      })));
    });

    // The user's own name, if the backend actually knows one. There is no
    // fallback initials: an invented "PA" in the corner would be a claim about
    // the user that nothing measured.
    call<any>("get_memory_overview").then((overview) => {
      if (!overview) return;
      setMessageCount(typeof overview.messageCount === "number" ? overview.messageCount : null);
      const profile = overview.profile ?? {};
      const raw = profile.name ?? profile.nome ?? profile.user_name;
      const value = raw && typeof raw === "object" ? raw.value : raw;
      if (typeof value === "string" && value.trim()) setProfileName(value.trim());
    });

    call<Record<string, string[]>>("get_loaded_plugins").then((v) => v && setPlugins(v));
    call<any[]>("list_permission_policies").then((v) => v && setPolicies(v));
  }, [ready, notify, refreshCC, refreshCounts, reloadHistory]);

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
    if (!text || thinking || !ready || reading) return;
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
  }, [input, thinking, ready, reading, notify]);

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
    if (!ready || thinking || reading) return;
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
  }, [ready, thinking, reading, readiness, notify]);

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
   * The moment is remembered for this session: the Brain forgets its context
   * immediately, but the stored log has no idea a boundary happened, and
   * without the mark a fresh conversation started minutes after the last one
   * would fold straight back into it in the rail. See lib/conversations.ts for
   * why it is not persisted across reloads.
   */
  const newConversation = useCallback(() => {
    call("clear_conversation").then(() => {
      setSessionBreaks((prev) => recordSessionBreak(prev));
      setMessages([]);
      setInput("");
      setReading(null);
      setView("chat");
      notify("Nova conversa");
    });
  }, [notify]);

  const copyConversation = useCallback(async () => {
    const source = reading
      ? reading.messages.map((m) => ({ role: m.role, content: m.content }))
      : messages.map((m) => ({ role: m.role, content: m.content }));
    const text = source
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
  }, [reading, messages, notify]);

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

  const forgetFact = useCallback((key: string) => {
    call<any>("forget_memory_fact", key).then((result) => {
      notify(result?.ok ? "Facto esquecido" : "Não foi possível esquecer", result?.ok ? "success" : "error");
      refreshMemory();
    });
  }, [notify, refreshMemory]);

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

  const sessions = useMemo(
    () => splitSessions(history, sessionBreaks),
    [history, sessionBreaks],
  );
  /* The live conversation is the newest stored one, and only while the shell
     actually holds it. Straight after "Nova conversa" there are no messages
     yet, so nothing is live and the rail highlights nothing. */
  const liveSessionId = messages.length > 0 ? (sessions[0]?.id ?? null) : null;

  /** Open a conversation from the rail. The live one is also how you get back. */
  const openSession = useCallback((session: Session) => {
    setReading((current) => {
      if (session.id === liveSessionId) return null;
      return current?.id === session.id ? null : session;
    });
    setView("chat");
    if (narrow) setRailOpen(false);
  }, [liveSessionId, narrow]);

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

  /** The messages actually on screen: the live conversation, or the one being read. */
  const shownMessages: Message[] = useMemo(() => {
    if (!reading) return messages;
    return reading.messages.map((m, index) => ({
      id: `read:${reading.id}:${index}`,
      role: m.role === "user" ? "user" : "assistant",
      content: m.content,
      timestamp: new Date(m.timestamp),
    }));
  }, [reading, messages]);

  /* The Brain's context window holds the live conversation only, so a message
     typed while reading an older one would be answered against the wrong
     history. Saying so is better than letting the user find out. */
  const readOnlyReason = reading
    ? "Volta à conversa atual para escrever."
    : undefined;

  const chatTitle = reading
    ? reading.title
    : messages.length
      ? (sessions[0]?.title ?? "Conversa")
      : "Nova conversa";

  const chatSubtitle = reading
    ? `${reading.messages.length} mensagens · arquivada`
    : messages.length
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
          routeLabel={routeLabel}
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
              sessions={sessions}
              liveId={liveSessionId}
              openId={reading ? reading.id : liveSessionId}
              query={chatQuery} onQuery={setChatQuery}
              onNew={newConversation}
              onOpen={openSession}
              loading={history === null}
              messageCount={messageCount}
              onOpenMemory={() => setView("memory")}
              drawer={railDocked ? "docked" : "open"}
              onCloseDrawer={() => setRailOpen(false)}
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
                    {reading && (
                      <Button size="sm" onClick={() => setReading(null)}>Voltar à conversa atual</Button>
                    )}
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
                <Conversation messages={shownMessages} status={activityLabel} thinking={thinking && !reading} />
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
                  readOnlyReason={readOnlyReason}
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
                  <ActivityPage events={activity} filter={activityFilter}
                                onFilter={setActivityFilter} loading={activityLoading} />
                )}
                {view === "permissions" && (
                  <PermissionsPage
                    pending={commandCenter?.permissions ?? []} policies={policies}
                    auditEvents={permissionAuditEvents} onResolve={resolvePermission} busy={resolving}
                  />
                )}
                {view === "agents" && <AgentsPage agents={agents} />}
                {view === "memory" && (
                  <MemoryPage memory={memoryData} onForget={forgetFact} loading={memoryLoading} />
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
                    onTestGroq={testGroq} onSetGroqModel={setGroqModel} onUpdate={updateSetting}
                    onTestSpeaker={testSpeaker} onTestMicrophone={testMicrophone}
                    onToggleEmergencyStop={toggleEmergencyStop}
                    onClearConversation={newConversation}
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
