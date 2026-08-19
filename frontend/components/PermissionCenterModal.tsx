/**
 * Permission Center.
 *
 * The rule this component exists to enforce: a person is never asked to approve
 * "an operation". They see the capability, the concrete target, the scope, the
 * risk, the reason and the tool that will run — then choose.
 */
import React from "react";
import { Badge, Button, EmptyState, Modal, RiskBadge, sanitizeArgs, StatusIndicator } from "./ui";

export type PermissionRequest = {
  id?: string;
  request_id?: string;
  action?: string;
  capability?: string;
  tool?: string;
  task_id?: string;
  target?: string;
  scope?: string;
  reason?: string;
  risk?: string;
  status?: string;
  agent?: string;
  requested_at?: string;
  args?: Record<string, any>;
};

export type PermissionDecision = "deny" | "allow_once" | "allow_for_task";

/** Plain-language description of what is about to happen. Never vague. */
function describeIntent(request: PermissionRequest): string {
  const capability = String(request.capability || request.action || "").toLowerCase();
  const target = request.target && request.target !== "-" ? request.target : null;
  const verbs: Record<string, string> = {
    "filesystem.read": "Ler o ficheiro",
    "filesystem.write": "Escrever no ficheiro",
    "filesystem.delete": "Apagar",
    "shell.execute": "Executar o comando",
    "process.start": "Iniciar o processo",
    "process.kill": "Terminar o processo",
    "browser.read": "Abrir e ler",
    "browser.interact": "Interagir com a página",
    "browser.submit": "Submeter na página",
    "external.send": "Enviar para o exterior",
    "git.write": "Escrever no repositório",
    "git.destructive": "Alterar o repositório de forma destrutiva",
    "credential.write": "Escrever credenciais em",
    "financial.transaction": "Executar uma transação financeira em",
    "system": "Alterar uma definição do sistema",
  };
  const verb = verbs[capability] ?? `Executar '${capability || "ação desconhecida"}'`;
  return target ? `${verb}: ${target}` : verb;
}

const SCOPE_LABEL: Record<string, string> = {
  current_workspace: "Dentro do workspace",
  current_project: "Dados da aplicação",
  explicit_target: "Fora do workspace",
  specific_path: "Caminho específico",
  external_service: "Serviço externo",
  system: "Sistema operativo",
};

export function PermissionCard({
  request,
  onResolve,
  busy,
}: {
  request: PermissionRequest;
  onResolve: (id: string, decision: PermissionDecision) => void;
  busy?: boolean;
}) {
  const id = request.id || request.request_id || "";
  const risk = String(request.risk || "medium").toLowerCase();
  const details = sanitizeArgs(request.args);
  const capability = request.capability || request.action || "unknown";
  const hasTask = Boolean(request.task_id);

  return (
    <article className={`perm-card perm-card--${risk}`} aria-label={`Pedido de permissão: ${capability}`}>
      <header className="perm-head">
        <RiskBadge risk={risk} />
        <Badge tone="neutral">{capability}</Badge>
        {request.scope && <Badge tone="info">{SCOPE_LABEL[request.scope] ?? request.scope}</Badge>}
      </header>

      <h3 className="perm-intent">{describeIntent(request)}</h3>

      <div className="perm-body">
        <dl className="kv">
          <dt>Capability</dt><dd className="mono">{capability}</dd>
          <dt>Alvo</dt><dd className="mono">{request.target || "—"}</dd>
          <dt>Scope</dt><dd>{SCOPE_LABEL[request.scope ?? ""] ?? request.scope ?? "—"}</dd>
          <dt>Ferramenta</dt><dd className="mono">{request.tool || capability}</dd>
          <dt>Motivo</dt><dd>{request.reason || "Pedido pela tarefa atual."}</dd>
          <dt>Tarefa</dt><dd className="mono">{request.task_id || "—"}</dd>
        </dl>

        {details.length > 0 && (
          <div>
            <div className="cc-tile-label">Argumentos</div>
            <div className="perm-args">
              {details.map(({ key, value }) => (
                <div key={key}><strong>{key}</strong>: {value}</div>
              ))}
            </div>
          </div>
        )}
      </div>

      <footer className="perm-actions">
        <Button variant="danger" onClick={() => onResolve(id, "deny")} disabled={busy}>
          Recusar
        </Button>
        <Button variant="allow-once" onClick={() => onResolve(id, "allow_once")} disabled={busy}>
          Permitir uma vez
        </Button>
        <Button
          variant="allow-task"
          onClick={() => onResolve(id, "allow_for_task")}
          disabled={busy || !hasTask || !request.target || request.target === "-"}
          title={
            !hasTask
              ? "Só disponível para pedidos associados a uma tarefa"
              : "Válido apenas para esta capability, este alvo e este scope, dentro desta tarefa"
          }
        >
          Permitir nesta tarefa
        </Button>
      </footer>
    </article>
  );
}

export default function PermissionCenterModal({
  visible,
  requests,
  policies,
  onClose,
  onResolve,
  busy,
}: {
  visible: boolean;
  requests: PermissionRequest[];
  policies: any[];
  onClose: () => void;
  onResolve: (id: string, decision: PermissionDecision) => void;
  busy?: boolean;
}) {
  return (
    <Modal
      open={visible}
      onClose={onClose}
      eyebrow="Permission center"
      title="Pedidos de autorização"
      width="wide"
    >
      <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 20 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div className="cc-tile-label">Pendentes ({requests.length})</div>
          {requests.length === 0 ? (
            <EmptyState
              title="Nenhum pedido pendente"
              hint="O Nano só pede autorização quando uma ação sai do que pode fazer sozinho."
            />
          ) : (
            requests.map((request) => (
              <PermissionCard
                key={request.id || request.request_id}
                request={request}
                onResolve={onResolve}
                busy={busy}
              />
            ))
          )}
        </div>

        <div>
          <div className="cc-tile-label">Policies ativas</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {(policies || []).map((policy: any) => (
              <div className="card" key={policy.capability} style={{ padding: 12 }}>
                <div className="mono" style={{ fontWeight: 600, fontSize: 12 }}>{policy.capability}</div>
                <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                  <RiskBadge risk={policy.risk} />
                  <StatusIndicator
                    state={policy.decision === "approval_required" ? "APPROVAL_REQUIRED" : policy.decision === "deny" ? "OFFLINE" : "READY"}
                    label={policy.decision}
                  />
                </div>
                <div className="tl-meta" style={{ marginTop: 6 }}>scope: {policy.scope}</div>
              </div>
            ))}
            {!policies?.length && <EmptyState title="Sem policies carregadas" />}
          </div>
        </div>
      </div>
    </Modal>
  );
}
