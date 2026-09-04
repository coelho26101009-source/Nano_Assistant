/**
 * The primary navigation, and the single source of truth for what can be
 * navigated to.
 *
 * WHY A TOP BAR. The old shell put nine flat entries in a left rail, which
 * meant the technical pages (tasks, activity, permissions, agents) sat at the
 * same level as the conversation and competed with it for attention. The rail
 * is now the conversation list, and navigation lives in five sections along the
 * top — Chat, Ferramentas, PC, Memória, Definições.
 *
 * NOTHING WAS DROPPED. Every one of the nine views is still reachable; the
 * multi-view sections show their views as sub-tabs in the stage header. `ViewId`
 * remains the union the shell switches on, so a view that exists here and
 * nowhere else would be a dead control — which is what
 * tests/test_ui_v1_contract.py checks.
 *
 * In the desktop shell this bar is also the window caption: it carries the drag
 * region and the minimise/maximise/close controls. The drag opt-out for
 * interactive children is applied by selector in globals.css, so a new control
 * added here cannot forget it and become un-clickable.
 */
import React, { useLayoutEffect, useRef, useState } from "react";

import AiModeMenu, { type ProviderMode } from "./AiModeMenu";
import { NanoLockup } from "./NanoLogo";
import WindowControls from "./TitleBar";
import type { CloudProviderKey, ProviderPayload } from "../lib/backend";

export type ViewId =
  | "chat" | "tasks" | "activity"
  | "permissions" | "agents" | "memory" | "knowledge" | "graph" | "integrations"
  | "capabilities" | "status" | "settings";

export type SectionId = "chat" | "tools" | "pc" | "memory" | "settings";

export type NavCounts = Partial<Record<ViewId, number>>;

/** One navigable page. `id` is a ViewId the shell renders — never a group. */
type ViewEntry = { id: ViewId; label: string; hint: string };

/** A top-level section. Its `section` key is deliberately NOT called `id`:
 *  a section is not a destination, and conflating the two is how a nav entry
 *  that leads nowhere gets introduced. */
type SectionEntry = { section: SectionId; label: string; views: ViewEntry[] };

export const SECTIONS: SectionEntry[] = [
  {
    section: "chat",
    label: "Chat",
    views: [{ id: "chat", label: "Conversa", hint: "Fala com o Nano" }],
  },
  {
    // FERRAMENTAS answers "what can this thing do for me?". It is a catalogue,
    // not a control surface: the providers that used to lead this section are
    // configuration and now live in Definições → IA, where they can be changed.
    section: "tools",
    label: "Ferramentas",
    views: [
      { id: "capabilities", label: "Capacidades", hint: "Tudo o que o Nano sabe fazer" },
      { id: "integrations", label: "Componentes", hint: "Módulos instalados que fornecem capacidades" },
      { id: "agents", label: "Agentes", hint: "Agentes registados e o que sabem fazer" },
    ],
  },
  {
    // PC is THIS COMPUTER — its state, what Nano may do to it, and what Nano
    // has actually done. Not the generic capability list, which is Ferramentas.
    section: "pc",
    label: "PC",
    views: [
      { id: "status", label: "Estado", hint: "Recursos e saúde desta máquina" },
      { id: "permissions", label: "Permissões", hint: "O que o Nano pode fazer neste computador" },
      { id: "activity", label: "Atividade", hint: "O que o Nano tem feito aqui" },
      { id: "tasks", label: "Tarefas", hint: "Trabalho em segundo plano" },
    ],
  },
  {
    // MEMÓRIA is what Nano remembers and what it knows. Three views, because
    // they answer three different questions: what do you remember about me,
    // what do you know about my world, and how is it connected?
    //
    // Privacidade is deliberately NOT a fourth view here. The switches that
    // govern memory are settings, they live with every other setting in
    // Definições → Memória and Definições → Privacidade, and duplicating them
    // would create two places to change one thing.
    section: "memory",
    label: "Memória",
    views: [
      { id: "memory", label: "Memórias", hint: "O que o Nano sabe sobre ti" },
      { id: "knowledge", label: "Second Brain", hint: "Pessoas, projetos e coisas que o Nano conhece" },
      { id: "graph", label: "Grafo", hint: "Como tudo se liga" },
    ],
  },
  {
    section: "settings",
    label: "Definições",
    views: [{ id: "settings", label: "Definições", hint: "Configurar o Nano" }],
  },
];

/** Which section owns a view. Falls back to chat so a bad id cannot blank the bar. */
export function sectionOf(view: ViewId): SectionId {
  return SECTIONS.find((s) => s.views.some((v) => v.id === view))?.section ?? "chat";
}

export function sectionEntry(section: SectionId): SectionEntry {
  return SECTIONS.find((s) => s.section === section) ?? SECTIONS[0];
}

export function viewEntry(view: ViewId): ViewEntry {
  for (const section of SECTIONS) {
    const found = section.views.find((v) => v.id === view);
    if (found) return found;
  }
  return SECTIONS[0].views[0];
}

/* Stroke glyphs, drawn rather than typed: an icon font is one more thing that
   can fail to load, and a missing glyph renders as a tofu box. */
const Glyph = ({ d, size = 17 }: { d: string; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);

const BELL = "M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0";
const CHEVRON = "m6 9 6 6 6-6";
const PANEL = "M3 5.5A2.5 2.5 0 0 1 5.5 3h13A2.5 2.5 0 0 1 21 5.5v13a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 18.5v-13ZM9 3v18";
const USER = "M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z";

/**
 * The bar.
 *
 * `agentState` and `routeLabel` come from the backend readiness payload — the
 * pill reports a measured state, never an assumed one. When the backend has
 * said nothing, StatusIndicator renders UNKNOWN rather than a reassuring green.
 */
export default function TopNav({
  view, onView, counts, agentState, healthLabel, providers, offline, busy,
  onSetMode, onSetPreferredCloud, onSetCloudModel, onOpenAiSettings,
  pendingCount, profileName, isDesktop, railOpen, onToggleRail, showRailToggle,
  version,
}: {
  view: ViewId;
  onView: (view: ViewId) => void;
  counts: NavCounts;
  agentState: string;
  healthLabel: string;
  /** The live provider payload. The pill renders from this and nothing else. */
  providers: ProviderPayload | null;
  offline: boolean;
  busy: boolean;
  onSetMode: (mode: ProviderMode) => void;
  onSetPreferredCloud: (provider: CloudProviderKey) => void;
  onSetCloudModel: (provider: CloudProviderKey, model: string) => void;
  onOpenAiSettings: () => void;
  pendingCount: number;
  profileName: string | null;
  isDesktop: boolean;
  railOpen: boolean;
  onToggleRail: () => void;
  showRailToggle: boolean;
  version?: string;
}) {
  const active = sectionOf(view);

  /* THE TRAVELLING ACTIVE MARKER.
   *
   * Measured, not guessed. The tabs are label-width, and those widths change
   * with the interface language, with the font finally loading, and with the
   * breakpoint that shrinks the padding — so any hardcoded geometry would be
   * wrong somewhere. The marker reads the real button's offsetLeft/offsetWidth
   * and animates between them.
   *
   * useLayoutEffect, not useEffect: measuring after paint would show the
   * marker at the old tab for one frame every time the section changes. */
  const navRef = useRef<HTMLElement | null>(null);
  const [marker, setMarker] = useState<{ left: number; width: number } | null>(null);

  useLayoutEffect(() => {
    const nav = navRef.current;
    if (!nav) return;

    const measure = () => {
      const current = nav.querySelector<HTMLElement>('[aria-current="page"]');
      if (!current) { setMarker(null); return; }
      setMarker({ left: current.offsetLeft, width: current.offsetWidth });
    };

    measure();

    // Re-measure when the bar reflows: a window resize, a breakpoint change, a
    // badge appearing beside a label. ResizeObserver catches all three without
    // polling.
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(nav);
    for (const child of Array.from(nav.children) as Element[]) observer.observe(child);
    return () => observer.disconnect();
  }, [active, counts]);

  /* A section's badge is the sum of what its pages need the user for. Only
     work that is genuinely waiting counts — see get_task_counts, where the
     badge deliberately excludes finished tasks. */
  const badgeFor = (entry: SectionEntry) =>
    entry.views.reduce((total, v) => total + (counts[v.id] ?? 0), 0);

  const initials = profileName
    ? profileName.trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase()
    : null;

  return (
    <header className="topbar" aria-label="Navegação principal">
      {showRailToggle && (
        <button
          type="button" className="icon-btn" onClick={onToggleRail}
          aria-expanded={railOpen}
          aria-label={railOpen ? "Fechar conversas" : "Abrir conversas"}
          title={railOpen ? "Fechar conversas (Ctrl+B)" : "Abrir conversas (Ctrl+B)"}
        >
          <Glyph d={PANEL} />
        </button>
      )}

      <div className="topbar__brand">
        <NanoLockup version={version} />
      </div>

      <nav className="topbar__nav" aria-label="Secções" ref={navRef}>
        <span
          className="topnav-indicator"
          data-ready={marker ? "true" : "false"}
          aria-hidden="true"
          style={marker
            ? { width: marker.width, transform: `translateX(${marker.left}px)` }
            : undefined}
        />
        {SECTIONS.map((entry) => {
          const badge = badgeFor(entry);
          return (
            <button
              key={entry.section} type="button" className="topnav-item"
              aria-current={active === entry.section ? "page" : undefined}
              onClick={() => onView(entry.views[0].id)}
            >
              {entry.label}
              {badge > 0 && (
                <span className="topnav-item__badge" aria-label={`${badge} por rever`}>
                  {badge > 99 ? "99+" : badge}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      <span className="topbar__spacer" />

      <div className="topbar__right">
        {/* The live route and health, and the fastest way to change it.
            Everything shown is measured by the backend; choosing a mode goes
            through the same set_provider_mode the Settings page uses. */}
        <AiModeMenu
          providers={providers}
          agentState={agentState}
          healthLabel={healthLabel}
          offline={offline}
          busy={busy}
          onSetMode={onSetMode}
          onSetPreferredCloud={onSetPreferredCloud}
          onSetCloudModel={onSetCloudModel}
          onOpenAiSettings={onOpenAiSettings}
        />

        <button
          type="button" className="icon-btn bell"
          onClick={() => onView("permissions")}
          aria-label={pendingCount > 0 ? `${pendingCount} autorizações por rever` : "Sem autorizações pendentes"}
          title={pendingCount > 0 ? `${pendingCount} por autorizar` : "Sem autorizações pendentes"}
        >
          <Glyph d={BELL} size={16} />
          {pendingCount > 0 && <span className="bell__dot" />}
        </button>

        <button
          type="button" className="profile-chip"
          onClick={() => onView("settings")}
          title={profileName ? `${profileName} — abrir definições` : "Perfil — abrir definições"}
          aria-label={profileName ? `Perfil de ${profileName}` : "Perfil"}
        >
          <span className="profile-chip__initials" aria-hidden="true">
            {initials ?? <Glyph d={USER} size={14} />}
          </span>
          <span className="profile-chip__caret" aria-hidden="true"><Glyph d={CHEVRON} size={14} /></span>
        </button>

        {isDesktop && <WindowControls />}
      </div>
    </header>
  );
}
