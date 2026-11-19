/**
 * The live context panels.
 *
 * These used to be a fixed inspector column pinned to the right of every
 * conversation. It made the main screen read as a control panel: six technical
 * cards competing with the chat for attention, and roughly 300 px of width the
 * conversation never got back. The redesign removes that column — the panels
 * now live on the page that owns them, PC › Estado, where they are the answer
 * to "what is Nano doing right now" instead of permanent furniture.
 *
 * Nothing was lost in the move, and nothing was softened. Every card still
 * shows only what the backend reported: a subsystem the backend has not
 * confirmed renders its real state (SETUP REQUIRED / UNAVAILABLE / UNKNOWN)
 * rather than a reassuring placeholder, and every card still links onward to
 * the page that owns the detail, so it is a summary and never a dead end.
 */
import React from "react";
import type { CommandCenterPayload, ProviderPayload, ReadinessPayload, TaskRow } from "../lib/backend";
import type { ViewId } from "../lib/navigation";
import {
  Button, EmptyState, Meter, MetricRow, Panel, Skeleton,
  StatusIndicator, ToolChip, elapsedSince, formatTime, usageTone,
} from "./ui";
import { locale, t } from "../lib/i18n";

const EVENT_TONE: Record<string, string> = {
  "task.created": "accent", "task.planning": "info", "task.step": "info",
  "task.started": "info", "task.retrying": "warn", "task.needs_attention": "warn",
  "task.cancelled": "warn", "task.completed": "ok", "tool.executed": "ok",
  "tool.failed": "error", "worker.error": "error",
  "security.emergency_stop": "error", "security.untrusted_content": "warn",
  "VoiceWakeCancelled": "warn", "WakeWordDetected": "accent",
};

const EVENT_LABEL: Record<string, string> = {
  "task.created": t("Tarefa criada"), "task.planning": t("A planear"), "task.step": t("Passo"),
  "task.started": t("Iniciada"), "task.retrying": t("Nova tentativa"),
  "task.needs_attention": t("Precisa de atenção"), "task.cancelled": t("Cancelada"),
  "task.completed": t("Concluída"), "tool.executed": t("Passou"), "tool.failed": t("Falhou"),
  "worker.started": t("Worker ativo"), "worker.stopped": t("Worker parado"),
  "worker.error": t("Erro no worker"), "security.emergency_stop": t("Paragem de emergência"),
  "security.untrusted_content": t("Conteúdo externo sinalizado"),
  "PermissionRequested": t("Autorização pedida"), "PermissionGranted": t("Autorização dada"),
  "PermissionDenied": t("Autorização recusada"), "WakeWordDetected": t("Wake detetada"),
  "VoiceWakeCancelled": t("Wake cancelada"), "VoiceRequestCreated": t("Pedido por voz"),
  "tasks.archived": t("Histórico arquivado"),
};

export function eventLabel(event: string): string {
  return EVENT_LABEL[event] ?? event;
}

export function ActivityTimeline({
  events, limit = 12,
}: { events: CommandCenterPayload["activities"]; limit?: number }) {
  if (!events?.length) {
    return <EmptyState title={t("Sem atividade")} hint={t("Os eventos aparecem aqui assim que o NANO fizer alguma coisa.")} />;
  }
  return (
    <div className="timeline">
      {events.slice(0, limit).map((event, index) => {
        const tone = EVENT_TONE[event.event] ?? "";
        const detail = event.payload?.tool || event.payload?.title || event.payload?.error || "";
        return (
          <div className="tl-item" key={`${event.timestamp}-${index}`}>
            <span className={`tl-dot${tone ? ` tl-dot--${tone}` : ""}`} aria-hidden="true" />
            <div className="tl-content">
              <div className="tl-label">{eventLabel(event.event)}</div>
              <div className="tl-meta">
                {formatTime(event.timestamp)}
                {detail ? ` · ${String(detail).slice(0, 42)}` : ""}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** A task is only "current" if it is genuinely active. */
function isActiveTask(task: TaskRow | null | undefined): task is TaskRow {
  if (!task) return false;
  return !["COMPLETED", "CANCELLED", "FAILED"].includes(String(task.status));
}

export default function ContextPanels({
  readiness, providers, commandCenter, loading,
  onOpenTask, onCancelTask, onNavigate,
}: {
  readiness: ReadinessPayload | null;
  providers: ProviderPayload | null;
  commandCenter: CommandCenterPayload | null;
  loading: boolean;
  onOpenTask: (taskId: string) => void;
  onCancelTask: (taskId: string) => void;
  onNavigate: (view: ViewId) => void;
}) {
  const task = isActiveTask(commandCenter?.current_task) ? commandCenter!.current_task! : null;
  const pending = commandCenter?.permissions ?? [];
  const system = commandCenter?.system ?? {};
  const agents = commandCenter?.agents?.agents ?? [];

  const recentTools = (commandCenter?.activities ?? [])
    .filter((e) => e.event === "tool.executed" || e.event === "tool.failed")
    .slice(0, 6);

  const route = providers?.route;

  if (loading && !commandCenter) {
    return (
      <div className="grid-auto">
        <Skeleton height={140} /><Skeleton height={140} /><Skeleton height={140} />
      </div>
    );
  }

  return (
    <div className="grid-auto">
      {/* 1. TAREFA ATUAL */}
      <Panel
        title={t("Tarefa atual")}
        action={task ? (
          <Button variant="ghost" size="sm" onClick={() => onCancelTask(task.id)}>{t("Cancelar")}</Button>
        ) : undefined}
      >
        {task ? (
          <>
            <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 8 }} className="truncate" title={task.title}>
              {task.title}
            </div>
            <div className="inline" style={{ marginBottom: 8 }}>
              <StatusIndicator state={task.status} />
              {elapsedSince(task.started_at) && (
                <span className="metric-value" style={{ fontSize: 11 }}>{elapsedSince(task.started_at)}</span>
              )}
            </div>
            <Meter value={task.progress ?? 0} />
            <MetricRow label={t("Progresso")} value={`${task.progress ?? 0}%`} />
            {task.retries > 0 && <MetricRow label={t("Tentativas")} value={`${task.retries} / 3`} />}
            <Button size="sm" block onClick={() => onOpenTask(task.id)} style={{ marginTop: 8 }}>{t("Ver detalhe")}</Button>
          </>
        ) : (
          <EmptyState title={t("Nenhuma tarefa em execução")} />
        )}
      </Panel>

      {/* Pending approvals surface above everything else. */}
      {pending.length > 0 && (
        <Panel
          title={t("Autorizações ({0})", { 0: pending.length })}
          action={<Button size="sm" variant="allow-task" onClick={() => onNavigate("permissions")}>{t("Rever")}</Button>}
        >
          {pending.slice(0, 3).map((request: any) => (
            <MetricRow
              key={request.id}
              label={request.action || request.capability}
              /* State comes from the request itself, never asserted here. */
              value={<StatusIndicator state={request.status === "pending" ? "APPROVAL_REQUIRED" : request.status}
                                      label={String(request.risk || "").toLowerCase()} />}
            />
          ))}
        </Panel>
      )}

      {/* 2. MODELO & PROVEDOR */}
      <Panel
        title={t("Modelo & provedor")}
        action={<Button variant="ghost" size="sm" onClick={() => onNavigate("integrations")} aria-label={t("Abrir integrações")}>↗</Button>}
      >
        {providers ? (
          <>
            <div className={`provider-row${route?.provider === "groq" ? " provider-row--primary" : ""}`}>
              <span className="provider-row__icon" aria-hidden="true">☁</span>
              <span className="provider-row__text">
                <span className="provider-row__name">{t("Groq (Cloud)")}</span>
                <span className="provider-row__model" title={providers.groq.model}>{providers.groq.model || "—"}</span>
              </span>
              <StatusIndicator state={providers.groq.state} />
            </div>
            <div className={`provider-row${route?.provider === "ollama" ? " provider-row--primary" : ""}`}>
              <span className="provider-row__icon" aria-hidden="true">▣</span>
              <span className="provider-row__text">
                <span className="provider-row__name">{t("Ollama (Local)")}</span>
                <span className="provider-row__model" title={providers.ollama.model}>{providers.ollama.model || "—"}</span>
              </span>
              <StatusIndicator state={providers.ollama.state} />
            </div>
            {route?.fallback && (
              <div className="tl-meta" style={{ whiteSpace: "normal", marginTop: 4 }}>{t("A usar o fallback local: {0}", { 0: route.reason })}</div>
            )}
            {!route?.usable && (
              <div className="tl-meta" style={{ whiteSpace: "normal", marginTop: 4, color: "var(--st-error)" }}>
                {route?.reason}
              </div>
            )}
            <MetricRow label={t("Modo")} value={providers.mode} />
          </>
        ) : (
          <EmptyState title={t("Sem informação de provedor")} />
        )}
      </Panel>

      {/* 3. VOZ & WAKE PHRASE */}
      <Panel
        title={t("Voz & wake phrase")}
        action={<Button variant="ghost" size="sm" onClick={() => onNavigate("settings")} aria-label={t("Abrir definições de voz")}>↗</Button>}
      >
        <MetricRow label={t("Voz")} value={<StatusIndicator state={readiness?.voice.state} />} />
        {readiness?.voice.blockers?.length ? (
          <div className="tl-meta" style={{ whiteSpace: "normal" }}>{readiness.voice.blockers.join(" · ")}</div>
        ) : null}
        <MetricRow
          label={`"${readiness?.wakePhrase.phrase ?? (locale() === "pt-PT" ? "ei nano" : "hey nano")}"`}
          value={<StatusIndicator state={readiness?.wakePhrase.state} />}
        />
        {readiness?.wakePhrase.error ? (
          <div className="tl-meta" style={{ whiteSpace: "normal" }}>{readiness.wakePhrase.error}</div>
        ) : null}
      </Panel>

      {/* 4. FERRAMENTAS & AGENTES */}
      <Panel title={t("Ferramentas & agentes")}>
        <button type="button" className="metric-row" style={{ background: "none", width: "100%" }}
                onClick={() => onNavigate("agents")}>
          <span className="metric-label">{t("Agentes")}</span>
          <span className="metric-value">{t("{0} registados ›", { 0: agents.length })}</span>
        </button>
        <button type="button" className="metric-row" style={{ background: "none", width: "100%" }}
                onClick={() => onNavigate("integrations")}>
          <span className="metric-label">{t("Integrações")}</span>
          <span className="metric-value">{t("ver ›")}</span>
        </button>
        {recentTools.length > 0 && (
          <div className="inline" style={{ marginTop: 6 }}>
            {recentTools.map((event, index) => (
              <ToolChip key={index} name={String(event.payload?.tool ?? "tool")} muted={event.event === "tool.failed"} />
            ))}
          </div>
        )}
      </Panel>

      {/* 5. SAÚDE DO SISTEMA
          This card absorbed the Estado page's separate "Recursos" tiles: they
          rendered the identical three numbers from the identical payload, one
          scroll apart. Used/total is carried here so nothing was lost. */}
      <Panel title={t("Saúde do sistema")}>
        <MetricRow label="CPU" value={`${system.cpu ?? 0}%`} />
        <Meter value={Number(system.cpu ?? 0)} tone={usageTone(Number(system.cpu ?? 0))} />
        <MetricRow label="RAM" value={`${system.ramUsed ?? 0} / ${system.ramTotal ?? 0} GB`} />
        <Meter value={Number(system.ram ?? 0)} tone={usageTone(Number(system.ram ?? 0))} />
        <MetricRow label={t("Disco")} value={`${system.diskUsed ?? 0} / ${system.diskTotal ?? 0} GB`} />
        <Meter value={Number(system.disk ?? 0)} tone={usageTone(Number(system.disk ?? 0))} />
        <MetricRow label={t("Worker")} value={<StatusIndicator state={readiness?.worker.state} />} />
        {/* Temperature is deliberately absent: psutil cannot read CPU
            temperature reliably on Windows, and a fabricated number is worse
            than none. */}
        <p className="dim" style={{ fontSize: 11, marginTop: 4, lineHeight: 1.5 }}>{t("A temperatura do CPU não é apresentada: não é legível de forma fiável nesta plataforma, e um valor inventado seria pior do que nenhum.")}</p>
      </Panel>

      {/* 6. ATIVIDADE RECENTE */}
      <Panel
        title={t("Atividade recente")}
        action={<Button variant="ghost" size="sm" onClick={() => onNavigate("activity")}>{t("Ver tudo")}</Button>}
      >
        <ActivityTimeline events={commandCenter?.activities ?? []} limit={6} />
      </Panel>
    </div>
  );
}
