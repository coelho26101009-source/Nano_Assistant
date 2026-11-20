/**
 * NANO on the phone. One link to the PC, one conversation, four tabs:
 * Chat, Agenda, Memória, Definições. First run goes through Onboarding.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { App as CapApp } from "@capacitor/app";
import Icon, { type IconName } from "./components/Icon";
import { NanoMark } from "./components/Brand";
import { useChat } from "./lib/chat";
import { t, type LanguageChoice } from "./lib/i18n";
import { NanoLink, RpcError, type LinkState } from "./lib/link";
import { isNative, notify, speak, syncReminders } from "./lib/native";
import { setPref } from "./lib/storage";
import { closeTopOverlay } from "./lib/overlay";
import AgendaScreen, { parseLocal, type AgendaData } from "./screens/AgendaScreen";
import ChatScreen from "./screens/ChatScreen";
import MemoryScreen from "./screens/MemoryScreen";
import Onboarding from "./screens/Onboarding";
import SettingsScreen, { type SpeakMode, type ThemeChoice } from "./screens/SettingsScreen";
import { UpdateBanner, useAutomaticUpdateCheck } from "./components/Updates";

type Tab = "chat" | "agenda" | "memory" | "settings";
type Conversation = { id: string; title: string; pinned: boolean };

const TABS: { id: Tab; icon: IconName; label: () => string }[] = [
  { id: "chat", icon: "chat", label: () => t("Chat") },
  { id: "agenda", icon: "calendar", label: () => t("Agenda") },
  { id: "memory", icon: "memory", label: () => t("Memória") },
  { id: "settings", icon: "settings", label: () => t("Definições") },
];

export function applyTheme(theme: ThemeChoice) {
  const light = theme === "light" || (theme === "system" && window.matchMedia?.("(prefers-color-scheme: light)").matches);
  document.documentElement.dataset.theme = light ? "light" : "dark";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", light ? "#FFFFFF" : "#181818");
}

export default function App({ initial }: { initial: { theme: ThemeChoice; language: LanguageChoice; speak: SpeakMode } }) {
  const link = useMemo(() => new NanoLink(), []);
  const [ready, setReady] = useState(false);
  const [paired, setPaired] = useState(false);
  const [state, setState] = useState<LinkState>("unpaired");
  const [tab, setTab] = useState<Tab>("chat");
  const [drawer, setDrawer] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [displayName, setDisplayName] = useState("");
  const [cameraReady, setCameraReady] = useState(false);
  const [agenda, setAgenda] = useState<AgendaData | null>(null);
  const [remindersOnPhone, setRemindersOnPhone] = useState(0);
  const [toastText, setToastText] = useState("");
  const [speakMode, setSpeakModeState] = useState<SpeakMode>(initial.speak);
  const [theme, setThemeState] = useState<ThemeChoice>(initial.theme);
  const voiceTurn = useRef(false);
  const visible = useRef(true);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const toast = useCallback((text: string) => {
    setToastText(text);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToastText(""), 2600);
  }, []);

  // A new test build on GitHub, found by the automatic check (Updates.tsx).
  const updateOffer = useAutomaticUpdateCheck();
  const [updateDismissed, setUpdateDismissed] = useState(false);

  const refreshAgenda = useCallback(async () => {
    try {
      const data = await link.call<AgendaData>("agenda.get", { days: 30 });
      setAgenda(data);
      const reminders = (data.reminders ?? []).flatMap((r) => {
        const at = parseLocal(r.due_at);
        return at ? [{ id: Number(r.id), text: r.text, at }] : [];
      });
      setRemindersOnPhone(await syncReminders(reminders));
    } catch { /* offline: the phone keeps the reminders it already scheduled */ }
  }, [link]);

  const chat = useChat(link, {
    onAnswer: (text) => {
      const wanted = speakMode === "always" || (speakMode === "voice" && voiceTurn.current);
      voiceTurn.current = false;
      if (wanted && visible.current) void speak(text);
      if (!visible.current) void notify("NANO", text);
    },
    onNotice: (title, text) => { if (!visible.current) void notify(title, text); },
    onReminder: (text) => { if (visible.current) toast(`⏰ ${text}`); },
    onTurnDone: () => { void refreshAgenda(); },
  });

  // NANO asked to see through the camera: its card is in the chat.
  const cameraAsks = chat.cameraRequests.length;
  useEffect(() => { if (cameraAsks > 0) { setDrawer(false); setTab("chat"); } }, [cameraAsks]);

  const connect = useCallback(async () => {
    await link.syncCursor();
    await chat.loadHistory();
    link.start();
    try {
      const hello = await link.call<{ displayName?: string; pcName?: string; features?: string[] }>("hello");
      setDisplayName(hello.displayName || "");
      setCameraReady(Array.isArray(hello.features) && hello.features.includes("camera"));
    } catch { /* shown by the connection state */ }
    void refreshAgenda();
  }, [link, chat, refreshAgenda]);

  useEffect(() => {
    const off = link.onState(setState);
    void (async () => {
      const has = await link.load();
      setPaired(has);
      setState(link.state);
      setReady(true);
      if (has) await connect();
    })();
    return () => { off(); link.stop(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [link]);

  // Back in the foreground: catch up with what happened on the PC meanwhile.
  //
  // `chat` is a new object on every render, so it is read through a ref: with
  // it in the dependencies this effect re-ran on every render, and because the
  // back-button handle arrives asynchronously, a cleanup that ran first left
  // the old listener behind. One press then closed the drawer, switched tab
  // and minimised the app at once.
  const loadHistoryRef = useRef(chat.loadHistory);
  loadHistoryRef.current = chat.loadHistory;
  useEffect(() => {
    const onVisible = () => {
      visible.current = document.visibilityState === "visible";
      if (visible.current && link.paired) { link.start(); void loadHistoryRef.current(); void refreshAgenda(); }
    };
    document.addEventListener("visibilitychange", onVisible);
    let disposed = false;
    let remove: (() => void) | undefined;
    if (isNative()) {
      void CapApp.addListener("backButton", () => {
        if (closeTopOverlay()) return;
        if (drawerRef.current) setDrawer(false);
        else if (tabRef.current !== "chat") setTab("chat");
        else void CapApp.minimizeApp();
      }).then((handle) => {
        if (disposed) void handle.remove();
        else remove = () => void handle.remove();
      });
    }
    return () => { disposed = true; document.removeEventListener("visibilitychange", onVisible); remove?.(); };
  }, [link, refreshAgenda]);

  const drawerRef = useRef(drawer);
  drawerRef.current = drawer;
  const tabRef = useRef(tab);
  tabRef.current = tab;

  const openDrawer = async () => {
    setDrawer(true);
    try {
      const result = await link.call<{ conversations: Conversation[] }>("conversations.list", { query: "" });
      setConversations(result.conversations ?? []);
    } catch { /* offline */ }
  };

  const openConversation = async (id: string) => {
    setDrawer(false);
    try {
      const result = await link.call<{ ok: boolean; activeId: string; messages: any[] }>("conversations.open", { id });
      if (result.ok) chat.showHistory(result.messages, result.activeId);
    } catch (error) {
      toast(error instanceof RpcError && error.code === "unreachable" ? t("Sem ligação ao PC.") : t("Não consegui abrir a conversa."));
    }
  };

  const newConversation = async () => {
    setDrawer(false);
    try {
      const result = await link.call<{ ok: boolean; activeId: string }>("conversations.create");
      if (result.ok) chat.showHistory([], result.activeId);
      setTab("chat");
    } catch {
      toast(t("Sem ligação ao PC."));
    }
  };

  const setSpeakMode = (mode: SpeakMode) => { setSpeakModeState(mode); void setPref("speak", mode); };
  const setTheme = (value: ThemeChoice) => { setThemeState(value); applyTheme(value); void setPref("theme", value); };
  const setLanguage = async (value: LanguageChoice) => { await setPref("language", value); window.location.reload(); };

  if (!ready) return <div className="onboard" />;

  if (!paired) {
    return <Onboarding onPaired={async () => { await link.load(); setPaired(true); await connect(); }} />;
  }

  const online = state === "online";
  const title = tab === "chat" ? "NANO" : TABS.find((x) => x.id === tab)!.label();

  return (
    <div className="app">
      <header className="topbar">
        {tab === "chat"
          ? <button className="icon-btn" onClick={() => void openDrawer()} aria-label={t("Conversas")}><Icon name="menu" /></button>
          : <span style={{ width: 4 }} />}
        <div className="topbar__title">
          {tab === "chat" && <NanoMark size={20} />}
          <h1>{title}</h1>
          <span className={`dot dot--${state === "unpaired" ? "offline" : state}`} role="img"
            aria-label={state === "online" ? t("Ligado") : state === "revoked" ? t("Removido no PC") : state === "offline" ? t("Sem ligação") : t("A ligar")} />
        </div>
        {tab === "chat" && (
          <button className="icon-btn" onClick={() => void newConversation()} aria-label={t("Nova conversa")} disabled={!online}>
            <Icon name="newChat" />
          </button>
        )}
      </header>

      <main className="page" style={tab === "chat" ? { overflow: "hidden" } : undefined}>
        {state === "offline" && tab !== "settings" && (
          <div className="banner" style={{ margin: "12px 16px 0" }}>
            <Icon name="wifi" size={20} />
            <span>{t("Não consigo chegar ao {0}. Confirma que o NANO está aberto no PC e que estás no mesmo Wi-Fi ou no Tailscale.", { 0: link.pcName })}</span>
          </div>
        )}
        {state === "revoked" && (
          <div className="banner banner--danger" style={{ margin: "12px 16px 0" }}>
            <Icon name="alert" size={20} />
            <span>{t("Este telemóvel foi removido no PC. Vai a Definições para o ligar de novo.")}</span>
          </div>
        )}
        {updateOffer && !updateDismissed && tab !== "settings" && (
          <UpdateBanner offer={updateOffer} toast={toast} onDismiss={() => setUpdateDismissed(true)} />
        )}
        {tab === "chat" && (
          <ChatScreen chat={chat} online={online} displayName={displayName} onVoiceTurn={() => { voiceTurn.current = true; }}
            cameraReady={cameraReady} />
        )}
        {tab === "agenda" && <AgendaScreen link={link} data={agenda} reload={refreshAgenda} online={online} toast={toast} />}
        {tab === "memory" && <MemoryScreen link={link} online={online} toast={toast} />}
        {tab === "settings" && (
          <SettingsScreen link={link} state={state} speakMode={speakMode} setSpeakMode={setSpeakMode}
            theme={theme} setTheme={setTheme} language={initial.language} setLanguage={(v) => void setLanguage(v)}
            onUnpaired={() => { setPaired(false); setTab("chat"); }} toast={toast} remindersOnPhone={remindersOnPhone} />
        )}
      </main>

      <nav className="bottomnav" aria-label={t("Secções")}>
        {TABS.map((item) => (
          <button key={item.id} aria-current={tab === item.id ? "page" : undefined} onClick={() => setTab(item.id)}>
            <span className="bottomnav__pill"><Icon name={item.icon} /></span>
            {item.label()}
          </button>
        ))}
      </nav>

      {drawer && (
        <>
          <div className="scrim" onClick={() => setDrawer(false)} />
          <aside className="drawer" aria-label={t("Conversas")}>
            <div className="drawer__head">
              <NanoMark size={22} />
              <strong style={{ flex: 1 }}>{t("Conversas")}</strong>
              <button className="icon-btn" onClick={() => setDrawer(false)} aria-label={t("Fechar")}><Icon name="close" /></button>
            </div>
            <div style={{ padding: "0 14px 8px" }}>
              <button className="btn btn--primary btn--block" onClick={() => void newConversation()} disabled={!online}>
                <Icon name="plus" />{t("Nova conversa")}
              </button>
            </div>
            <div className="drawer__list">
              {conversations.length === 0 && <p className="hint" style={{ padding: 12 }}>{online ? t("Ainda não há conversas.") : t("Sem ligação ao PC.")}</p>}
              {conversations.some((c) => c.pinned) && <div className="drawer__group">{t("Fixadas")}</div>}
              {conversations.filter((c) => c.pinned).map((c) => (
                <button key={c.id} className="drawer__item" aria-current={c.id === chat.activeId} onClick={() => void openConversation(c.id)}>
                  {c.title || t("Conversa sem título")}
                </button>
              ))}
              {conversations.some((c) => c.pinned) && <div className="drawer__group">{t("Recentes")}</div>}
              {conversations.filter((c) => !c.pinned).map((c) => (
                <button key={c.id} className="drawer__item" aria-current={c.id === chat.activeId} onClick={() => void openConversation(c.id)}>
                  {c.title || t("Conversa sem título")}
                </button>
              ))}
            </div>
          </aside>
        </>
      )}

      {toastText && <div className="toast" role="status">{toastText}</div>}
    </div>
  );
}
