/** Full lifecycle of one task: plan, execution, verification, retry, result. */
import React from "react";
import { ActivityTimeline } from "./Inspector";
import {
  Badge, Button, EmptyState, Meter, Modal, StatusIndicator,
  ToolChip, formatTime, sanitizeArgs,
} from "./ui";

export default function TaskDetailModal({
  visible,
  task,
  events,
  permissions,
  onClose,
  onCancel,
}: {
  visible: boolean;
  task: any;
  events: any[];
  permissions: any[];
  onClose: () => void;
  onCancel?: (taskId: string) => void;
}) {
  if (!visible) return null;

  if (!task) {
    return (
      <Modal open={visible} onClose={onClose} eyebrow="Task" title="Detalhe da tarefa">
        <EmptyState title="Tarefa não encontrada" />
      </Modal>
    );
  }

  const plan: string[] = task.metadata?.plan?.steps ?? [];
  const steps: any[] = Array.isArray(task.result?.steps) ? task.result.steps : [];
  const isTerminal = ["COMPLETED", "FAILED", "CANCELLED", "NEEDS_ATTENTION"].includes(task.status);

  return (
    <Modal
      open={visible}
      onClose={onClose}
      eyebrow={`Task · ${task.id?.slice(0, 8)}`}
      title={task.title || "Tarefa"}
      width="wide"
      footer={
        <>
          {onCancel && !isTerminal && (
            <Button variant="danger" onClick={() => onCancel(task.id)}>Cancelar tarefa</Button>
          )}
          <Button onClick={onClose}>Fechar</Button>
        </>
      }
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
        <StatusIndicator state={task.status} label={task.status} />
        <Badge tone="neutral">{task.task_type}</Badge>
        <Badge tone="info">prioridade {task.priority}</Badge>
        {task.retries > 0 && <Badge tone="neutral">{task.retries} retries</Badge>}
      </div>

      <Meter value={task.progress ?? 0} />
      <div className="tl-meta" style={{ marginTop: 6, marginBottom: 20 }}>
        {task.progress ?? 0}% · criada {formatTime(task.created_at)}
        {task.started_at ? ` · iniciada ${formatTime(task.started_at)}` : ""}
        {task.finished_at ? ` · terminada ${formatTime(task.finished_at)}` : ""}
      </div>

      {task.error && (
        <div className="error-state" style={{ marginBottom: 20 }}>{task.error}</div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
        <section>
          <div className="cc-tile-label">Plano</div>
          {plan.length ? (
            <ol style={{ paddingLeft: 18, display: "flex", flexDirection: "column", gap: 6 }}>
              {plan.map((step, index) => (
                <li key={index} style={{ fontSize: 13, color: "var(--text-muted)" }}>{step}</li>
              ))}
            </ol>
          ) : (
            <EmptyState title="Sem plano registado" />
          )}

          <div className="cc-tile-label" style={{ marginTop: 20 }}>Execução e verificação</div>
          {steps.length ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {steps.map((entry: any, index: number) => {
                const result = entry.result ?? {};
                const metadata = result.metadata ?? {};
                const args = sanitizeArgs(metadata.args);
                return (
                  <div className="card" key={index} style={{ padding: 12 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <ToolChip name={entry.step} muted={!result.success} />
                      <StatusIndicator
                        state={result.success ? "READY" : result.status === "permission_denied" ? "APPROVAL_REQUIRED" : "ERROR"}
                        label={result.status}
                      />
                      {metadata.verified !== undefined && (
                        <Badge tone={metadata.verified ? "accent" : "neutral"}>
                          {metadata.verified ? "verificado" : "não verificado"}
                        </Badge>
                      )}
                      {metadata.trust === "UNTRUSTED_EXTERNAL" && <Badge tone="info">externo</Badge>}
                    </div>
                    <dl className="kv" style={{ marginTop: 10 }}>
                      <dt>Capability</dt><dd className="mono">{metadata.capability ?? "—"}</dd>
                      <dt>Scope</dt><dd className="mono">{metadata.scope ?? "—"}</dd>
                      <dt>Duração</dt><dd className="mono">{metadata.duration_ms ?? "—"} ms</dd>
                      <dt>Retry policy</dt><dd className="mono">{metadata.retry_policy ?? "—"}</dd>
                      {metadata.verification && (<><dt>Verificação</dt><dd className="mono">{metadata.verification}</dd></>)}
                    </dl>
                    {args.length > 0 && (
                      <div className="perm-args" style={{ marginTop: 10 }}>
                        {args.map(({ key, value }) => (<div key={key}><strong>{key}</strong>: {value}</div>))}
                      </div>
                    )}
                    {result.error && <div className="error-state" style={{ marginTop: 10 }}>{result.error}</div>}
                  </div>
                );
              })}
            </div>
          ) : (
            <EmptyState title="Ainda sem passos executados" />
          )}
        </section>

        <section>
          <div className="cc-tile-label">Permissões relacionadas</div>
          {permissions?.length ? (
            permissions.map((request: any) => (
              <div className="card" key={request.id} style={{ padding: 12, marginBottom: 8 }}>
                <div className="mono" style={{ fontSize: 12 }}>{request.action || request.capability}</div>
                <div className="tl-meta">{request.target}</div>
              </div>
            ))
          ) : (
            <EmptyState title="Nenhuma permissão pendente" />
          )}

          <div className="cc-tile-label" style={{ marginTop: 20 }}>Eventos</div>
          <ActivityTimeline events={events as any} limit={20} />
        </section>
      </div>
    </Modal>
  );
}
