/**
 * The main column's header, and the window caption in the desktop shell.
 *
 * The Chatbot UI layout this follows keeps navigation in the sidebar and
 * leaves the header for what describes the open page: its title on the left,
 * and on the right the model selector and whatever acts on the page. Here that
 * is the AI selector, the approvals bell and, in the desktop shell, the
 * minimise / maximise / close cluster.
 *
 * DRAG REGIONS ARE DELIBERATE. The bar carries `-webkit-app-region: drag`, and
 * every interactive child opts out by selector in globals.css, so a new control
 * added here cannot forget it and become un-clickable.
 */
import React from "react";

import AiModeMenu, { type ProviderMode } from "./AiModeMenu";
import Icon from "./Icon";
import WindowControls from "./TitleBar";
import type { CloudProviderKey, ProviderPayload } from "../lib/backend";

export default function TopBar({
  title, actions, showMenu, railOpen, onToggleRail,
  providers, agentState, healthLabel, offline, busy,
  onSetMode, onSetPreferredCloud, onSetCloudModel, onOpenAiSettings,
  pendingCount, onOpenPermissions, isDesktop,
}: {
  /** The open page or conversation. Empty on the home screen, which names itself. */
  title: string;
  /** Page-specific controls, rendered before the AI selector. */
  actions?: React.ReactNode;
  /** Whether the sidebar is out of reach at this width and needs a button here. */
  showMenu: boolean;
  railOpen: boolean;
  onToggleRail: () => void;
  /** The live provider payload. The selector renders from this and nothing else. */
  providers: ProviderPayload | null;
  agentState: string;
  healthLabel: string;
  offline: boolean;
  busy: boolean;
  onSetMode: (mode: ProviderMode) => void;
  onSetPreferredCloud: (provider: CloudProviderKey) => void;
  onSetCloudModel: (provider: CloudProviderKey, model: string) => void;
  onOpenAiSettings: () => void;
  pendingCount: number;
  onOpenPermissions: () => void;
  isDesktop: boolean;
}) {
  return (
    <header className="topbar">
      {showMenu && (
        <button
          type="button" className="icon-btn topbar__menu" onClick={onToggleRail}
          aria-expanded={railOpen}
          aria-label={railOpen ? "Fechar barra lateral" : "Abrir barra lateral"}
          title={railOpen ? "Fechar barra lateral (Ctrl+B)" : "Abrir barra lateral (Ctrl+B)"}
        >
          <Icon name="menu" />
        </button>
      )}

      {/* No empty heading: the home screen's own headline is the page's h1. */}
      {title && <h1 className="topbar__title">{title}</h1>}

      <span className="topbar__spacer" />

      <div className="topbar__right">
        {actions}

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
          onClick={onOpenPermissions}
          aria-label={pendingCount > 0 ? `${pendingCount} autorizações por rever` : "Sem autorizações pendentes"}
          title={pendingCount > 0 ? `${pendingCount} por autorizar` : "Sem autorizações pendentes"}
        >
          <Icon name="bell" size={17} />
          {pendingCount > 0 && <span className="bell__dot" />}
        </button>

        {isDesktop && <WindowControls />}
      </div>
    </header>
  );
}
