import { useState, useEffect } from "react";
import Head from "next/head";
import Chat from "../components/Chat";
import ConfirmModal from "../components/Confirm";
import MiniPillBar from "../components/MiniPillBar";
import PermissionCenterModal from "../components/PermissionCenterModal";
import PluginCodeModal from "../components/PluginCodeModal";
import SettingsModal from "../components/SettingsModal";
import TaskDetailModal from "../components/TaskDetailModal";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  streaming?: boolean;
}

export interface ConfirmRequest {
  requestId: string;
  message: string;
  meta: Record<string, any>;
}

interface SystemStats {
  cpu: number;
  ram: number;
  ramUsed: number;
  ramTotal: number;
  disk: number;
  diskUsed: number;
  diskTotal: number;
}

interface PluginCodeData {
  name: string;
  code: string;
  tools: string[];
  filename: string;
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isThinking, setIsThinking] = useState(false);
  const [statusText, setStatusText] = useState("");
  const [confirmReq, setConfirmReq] = useState<ConfirmRequest | null>(null);
  const [plugins, setPlugins] = useState<Record<string, string[]>>({});
  const [eelReady, setEelReady] = useState(false);
  const [stats, setStats] = useState<SystemStats>({ cpu: 0, ram: 0, ramUsed: 0, ramTotal: 0, disk: 0, diskUsed: 0, diskTotal: 0 });
  
  // Navigation & View states
  const [sidebarTab, setSidebarTab] = useState<"home" | "plugins">("home");
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [isMiniMode, setIsMiniMode] = useState(false);
  const [selectedPluginCode, setSelectedPluginCode] = useState<PluginCodeData | null>(null);
  const [settingsVisible, setSettingsVisible] = useState(false);
  const [permissionCenterVisible, setPermissionCenterVisible] = useState(false);
  const [taskDetailVisible, setTaskDetailVisible] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [taskDetail, setTaskDetail] = useState<any>(null);
  const [audioDevices, setAudioDevices] = useState<{inputs: {id: number; name: string}[]; outputs: {id: number; name: string}[] } | null>(null);
  const [selectedInput, setSelectedInput] = useState<number | null>(null);
  const [selectedOutput, setSelectedOutput] = useState<number | null>(null);
  const [commandCenter, setCommandCenter] = useState<any>(null);
  const [permissionPolicies, setPermissionPolicies] = useState<any[]>([]);

  const electronDragStyle = { WebkitAppRegion: "drag" } as React.CSSProperties;
  const electronNoDragStyle = { WebkitAppRegion: "no-drag" } as React.CSSProperties;

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme(prev => (prev === "dark" ? "light" : "dark"));
  };

  useEffect(() => {
    let attempts = 0;
    const check = setInterval(() => {
      attempts++;
      if ((window as any).eel) {
        clearInterval(check);
        setEelReady(true);
      } else if (attempts > 80) {
        clearInterval(check);
      }
    }, 100);
    return () => clearInterval(check);
  }, []);

  // Load audio devices once eel is ready
  useEffect(() => {
    if (!eelReady) return;
    const eel = (window as any).eel;
    eel.get_audio_devices()((dev: any) => {
      setAudioDevices(dev);
      // Load previously saved device ids from config if any
      const cfg = (window as any).CONFIG?.audio || {};
      if (cfg.input_device !== undefined) setSelectedInput(cfg.input_device);
      if (cfg.output_device !== undefined) setSelectedOutput(cfg.output_device);
    });
  }, [eelReady]);

  const handleSaveSettings = (inputId: number, outputId: number) => {
    const eel = (window as any).eel;
    if (eel) {
      eel.set_input_device(inputId)();
      eel.set_output_device(outputId)();
    }
    setSelectedInput(inputId);
    setSelectedOutput(outputId);
    setSettingsVisible(false);
  };

  useEffect(() => {
    if (!eelReady) return;
    const eel = (window as any).eel;

    const refreshCommandCenter = () => {
      if (!eel || !eel.get_command_center_state) return;
      eel.get_command_center_state()((data: any) => {
        if (data) setCommandCenter(data);
      });
      if (eel.list_permission_policies) {
        eel.list_permission_policies()((policies: any[]) => {
          if (policies) setPermissionPolicies(policies);
        });
      }
    };

    refreshCommandCenter();
    const commandInterval = setInterval(refreshCommandCenter, 4000);

    eel["expose"]((reqId: string, msg: string, meta: any) => {
      setConfirmReq({ requestId: reqId, message: msg, meta });
    }, "on_confirm_request");

    // Evento de ativação por Wake-word ("Nano" / "Olá Nano")
    eel["expose"](() => {
      setIsMiniMode(true);
      setIsThinking(true);
      setStatusText("A ouvir Nano...");
    }, "on_wake_detected");

    eel["expose"]((msgId: string, userText?: string) => {
      setIsThinking(true);
      setStatusText("A processar...");
      setMessages(prev => {
        const next = [...prev];
        if (userText && (next.length === 0 || next[next.length - 1].role !== "user" || next[next.length - 1].content !== userText)) {
          next.push({ id: crypto.randomUUID(), role: "user", content: userText, timestamp: new Date() });
        }
        if (!next.some(m => m.id === msgId)) {
          next.push({ id: msgId, role: "assistant", content: "", timestamp: new Date(), streaming: true });
        }
        return next;
      });
    }, "on_stream_start");

    eel["expose"]((msgId: string, status: string) => {
      setStatusText(status);
    }, "on_stream_status");

    eel["expose"]((msgId: string, chunk: string) => {
      setMessages(prev => {
        const exists = prev.some(m => m.id === msgId);
        if (!exists) {
          return [...prev, { id: msgId, role: "assistant", content: chunk, timestamp: new Date(), streaming: true }];
        }
        return prev.map(m => m.id === msgId ? { ...m, content: m.content + chunk, streaming: true } : m);
      });
    }, "on_stream_chunk");

    eel["expose"]((msgId: string, finalData: any) => {
      setIsThinking(false);
      setStatusText("");
      const finalTxt = finalData?.text || finalData?.error;
      if (finalTxt !== undefined) {
        setMessages(prev => {
          const exists = prev.some(m => m.id === msgId);
          if (!exists) {
            return [...prev, { id: msgId, role: "assistant", content: finalTxt, timestamp: new Date(), streaming: false }];
          }
          return prev.map(m => m.id === msgId ? { ...m, content: finalTxt, streaming: false } : m);
        });
      } else {
        setMessages(prev => prev.map(m => m.id === msgId ? { ...m, streaming: false } : m));
      }
    }, "on_stream_end");

    eel["expose"]((userText: string, assistantText: string) => {
      setMessages(prev => [
        ...prev,
        { id: crypto.randomUUID(), role: "user", content: userText, timestamp: new Date() },
        { id: crypto.randomUUID(), role: "assistant", content: assistantText, timestamp: new Date() },
      ]);
    }, "on_voice_exchange");

    eel.get_conversation_history()((history: any[]) => {
      if (!history?.length) return;
      setMessages(
        history.slice(-40).map((m: any) => ({
          id: crypto.randomUUID(),
          role: m.role as "user" | "assistant",
          content: m.content,
          timestamp: new Date(m.timestamp)
        }))
      );
    });

    eel.get_loaded_plugins()((p: any) => {
      if (p) setPlugins(p);
    });

    const updateStats = () => {
      try {
        eel.get_system_stats()((value: SystemStats) => {
          if (!value) return;
          setStats({
            cpu: Number(value.cpu) || 0,
            ram: Number(value.ram) || 0,
            ramUsed: Number(value.ramUsed) || 0,
            ramTotal: Number(value.ramTotal) || 0,
            disk: Number(value.disk) || 0,
            diskUsed: Number(value.diskUsed) || 0,
            diskTotal: Number(value.diskTotal) || 0,
          });
        });
      } catch (_) {}
    };
    updateStats();
    const statsInterval = setInterval(updateStats, 3000);

    return () => {
      clearInterval(statsInterval);
      clearInterval(commandInterval);
    };
  }, [eelReady]);

  const handleSendMessage = (textToSend?: string) => {
    const text = (textToSend !== undefined ? textToSend : input).trim();
    if (!text || isThinking || !eelReady) return;
    const eel = (window as any).eel;
    try {
      if (eel?.stop_voice) eel.stop_voice()(() => {});
    } catch (_) {}
    const msgId = crypto.randomUUID();
    setMessages(prev => [
      ...prev,
      { id: crypto.randomUUID(), role: "user", content: text, timestamp: new Date() },
      { id: msgId, role: "assistant", content: "", timestamp: new Date(), streaming: true }
    ]);
    if (textToSend === undefined) setInput("");
    setIsThinking(true);
    setStatusText("A processar...");

    eel.send_message(text, msgId)((result: any) => {
      setIsThinking(false);
      setStatusText("");
      const txt = result?.text || result?.error || "Sem resposta.";
      setMessages(prev => prev.map(m => m.id === msgId ? { ...m, content: txt, streaming: false } : m));
    });
  };

  const openTaskDetails = (taskId: string) => {
    const eel = (window as any).eel;
    if (!eel || !eel.get_task_detail) return;
    setSelectedTaskId(taskId);
    eel.get_task_detail(taskId)((result: any) => {
      if (result?.ok) {
        setTaskDetail(result.task);
        setTaskDetailVisible(true);
      }
    });
  };

  const handlePermissionDecision = (requestId: string, decision: "deny" | "allow_once" | "allow_for_task") => {
    const eel = (window as any).eel;
    if (!eel || !eel.resolve_permission) return;
    eel.resolve_permission(requestId, decision)((result: any) => {
      if (result?.ok) {
        setPermissionCenterVisible(false);
        if (eel.get_command_center_state) {
          eel.get_command_center_state()((data: any) => {
            if (data) setCommandCenter(data);
          });
        }
        if (eel.list_permission_policies) {
          eel.list_permission_policies()((policies: any[]) => {
            if (policies) setPermissionPolicies(policies);
          });
        }
      }
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const handleConfirm = (confirmed: boolean) => {
    if (!confirmReq) return;
    const eel = (window as any).eel;
    if (eel) eel.confirm_action(confirmReq.requestId, confirmed)(() => {});
    setConfirmReq(null);
  };

  const startVoice = () => {
    if (!eelReady || isThinking) return;
    const eel = (window as any).eel;
    try {
      if (eel?.stop_voice) eel.stop_voice()(() => {});
    } catch (_) {}
    setIsThinking(true);
    setStatusText("A ouvir...");
    eel.start_voice_listen()((result: any) => {
      setIsThinking(false);
      setStatusText("");
      if (result?.text) {
        if (isMiniMode) {
          handleSendMessage(result.text);
        } else {
          setInput(result.text);
        }
      }
    });
  };

  const handleStopVoice = () => {
    const eel = (window as any).eel;
    if (eel?.stop_voice) eel.stop_voice()(() => {});
    setIsThinking(false);
    setStatusText("");
  };

  const startNewChat = () => {
    const eel = (window as any).eel;
    if (eel) eel.clear_conversation()(() => {});
    setMessages([]);
    setInput("");
  };

  const handleOpenPluginCode = (pluginName: string) => {
    const eel = (window as any).eel;
    if (!eel || !eel.get_plugin_code) {
      setSelectedPluginCode({
        name: pluginName,
        code: `# Código do plugin ${pluginName}\n# A carregar...`,
        tools: plugins[pluginName] || [],
        filename: `${pluginName}.py`
      });
      return;
    }
    eel.get_plugin_code(pluginName)((res: any) => {
      if (res && res.ok) {
        setSelectedPluginCode({
          name: res.name,
          code: res.code,
          tools: res.tools || [],
          filename: res.filename || `${pluginName}.py`
        });
      } else {
        setSelectedPluginCode({
          name: pluginName,
          code: `# Não foi possível ler ${pluginName}.py\n# ${res?.error || "Erro desconhecido"}`,
          tools: plugins[pluginName] || [],
          filename: `${pluginName}.py`
        });
      }
    });
  };

  const quickPrompts = [
    { label: "Codigo", prompt: "Escreve uma funcao Python para automatizar tarefas no Windows." },
    { label: "Aprender", prompt: "Explica de forma concisa como funcionam os modelos de linguagem locais." },
    { label: "Dia a dia", prompt: "Quais sao as minhas tarefas e lembretes para hoje?" },
    { label: "Escrever", prompt: "Redige um email profissional e direto." },
    { label: "Surpreende-me", prompt: "Da-me uma sugestao de automacao util para o meu computador." },
  ];

  return (
    <>
      <Head>
        <title>Nano</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>

      {/* Electron Header */}
      {typeof window !== "undefined" && (window as any).nanoApp?.isElectron && (
        <div style={{
          position: "fixed", top: 0, left: 0, right: 0, height: 32,
          background: "var(--bg-sidebar)", zIndex: 10000, display: "flex",
          alignItems: "center", justifyContent: "space-between", ...electronDragStyle,
          borderBottom: "1px solid var(--border)"
        }}>
          <span style={{
            marginLeft: 14, fontSize: 11, fontWeight: 600,
            color: "var(--teal)", ...electronDragStyle
          }}>
            Nano
          </span>
          <div style={{ display: "flex", ...electronNoDragStyle }}>
            {["─", "□", "✕"].map((icon, i) => (
              <button
                key={icon}
                onClick={[
                  () => (window as any).nanoApp?.minimize(),
                  () => (window as any).nanoApp?.maximize(),
                  () => (window as any).nanoApp?.close()
                ][i]}
                style={{
                  width: 46, height: 32, background: "transparent", border: "none",
                  color: i === 2 ? "#ff5f57" : "var(--text-muted)", fontSize: i === 2 ? 12 : 14,
                  cursor: "pointer", ...electronNoDragStyle
                }}
              >
                {icon}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Barra Compacta / Modo 2º Plano no topo */}
      {isMiniMode && (
        <MiniPillBar
          isThinking={isThinking}
          statusText={statusText}
          onSend={(txt) => handleSendMessage(txt)}
          onVoice={startVoice}
          onStop={handleStopVoice}
          theme={theme}
          onToggleTheme={toggleTheme}
          eelReady={eelReady}
        />
      )}

      {/* Layout Completo (Vuxio style) */}
      <div
        className="nano-app-layout"
        style={{
          display: isMiniMode ? "none" : "flex",
          paddingTop: typeof window !== "undefined" && (window as any).nanoApp?.isElectron ? 32 : 0
        }}
      >
        {/* ── BARRA LATERAL (SIDEBAR) ── */}
        <aside className="nano-sidebar">
          <div className="sidebar-header">
            <div className="brand-logo">
              <div className="brand-mark">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="#0A0D12">
                  <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
                </svg>
              </div>
              <span className="brand-name">Nano</span>
            </div>
            {/* Settings trigger */}
            <button className="sidebar-settings-btn" onClick={() => setSettingsVisible(true)}>⚙</button>
          </div>

          <div className="sidebar-nav-tabs">
            <button
              type="button"
              className={`sidebar-nav-btn ${sidebarTab === "home" ? "active" : ""}`}
              onClick={() => setSidebarTab("home")}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                <polyline points="9 22 9 12 15 12 15 22" />
              </svg>
              Home
            </button>
            <button
              type="button"
              className={`sidebar-nav-btn ${sidebarTab === "plugins" ? "active" : ""}`}
              onClick={() => setSidebarTab("plugins")}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="16 18 22 12 16 6" />
                <polyline points="8 6 2 12 8 18" />
              </svg>
              Plugins
            </button>
          </div>

          <button type="button" className="btn-new-chat" onClick={startNewChat}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            Nova Conversa
          </button>

          {/* Conteúdo da Sidebar */}
          <div className="sidebar-content">
            {sidebarTab === "home" ? (
              <>
                <div className="section-label">Recentes</div>
                {messages.length > 0 ? (
                  <div className="recent-item active">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                    </svg>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                      {messages[0]?.content?.slice(0, 24) || "Conversa ativa"}...
                    </span>
                  </div>
                ) : (
                  <div className="recent-item" onClick={startNewChat}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                    </svg>
                    <span>Nova sessão...</span>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="section-label">Arsenal ({Object.keys(plugins).length} plugins)</div>
                {Object.entries(plugins).map(([name, tools]) => (
                  <div
                    key={name}
                    className="plugin-card"
                    onClick={() => handleOpenPluginCode(name)}
                    title="Clica para ver o código-fonte deste plugin"
                  >
                    <div className="plugin-card-header">
                      <span className="plugin-card-title">{name}</span>
                      <span className="plugin-card-badge">{tools.length}</span>
                    </div>
                    <div className="plugin-card-tools">
                      {tools.join(" · ")}
                    </div>
                  </div>
                ))}
              </>
            )}
          </div>

          {/* Rodapé da Sidebar: Uso do Sistema & Conta */}
          <div className="sidebar-footer">
            <div className="system-stats-compact">
              <div className="stat-row">
                <span>CPU {stats.cpu}%</span>
                <div className="stat-track">
                  <div className="stat-fill" style={{ width: `${Math.min(100, stats.cpu)}%` }} />
                </div>
              </div>
              <div className="stat-row">
                <span>RAM {stats.ramUsed}/{stats.ramTotal}GB</span>
                <div className="stat-track">
                  <div className="stat-fill" style={{ width: `${Math.min(100, stats.ram)}%` }} />
                </div>
              </div>
            </div>

            <div className="user-profile-row">
              <div className="user-avatar-info">
                <div className="user-avatar">S</div>
                <div>
                  <div className="user-name-text">Simão Coelho</div>
                  <div className="user-status-text">
                    {eelReady ? "● Online" : "○ A ligar..."}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </aside>

        {/* ── ÁREA DE TRABALHO PRINCIPAL ── */}
        <main className="nano-workspace">
          <div className="workspace-topbar">
            <button
              type="button"
              className="topbar-btn"
              onClick={toggleTheme}
              title="Alternar tema"
            >
              {theme === "dark" ? "Tema Claro" : "Tema Escuro"}
            </button>

            <button
              type="button"
              className="topbar-btn"
              onClick={() => setIsMiniMode(true)}
              title="Mudar para barra compacta no topo"
            >
              Modo Barra
            </button>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, margin: '16px 0' }}>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 14, padding: 16 }}>
              <div style={{ color: 'var(--text-muted)', fontSize: 12, textTransform: 'uppercase', letterSpacing: 1 }}>Current task</div>
              <div style={{ fontSize: 18, fontWeight: 700, marginTop: 8 }}>{commandCenter?.current_task?.title || 'No active task'}</div>
              <div style={{ color: 'var(--text-muted)', marginTop: 4 }}>{commandCenter?.current_task?.status || 'idle'}</div>
              <div style={{ marginTop: 12, fontSize: 12, color: 'var(--text-muted)' }}>Progress {commandCenter?.current_task?.progress ?? 0}%</div>
              {commandCenter?.current_task && (
                <button type="button" onClick={() => openTaskDetails(commandCenter.current_task.id)} style={{ marginTop: 12, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 8, padding: '8px 10px', cursor: 'pointer' }}>
                  View task detail
                </button>
              )}
            </div>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 14, padding: 16 }}>
              <div style={{ color: 'var(--text-muted)', fontSize: 12, textTransform: 'uppercase', letterSpacing: 1 }}>Queue</div>
              <div style={{ marginTop: 8, display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
                {Object.entries(commandCenter?.task_summary || {}).map(([key, value]) => (
                  <div key={key} style={{ background: 'var(--bg-sidebar)', borderRadius: 10, padding: '8px 10px' }}>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{String(key)}</div>
                    <div style={{ fontSize: 18, fontWeight: 700 }}>{Number(value) || 0}</div>
                  </div>
                ))}
              </div>
            </div>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 14, padding: 16 }}>
              <div style={{ color: 'var(--text-muted)', fontSize: 12, textTransform: 'uppercase', letterSpacing: 1 }}>Permissions</div>
              <div style={{ marginTop: 8, color: 'var(--text-muted)' }}>{(commandCenter?.permissions || []).length ? `${(commandCenter?.permissions || []).length} pending` : 'No pending requests'}</div>
              {(commandCenter?.permissions || []).slice(0, 2).map((perm: any) => (
                <div key={perm.id || perm.action} style={{ marginTop: 8, background: 'var(--bg-sidebar)', borderRadius: 10, padding: 8, fontSize: 12 }}>
                  <div style={{ fontWeight: 700 }}>{perm.action}</div>
                  <div>{perm.target}</div>
                  <div style={{ color: 'var(--text-muted)' }}>{perm.risk}</div>
                </div>
              ))}
              <button type="button" onClick={() => setPermissionCenterVisible(true)} style={{ marginTop: 12, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 8, padding: '8px 10px', cursor: 'pointer', width: '100%' }}>
                Open permission center
              </button>
            </div>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 14, padding: 16 }}>
              <div style={{ color: 'var(--text-muted)', fontSize: 12, textTransform: 'uppercase', letterSpacing: 1 }}>System</div>
              <div style={{ marginTop: 8, fontSize: 13 }}>CPU {stats.cpu}% · RAM {stats.ram}% · Disk {stats.disk}%</div>
              <div style={{ marginTop: 6, fontSize: 12, color: 'var(--text-muted)' }}>Worker: {commandCenter?.worker?.running ? 'RUNNING' : 'IDLE'}</div>
            </div>
          </div>

          <div style={{ marginBottom: 12, display: 'grid', gridTemplateColumns: '1.2fr 0.8fr', gap: 12 }}>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 14, padding: 16 }}>
              <div style={{ color: 'var(--text-muted)', fontSize: 12, textTransform: 'uppercase', letterSpacing: 1 }}>Recent activity</div>
              <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                {(commandCenter?.activities || []).slice(0, 8).map((event: any, idx: number) => (
                  <div key={`${event.event}-${idx}`} style={{ fontSize: 13, color: 'var(--text-muted)' }}>
                    • {event.event}
                  </div>
                ))}
              </div>
            </div>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 14, padding: 16 }}>
              <div style={{ color: 'var(--text-muted)', fontSize: 12, textTransform: 'uppercase', letterSpacing: 1 }}>Agents</div>
              <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                {(commandCenter?.agents?.agents || []).slice(0, 6).map((agent: any) => (
                  <div key={agent.name} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 13 }}>
                    <span>{agent.name}</span>
                    <span style={{ color: agent.status === 'online' ? 'var(--green)' : 'var(--text-muted)' }}>{agent.status}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 0.8fr', gap: 12, marginBottom: 12 }}>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 14, padding: 16 }}>
              <div style={{ color: 'var(--text-muted)', fontSize: 12, textTransform: 'uppercase', letterSpacing: 1 }}>Task detail</div>
              <div style={{ marginTop: 10, fontWeight: 700 }}>{commandCenter?.current_task?.title || 'No active task'}</div>
              <div style={{ marginTop: 6, color: 'var(--text-muted)', fontSize: 13 }}>Status: {commandCenter?.current_task?.status || 'idle'}</div>
              <div style={{ marginTop: 6, color: 'var(--text-muted)', fontSize: 13 }}>Progress: {commandCenter?.current_task?.progress ?? 0}%</div>
              {commandCenter?.current_task && (
                <button type="button" onClick={() => openTaskDetails(commandCenter.current_task.id)} style={{ marginTop: 12, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 8, padding: '8px 10px', cursor: 'pointer' }}>
                  Open detail panel
                </button>
              )}
            </div>
            <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 14, padding: 16 }}>
              <div style={{ color: 'var(--text-muted)', fontSize: 12, textTransform: 'uppercase', letterSpacing: 1 }}>Permission center</div>
              <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                {permissionPolicies.length ? permissionPolicies.slice(0, 4).map((policy: any) => (
                  <div key={policy.capability} style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                    {policy.capability} · {policy.decision} · {policy.scope}
                  </div>
                )) : <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>No custom permission policies yet.</div>}
                <button type="button" onClick={() => setPermissionCenterVisible(true)} style={{ marginTop: 8, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 8, padding: '8px 10px', cursor: 'pointer' }}>
                  Review permissions
                </button>
              </div>
            </div>
          </div>

          {messages.length === 0 ? (
            /* Hero Central Inspirado no Vuxio */
            <div className="hero-container">
              <div className="hero-heading">
                <div className="hero-symbol" />
                <h1 className="hero-title">Olá de novo, Simão</h1>
              </div>

              <p className="hero-subtitle">
                O teu assistente pessoal local-first. Privado, seguro e super-rápido.
                <br />
                Controla o teu computador com voz, texto e automações inteligentes.
              </p>

              <div className="prompt-card">
                <textarea
                  className="prompt-textarea"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={eelReady ? "Como te posso ajudar hoje?" : "A inicializar Nano..."}
                  rows={2}
                  disabled={isThinking || !eelReady}
                />

                <div className="prompt-card-bottom">
                  <div className="prompt-card-tools">
                    <button
                      type="button"
                      className="prompt-action-icon"
                      onClick={startVoice}
                      disabled={!eelReady || isThinking}
                      title="Comando de Voz"
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                        <line x1="12" y1="19" x2="12" y2="23" />
                      </svg>
                    </button>

                    {isThinking && (
                      <button
                        type="button"
                        className="topbar-btn"
                        onClick={handleStopVoice}
                        title="Parar de Falar / Cancelar"
                        style={{ color: "var(--red)", borderColor: "var(--red)" }}
                      >
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                          <rect x="6" y="6" width="12" height="12" rx="2" />
                        </svg>
                        Parar
                      </button>
                    )}

                    <span className="prompt-model-badge">Groq Llama 3.3 · Local Ollama</span>
                  </div>

                  <button
                    type="button"
                    className="prompt-send-btn"
                    onClick={() => handleSendMessage()}
                    disabled={isThinking || !input.trim() || !eelReady}
                  >
                    Enviar
                  </button>
                </div>
              </div>

              <div className="suggestion-pills-row">
                {quickPrompts.map((item) => (
                  <button
                    key={item.label}
                    type="button"
                    className="suggestion-pill"
                    onClick={() => handleSendMessage(item.prompt)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>

              <div className="hero-footer-note">
                Nano Assistant · Respostas rápidas com suporte a plugins locais e cloud.
              </div>
            </div>
          ) : (
            /* Fluxo de Conversação com Chat e Input Fixo */
            <>
              <div className="chat-flow-container">
                <Chat messages={messages} isThinking={isThinking} />
              </div>

              <div className="chat-bottom-input-fixed">
                <div className="prompt-card">
                  <textarea
                    className="prompt-textarea"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="Pergunta ao Nano..."
                    rows={1}
                    disabled={isThinking || !eelReady}
                  />
                  <div className="prompt-card-bottom">
                    <div className="prompt-card-tools">
                      <button
                        type="button"
                        className="prompt-action-icon"
                        onClick={startVoice}
                        disabled={!eelReady || isThinking}
                        title="Voz"
                      >
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                          <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                          <line x1="12" y1="19" x2="12" y2="23" />
                        </svg>
                      </button>

                      {isThinking && (
                        <button
                          type="button"
                          className="topbar-btn"
                          onClick={handleStopVoice}
                          title="Parar de Falar / Cancelar"
                          style={{ color: "var(--red)", borderColor: "var(--red)" }}
                        >
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                            <rect x="6" y="6" width="12" height="12" rx="2" />
                          </svg>
                          Parar
                        </button>
                      )}

                      <span className="prompt-model-badge">Groq / Ollama</span>
                    </div>
                    <button
                      type="button"
                      className="prompt-send-btn"
                      onClick={() => handleSendMessage()}
                      disabled={isThinking || !input.trim() || !eelReady}
                    >
                      Enviar
                    </button>
                  </div>
                </div>
              </div>
            </>
          )}
        </main>
      </div>

      {/* Modal de Configurações */}
      {settingsVisible && (
        <SettingsModal
          visible={settingsVisible}
          audioDevices={audioDevices}
          selectedInput={selectedInput}
          selectedOutput={selectedOutput}
          onSave={handleSaveSettings}
          onClose={() => setSettingsVisible(false)}
        />
      )}

      {/* Modal de Exibição de Código de Plugin */}
      {selectedPluginCode && (
        <PluginCodeModal
          pluginName={selectedPluginCode.name}
          code={selectedPluginCode.code}
          tools={selectedPluginCode.tools}
          filename={selectedPluginCode.filename}
          onClose={() => setSelectedPluginCode(null)}
        />
      )}

      <PermissionCenterModal
        visible={permissionCenterVisible}
        requests={(commandCenter?.permissions || []).map((perm: any) => ({
          ...perm,
          id: perm.id || perm.request_id || `${perm.action}-${perm.task_id || "unknown"}`,
          request_id: perm.id || perm.request_id || `${perm.action}-${perm.task_id || "unknown"}`,
          action: perm.action || perm.capability,
          capability: perm.capability || perm.action,
          task_id: perm.task_id || commandCenter?.current_task?.id || "-",
          risk: perm.risk || "medium",
          target: perm.target || "workspace",
          reason: perm.reason || "Requested by current task.",
          args: perm.args || {},
        }))}
        policies={permissionPolicies}
        onClose={() => setPermissionCenterVisible(false)}
        onResolve={handlePermissionDecision}
      />

      <TaskDetailModal
        visible={taskDetailVisible}
        task={taskDetail || commandCenter?.current_task}
        events={commandCenter?.activities || []}
        onClose={() => setTaskDetailVisible(false)}
      />

      {/* Modal de Guardrail / Confirmação de Ações */}
      {confirmReq && (
        <ConfirmModal
          message={confirmReq.message}
          meta={confirmReq.meta}
          onConfirm={() => handleConfirm(true)}
          onCancel={() => handleConfirm(false)}
        />
      )}
    </>
  );
}
