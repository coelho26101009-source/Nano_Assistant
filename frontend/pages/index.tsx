import { useState, useEffect, useRef } from "react";
import Head from "next/head";
import Sun from "../components/Sun";
import Chat from "../components/Chat";
import ConfirmModal from "../components/Confirm";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
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

interface WeatherData {
  temp: number;
  city: string;
  desc: string;
  humidity: number;
  wind: number;
  feelsLike: number;
}

export default function Home() {
  const [messages, setMessages]     = useState<Message[]>([]);
  const [input, setInput]           = useState("");
  const [isThinking, setIsThinking] = useState(false);
  const [statusText, setStatusText] = useState("");
  const [confirmReq, setConfirmReq] = useState<ConfirmRequest | null>(null);
  const [plugins, setPlugins]       = useState<Record<string, string[]>>({});
  const [eelReady, setEelReady]     = useState(false);
  const [clock, setClock]           = useState("");
  const [uptime, setUptime]         = useState(0);
  const [cmdCount, setCmdCount]     = useState(0);
  const [stats, setStats]           = useState<SystemStats>({
    cpu: 0, ram: 0, ramUsed: 0, ramTotal: 16, disk: 38, diskUsed: 180, diskTotal: 475
  });
  const [weather, setWeather]       = useState<WeatherData>({
    temp: 0, city: "Braga, PT", desc: "—", humidity: 0, wind: 0, feelsLike: 0
  });

  // ── Clock ─────────────────────────────────────────────────────────────────
  useEffect(() => {
    const tick = () => {
      const now = new Date();
      const h = now.getHours().toString().padStart(2, "0");
      const m = now.getMinutes().toString().padStart(2, "0");
      const s = now.getSeconds().toString().padStart(2, "0");
      const d = now.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
      setClock(`${h}:${m}:${s}  |  ${d}`);
      setUptime(prev => prev + 1);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  // ── Aguarda eel.js ────────────────────────────────────────────────────────
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

  // ── Eel ready: carrega dados + stats ──────────────────────────────────────
  useEffect(() => {
    if (!eelReady) return;
    const eel = (window as any).eel;

    // Guardrail callback
    eel["expose"]((reqId: string, msg: string, meta: any) => {
      setConfirmReq({ requestId: reqId, message: msg, meta });
    }, "on_confirm_request");

    // Conversas iniciadas pela wake-word aparecem no chat
    eel["expose"]((userText: string, assistantText: string) => {
      setMessages(prev => [
        ...prev,
        { id: crypto.randomUUID(), role: "user",      content: userText,      timestamp: new Date() },
        { id: crypto.randomUUID(), role: "assistant", content: assistantText, timestamp: new Date() },
      ]);
    }, "on_voice_exchange");

    // Histórico
    eel.get_conversation_history()((history: any[]) => {
      if (!history?.length) return;
      setMessages(history.slice(-40).map((m: any) => ({
        id: crypto.randomUUID(),
        role: m.role as "user" | "assistant",
        content: m.content,
        timestamp: new Date(m.timestamp),
      })));
    });

    // Plugins
    eel.get_loaded_plugins()((p: any) => {
      if (p) setPlugins(p);
    });

    // Stats do sistema (via plugin ghost_organizer)
    const fetchStats = () => {
      try {
        eel.send_message("__INTERNAL_STATS__")((result: any) => {
          // Ignora — stats vêm do widget separado
        });
      } catch(_) {}
    };

    // Busca clima via wttr.in diretamente
    fetch("https://wttr.in/Braga?format=j1&lang=pt")
      .then(r => r.json())
      .then(data => {
        const c = data.current_condition?.[0];
        if (c) {
          setWeather({
            temp: parseInt(c.temp_C),
            city: "Braga, PT",
            desc: c.weatherDesc?.[0]?.value || "—",
            humidity: parseInt(c.humidity),
            wind: parseFloat(c.windspeedKmph),
            feelsLike: parseInt(c.FeelsLikeC),
          });
        }
      }).catch(() => {});

    // Simula stats (serão substituídas por valores reais)
    const statsInterval = setInterval(() => {
      setStats(prev => ({
        ...prev,
        cpu: Math.round(Math.random() * 20 + 8),
        ram: Math.round(45 + Math.random() * 10),
        ramUsed: parseFloat((7 + Math.random()).toFixed(1)),
      }));
    }, 3000);

    return () => clearInterval(statsInterval);
  }, [eelReady]);

  // ── Formata uptime ────────────────────────────────────────────────────────
  const formatUptime = (s: number) => {
    const h = Math.floor(s / 3600).toString().padStart(2, "0");
    const m = Math.floor((s % 3600) / 60).toString().padStart(2, "0");
    const sec = (s % 60).toString().padStart(2, "0");
    return `${h}:${m}:${sec}`;
  };

  // ── Enviar mensagem ────────────────────────────────────────────────────────
  const sendMessage = () => {
    const text = input.trim();
    if (!text || isThinking || !eelReady) return;
    const eel = (window as any).eel;

    setMessages(prev => [...prev, {
      id: crypto.randomUUID(), role: "user",
      content: text, timestamp: new Date(),
    }]);
    setInput("");
    setIsThinking(true);
    setStatusText("A processar...");
    setCmdCount(prev => prev + 1);

    eel.send_message(text)((result: any) => {
      setIsThinking(false);
      setStatusText("");
      const txt = result?.text || result?.error || "Sem resposta.";
      setMessages(prev => [...prev, {
        id: crypto.randomUUID(), role: "assistant",
        content: txt, timestamp: new Date(),
      }]);
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
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
    setIsThinking(true); setStatusText("A ouvir...");
    eel.start_voice_listen()((result: any) => {
      setIsThinking(false); setStatusText("");
      if (result?.text) setInput(result.text);
    });
  };

  const clearConversation = () => {
    const eel = (window as any).eel;
    if (eel) eel.clear_conversation()(() => {});
    setMessages([]);
  };

  return (
    <>
      <Head>
        <title>H.E.L.I.O.S.</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>

      {/* Barra de título custom para Electron */}
      {typeof window !== 'undefined' && (window as any).heliosApp?.isElectron && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, height: 32,
          background: 'rgba(3,5,8,0.95)', zIndex: 10000,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          WebkitAppRegion: 'drag' as any, borderBottom: '1px solid rgba(255,184,0,0.1)'
        }}>
          <span style={{ marginLeft: 12, fontSize: 10, fontFamily: 'Orbitron,monospace',
            color: 'var(--gold)', letterSpacing: '0.2em', WebkitAppRegion: 'drag' as any }}>
            H.E.L.I.O.S.
          </span>
          <div style={{ display: 'flex', WebkitAppRegion: 'no-drag' as any }}>
            {[['─', () => (window as any).heliosApp?.minimize(), '#888'],
              ['□', () => (window as any).heliosApp?.maximize(), '#888'],
              ['✕', () => (window as any).heliosApp?.close(), '#ff5f57']
            ].map(([icon, fn, color], i) => (
              <button key={i} onClick={fn as any} style={{
                width: 46, height: 32, background: 'transparent', border: 'none',
                color: color as string, fontSize: i === 2 ? 12 : 14, cursor: 'pointer',
                transition: 'background 0.15s'
              }}
              onMouseEnter={e => (e.target as HTMLElement).style.background = i === 2 ? '#ff5f5733' : '#ffffff11'}
              onMouseLeave={e => (e.target as HTMLElement).style.background = 'transparent'}
              >{icon}</button>
            ))}
          </div>
        </div>
      )}

      <div className="app" style={typeof window !== 'undefined' && (window as any).heliosApp?.isElectron ? {paddingTop: 32} : {}}>

        {/* ══ LEFT PANEL ══ */}
        <aside className="sidebar">

          {/* System Stats */}
          <div className="widget">
            <div className="widget-header">
              <div className="widget-title">
                <span className="widget-title-icon">⚡</span>
                System Stats
              </div>
              <span className="widget-refresh">↻</span>
            </div>
            <div className="stat-bar-row">
              <span className="stat-bar-label">CPU</span>
              <div className="stat-bar-track">
                <div className="stat-bar-fill" style={{ width: `${stats.cpu}%` }} />
              </div>
              <span className="stat-bar-value">{stats.cpu}%</span>
            </div>
            <div className="stat-bar-row">
              <span className="stat-bar-label">RAM</span>
              <div className="stat-bar-track">
                <div className="stat-bar-fill" style={{ width: `${stats.ram}%` }} />
              </div>
              <span className="stat-bar-value">{stats.ram}%</span>
            </div>
            <div className="stat-cards">
              <div className="stat-card">
                <div className="stat-card-label">CPU</div>
                <div className="stat-card-value">{stats.cpu}%</div>
              </div>
              <div className="stat-card">
                <div className="stat-card-label">Memory</div>
                <div className="stat-card-value">{stats.ramUsed} GB</div>
              </div>
              <div className="stat-card">
                <div className="stat-card-label">Disk</div>
                <div className="stat-card-value">{stats.diskUsed}/{stats.diskTotal}GB</div>
              </div>
            </div>
          </div>

          {/* Weather */}
          <div className="widget">
            <div className="widget-header">
              <div className="widget-title">
                <span className="widget-title-icon">🌤</span>
                Weather
              </div>
            </div>
            <div className="weather-temp">{weather.temp}°C</div>
            <div className="weather-city">{weather.city}</div>
            <div className="weather-desc">{weather.desc}</div>
            <div className="weather-row">
              <div className="weather-item">
                <div className="weather-item-label">Humidity</div>
                <div className="weather-item-value">{weather.humidity}%</div>
              </div>
              <div className="weather-item">
                <div className="weather-item-label">Wind</div>
                <div className="weather-item-value">{weather.wind} km/h</div>
              </div>
              <div className="weather-item">
                <div className="weather-item-label">Feels</div>
                <div className="weather-item-value">{weather.feelsLike}°C</div>
              </div>
            </div>
          </div>

          {/* Arsenal */}
          <div className="widget" style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
            <div className="widget-header">
              <div className="widget-title">
                <span className="widget-title-icon">🛠</span>
                Arsenal
              </div>
              <span style={{ fontFamily: "Share Tech Mono", fontSize: 10, color: "var(--gold)" }}>
                {Object.keys(plugins).length} plugins
              </span>
            </div>
            <div className="plugin-list" style={{ flex: 1, overflow: "hidden" }}>
              {Object.entries(plugins).map(([name, tools]) => (
                <div key={name} className="plugin-item">
                  <div className="plugin-name">{name}</div>
                  {(tools as string[]).map((t: string) => (
                    <div key={t} className="plugin-tool">{t}</div>
                  ))}
                </div>
              ))}
            </div>
          </div>

          {/* Uptime */}
          <div className="widget">
            <div className="widget-header">
              <div className="widget-title">
                <span className="widget-title-icon">⏱</span>
                System Uptime
              </div>
              <span style={{ fontFamily: "Share Tech Mono", fontSize: 10, color: "var(--text-dim)" }}>
                {formatUptime(uptime)}
              </span>
            </div>
            <div className="uptime-grid">
              <div className="uptime-item">
                <div className="uptime-item-label">Session</div>
                <div className="uptime-item-value">1</div>
              </div>
              <div className="uptime-item">
                <div className="uptime-item-label">Commands</div>
                <div className="uptime-item-value">{cmdCount}</div>
              </div>
            </div>
          </div>

          {/* Eel status */}
          <div className="eel-status">
            <div className="eel-dot" style={{ background: eelReady ? "var(--green)" : "var(--red)" }} />
            <span style={{ color: eelReady ? "var(--green)" : "var(--red)" }}>
              {eelReady ? "HELIOS ONLINE" : "A LIGAR..."}
            </span>
          </div>

        </aside>

        {/* ══ CENTER ══ */}
        <main className="main">
          <div className="main-topbar" />

          <div className="main-online">
            <div className="main-online-dot" />
            ONLINE
          </div>

          <Sun isThinking={isThinking} statusText={statusText} />

          {/* Input centralizado em baixo */}
          <div className="center-input-wrapper">
            <div className="input-glass">
              <textarea
                className="input-field"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={eelReady ? "Fala comigo, Simão..." : "A ligar..."}
                rows={1}
                disabled={isThinking || !eelReady}
              />
              <button className="btn-voice" onClick={startVoice}
                disabled={!eelReady || isThinking} title="Voz">
                🎤
              </button>
              <button className="btn-send" onClick={sendMessage}
                disabled={isThinking || !input.trim() || !eelReady}>
                ➤
              </button>
            </div>
          </div>
        </main>

        {/* ══ RIGHT PANEL — Chat ══ */}
        <div className="chat-panel">
          <div className="chat-header">
            <span className="chat-header-title">Conversation</span>
            <div className="chat-header-actions">
              <button className="chat-action-btn" onClick={clearConversation}>Clear</button>
            </div>
          </div>

          <Chat messages={messages} isThinking={isThinking} />

        </div>

      </div>

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
