/**
 * Workspace navigation rail.
 *
 * Collapsible: collapsing hides labels, never state. Every item routes to a
 * real page — there are no decorative entries, because a nav item that does
 * nothing is worse than one that is absent.
 */
import React from "react";
import NanoLogo, { NanoWordmark } from "./NanoLogo";
import { Button, StatusIndicator } from "./ui";

export type ViewId =
  | "chat" | "tasks" | "activity"
  | "permissions" | "agents" | "memory" | "integrations"
  | "status" | "settings";

export type NavCounts = Partial<Record<ViewId, number>>;

type NavEntry = { id: ViewId; icon: React.ReactNode; label: string; section: string };

/* Simple stroke glyphs: they stay legible at 18 px and carry no external
   icon-font dependency. */
const Glyph = ({ d, filled }: { d: string; filled?: boolean }) => (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} fill={filled ? "currentColor" : "none"} />
  </svg>
);

const NAV: NavEntry[] = [
  { id: "chat", section: "Workspace", label: "Conversa",
    icon: <Glyph d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9 9 0 0 1-3.9-.9L3 21l1.9-5.1A8.4 8.4 0 0 1 12 3.1a8.4 8.4 0 0 1 9 8.4Z" /> },
  { id: "tasks", section: "Workspace", label: "Tarefas",
    icon: <Glyph d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01" /> },
  { id: "activity", section: "Workspace", label: "Atividade",
    icon: <Glyph d="M3 12h4l3 8 4-16 3 8h4" /> },

  { id: "permissions", section: "Controlo", label: "Permissões",
    icon: <Glyph d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" /> },
  { id: "agents", section: "Controlo", label: "Agentes",
    icon: <Glyph d="M12 2 3 7v10l9 5 9-5V7l-9-5Z" /> },
  { id: "memory", section: "Controlo", label: "Memória",
    icon: <Glyph d="M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Zm0 0v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /> },
  { id: "integrations", section: "Controlo", label: "Integrações",
    icon: <Glyph d="M12 2 3 7l9 5 9-5-9-5ZM3 12l9 5 9-5M3 17l9 5 9-5" /> },

  { id: "status", section: "Sistema", label: "Estado",
    icon: <Glyph d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v5l3 2" /> },
];

const SECTIONS = ["Workspace", "Controlo", "Sistema"];

export default function Sidebar({
  view, onView, collapsed, onToggleCollapsed, counts,
  agentState, healthLabel, onNewChat, onSettings, version,
}: {
  view: ViewId;
  onView: (view: ViewId) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  counts: NavCounts;
  agentState: string;
  healthLabel: string;
  onNewChat: () => void;
  onSettings: () => void;
  version: string;
}) {
  return (
    <nav className="sidebar" data-collapsed={collapsed} aria-label="Navegação principal">
      <div className="sidebar-brand">
        <NanoWordmark version={version} collapsed={collapsed} />
        <span className="sidebar-brand__spacer" />
        <Button
          variant="ghost" icon size="sm"
          onClick={onToggleCollapsed}
          aria-label={collapsed ? "Expandir barra lateral" : "Recolher barra lateral"}
          title={collapsed ? "Expandir (Ctrl+B)" : "Recolher (Ctrl+B)"}
        >
          {collapsed ? "»" : "«"}
        </Button>
      </div>

      <div className="sidebar-cta">
        <Button variant="primary" block onClick={onNewChat} title="Nova conversa (Ctrl+N)">
          {collapsed ? "+" : "+  Nova conversa"}
        </Button>
      </div>

      <div className="sidebar-scroll">
        {SECTIONS.map((section) => (
          <div className="sidebar-section" key={section}>
            <div className="sidebar-section__label section-label">{section}</div>
            {NAV.filter((item) => item.section === section).map((item) => {
              const count = counts[item.id];
              const isAlert = item.id === "permissions" || item.id === "tasks";
              return (
                <button
                  key={item.id} type="button" className="nav-item"
                  aria-current={view === item.id ? "page" : undefined}
                  onClick={() => onView(item.id)}
                  title={collapsed ? item.label : undefined}
                >
                  <span className="nav-item__icon">{item.icon}</span>
                  <span className="nav-item__label">{item.label}</span>
                  {count ? (
                    <span className={`nav-item__badge${isAlert ? " nav-item__badge--alert" : ""}`}>
                      {count > 99 ? "99+" : count}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      <div className="sidebar-footer">
        <div className="sidebar-health">
          <StatusIndicator state={agentState} label={collapsed ? "" : undefined} />
          {!collapsed && (
            <div className="sidebar-health__text">
              <div className="sidebar-health__hint truncate">{healthLabel}</div>
            </div>
          )}
        </div>
        <div className="sidebar-profile">
          <NanoLogo size={22} bare />
          {!collapsed && (
            <div className="sidebar-profile__text">
              <div className="sidebar-profile__name">Nano</div>
              <div className="sidebar-profile__hint">Local-first</div>
            </div>
          )}
          <Button
            variant="ghost" icon size="sm" onClick={onSettings}
            aria-label="Definições" title="Definições"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2v.2a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.6 1.7 1.7 0 0 0-1.9.4l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.7 8a1.7 1.7 0 0 0-.4-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V2a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V8a1.7 1.7 0 0 0 1.5 1H22a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" />
            </svg>
          </Button>
        </div>
      </div>
    </nav>
  );
}
