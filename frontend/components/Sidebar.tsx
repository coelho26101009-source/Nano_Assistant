/** Workspace navigation rail. Collapsible; never hides state, only labels. */
import React from "react";
import { Button, StatusIndicator } from "./ui";

export type ViewId =
  | "chat" | "tasks" | "permissions" | "agents"
  | "memory" | "plugins" | "activity" | "health";

export type NavCounts = Partial<Record<ViewId, number>>;

const NAV: { id: ViewId; icon: string; label: string; section: string }[] = [
  { id: "chat",        icon: "◈", label: "Conversa",    section: "Workspace" },
  { id: "tasks",       icon: "▤", label: "Tarefas",     section: "Workspace" },
  { id: "activity",    icon: "≋", label: "Atividade",   section: "Workspace" },
  { id: "permissions", icon: "⛨", label: "Permissões",  section: "Controlo" },
  { id: "agents",      icon: "◇", label: "Agentes",     section: "Controlo" },
  { id: "memory",      icon: "▦", label: "Memória",     section: "Controlo" },
  { id: "plugins",     icon: "⬡", label: "Integrações", section: "Controlo" },
  { id: "health",      icon: "◉", label: "Estado",       section: "Sistema" },
];

export default function Sidebar({
  view,
  onView,
  collapsed,
  onToggleCollapsed,
  counts,
  agentState,
  onNewChat,
  onSettings,
}: {
  view: ViewId;
  onView: (view: ViewId) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  counts: NavCounts;
  agentState: string;
  onNewChat: () => void;
  onSettings: () => void;
}) {
  const sections = Array.from(new Set(NAV.map((item) => item.section)));

  return (
    <nav className="sidebar" data-collapsed={collapsed} aria-label="Navegação principal">
      <div className="sidebar-brand">
        <span className="brand-mark" aria-hidden="true">N</span>
        <span className="brand-name">Nano</span>
      </div>

      <div className="sidebar-scroll">
        <div className="sidebar-section">
          <Button variant="primary" block onClick={onNewChat} title="Nova conversa (Ctrl+N)">
            {collapsed ? "＋" : "＋  Nova conversa"}
          </Button>
        </div>

        {sections.map((section) => (
          <div className="sidebar-section" key={section}>
            <div className="sidebar-section-label">{section}</div>
            {NAV.filter((item) => item.section === section).map((item) => {
              const count = counts[item.id];
              const isAlert = item.id === "permissions" && (count ?? 0) > 0;
              return (
                <button
                  key={item.id}
                  type="button"
                  className="nav-item"
                  aria-current={view === item.id ? "page" : undefined}
                  onClick={() => onView(item.id)}
                  title={item.label}
                >
                  <span className="nav-item-icon" aria-hidden="true">{item.icon}</span>
                  <span className="nav-item-label">{item.label}</span>
                  {count ? (
                    <span className={`nav-item-badge${isAlert ? " nav-item-badge--alert" : ""}`}>{count}</span>
                  ) : null}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      <div className="sidebar-footer">
        {!collapsed && (
          <div style={{ padding: "0 8px 8px" }}>
            <StatusIndicator state={agentState} />
          </div>
        )}
        <button type="button" className="nav-item" onClick={onSettings} title="Definições">
          <span className="nav-item-icon" aria-hidden="true">⚙</span>
          <span className="nav-item-label">Definições</span>
        </button>
        <button
          type="button"
          className="nav-item"
          onClick={onToggleCollapsed}
          title={collapsed ? "Expandir barra lateral" : "Recolher barra lateral"}
          aria-label={collapsed ? "Expandir barra lateral" : "Recolher barra lateral"}
        >
          <span className="nav-item-icon" aria-hidden="true">{collapsed ? "»" : "«"}</span>
          <span className="nav-item-label">Recolher</span>
        </button>
      </div>
    </nav>
  );
}
