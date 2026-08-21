/**
 * The non-conversation pages: Tasks, Activity, Permissions, Agents, Memory,
 * Integrations and Status.
 *
 * Every value rendered here comes from the backend. Where the backend has
 * nothing, the page says so explicitly rather than showing a plausible zero.
 */
import React, { useMemo, useState } from "react";
import { ActivityTimeline, eventLabel } from "./Inspector";
import type {
  ActivityEvent, CommandCenterPayload, ProviderPayload,
  ReadinessPayload, TaskCounts, TaskRow,
} from "../lib/backend";
import {
  Badge, Button, ConfirmDialog, EmptyState, ErrorState, Meter, MetricRow,
  Panel, RiskBadge, StatusIndicator, Tabs, ToolChip,
  elapsedSince, formatDateTime, formatTime, sanitizeArgs, usageTone,
} from "./ui";

const TERMINAL = ["COMPLETED", "CANCELLED", "FAILED"];

/* ========================================================================
   TAREFAS
   ====================================================================== */

export type TaskScope = "active" | "attention" | "completed" | "cancelled" | "failed";

export function TasksPage({
  tasks, counts, scope, onScope, loading, onOpenTask, onCancelTask, onArchive, query, onQuery,
}: {
  tasks: TaskRow[] | null;
  counts: TaskCounts | null;
  scope: TaskScope;
  onScope: (scope: TaskScope) => void;
  loading: boolean;
  onOpenTask: (id: string) => void;
  onCancelTask: (id: string) => void;
  onArchive: () => void;
  query: string;
  onQuery: (value: string) => void;
}) {
  const [confirmArchive, setConfirmArchive] = useState(false);
  const byStatus = counts?.byStatus ?? {};

  const finishedCount =
    (byStatus.COMPLETED ?? 0) + (byStatus.CANCELLED ?? 0) + (byStatus.FAILED ?? 0);

  const visible = useMemo(() => {
    const rows = tasks ?? [];
    const needle = query.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((t) => t.title.toLowerCase().includes(needle) || t.id.startsWith(needle));
  }, [tasks, query]);

  return (
    <div className="page__inner page__inner--wide">
      <div className="inline" style={{ justifyContent: "space-between", marginBottom: 16 }}>
        <Tabs<TaskScope>
          value={scope} onChange={onScope}
          tabs={[
            { value: "active", label: "Ativas", count: counts?.active },
            { value: "attention", label: "Precisa de atenção", count: counts?.attention },
            { value: "completed", label: "Concluídas", count: byStatus.COMPLETED },
            { value: "cancelled", label: "Canceladas", count: byStatus.CANCELLED },
            { value: "failed", label: "Falhadas", count: byStatus.FAILED },
          ]}
        />
        <div className="inline">
          <input
            className="input" style={{ width: 200 }} placeholder="Procurar tarefa…"
            value={query} onChange={(e) => onQuery(e.target.value)} aria-label="Procurar tarefa"
          />
          <Button size="sm" onClick={() => setConfirmArchive(true)} disabled={finishedCount === 0}
                  title={finishedCount === 0 ? "Não há tarefas terminadas para arquivar" : undefined}>
            Arquivar terminadas ({finishedCount})
          </Button>
        </div>
      </div>

      {loading && !tasks ? (
        <EmptyState title="A carregar tarefas…" />
      ) : visible.length === 0 ? (
        <EmptyState
          title={scope === "active" ? "Nenhuma tarefa em execução" : "Nada nesta vista"}
          hint={scope === "active"
            ? "As tarefas em segundo plano aparecem aqui com estado e progresso reais."
            : "Muda de separador para ver tarefas noutro estado."}
        />
      ) : (
        <div className="stack stack--tight">
          {visible.map((task) => {
            const terminal = TERMINAL.includes(task.status);
            return (
              <div className="row-item" key={task.id}>
                <StatusIndicator state={task.status} label="" />
                <button type="button" className="row-item__main" style={{ background: "none" }}
                        onClick={() => onOpenTask(task.id)}>
                  <div className="row-item__title">{task.title}</div>
                  <div className="row-item__meta">
                    {task.status} · {task.progress}% · {task.task_type}
                    {task.retries ? ` · ${task.retries} tentativas` : ""} · {formatDateTime(task.updated_at)}
                  </div>
                </button>
                <div style={{ width: 80, flex: "none" }}><Meter value={task.progress ?? 0} /></div>
                <div className="row-item__actions">
                  <Button size="sm" onClick={() => onOpenTask(task.id)}>Detalhe</Button>
                  {!terminal && <Button size="sm" variant="danger" onClick={() => onCancelTask(task.id)}>Cancelar</Button>}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <ConfirmDialog
        open={confirmArchive}
        title="Arquivar tarefas terminadas?"
        confirmLabel="Arquivar"
        message={
          <>
            Vai remover {finishedCount} tarefa(s) concluídas, canceladas ou falhadas da fila.
            <br /><br />
            <strong>Isto limpa apenas a lista de tarefas.</strong> O registo de auditoria de
            permissões e o histórico de atividade mantêm-se intactos — são o registo de
            segurança e não são apagados a partir daqui.
          </>
        }
        onConfirm={() => { setConfirmArchive(false); onArchive(); }}
        onCancel={() => setConfirmArchive(false)}
      />
    </div>
  );
}

/* ========================================================================
   ATIVIDADE
   ====================================================================== */

export type ActivityFilter = "all" | "tasks" | "tools" | "permissions" | "voice" | "system" | "errors";

export function ActivityPage({
  events, filter, onFilter, loading,
}: {
  events: ActivityEvent[] | null;
  filter: ActivityFilter;
  onFilter: (filter: ActivityFilter) => void;
  loading: boolean;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="page__inner">
      <Tabs<ActivityFilter>
        value={filter} onChange={onFilter}
        tabs={[
          { value: "all", label: "Tudo" }, { value: "tasks", label: "Tarefas" },
          { value: "tools", label: "Ferramentas" }, { value: "permissions", label: "Permissões" },
          { value: "voice", label: "Voz" }, { value: "system", label: "Sistema" },
          { value: "errors", label: "Erros" },
        ]}
      />

      <div style={{ marginTop: 16 }}>
        {loading && !events ? (
          <EmptyState title="A carregar atividade…" />
        ) : !events?.length ? (
          <EmptyState title="Sem eventos" hint="A atividade aparece aqui assim que o Nano executar alguma coisa." />
        ) : (
          <div className="stack stack--tight">
            {events.map((event, index) => {
              const id = `${event.timestamp}-${index}`;
              const open = expanded === id;
              const payloadKeys = Object.keys(event.payload ?? {});
              return (
                <div className="row-item" key={id} style={{ alignItems: "flex-start" }}>
                  <div className="row-item__main">
                    <div className="inline">
                      <span className="row-item__title" style={{ flex: "none" }}>{eventLabel(event.event)}</span>
                      <Badge tone="neutral">{event.event}</Badge>
                    </div>
                    <div className="row-item__meta">
                      {formatDateTime(event.timestamp)}
                      {event.payload?.tool ? ` · ${event.payload.tool}` : ""}
                      {event.payload?.error ? ` · ${String(event.payload.error).slice(0, 60)}` : ""}
                    </div>
                    {open && payloadKeys.length > 0 && (
                      <div className="error-state__details" style={{ marginTop: 8 }}>
                        {payloadKeys.map((key) => (
                          <div key={key}>{key}: {String(event.payload[key]).slice(0, 200)}</div>
                        ))}
                      </div>
                    )}
                  </div>
                  {payloadKeys.length > 0 && (
                    <Button size="sm" variant="ghost" onClick={() => setExpanded(open ? null : id)}
                            aria-expanded={open}>
                      {open ? "ocultar" : "detalhes"}
                    </Button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/* ========================================================================
   PERMISSÕES
   ====================================================================== */

const SCOPE_LABEL: Record<string, string> = {
  current_workspace: "Dentro do workspace",
  current_project: "Dados da aplicação",
  explicit_target: "Fora do workspace",
  specific_path: "Caminho específico",
  external_service: "Serviço externo",
  system: "Sistema operativo",
};

const CAPABILITY_VERB: Record<string, string> = {
  "filesystem.read": "Ler o ficheiro", "filesystem.write": "Escrever no ficheiro",
  "filesystem.delete": "Apagar", "shell.execute": "Executar o comando",
  "process.start": "Iniciar o processo", "process.kill": "Terminar o processo",
  "browser.read": "Abrir e ler", "browser.interact": "Interagir com a página",
  "browser.submit": "Submeter na página", "external.send": "Enviar para o exterior",
  "git.write": "Escrever no repositório", "git.destructive": "Alterar o repositório de forma destrutiva",
  "credential.write": "Escrever credenciais em", "financial.transaction": "Executar uma transação em",
  "system": "Alterar uma definição do sistema",
};

function describeIntent(request: any): string {
  const capability = String(request.capability || request.action || "").toLowerCase();
  const target = request.target && request.target !== "-" ? request.target : null;
  const verb = CAPABILITY_VERB[capability] ?? `Executar '${capability || "ação desconhecida"}'`;
  return target ? `${verb}: ${target}` : verb;
}

export function PermissionCard({
  request, onResolve, busy,
}: {
  request: any;
  onResolve: (id: string, decision: "deny" | "allow_once" | "allow_for_task") => void;
  busy?: boolean;
}) {
  const id = request.id || request.request_id || "";
  const risk = String(request.risk || "medium").toLowerCase();
  const details = sanitizeArgs(request.args);
  const capability = request.capability || request.action || "unknown";
  const hasTask = Boolean(request.task_id);
  const hasTarget = Boolean(request.target && request.target !== "-");

  return (
    <article className={`perm-card perm-card--${risk}`} aria-label={`Pedido de permissão: ${capability}`}>
      <header className="perm-card__head">
        <RiskBadge risk={risk} />
        <Badge tone="neutral">{capability}</Badge>
        {request.scope && <Badge tone="info">{SCOPE_LABEL[request.scope] ?? request.scope}</Badge>}
      </header>

      <h3 className="perm-card__intent">{describeIntent(request)}</h3>

      <div className="perm-card__body">
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
            <div className="section-label" style={{ marginBottom: 6 }}>Argumentos</div>
            <div className="perm-card__args">
              {details.map(({ key, value }) => (
                <div key={key}><strong>{key}</strong>: {value}</div>
              ))}
            </div>
          </div>
        )}
      </div>

      <footer className="perm-card__actions">
        <Button variant="danger" onClick={() => onResolve(id, "deny")} disabled={busy}>Recusar</Button>
        <Button variant="allow-once" onClick={() => onResolve(id, "allow_once")} disabled={busy}>
          Permitir uma vez
        </Button>
        <Button
          variant="allow-task" onClick={() => onResolve(id, "allow_for_task")}
          disabled={busy || !hasTask || !hasTarget}
          title={
            !hasTask ? "Só disponível para pedidos associados a uma tarefa"
              : !hasTarget ? "Precisa de um alvo concreto"
              : "Válido só para esta capability, este alvo e este scope, dentro desta tarefa"
          }
        >
          Permitir nesta tarefa
        </Button>
      </footer>
    </article>
  );
}

export function PermissionsPage({
  pending, policies, auditEvents, onResolve, busy,
}: {
  pending: any[];
  policies: any[];
  auditEvents: ActivityEvent[];
  onResolve: (id: string, decision: "deny" | "allow_once" | "allow_for_task") => void;
  busy?: boolean;
}) {
  return (
    <div className="page__inner">
      <h2 className="page-title">
        Pedidos pendentes
        {pending.length > 0 && <Badge tone="accent">{pending.length}</Badge>}
      </h2>
      {pending.length === 0 ? (
        <EmptyState
          title="Nada à espera de decisão"
          hint="Quando o Nano precisar de sair do que pode fazer sozinho, o pedido aparece aqui com o alvo e o scope exactos."
        />
      ) : (
        <div className="stack">
          {pending.map((request) => (
            <PermissionCard key={request.id || request.request_id} request={request}
                            onResolve={onResolve} busy={busy} />
          ))}
        </div>
      )}

      <h2 className="page-title">Decisões recentes</h2>
      {auditEvents.length === 0 ? (
        <EmptyState title="Sem decisões registadas" />
      ) : (
        <div className="card"><ActivityTimeline events={auditEvents} limit={20} /></div>
      )}

      <h2 className="page-title">
        Policies <Badge tone="neutral">{policies.length}</Badge>
      </h2>
      <p className="dim" style={{ marginBottom: 12, maxWidth: "64ch" }}>
        A autorização permanente está desativada por desenho: nenhuma capability pode ser
        concedida para sempre. As decisões são sempre por execução ou por tarefa.
      </p>
      <div className="grid-auto">
        {policies.map((policy: any) => (
          <div className="tile" key={policy.capability}>
            <div className="tile__label">{policy.capability}</div>
            <div className="inline" style={{ marginTop: 4 }}>
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

/* ========================================================================
   AGENTES
   ====================================================================== */

export function AgentsPage({ agents }: { agents: any[] | null }) {
  const [selected, setSelected] = useState<any | null>(null);
  const list = agents ?? [];

  return (
    <div className="page__inner">
      <h2 className="page-title">Agentes registados <Badge tone="info">experimental</Badge></h2>
      <p className="dim" style={{ marginBottom: 16, maxWidth: "68ch" }}>
        Estes agentes estão registados e são seleccionados ao planear uma tarefa, mas
        ainda não despacham execução própria: todas as ferramentas correm pela autoridade
        central de execução. O estado reflecte o registo, não capacidade autónoma.
      </p>

      {list.length === 0 ? (
        <EmptyState title="Sem agentes registados" />
      ) : (
        <div className="grid-auto">
          {list.map((agent: any) => (
            <button type="button" className="tile card--interactive" key={agent.name}
                    onClick={() => setSelected(agent)} style={{ textAlign: "left" }}>
              <div className="tile__label">{agent.name}</div>
              <StatusIndicator state={agent.state || "EXPERIMENTAL"} />
              <p className="dim" style={{ fontSize: 12, marginTop: 8 }}>{agent.description}</p>
              <div className="inline" style={{ marginTop: 8 }}>
                {(agent.capabilities ?? []).slice(0, 3).map((c: string) => <ToolChip key={c} name={c} muted />)}
                {(agent.capabilities ?? []).length > 3 && (
                  <span className="dim" style={{ fontSize: 11 }}>+{agent.capabilities.length - 3}</span>
                )}
              </div>
            </button>
          ))}
        </div>
      )}

      {selected && (
        <div className="card" style={{ marginTop: 20 }}>
          <div className="inline" style={{ justifyContent: "space-between", marginBottom: 12 }}>
            <h3 style={{ fontSize: 16, fontWeight: 600 }}>{selected.name}</h3>
            <Button size="sm" variant="ghost" onClick={() => setSelected(null)}>Fechar</Button>
          </div>
          <p className="muted" style={{ marginBottom: 12 }}>{selected.description}</p>
          <dl className="kv">
            <dt>Estado</dt><dd><StatusIndicator state={selected.state} /></dd>
            <dt>Tipos</dt><dd>{(selected.taskTypes ?? []).join(", ") || "—"}</dd>
          </dl>
          <div className="section-label" style={{ margin: "14px 0 6px" }}>Capabilities</div>
          <div className="inline">{(selected.capabilities ?? []).map((c: string) => <ToolChip key={c} name={c} muted />)}</div>
          <div className="section-label" style={{ margin: "14px 0 6px" }}>Ferramentas</div>
          <div className="inline">{(selected.tools ?? []).map((t: string) => <ToolChip key={t} name={t} />)}</div>
        </div>
      )}
    </div>
  );
}

/* ========================================================================
   MEMÓRIA
   ====================================================================== */

export function MemoryPage({
  memory, onForget, loading,
}: {
  memory: any | null;
  onForget: (key: string) => void;
  loading: boolean;
}) {
  const [query, setQuery] = useState("");
  const [confirmKey, setConfirmKey] = useState<string | null>(null);

  const facts = useMemo(() => {
    const all = memory?.facts ?? [];
    const needle = query.trim().toLowerCase();
    if (!needle) return all;
    return all.filter((f: any) => f.key.toLowerCase().includes(needle) || String(f.value).toLowerCase().includes(needle));
  }, [memory, query]);

  if (loading && !memory) return <div className="page__inner"><EmptyState title="A carregar memória…" /></div>;

  const profileEntries = Object.entries(memory?.profile ?? {});

  return (
    <div className="page__inner">
      <h2 className="page-title">Perfil</h2>
      {profileEntries.length === 0 ? (
        <EmptyState title="Sem perfil guardado" hint="O Nano guarda aqui preferências duradouras que aprender sobre ti." />
      ) : (
        <div className="card">
          <dl className="kv">
            {profileEntries.map(([key, value]) => (
              <React.Fragment key={key}>
                <dt>{key}</dt>
                <dd className="mono">{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd>
              </React.Fragment>
            ))}
          </dl>
        </div>
      )}

      <div className="inline" style={{ justifyContent: "space-between", margin: "24px 0 12px" }}>
        <h2 className="page-title" style={{ margin: 0 }}>
          Factos guardados <Badge tone="neutral">{memory?.facts?.length ?? 0}</Badge>
        </h2>
        <input className="input" style={{ width: 220 }} placeholder="Procurar na memória…"
               value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Procurar na memória" />
      </div>

      {facts.length === 0 ? (
        <EmptyState title="Nada guardado" hint="Diz ao Nano para se lembrar de alguma coisa e aparece aqui." />
      ) : (
        <div className="stack stack--tight">
          {facts.map((fact: any) => (
            <div className="row-item" key={fact.key}>
              <div className="row-item__main">
                <div className="row-item__title mono">{fact.key}</div>
                <div className="row-item__meta">{fact.value}</div>
              </div>
              <Button size="sm" variant="danger" onClick={() => setConfirmKey(fact.key)}>Esquecer</Button>
            </div>
          ))}
        </div>
      )}

      <h2 className="page-title">Documentos (RAG)</h2>
      {memory?.documentsSupported ? (
        <EmptyState title="Sem documentos indexados" />
      ) : (
        <div className="card">
          <StatusIndicator state="SETUP_REQUIRED" label="Indexação de documentos indisponível" />
          <p className="dim" style={{ fontSize: 12, marginTop: 8 }}>{memory?.documentsNote}</p>
        </div>
      )}

      <h2 className="page-title">Conversa</h2>
      <div className="card">
        <MetricRow label="Mensagens guardadas" value={memory?.messageCount ?? 0} />
        <p className="dim" style={{ fontSize: 12, marginTop: 8 }}>
          O histórico fica apenas neste computador, numa base de dados local.
        </p>
      </div>

      <ConfirmDialog
        open={Boolean(confirmKey)} danger
        title="Esquecer este facto?"
        confirmLabel="Esquecer"
        message={<>O Nano vai deixar de saber <strong>{confirmKey}</strong>. Isto não pode ser desfeito.</>}
        onConfirm={() => { if (confirmKey) onForget(confirmKey); setConfirmKey(null); }}
        onCancel={() => setConfirmKey(null)}
      />
    </div>
  );
}

/* ========================================================================
   INTEGRAÇÕES
   ====================================================================== */

export function IntegrationsPage({
  providers, plugins, readiness, onOpenSettings, onOpenPlugin,
}: {
  providers: ProviderPayload | null;
  plugins: Record<string, string[]>;
  readiness: ReadinessPayload | null;
  onOpenSettings: () => void;
  onOpenPlugin: (name: string) => void;
}) {
  const pluginEntries = Object.entries(plugins ?? {});
  const toolTotal = pluginEntries.reduce((sum, [, tools]) => sum + tools.length, 0);

  return (
    <div className="page__inner">
      <h2 className="page-title">Provedores de IA</h2>
      <div className="grid-auto">
        {providers ? (
          <>
            <div className="tile">
              <div className="inline" style={{ justifyContent: "space-between", marginBottom: 8 }}>
                <div className="tile__label" style={{ margin: 0 }}>Groq · Cloud</div>
                <Badge tone="accent">principal</Badge>
              </div>
              <StatusIndicator state={providers.groq.state} />
              <MetricRow label="Modelo" value={providers.groq.model || "—"} />
              <MetricRow label="Chave" value={providers.groq.secret.configured ? providers.groq.secret.masked : "não configurada"} />
              <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>{providers.groq.detail}</p>
              <Button size="sm" block style={{ marginTop: 10 }} onClick={onOpenSettings}>Configurar</Button>
            </div>
            <div className="tile">
              <div className="inline" style={{ justifyContent: "space-between", marginBottom: 8 }}>
                <div className="tile__label" style={{ margin: 0 }}>Ollama · Local</div>
                <Badge tone="info">fallback</Badge>
              </div>
              <StatusIndicator state={providers.ollama.state} />
              <MetricRow label="Modelo" value={providers.ollama.model || "—"} />
              <MetricRow label="API" value={providers.ollama.url ?? "—"} />
              <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>{providers.ollama.detail}</p>
              <Button size="sm" block style={{ marginTop: 10 }} onClick={onOpenSettings}>Configurar</Button>
            </div>
          </>
        ) : <EmptyState title="Sem informação de provedores" />}
      </div>

      <h2 className="page-title">
        Componentes locais <Badge tone="neutral">{pluginEntries.length}</Badge>
      </h2>
      <p className="dim" style={{ marginBottom: 12, maxWidth: "68ch" }}>
        {toolTotal} ferramentas carregadas. Todas correm pela autoridade central de execução:
        passam por policy, permissão e validação de scope antes de executar.
      </p>
      {pluginEntries.length === 0 ? (
        <EmptyState title="Nenhum componente carregado" />
      ) : (
        <div className="grid-auto">
          {pluginEntries.map(([name, tools]) => (
            <button type="button" className="tile card--interactive" key={name}
                    onClick={() => onOpenPlugin(name)} style={{ textAlign: "left" }}>
              <div className="tile__label">{name}</div>
              <div style={{ fontSize: 13, color: "var(--text-muted)" }}>{tools.length} ferramentas</div>
              <div className="inline" style={{ marginTop: 8 }}>
                {tools.slice(0, 3).map((tool) => <ToolChip key={tool} name={tool} muted />)}
                {tools.length > 3 && <span className="dim" style={{ fontSize: 11 }}>+{tools.length - 3}</span>}
              </div>
            </button>
          ))}
        </div>
      )}

      <h2 className="page-title">Capacidades externas</h2>
      <div className="grid-auto">
        <div className="tile">
          <div className="tile__label">Browser</div>
          <StatusIndicator state={readiness?.browser.state} />
          <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>
            Navegação e extração web. Requer o Playwright instalado.
          </p>
        </div>
        <div className="tile">
          <div className="tile__label">Visão</div>
          <StatusIndicator state={readiness?.vision.state} />
          <p className="dim" style={{ fontSize: 11, marginTop: 8 }}>Ainda não implementado nesta versão.</p>
        </div>
      </div>
    </div>
  );
}

/* ========================================================================
   ESTADO
   ====================================================================== */

export function StatusPage({
  readiness, providers, commandCenter, onToggleEmergencyStop,
}: {
  readiness: ReadinessPayload | null;
  providers: ProviderPayload | null;
  commandCenter: CommandCenterPayload | null;
  onToggleEmergencyStop: (enabled: boolean) => void;
}) {
  const system = commandCenter?.system ?? {};
  const [confirmStop, setConfirmStop] = useState(false);

  const services = [
    { name: "Motor do Nano", state: readiness ? "READY" : "BACKEND_OFFLINE", note: "Servidor Python e ponte com a UI" },
    { name: "Groq (cloud)", state: providers?.groq.state, note: providers?.groq.detail },
    { name: "Ollama (local)", state: providers?.ollama.state, note: providers?.ollama.detail },
    { name: "Voz / STT", state: readiness?.voice.state, note: readiness?.voice.blockers?.join(" · ") || "Transcrição local" },
    { name: "Wake phrase", state: readiness?.wakePhrase.state, note: `"${readiness?.wakePhrase.phrase ?? "hey nano"}"` },
    { name: "Worker de tarefas", state: readiness?.worker.state, note: `Fila: ${readiness?.worker.queue_size ?? 0}` },
    { name: "Browser", state: readiness?.browser.state, note: "Requer Playwright" },
  ];

  return (
    <div className="page__inner">
      <h2 className="page-title">Serviços</h2>
      <div className="stack stack--tight">
        {services.map((service) => (
          <div className="row-item" key={service.name}>
            <div className="row-item__main">
              <div className="row-item__title">{service.name}</div>
              <div className="row-item__meta">{service.note || "—"}</div>
            </div>
            <StatusIndicator state={service.state} />
          </div>
        ))}
      </div>

      <h2 className="page-title">Recursos</h2>
      <div className="grid-auto">
        <div className="tile">
          <div className="tile__label">CPU</div>
          <div className="tile__value">{system.cpu ?? 0}%</div>
          <Meter value={Number(system.cpu ?? 0)} tone={usageTone(Number(system.cpu ?? 0))} />
        </div>
        <div className="tile">
          <div className="tile__label">Memória</div>
          <div className="tile__value">{system.ram ?? 0}%</div>
          <Meter value={Number(system.ram ?? 0)} tone={usageTone(Number(system.ram ?? 0))} />
          <p className="dim" style={{ fontSize: 11, marginTop: 6 }}>
            {system.ramUsed ?? 0} / {system.ramTotal ?? 0} GB
          </p>
        </div>
        <div className="tile">
          <div className="tile__label">Disco</div>
          <div className="tile__value">{system.disk ?? 0}%</div>
          <Meter value={Number(system.disk ?? 0)} tone={usageTone(Number(system.disk ?? 0))} />
          <p className="dim" style={{ fontSize: 11, marginTop: 6 }}>
            {system.diskUsed ?? 0} / {system.diskTotal ?? 0} GB
          </p>
        </div>
      </div>
      {/* Temperature is deliberately absent: psutil cannot read CPU temperature
          reliably on Windows, and a fabricated number is worse than none. */}
      <p className="dim" style={{ fontSize: 11, marginTop: 10 }}>
        A temperatura do CPU não é apresentada: não é legível de forma fiável nesta
        plataforma, e um valor inventado seria pior do que nenhum.
      </p>

      <h2 className="page-title">Segurança</h2>
      <div className="card" style={{ maxWidth: 560 }}>
        <MetricRow label="Modo de autonomia" value={readiness?.autonomyMode ?? "—"} />
        <div style={{ height: 12 }} />
        <p className="muted" style={{ marginBottom: 12, fontSize: 13 }}>
          A paragem de emergência bloqueia imediatamente toda a execução de ferramentas,
          em qualquer caminho. Nada corre enquanto estiver ativa.
        </p>
        <div className="inline">
          <StatusIndicator
            state={readiness?.emergencyStop ? "OFFLINE" : "READY"}
            label={readiness?.emergencyStop ? "Execução bloqueada" : "Execução permitida"}
          />
          <span style={{ flex: 1 }} />
          {readiness?.emergencyStop ? (
            <Button variant="primary" onClick={() => onToggleEmergencyStop(false)}>Retomar execução</Button>
          ) : (
            <Button variant="danger" onClick={() => setConfirmStop(true)}>Parar tudo</Button>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirmStop} danger
        title="Parar toda a execução?"
        confirmLabel="Parar tudo"
        message="Nenhuma ferramenta poderá correr até retomares. As tarefas em curso são bloqueadas."
        onConfirm={() => { setConfirmStop(false); onToggleEmergencyStop(true); }}
        onCancel={() => setConfirmStop(false)}
      />
    </div>
  );
}
