/**
 * Context inspector.
 *
 * Shows only what the backend has actually reported. Any subsystem the backend
 * has not confirmed renders its real state (SETUP REQUIRED / NOT AVAILABLE /
 * UNKNOWN) rather than a reassuring placeholder.
 */
import React from "react";
import type { CommandCenterPayload, ReadinessPayload } from "../lib/backend";
import {
  Badge, Button, EmptyState, Meter, MetricRow, Panel,
  Skeleton, StatusIndicator, ToolChip, formatTime,
} from "./ui";

const EVENT_TONE: Record<string, string> = {
  "task.created": "accent",
  "task.planning": "info",
  "task.step": "info",
  "task.started": "info",
  "task.retrying": "warn",
  "task.needs_attention": "warn",
  "task.cancelled": "warn",
  "task.completed": "ok",
  "tool.executed": "ok",
  "tool.failed": "error",
  "worker.error": "error",
  "security.emergency_stop": "error",
  "security.untrusted_content": "warn",
};

const EVENT_LABEL: Record<string, string> = {
  "task.created": "Tarefa criada",
  "task.planning": "A planear",
  "task.step": "Passo",
  "task.started": "Iniciada",
  "task.retrying": "Retry",
  "task.needs_attention": "Precisa de atenção",
  "task.cancelled": "Cancelada",
  "task.completed": "Concluída",
  "tool.executed": "Ferramenta executada",
  "tool.failed": "Ferramenta falhou",
  "worker.started": "Worker ativo",
  "worker.stopped": "Worker parado",
  "worker.error": "Erro no worker",
  "security.emergency_stop": "Paragem de emergência",
  "security.untrusted_content": "Conteúdo externo sinalizado",
};

export function ActivityTimeline({
  events,
  limit = 12,
}: {
  events: CommandCenterPayload["activities"];
  limit?: number;
}) {
  if (!events?.length) {
    return <EmptyState title="Sem atividade" hint="Os eventos aparecem aqui assim que o Nano executar algo." />;
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
              <div className="tl-label">{EVENT_LABEL[event.event] ?? event.event}</div>
              <div className="tl-meta">
                {formatTime(event.timestamp)}
                {detail ? ` · ${String(detail).slice(0, 48)}` : ""}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function Inspector({
  collapsed,
  onToggle,
  readiness,
  commandCenter,
  loading,
  onOpenTask,
  onOpenPermissions,
  onCancelTask,
}: {
  collapsed: boolean;
  onToggle: () => void;
  readiness: ReadinessPayload | null;
  commandCenter: CommandCenterPayload | null;
  loading: boolean;
  onOpenTask: (taskId: string) => void;
  onOpenPermissions: () => void;
  onCancelTask: (taskId: string) => void;
}) {
  const task = commandCenter?.current_task ?? null;
  const pending = commandCenter?.permissions ?? [];
  const system = commandCenter?.system ?? {};
  const agents = commandCenter?.agents?.agents ?? [];

  const recentTools = (commandCenter?.activities ?? [])
    .filter((event) => event.event === "tool.executed" || event.event === "tool.failed")
    .slice(0, 6);

  return (
    <aside className="inspector" data-collapsed={collapsed} aria-label="Inspector de contexto">
      <header className="inspector-header">
        <span className="inspector-title">Inspector</span>
        <Button variant="ghost" icon size="sm" onClick={onToggle} aria-label="Fechar inspector">✕</Button>
      </header>

      <div className="inspector-scroll">
        {loading && !commandCenter ? (
          <>
            <Skeleton height={78} />
            <Skeleton height={110} />
            <Skeleton height={140} />
          </>
        ) : (
          <>
            <Panel title="Tarefa atual">
              {task ? (
                <>
                  <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 6 }}>{task.title}</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                    <StatusIndicator state={task.status} label={task.status} />
                    <Badge tone="neutral">{task.task_type}</Badge>
                  </div>
                  <Meter value={task.progress ?? 0} />
                  <MetricRow label="Progresso" value={`${task.progress ?? 0}%`} />
                  <MetricRow label="Retries" value={task.retries ?? 0} />
                  <MetricRow label="Último evento" value={task.last_event ?? "—"} />
                  <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                    <Button size="sm" onClick={() => onOpenTask(task.id)}>Detalhe</Button>
                    <Button size="sm" variant="danger" onClick={() => onCancelTask(task.id)}>Cancelar</Button>
                  </div>
                </>
              ) : (
                <EmptyState title="Nenhuma tarefa ativa" />
              )}
            </Panel>

            {pending.length > 0 && (
              <Panel
                title={`Permissões pendentes (${pending.length})`}
                action={<Button size="sm" variant="allow-task" onClick={onOpenPermissions}>Rever</Button>}
              >
                {pending.slice(0, 3).map((request: any) => (
                  <div key={request.id} className="metric-row">
                    <span className="metric-label">{request.action || request.capability}</span>
                    <span className="metric-value">{String(request.risk || "").toLowerCase()}</span>
                  </div>
                ))}
              </Panel>
            )}

            <Panel title="Modelo">
              <MetricRow label="Estado" value={<StatusIndicator state={readiness?.model.state} />} />
              <MetricRow label="Provider" value={readiness?.model.provider ?? "—"} />
              <MetricRow label="Local" value={readiness?.model.local.model ?? "—"} />
              <MetricRow
                label="Local online"
                value={readiness ? (readiness.model.local.online ? "sim" : "não") : "—"}
              />
              <MetricRow
                label="Cloud"
                value={readiness?.model.cloud.configured ? readiness.model.cloud.model : "não configurado"}
              />
            </Panel>

            <Panel title="Voz e wake word">
              <MetricRow label="Voz" value={<StatusIndicator state={readiness?.voice.state} />} />
              {readiness?.voice.blockers?.length ? (
                <div className="tl-meta" style={{ whiteSpace: "normal" }}>
                  {readiness.voice.blockers.join(" · ")}
                </div>
              ) : null}
              <MetricRow label="Wake word" value={<StatusIndicator state={readiness?.wakeWord.state} />} />
              {readiness?.wakeWord.error ? (
                <div className="tl-meta" style={{ whiteSpace: "normal" }}>{readiness.wakeWord.error}</div>
              ) : null}
            </Panel>

            <Panel title="Ferramentas recentes">
              {recentTools.length ? (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {recentTools.map((event, index) => (
                    <ToolChip
                      key={index}
                      name={String(event.payload?.tool ?? "tool")}
                      muted={event.event === "tool.failed"}
                    />
                  ))}
                </div>
              ) : (
                <EmptyState title="Nenhuma ferramenta usada" />
              )}
            </Panel>

            <Panel title="Agentes">
              {agents.length ? (
                agents.map((agent: any) => (
                  <MetricRow
                    key={agent.name}
                    label={agent.name}
                    value={<StatusIndicator state="EXPERIMENTAL" label="registado" />}
                  />
                ))
              ) : (
                <EmptyState title="Sem agentes registados" />
              )}
            </Panel>

            <Panel title="Sistema">
              <MetricRow label="CPU" value={`${system.cpu ?? 0}%`} />
              <Meter value={Number(system.cpu ?? 0)} tone="info" />
              <MetricRow label="RAM" value={`${system.ramUsed ?? 0} / ${system.ramTotal ?? 0} GB`} />
              <Meter value={Number(system.ram ?? 0)} tone="info" />
              <MetricRow label="Worker" value={<StatusIndicator state={readiness?.worker.state} />} />
              <MetricRow label="Fila" value={readiness?.worker.queue_size ?? 0} />
            </Panel>

            <Panel title="Atividade">
              <ActivityTimeline events={commandCenter?.activities ?? []} limit={8} />
            </Panel>
          </>
        )}
      </div>
    </aside>
  );
}
