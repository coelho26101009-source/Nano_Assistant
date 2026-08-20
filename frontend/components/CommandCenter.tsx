/**
 * Command Center views rendered in the main workspace.
 *
 * Every number and every state here comes from the backend. Where the backend
 * has nothing to report, the view says so.
 */
import React from "react";
import { ActivityTimeline } from "./Inspector";
import type { CommandCenterPayload, ReadinessPayload } from "../lib/backend";
import {
  Badge, Button, EmptyState, Meter, MetricRow, Panel,
  RiskBadge, StatusIndicator, formatTime,
} from "./ui";

export function TasksView({
  data,
  onOpenTask,
  onCancelTask,
}: {
  data: CommandCenterPayload | null;
  onOpenTask: (id: string) => void;
  onCancelTask: (id: string) => void;
}) {
  const tasks = data?.tasks ?? [];
  const summary = data?.task_summary ?? {};
  const active = Object.entries(summary).filter(([, count]) => count > 0);

  return (
    <div style={{ padding: 24, overflowY: "auto" }}>
      <div className="cc-grid">
        {active.length ? active.map(([status, count]) => (
          <div className="cc-tile" key={status}>
            <div className="cc-tile-label">{status.replace(/_/g, " ")}</div>
            <div className="cc-tile-value">{count}</div>
          </div>
        )) : (
          <div className="cc-tile">
            <div className="cc-tile-label">Fila</div>
            <div className="cc-tile-value">0</div>
          </div>
        )}
      </div>

      <h2 className="cc-section-title">Tarefas</h2>
      {tasks.length === 0 ? (
        <EmptyState title="Sem tarefas" hint="As tarefas em segundo plano aparecem aqui com estado e progresso reais." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {tasks.map((task: any) => {
            const terminal = ["COMPLETED", "FAILED", "CANCELLED", "NEEDS_ATTENTION"].includes(task.status);
            return (
              <div className="task-row" key={task.id}>
                <StatusIndicator state={task.status} label="" />
                <button
                  type="button"
                  className="task-row-main"
                  onClick={() => onOpenTask(task.id)}
                  style={{ background: "none", textAlign: "left" }}
                >
                  <div className="task-row-title">{task.title}</div>
                  <div className="task-row-meta">
                    {task.status} · {task.progress}% · {task.task_type}
                    {task.retries ? ` · ${task.retries} retries` : ""} · {formatTime(task.updated_at)}
                  </div>
                </button>
                <div style={{ width: 90 }}><Meter value={task.progress ?? 0} /></div>
                {!terminal && (
                  <Button size="sm" variant="danger" onClick={() => onCancelTask(task.id)}>Cancelar</Button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function ActivityView({ data }: { data: CommandCenterPayload | null }) {
  return (
    <div style={{ padding: 24, overflowY: "auto", maxWidth: 760 }}>
      <h2 className="cc-section-title">Stream de atividade</h2>
      <div className="card">
        <ActivityTimeline events={data?.activities ?? []} limit={40} />
      </div>
    </div>
  );
}

export function AgentsView({
  data,
  readiness,
}: {
  data: CommandCenterPayload | null;
  readiness: ReadinessPayload | null;
}) {
  const agents = data?.agents?.agents ?? [];
  return (
    <div style={{ padding: 24, overflowY: "auto" }}>
      <h2 className="cc-section-title">
        Agentes <Badge tone="info">experimental</Badge>
      </h2>
      <p className="dim" style={{ marginBottom: 16, maxWidth: "60ch" }}>
        Os agentes estão registados mas ainda não despacham execução própria: todas as
        ferramentas correm pela autoridade central de execução. O estado abaixo reflete
        o registo, não capacidade autónoma.
      </p>
      <div className="cc-grid">
        {agents.map((agent: any) => (
          <div className="cc-tile" key={agent.name}>
            <div className="cc-tile-label">{agent.name}</div>
            <StatusIndicator state="EXPERIMENTAL" label="registado" />
            <p className="dim" style={{ fontSize: 12, marginTop: 8 }}>{agent.description}</p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
              {(agent.capabilities ?? []).slice(0, 4).map((capability: string) => (
                <span className="tool-chip tool-chip--muted" key={capability}>{capability}</span>
              ))}
            </div>
          </div>
        ))}
        {!agents.length && <EmptyState title="Sem agentes registados" />}
      </div>

      <h2 className="cc-section-title">Router de modelos</h2>
      <div className="cc-grid">
        <div className="cc-tile">
          <div className="cc-tile-label">Estado</div>
          <StatusIndicator state={readiness?.model.state} />
          <MetricRow label="Provider" value={readiness?.model.provider ?? "—"} />
          {readiness?.model.detail && (
            <p className="dim" style={{ fontSize: 11, marginTop: 6 }}>{readiness.model.detail}</p>
          )}
        </div>
        <div className="cc-tile">
          <div className="cc-tile-label">Local</div>
          <MetricRow label="Modelo" value={readiness?.model.local.model ?? "—"} />
          <MetricRow label="Ollama" value={readiness?.model.local.online ? "online" : "offline"} />
          <MetricRow label="Modelo pronto" value={readiness?.model.local.modelReady ? "sim" : "não"} />
        </div>
        <div className="cc-tile">
          <div className="cc-tile-label">Cloud</div>
          <MetricRow label="Modelo" value={readiness?.model.cloud.model ?? "—"} />
          <MetricRow label="Configurado" value={readiness?.model.cloud.configured ? "sim" : "não"} />
        </div>
      </div>
    </div>
  );
}

export function HealthView({
  readiness,
  data,
  onToggleEmergencyStop,
}: {
  readiness: ReadinessPayload | null;
  data: CommandCenterPayload | null;
  onToggleEmergencyStop: (enabled: boolean) => void;
}) {
  const system = data?.system ?? {};
  const providers = readiness?.providers ?? {};

  return (
    <div style={{ padding: 24, overflowY: "auto" }}>
      <h2 className="cc-section-title">Estado do sistema</h2>
      <div className="cc-grid">
        <div className="cc-tile">
          <div className="cc-tile-label">Agente</div>
          <StatusIndicator state={readiness?.agent.state} />
          <MetricRow label="Modo de autonomia" value={readiness?.autonomyMode ?? "—"} />
        </div>
        <div className="cc-tile">
          <div className="cc-tile-label">Worker</div>
          <StatusIndicator state={readiness?.worker.state} />
          <MetricRow label="Fila" value={readiness?.worker.queue_size ?? 0} />
        </div>
        <div className="cc-tile">
          <div className="cc-tile-label">Voz</div>
          <StatusIndicator state={readiness?.voice.state} />
          {readiness?.voice.blockers?.length ? (
            <p className="dim" style={{ fontSize: 11, marginTop: 6 }}>{readiness.voice.blockers.join(" · ")}</p>
          ) : null}
        </div>
        <div className="cc-tile">
          <div className="cc-tile-label">Wake word</div>
          <StatusIndicator state={readiness?.wakeWord.state} />
          <p className="dim" style={{ fontSize: 11, marginTop: 6 }}>
            {readiness?.wakeWord.error ?? `modelo: ${readiness?.wakeWord.modelStatus ?? "—"}`}
          </p>
        </div>
        <div className="cc-tile">
          <div className="cc-tile-label">"{readiness?.wakePhrase.phrase ?? "hey nano"}"</div>
          <StatusIndicator state={readiness?.wakePhrase.state} />
          <p className="dim" style={{ fontSize: 11, marginTop: 6 }}>
            {readiness?.wakePhrase.error ?? `estado: ${readiness?.wakePhrase.turnState ?? "—"}`}
          </p>
        </div>
        <div className="cc-tile">
          <div className="cc-tile-label">Browser</div>
          <StatusIndicator state={readiness?.browser.state} />
        </div>
        <div className="cc-tile">
          <div className="cc-tile-label">Vision</div>
          <StatusIndicator state={readiness?.vision.state} />
        </div>
      </div>

      <h2 className="cc-section-title">Providers</h2>
      <div className="cc-grid">
        {Object.entries(providers).map(([name, state]) => (
          <div className="cc-tile" key={name}>
            <div className="cc-tile-label">{name}</div>
            <StatusIndicator state={String(state).toUpperCase()} label={String(state)} />
          </div>
        ))}
      </div>

      <h2 className="cc-section-title">Recursos</h2>
      <div className="card" style={{ maxWidth: 520 }}>
        <MetricRow label="CPU" value={`${system.cpu ?? 0}%`} />
        <Meter value={Number(system.cpu ?? 0)} tone="info" />
        <div style={{ height: 12 }} />
        <MetricRow label="RAM" value={`${system.ramUsed ?? 0} / ${system.ramTotal ?? 0} GB`} />
        <Meter value={Number(system.ram ?? 0)} tone="info" />
        <div style={{ height: 12 }} />
        <MetricRow label="Disco" value={`${system.diskUsed ?? 0} / ${system.diskTotal ?? 0} GB`} />
        <Meter value={Number(system.disk ?? 0)} tone="info" />
      </div>

      <h2 className="cc-section-title">Paragem de emergência</h2>
      <div className="card" style={{ maxWidth: 520 }}>
        <p className="muted" style={{ marginBottom: 12, fontSize: 13 }}>
          Bloqueia imediatamente toda a execução de ferramentas, em qualquer caminho.
          Nada corre enquanto estiver ativa.
        </p>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <StatusIndicator
            state={readiness?.emergencyStop ? "OFFLINE" : "READY"}
            label={readiness?.emergencyStop ? "Execução bloqueada" : "Execução permitida"}
          />
          <span style={{ flex: 1 }} />
          {readiness?.emergencyStop ? (
            <Button variant="primary" onClick={() => onToggleEmergencyStop(false)}>Retomar execução</Button>
          ) : (
            <Button variant="danger" onClick={() => onToggleEmergencyStop(true)}>Parar tudo</Button>
          )}
        </div>
      </div>
    </div>
  );
}

export function MemoryView({ facts }: { facts: Record<string, any> | null }) {
  const entries = Object.entries(facts ?? {});
  return (
    <div style={{ padding: 24, overflowY: "auto", maxWidth: 720 }}>
      <h2 className="cc-section-title">Memória persistente</h2>
      {entries.length === 0 ? (
        <EmptyState title="Sem factos guardados" hint="O Nano guarda aqui preferências duradouras que aprender sobre ti." />
      ) : (
        <div className="card">
          <dl className="kv">
            {entries.map(([key, value]) => (
              <React.Fragment key={key}>
                <dt>{key}</dt>
                <dd className="mono">{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd>
              </React.Fragment>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}

export function PluginsView({
  plugins,
  onOpenCode,
}: {
  plugins: Record<string, string[]>;
  onOpenCode: (name: string) => void;
}) {
  const entries = Object.entries(plugins ?? {});
  return (
    <div style={{ padding: 24, overflowY: "auto" }}>
      <h2 className="cc-section-title">Integrações</h2>
      <p className="dim" style={{ marginBottom: 16, maxWidth: "62ch" }}>
        Todas as ferramentas destes plugins correm pela autoridade central de execução:
        passam por policy, permissão e validação de scope antes de executar.
      </p>
      {entries.length === 0 ? (
        <EmptyState title="Nenhum plugin carregado" />
      ) : (
        <div className="cc-grid">
          {entries.map(([name, tools]) => (
            <button
              type="button"
              className="cc-tile card--interactive"
              key={name}
              onClick={() => onOpenCode(name)}
              style={{ textAlign: "left" }}
            >
              <div className="cc-tile-label">{name}</div>
              <div style={{ fontSize: 13, color: "var(--text-muted)" }}>{tools.length} ferramentas</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
                {tools.slice(0, 4).map((tool) => (
                  <span className="tool-chip tool-chip--muted" key={tool}>{tool}</span>
                ))}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function PermissionsView({
  data,
  policies,
  onResolve,
}: {
  data: CommandCenterPayload | null;
  policies: any[];
  onResolve: (id: string, decision: "deny" | "allow_once" | "allow_for_task") => void;
}) {
  const pending = data?.permissions ?? [];
  return (
    <div style={{ padding: 24, overflowY: "auto" }}>
      <h2 className="cc-section-title">Permissões pendentes ({pending.length})</h2>
      {pending.length === 0 ? (
        <EmptyState
          title="Nada à espera de decisão"
          hint="Quando o Nano precisar de sair do que pode fazer sozinho, o pedido aparece aqui com o alvo e o scope exactos."
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 720 }}>
          {pending.map((request: any) => (
            <div className="card" key={request.id}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
                <RiskBadge risk={request.risk} />
                <Badge tone="neutral">{request.action || request.capability}</Badge>
                {request.scope && <Badge tone="info">{request.scope}</Badge>}
              </div>
              <div className="mono" style={{ fontSize: 13, marginBottom: 10 }}>{request.target || "—"}</div>
              <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>{request.reason}</p>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <Button variant="danger" size="sm" onClick={() => onResolve(request.id, "deny")}>Recusar</Button>
                <Button variant="allow-once" size="sm" onClick={() => onResolve(request.id, "allow_once")}>Permitir uma vez</Button>
                <Button
                  variant="allow-task"
                  size="sm"
                  disabled={!request.task_id}
                  onClick={() => onResolve(request.id, "allow_for_task")}
                >
                  Permitir nesta tarefa
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <h2 className="cc-section-title">Policies</h2>
      <div className="cc-grid">
        {(policies ?? []).map((policy: any) => (
          <div className="cc-tile" key={policy.capability}>
            <div className="cc-tile-label">{policy.capability}</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
              <RiskBadge risk={policy.risk} />
              <Badge tone="neutral">{policy.decision}</Badge>
            </div>
            <p className="dim" style={{ fontSize: 11, marginTop: 6 }}>scope: {policy.scope}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
