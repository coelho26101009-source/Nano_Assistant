/**
 * Nano application shell.
 *
 * Layout: sidebar · workspace · inspector · status bar.
 * All state shown here is read from the Python backend through lib/backend.
 * Nothing is mocked and nothing defaults to "ready".
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Head from "next/head";

import CommandPalette, { Command } from "../components/CommandPalette";
import ConfirmModal from "../components/Confirm";
import Inspector from "../components/Inspector";
import PermissionCenterModal, { PermissionDecision } from "../components/PermissionCenterModal";
import PluginCodeModal from "../components/PluginCodeModal";
import SettingsModal from "../components/SettingsModal";
import MiniPillBar from "../components/MiniPillBar";
import Sidebar, { ViewId } from "../components/Sidebar";
import TaskDetailModal from "../components/TaskDetailModal";
import { Composer, Conversation, Message } from "../components/Conversation";
import {
  ActivityView, AgentsView, HealthView, MemoryView,
  PermissionsView, PluginsView, TasksView,
} from "../components/CommandCenter";
import { Button, ErrorState, StatusIndicator, stateLabel } from "../components/ui";
import {
  CommandCenterPayload, ReadinessPayload,
  call, expose, useBridgeReady, usePolled,
} from "../lib/backend";

export interface ConfirmRequest {
  requestId: string;
  message: string;
  meta: Record<string, any>;
}

const VIEW_TITLES: Record<ViewId, string> = {
  chat: "Conversa",
  tasks: "Tarefas",
  activity: "Atividade",
  permissions: "Permissões",
  agents: "Agentes e modelo",
  memory: "Memória",
  plugins: "Integrações",
  health: "Estado do sistema",
};

export default function Home() {
  const { ready, gaveUp } = useBridgeReady();

  const [view, setView] = useState<ViewId>("chat");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const [status, setStatus] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [toast, setToast] = useState<string | null>(null);
  // Compact overlay used when the wake word fires; kept as a real feature.
  const [miniMode, setMiniMode] = useState(false);

  const [confirmReq, setConfirmReq] = useState<ConfirmRequest | null>(null);
  const [permissionsOpen, setPermissionsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [taskDetail, setTaskDetail] = useState<any>(null);
  const [taskDetailEvents, setTaskDetailEvents] = useState<any[]>([]);
  const [taskDetailOpen, setTaskDetailOpen] = useState(false);
  const [pluginCode, setPluginCode] = useState<any>(null);

  const [plugins, setPlugins] = useState<Record<string, string[]>>({});
  const [policies, setPolicies] = useState<any[]>([]);
  const [facts, setFacts] = useState<Record<string, any> | null>(null);
  const [audioDevices, setAudioDevices] = useState<any>(null);
  const [resolving, setResolving] = useState(false);

  const { data: readiness, refresh: refreshReadiness } =
    usePolled<ReadinessPayload>("get_system_readiness", 6000, ready);
  const { data: commandCenter, loading: ccLoading, refresh: refreshCommandCenter } =
    usePolled<CommandCenterPayload>("get_command_center_state", 3000, ready);

  const toastTimer = useRef<number>();
  const notify = useCallback((text: string) => {
    setToast(text);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 2600);
  }, []);

  useEffect(() => { document.documentElement.setAttribute("data-theme", theme); }, [theme]);

  /* ── Streaming events from the backend ──────────────────────────────── */
  useEffect(() => {
    if (!ready) return;

    expose((reqId: string, msg: string, meta: any) => {
      setConfirmReq({ requestId: reqId, message: msg, meta });
    }, "on_confirm_request");

    expose(() => {
      setMiniMode(true);
      setThinking(true);
      setStatus("A ouvir o Nano…");
    }, "on_wake_detected");

    expose((msgId: string, userText?: string) => {
      setThinking(true);
      setStatus("A processar…");
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (userText && (!last || last.role !== "user" || last.content !== userText)) {
          next.push({ id: crypto.randomUUID(), role: "user", content: userText, timestamp: new Date() });
        }
        if (!next.some((m) => m.id === msgId)) {
          next.push({ id: msgId, role: "assistant", content: "", timestamp: new Date(), streaming: true });
        }
        return next;
      });
    }, "on_stream_start");

    expose((_msgId: string, text: string) => {
      setStatus(text);
      // Tool activity arrives as "_thinking_" status lines; surface the tool
      // names on the message so the person can see what actually ran.
      const match = /⚙️\s*(.+?)\.\.\./.exec(text);
      if (match) {
        const tools = match[1].split(",").map((tool) => tool.trim()).filter(Boolean);
        setMessages((prev) => {
          const next = [...prev];
          for (let i = next.length - 1; i >= 0; i -= 1) {
            if (next[i].role === "assistant") {
              next[i] = { ...next[i], tools: Array.from(new Set([...(next[i].tools ?? []), ...tools])) };
              break;
            }
          }
          return next;
        });
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
      setStatus("");
      const text = final?.text ?? final?.error;
      setMessages((prev) => {
        if (!prev.some((m) => m.id === msgId)) {
          return text ? [...prev, { id: msgId, role: "assistant", content: text, timestamp: new Date() }] : prev;
        }
        return prev.map((m) => (m.id === msgId ? { ...m, content: text ?? m.content, streaming: false } : m));
      });
    }, "on_stream_end");

    expose((userText: string, assistantText: string) => {
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: "user", content: userText, timestamp: new Date() },
        { id: crypto.randomUUID(), role: "assistant", content: assistantText, timestamp: new Date() },
      ]);
    }, "on_voice_exchange");

    call<any[]>("get_conversation_history").then((history) => {
      if (!history?.length) return;
      setMessages(history.slice(-40).map((m: any) => ({
        id: crypto.randomUUID(),
        role: m.role === "user" ? "user" : "assistant",
        content: m.content,
        timestamp: new Date(m.timestamp),
      })));
    });

    call<Record<string, string[]>>("get_loaded_plugins").then((value) => value && setPlugins(value));
    call<any[]>("list_permission_policies").then((value) => value && setPolicies(value));
    call<Record<string, any>>("get_memory_facts").then(setFacts);
    call<any>("get_audio_devices").then(setAudioDevices);
  }, [ready]);

  /* ── Keyboard shortcuts ─────────────────────────────────────────────── */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const mod = event.ctrlKey || event.metaKey;
      if (mod && event.key.toLowerCase() === "k") { event.preventDefault(); setPaletteOpen((open) => !open); }
      else if (mod && event.key.toLowerCase() === "b") { event.preventDefault(); setSidebarCollapsed((v) => !v); }
      else if (mod && event.key.toLowerCase() === "i") { event.preventDefault(); setInspectorCollapsed((v) => !v); }
      else if (mod && event.key.toLowerCase() === "n") { event.preventDefault(); newConversation(); }
      else if (mod && event.key.toLowerCase() === "m") { event.preventDefault(); startVoice(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  /* ── Actions ────────────────────────────────────────────────────────── */
  const sendMessage = useCallback((override?: string) => {
    const text = (override ?? input).trim();
    if (!text || thinking || !ready) return;
    const msgId = crypto.randomUUID();
    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "user", content: text, timestamp: new Date() },
      { id: msgId, role: "assistant", content: "", timestamp: new Date(), streaming: true },
    ]);
    if (override === undefined) setInput("");
    setThinking(true);
    setStatus("A processar…");
    setView("chat");

    call<any>("send_message", text, msgId).then((result) => {
      setThinking(false);
      setStatus("");
      const answer = result?.text ?? result?.error ?? "Sem resposta do motor do Nano.";
      setMessages((prev) => prev.map((m) => (m.id === msgId ? { ...m, content: answer, streaming: false } : m)));
      refreshCommandCenter();
    });
  }, [input, thinking, ready, refreshCommandCenter]);

  const stopWork = useCallback(() => {
    call("stop_voice");
    const current = commandCenter?.current_task;
    if (current && !["COMPLETED", "FAILED", "CANCELLED"].includes(current.status)) {
      call("cancel_agent_task", current.id).then(() => { notify("Tarefa cancelada"); refreshCommandCenter(); });
    }
    setThinking(false);
    setStatus("");
  }, [commandCenter, notify, refreshCommandCenter]);

  const startVoice = useCallback(() => {
    if (!ready || thinking) return;
    if (readiness && readiness.voice.state !== "READY") {
      notify(`Voz: ${stateLabel(readiness.voice.state)}`);
      return;
    }
    setThinking(true);
    setStatus("A ouvir…");
    call<any>("start_voice_listen").then((result) => {
      setThinking(false);
      setStatus("");
      if (result?.text) setInput(result.text);
      else if (result?.error) notify(`Voz falhou: ${result.error}`);
    });
  }, [ready, thinking, readiness, notify]);

  const newConversation = useCallback(() => {
    call("clear_conversation").then(() => {
      setMessages([]);
      setInput("");
      setView("chat");
      notify("Nova conversa");
    });
  }, [notify]);

  const openTask = useCallback((taskId: string) => {
    call<any>("get_task_detail", taskId).then((result) => {
      if (result?.ok) {
        setTaskDetail(result.task);
        setTaskDetailEvents(result.events ?? []);
        setTaskDetailOpen(true);
      } else {
        notify("Tarefa não encontrada");
      }
    });
  }, [notify]);

  const cancelTask = useCallback((taskId: string) => {
    call<any>("cancel_agent_task", taskId).then((result) => {
      notify(result?.ok ? "Tarefa cancelada" : "Não foi possível cancelar");
      refreshCommandCenter();
      setTaskDetailOpen(false);
    });
  }, [notify, refreshCommandCenter]);

  const resolvePermission = useCallback((requestId: string, decision: PermissionDecision) => {
    setResolving(true);
    call<any>("resolve_permission", requestId, decision).then((result) => {
      setResolving(false);
      if (result?.ok) {
        notify(decision === "deny" ? "Pedido recusado" : "Autorização concedida");
        refreshCommandCenter();
        refreshReadiness();
      } else {
        notify(`Recusado pela policy: ${result?.error ?? "erro"}`);
      }
    });
  }, [notify, refreshCommandCenter, refreshReadiness]);

  const toggleEmergencyStop = useCallback((enabled: boolean) => {
    call<any>("set_emergency_stop", enabled).then(() => {
      notify(enabled ? "Execução bloqueada" : "Execução retomada");
      refreshReadiness();
      refreshCommandCenter();
    });
  }, [notify, refreshReadiness, refreshCommandCenter]);

  const openPluginCode = useCallback((name: string) => {
    call<any>("get_plugin_code", name).then((result) => {
      if (result?.ok) setPluginCode(result);
      else notify("Não foi possível ler o plugin");
    });
  }, [notify]);

  /* ── Command palette ────────────────────────────────────────────────── */
  const commands: Command[] = useMemo(() => [
    { id: "new", label: "Nova conversa", hint: "Ctrl+N", run: newConversation },
    { id: "chat", label: "Ir para Conversa", run: () => setView("chat") },
    { id: "tasks", label: "Ir para Tarefas", run: () => setView("tasks") },
    { id: "activity", label: "Ir para Atividade", run: () => setView("activity") },
    { id: "perms", label: "Ir para Permissões", run: () => setView("permissions") },
    { id: "agents", label: "Ir para Agentes e modelo", run: () => setView("agents") },
    { id: "memory", label: "Ir para Memória", run: () => setView("memory") },
    { id: "plugins", label: "Ir para Integrações", run: () => setView("plugins") },
    { id: "health", label: "Ir para Estado do sistema", run: () => setView("health") },
    { id: "inspector", label: "Alternar inspector", hint: "Ctrl+I", run: () => setInspectorCollapsed((v) => !v) },
    { id: "sidebar", label: "Alternar barra lateral", hint: "Ctrl+B", run: () => setSidebarCollapsed((v) => !v) },
    { id: "theme", label: "Alternar tema", run: () => setTheme((t) => (t === "dark" ? "light" : "dark")) },
    { id: "settings", label: "Abrir definições", run: () => setSettingsOpen(true) },
    {
      id: "estop",
      label: readiness?.emergencyStop ? "Retomar execução" : "Paragem de emergência",
      run: () => toggleEmergencyStop(!readiness?.emergencyStop),
    },
  ], [newConversation, readiness, toggleEmergencyStop]);

  const pendingCount = commandCenter?.permissions?.length ?? 0;
  const agentState = readiness?.agent.state ?? (gaveUp ? "OFFLINE" : "UNKNOWN");

  return (
    <>
      <Head>
        <title>Nano</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>

      <div className="app">
        <Sidebar
          view={view}
          onView={setView}
          collapsed={sidebarCollapsed}
          onToggleCollapsed={() => setSidebarCollapsed((v) => !v)}
          counts={{ permissions: pendingCount, tasks: commandCenter?.worker?.queue_size ?? 0 }}
          agentState={agentState}
          onNewChat={newConversation}
          onSettings={() => setSettingsOpen(true)}
        />

        <main className="workspace">
          <header className="topbar">
            <span className="topbar-title">{VIEW_TITLES[view]}</span>
            <StatusIndicator state={agentState} />
            {readiness?.emergencyStop && <StatusIndicator state="OFFLINE" label="Paragem de emergência" />}
            <span className="topbar-spacer" />
            <Button variant="ghost" size="sm" onClick={() => setPaletteOpen(true)}>
              Comandos <kbd>Ctrl</kbd><kbd>K</kbd>
            </Button>
            {inspectorCollapsed && (
              <Button variant="ghost" size="sm" icon onClick={() => setInspectorCollapsed(false)} aria-label="Abrir inspector">◧</Button>
            )}
          </header>

          {gaveUp && (
            <div style={{ padding: 16 }}>
              <ErrorState message="O motor do Nano não respondeu. Verifica se o processo Python está a correr e recarrega." />
            </div>
          )}

          {view === "chat" && (
            <>
              <Conversation messages={messages} status={status} thinking={thinking} />
              <Composer
                value={input}
                onChange={setInput}
                onSend={() => sendMessage()}
                onStop={stopWork}
                onVoice={startVoice}
                thinking={thinking}
                disabled={!ready}
                voiceState={readiness?.voice.state ?? "UNKNOWN"}
                showSuggestions={messages.length === 0}
              />
            </>
          )}
          {view === "tasks" && <TasksView data={commandCenter} onOpenTask={openTask} onCancelTask={cancelTask} />}
          {view === "activity" && <ActivityView data={commandCenter} />}
          {view === "permissions" && (
            <PermissionsView data={commandCenter} policies={policies} onResolve={resolvePermission} />
          )}
          {view === "agents" && <AgentsView data={commandCenter} readiness={readiness} />}
          {view === "memory" && <MemoryView facts={facts} />}
          {view === "plugins" && <PluginsView plugins={plugins} onOpenCode={openPluginCode} />}
          {view === "health" && (
            <HealthView readiness={readiness} data={commandCenter} onToggleEmergencyStop={toggleEmergencyStop} />
          )}
        </main>

        <Inspector
          collapsed={inspectorCollapsed}
          onToggle={() => setInspectorCollapsed(true)}
          readiness={readiness}
          commandCenter={commandCenter}
          loading={ccLoading}
          onOpenTask={openTask}
          onOpenPermissions={() => setPermissionsOpen(true)}
          onCancelTask={cancelTask}
        />

        <footer className="statusbar">
          <span className="statusbar-item"><StatusIndicator state={agentState} /></span>
          <button type="button" className="statusbar-item" onClick={() => setView("agents")}>
            modelo: {readiness?.model.local.model ?? "—"} · {readiness?.model.provider ?? "—"}
          </button>
          <button type="button" className="statusbar-item" onClick={() => setView("tasks")}>
            fila: {readiness?.worker.queue_size ?? 0}
          </button>
          {pendingCount > 0 && (
            <button type="button" className="statusbar-item" onClick={() => setView("permissions")}>
              <StatusIndicator state="APPROVAL_REQUIRED" label={`${pendingCount} por autorizar`} />
            </button>
          )}
          <span className="statusbar-spacer" />
          {toast && <span className="statusbar-item" role="status">{toast}</span>}
          <span className="statusbar-item">voz: {stateLabel(readiness?.voice.state)}</span>
          <span className="statusbar-item">wake: {stateLabel(readiness?.wakeWord.state)}</span>
          <button type="button" className="statusbar-item" onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}>
            {theme === "dark" ? "escuro" : "claro"}
          </button>
        </footer>
      </div>

      {miniMode && (
        <MiniPillBar
          isThinking={thinking}
          statusText={status}
          eelReady={ready}
          theme={theme}
          onToggleTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
          onSend={(text: string) => { sendMessage(text); setMiniMode(false); }}
          onVoice={startVoice}
          onStop={() => { stopWork(); setMiniMode(false); }}
        />
      )}

      <CommandPalette open={paletteOpen} commands={commands} onClose={() => setPaletteOpen(false)} />

      {confirmReq && (
        <ConfirmModal
          message={confirmReq.message}
          meta={confirmReq.meta}
          onConfirm={() => { call("confirm_action", confirmReq.requestId, true); setConfirmReq(null); }}
          onCancel={() => { call("confirm_action", confirmReq.requestId, false); setConfirmReq(null); }}
        />
      )}

      <PermissionCenterModal
        visible={permissionsOpen}
        requests={commandCenter?.permissions ?? []}
        policies={policies}
        busy={resolving}
        onClose={() => setPermissionsOpen(false)}
        onResolve={resolvePermission}
      />

      <TaskDetailModal
        visible={taskDetailOpen}
        task={taskDetail}
        events={taskDetailEvents}
        permissions={commandCenter?.permissions ?? []}
        onClose={() => setTaskDetailOpen(false)}
        onCancel={cancelTask}
      />

      {settingsOpen && (
        <SettingsModal
          visible={settingsOpen}
          audioDevices={audioDevices}
          devices={audioDevices}
          selectedInput={null}
          selectedOutput={null}
          onSave={(inputId: number, outputId: number) => {
            call("set_input_device", inputId);
            call("set_output_device", outputId);
            setSettingsOpen(false);
            notify("Dispositivos guardados");
          }}
          onClose={() => setSettingsOpen(false)}
        />
      )}

      {pluginCode && (
        <PluginCodeModal
          pluginName={pluginCode.name}
          code={pluginCode.code}
          tools={pluginCode.tools ?? []}
          filename={pluginCode.filename}
          onClose={() => setPluginCode(null)}
        />
      )}
    </>
  );
}
