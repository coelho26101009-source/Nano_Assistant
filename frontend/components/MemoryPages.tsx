/**
 * Memória: what Nano remembers, what it knows, and how the two connect.
 *
 * Three views, one section:
 *
 *   Memórias      the durable facts, with their provenance and their controls
 *   Second Brain  the entities those facts are about
 *   Grafo         the same entities, drawn
 *
 * THE PRODUCT ARGUMENT
 * --------------------
 * A memory the user cannot see is a memory they cannot trust. So every row here
 * shows where it came from — the user asked for it, the user typed it, or Nano
 * inferred it — and an inferred one is visibly a SUGGESTION until the user
 * promotes it. Nothing is presented as an established fact because Nano guessed
 * well.
 *
 * The same honesty applies to the mechanism: the footer names the retrieval
 * engine that is actually running (SQLite FTS5 with BM25, or the degraded
 * textual fallback) rather than implying a semantic search this build does not
 * perform.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";

import {
  Badge, Button, ConfirmDialog, EmptyState, Field, Modal, Panel,
  Skeleton, StatusIndicator, Tabs, formatDateTime,
} from "./ui";
import type {
  KnowledgeEdge, KnowledgeGraphPayload, KnowledgeNode, Memory, MemoryOverview,
} from "../lib/memory";
import { kindLabel, nodeTypeLabel, originLabel, relationLabel } from "../lib/memory";

/* ======================================================================
   Shared bits
   ==================================================================== */

const Glyph = ({ d, size = 15 }: { d: string; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);

const PIN = "M12 17v5M9 3h6l-1 6 3 3v2H7v-2l3-3-1-6Z";
const SEARCH = "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm10 2-4.35-4.35";

/** The stored value of a profile entry, whether or not it is wrapped.
 *
 * memory.remember_preference stores each entry as {value, source, updated_at}.
 * Dumping that object rendered literal JSON in the user's face; show the value,
 * and let the provenance sit quietly beside it. */
function profileValue(entry: any): string {
  const raw = entry && typeof entry === "object" && "value" in entry ? entry.value : entry;
  if (raw === null || raw === undefined) return "—";
  return typeof raw === "object" ? JSON.stringify(raw) : String(raw);
}

/** Where the entry came from, when the backend recorded it. */
function profileSource(entry: any): string {
  return entry && typeof entry === "object" && typeof entry.source === "string" ? entry.source : "";
}

/** The provenance chip. Reading it should answer "why does Nano know this?". */
function OriginBadge({ memory }: { memory: Memory }) {
  const suggestion = memory.status === "candidate";
  return (
    <span className={`origin-chip origin-chip--${suggestion ? "candidate" : memory.origin}`}
          title={originLabel(String(memory.origin))}>
      {suggestion ? "sugestão" : originLabel(String(memory.origin))}
    </span>
  );
}

/**
 * How the search box behaves everywhere in this section.
 *
 * Filtering happens on the rows already fetched. The backend has a ranked
 * search too (`search_memories`), and it is what the model uses; typing in a
 * list is a different job — it must be instant and it must never reorder the
 * list under the user's cursor.
 */
function SearchField({
  value, onChange, placeholder, id,
}: { value: string; onChange: (v: string) => void; placeholder: string; id: string }) {
  return (
    <span className="search search--inline">
      <span className="search__icon"><Glyph d={SEARCH} size={14} /></span>
      <label className="sr-only" htmlFor={id}>{placeholder}</label>
      <input id={id} className="input" type="search" placeholder={placeholder}
             value={value} onChange={(event) => onChange(event.target.value)} />
    </span>
  );
}

function RetrievalFooter({ overview }: { overview: MemoryOverview | null }) {
  const retrieval = overview?.retrieval;
  if (!retrieval) return null;
  return (
    <p className="dim memory-footnote">
      Pesquisa: <strong>{retrieval.engine}</strong> · {retrieval.entries} entradas
      indexadas · tudo neste computador, nada é enviado para fora.
    </p>
  );
}

/* ======================================================================
   MEMÓRIAS
   ==================================================================== */

type MemoryScope = "active" | "candidate" | "archived";

const SCOPES: { id: MemoryScope; label: string }[] = [
  { id: "active", label: "Ativas" },
  { id: "candidate", label: "Sugestões" },
  { id: "archived", label: "Arquivadas" },
];

export function MemoriesPage({
  overview, loading, onCreate, onUpdate, onDelete, onClearAll, onOpenSettings,
}: {
  overview: MemoryOverview | null;
  loading: boolean;
  onCreate: (text: string, kind: string, importance: number) => void;
  onUpdate: (id: string, patch: Record<string, any>) => void;
  onDelete: (id: string) => void;
  onClearAll: () => void;
  onOpenSettings: () => void;
}) {
  const [scope, setScope] = useState<MemoryScope>("active");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [editing, setEditing] = useState<Memory | null>(null);
  const [draft, setDraft] = useState("");
  const [creating, setCreating] = useState(false);
  const [newText, setNewText] = useState("");
  const [newKind, setNewKind] = useState("fact");
  const [confirmDelete, setConfirmDelete] = useState<Memory | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);

  const memories = overview?.memories ?? [];
  const kinds = overview?.kinds ?? [];
  const profileEntries = Object.entries(overview?.profile ?? {});

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return memories
      .filter((memory) => memory.status === scope)
      .filter((memory) => !kind || memory.kind === kind)
      .filter((memory) => !needle
        || memory.text.toLowerCase().includes(needle)
        || memory.tags.some((tag) => tag.toLowerCase().includes(needle)));
  }, [memories, scope, kind, query]);

  const counts = useMemo(() => ({
    active: memories.filter((m) => m.status === "active").length,
    candidate: memories.filter((m) => m.status === "candidate").length,
    archived: memories.filter((m) => m.status === "archived").length,
  }), [memories]);

  if (loading && !overview) {
    return (
      <div className="page__inner stack">
        <Skeleton height={56} /><Skeleton height={72} /><Skeleton height={72} />
      </div>
    );
  }

  if (overview && !overview.ready) {
    // The state is READ from the migration report, never asserted. A literal
    // here would claim a failure the UI had not measured -- which is the exact
    // thing tests/test_ui_contract.py forbids, and for good reason: a hardcoded
    // status is a status that stays wrong after the cause is fixed.
    const migrationState = overview.migration?.ok === false ? "ERROR" : "UNKNOWN";
    return (
      <div className="page__inner">
        <Panel title="Memória indisponível">
          <StatusIndicator state={migrationState}
                           label="A base de dados não pôde ser migrada" />
          <p className="muted" style={{ fontSize: 13, marginTop: 10, lineHeight: 1.7 }}>
            O Nano continua a responder, mas não guarda conversas nem memórias nesta
            sessão. Nada foi apagado — a base de dados anterior está intacta.
          </p>
          {overview.migration?.error && (
            <p className="dim mono" style={{ fontSize: 11, marginTop: 8 }}>
              {overview.migration.error}
            </p>
          )}
        </Panel>
      </div>
    );
  }

  return (
    <div className="page__inner">
      <div className="memory-head">
        <div className="memory-head__stats">
          <span className="stat-chip"><strong>{counts.active}</strong> ativas</span>
          <span className="stat-chip"><strong>{counts.candidate}</strong> sugestões</span>
          <span className="stat-chip">
            <strong>{overview?.knowledge?.nodes ?? 0}</strong> nós no Second Brain
          </span>
          <span className="stat-chip">
            <strong>{overview?.conversationCount ?? 0}</strong> conversas
          </span>
        </div>
        <span className="stage__spacer" />
        <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
          Adicionar memória
        </Button>
      </div>

      {!overview?.longTermEnabled && (
        <div className="notice notice--warn">
          <strong>A memória de longo prazo está desligada.</strong> O Nano não guarda
          nem consulta nada entre conversas. O que já está guardado continua aqui.
          <Button size="sm" onClick={onOpenSettings}>Abrir definições</Button>
        </div>
      )}

      <div className="memory-toolbar">
        <Tabs
          value={scope}
          onChange={(next) => setScope(next as MemoryScope)}
          tabs={SCOPES.map((entry) => ({
            value: entry.id,
            label: entry.label,
            count: counts[entry.id],
          }))}
        />
        <span className="stage__spacer" />
        <select className="input input--select" value={kind} aria-label="Filtrar por tipo"
                onChange={(event) => setKind(event.target.value)}>
          <option value="">Todos os tipos</option>
          {kinds.map((entry) => (
            <option key={entry} value={entry}>{kindLabel(entry)}</option>
          ))}
        </select>
        <SearchField id="memory-search" value={query} onChange={setQuery}
                     placeholder="Procurar nas memórias…" />
      </div>

      {scope === "candidate" && counts.candidate > 0 && (
        <p className="dim memory-note">
          Estas são coisas que o Nano <em>reparou</em> durante as conversas. Não são
          usadas nas respostas enquanto não as guardares.
        </p>
      )}

      {rows.length === 0 ? (
        <EmptyState
          title={query || kind ? "Nada corresponde a esse filtro" : "Nada guardado aqui"}
          hint={scope === "active"
            ? "Diz “lembra-te que…” numa conversa, ou adiciona uma memória à mão."
            : scope === "candidate"
              ? "O Nano ainda não sugeriu nada. Sugere pouco, de propósito."
              : "Memórias arquivadas aparecem aqui."}
        />
      ) : (
        <div className="stack stack--tight">
          {rows.map((memory) => (
            <article className="memory-card" key={memory.id}>
              <div className="memory-card__main">
                <p className="memory-card__text">{memory.text}</p>
                <div className="memory-card__meta">
                  <Badge tone="neutral">{kindLabel(memory.kind)}</Badge>
                  <OriginBadge memory={memory} />
                  {memory.pinned && (
                    <span className="origin-chip origin-chip--pinned" title="Sempre no contexto">
                      <Glyph d={PIN} size={11} /> fixada
                    </span>
                  )}
                  {memory.tags.map((tag) => (
                    <span className="tag-chip" key={tag}>{tag}</span>
                  ))}
                  <span className="dim">
                    {formatDateTime(memory.updatedAt)}
                    {memory.useCount > 0 && ` · usada ${memory.useCount}×`}
                  </span>
                </div>
              </div>
              <div className="memory-card__actions">
                {memory.status === "candidate" ? (
                  <Button size="sm" variant="primary"
                          onClick={() => onUpdate(memory.id, { status: "active" })}
                          title="Passar a memória permanente">
                    Guardar
                  </Button>
                ) : (
                  <Button size="sm" icon
                          aria-label={memory.pinned ? "Deixar de fixar" : "Fixar"}
                          title={memory.pinned
                            ? "Deixar de incluir sempre no contexto"
                            : "Incluir sempre no contexto"}
                          className={memory.pinned ? "is-active" : ""}
                          onClick={() => onUpdate(memory.id, { pinned: !memory.pinned })}>
                    <Glyph d={PIN} size={14} />
                  </Button>
                )}
                <Button size="sm"
                        onClick={() => { setDraft(memory.text); setEditing(memory); }}>
                  Editar
                </Button>
                <Button size="sm" variant="danger" onClick={() => setConfirmDelete(memory)}>
                  Apagar
                </Button>
              </div>
            </article>
          ))}
        </div>
      )}

      {profileEntries.length > 0 && (
        <>
          <h3 className="page-title" style={{ marginTop: "var(--sp-8)" }}>Perfil</h3>
          <div className="card">
            <dl className="kv">
              {profileEntries.map(([key, value]) => (
                <React.Fragment key={key}>
                  <dt>{key}</dt>
                  <dd>
                    <span className="mono">{profileValue(value)}</span>
                    {profileSource(value) && (
                      <span className="dim" style={{ marginLeft: 8, fontSize: 11 }}>
                        · {profileSource(value)}
                      </span>
                    )}
                  </dd>
                </React.Fragment>
              ))}
            </dl>
          </div>
        </>
      )}

      <div className="memory-danger">
        <Button size="sm" variant="danger" onClick={() => setConfirmClear(true)}>
          Esquecer tudo
        </Button>
        <span className="dim" style={{ fontSize: 11 }}>
          Apaga todas as memórias. As conversas não são afetadas.
        </span>
      </div>

      <RetrievalFooter overview={overview} />

      <Modal
        open={Boolean(editing)} onClose={() => setEditing(null)} title="Editar memória"
        width="narrow"
        footer={
          <>
            <Button onClick={() => setEditing(null)}>Cancelar</Button>
            <Button variant="primary" onClick={() => {
              if (editing && draft.trim()) onUpdate(editing.id, { text: draft.trim() });
              setEditing(null);
            }}>Guardar</Button>
          </>
        }
      >
        <Field label="Texto da memória"
               hint="Escrito como o Nano vai lê-lo. Chaves e palavras-passe são recusadas.">
          <textarea className="input input--area" rows={4} value={draft}
                    onChange={(event) => setDraft(event.target.value)} />
        </Field>
      </Modal>

      <Modal
        open={creating} onClose={() => setCreating(false)} title="Nova memória"
        width="narrow"
        footer={
          <>
            <Button onClick={() => setCreating(false)}>Cancelar</Button>
            <Button variant="primary" onClick={() => {
              if (newText.trim()) onCreate(newText.trim(), newKind, 4);
              setNewText("");
              setCreating(false);
            }}>Guardar</Button>
          </>
        }
      >
        <Field label="O que queres que o Nano saiba"
               hint="Uma frase. Ex.: “A minha placa gráfica é uma GTX 1660 Ti.”">
          <textarea className="input input--area" rows={3} value={newText}
                    onChange={(event) => setNewText(event.target.value)} />
        </Field>
        <Field label="Tipo">
          <select className="input input--select" value={newKind}
                  onChange={(event) => setNewKind(event.target.value)}>
            {kinds.map((entry) => (
              <option key={entry} value={entry}>{kindLabel(entry)}</option>
            ))}
          </select>
        </Field>
      </Modal>

      <ConfirmDialog
        open={Boolean(confirmDelete)} danger title="Apagar esta memória?"
        confirmLabel="Apagar"
        message={<>O Nano deixa de saber <strong>{confirmDelete?.text}</strong>.</>}
        onConfirm={() => { if (confirmDelete) onDelete(confirmDelete.id); setConfirmDelete(null); }}
        onCancel={() => setConfirmDelete(null)}
      />

      <ConfirmDialog
        open={confirmClear} danger title="Esquecer tudo?"
        confirmLabel="Esquecer tudo"
        message="Todas as memórias de longo prazo são apagadas deste computador. As conversas e o Second Brain não são afetados."
        onConfirm={() => { setConfirmClear(false); onClearAll(); }}
        onCancel={() => setConfirmClear(false)}
      />
    </div>
  );
}

/* ======================================================================
   SECOND BRAIN
   ==================================================================== */

export function KnowledgePage({
  nodes, types, stats, loading, onOpenNode, onCreate, overview,
}: {
  nodes: KnowledgeNode[] | null;
  types: string[];
  stats: { nodes: number; edges: number } | null;
  loading: boolean;
  onOpenNode: (id: string) => void;
  onCreate: (title: string, type: string, summary: string) => void;
  overview: MemoryOverview | null;
}) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [nodeType, setNodeType] = useState("topic");
  const [summary, setSummary] = useState("");

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (nodes ?? [])
      .filter((node) => !type || node.type === type)
      .filter((node) => !needle
        || node.title.toLowerCase().includes(needle)
        || node.summary.toLowerCase().includes(needle)
        || node.tags.some((tag) => tag.toLowerCase().includes(needle)));
  }, [nodes, type, query]);

  if (loading && nodes === null) {
    return (
      <div className="page__inner stack">
        <Skeleton height={56} /><Skeleton height={90} /><Skeleton height={90} />
      </div>
    );
  }

  return (
    <div className="page__inner">
      <div className="memory-head">
        <div className="memory-head__stats">
          <span className="stat-chip"><strong>{stats?.nodes ?? 0}</strong> nós</span>
          <span className="stat-chip"><strong>{stats?.edges ?? 0}</strong> ligações</span>
        </div>
        <span className="stage__spacer" />
        <Button size="sm" variant="primary" onClick={() => setCreating(true)}>Novo nó</Button>
      </div>

      <div className="memory-toolbar">
        <select className="input input--select" value={type} aria-label="Filtrar por tipo"
                onChange={(event) => setType(event.target.value)}>
          <option value="">Todos os tipos</option>
          {types.map((entry) => (
            <option key={entry} value={entry}>{nodeTypeLabel(entry)}</option>
          ))}
        </select>
        <span className="stage__spacer" />
        <SearchField id="knowledge-search" value={query} onChange={setQuery}
                     placeholder="Procurar no Second Brain…" />
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title={query || type ? "Nada corresponde a esse filtro" : "O Second Brain está vazio"}
          hint={
            "Os nós nascem das memórias que nomeiam alguma coisa concreta — um "
            + "dispositivo, um programa, um projeto, uma pessoa. Guarda uma memória "
            + "dessas, ou cria um nó à mão."
          }
        />
      ) : (
        <div className="node-grid">
          {rows.map((node) => (
            <button type="button" className="node-card" key={node.id}
                    onClick={() => onOpenNode(node.id)} title={`Abrir ${node.title}`}>
              <span className={`node-card__type node-type--${node.type}`}>
                {nodeTypeLabel(node.type)}
              </span>
              <span className="node-card__title">{node.title}</span>
              {node.summary && <span className="node-card__summary">{node.summary}</span>}
              <span className="node-card__foot">
                {node.mentionCount > 1 && <span>{node.mentionCount} menções</span>}
                {node.tags.slice(0, 2).map((tag) => (
                  <span className="tag-chip" key={tag}>{tag}</span>
                ))}
              </span>
            </button>
          ))}
        </div>
      )}

      <RetrievalFooter overview={overview} />

      <Modal
        open={creating} onClose={() => setCreating(false)} title="Novo nó" width="narrow"
        footer={
          <>
            <Button onClick={() => setCreating(false)}>Cancelar</Button>
            <Button variant="primary" onClick={() => {
              if (title.trim()) onCreate(title.trim(), nodeType, summary.trim());
              setTitle(""); setSummary(""); setCreating(false);
            }}>Criar</Button>
          </>
        }
      >
        <Field label="Nome" hint="Como lhe chamas. Ex.: “Nano Assistant”, “GTX 1660 Ti”.">
          <input className="input" value={title} maxLength={90}
                 onChange={(event) => setTitle(event.target.value)} />
        </Field>
        <Field label="Tipo">
          <select className="input input--select" value={nodeType}
                  onChange={(event) => setNodeType(event.target.value)}>
            {types.map((entry) => (
              <option key={entry} value={entry}>{nodeTypeLabel(entry)}</option>
            ))}
          </select>
        </Field>
        <Field label="Resumo" hint="Opcional. Uma linha que explique o que isto é.">
          <textarea className="input input--area" rows={3} value={summary}
                    onChange={(event) => setSummary(event.target.value)} />
        </Field>
      </Modal>
    </div>
  );
}

/* ======================================================================
   NODE DETAIL
   ==================================================================== */

export function NodeDetailModal({
  detail, open, onClose, onOpenNode, onDelete, onUpdate,
}: {
  detail: any | null;
  open: boolean;
  onClose: () => void;
  onOpenNode: (id: string) => void;
  onDelete: (id: string) => void;
  onUpdate: (id: string, patch: Record<string, any>) => void;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [editing, setEditing] = useState(false);
  const [summary, setSummary] = useState("");

  const node: KnowledgeNode | null = detail?.node ?? null;
  useEffect(() => { setSummary(node?.summary ?? ""); setEditing(false); }, [node?.id]);

  if (!node) return null;
  const edges: KnowledgeEdge[] = detail?.edges ?? [];
  const memories: Memory[] = detail?.memories ?? [];
  const conversations: { id: string; title: string }[] = detail?.conversations ?? [];

  return (
    <>
      <Modal
        open={open} onClose={onClose} title={node.title}
        footer={
          <>
            <Button variant="danger" onClick={() => setConfirmDelete(true)}>Apagar nó</Button>
            <span className="stage__spacer" />
            <Button onClick={onClose}>Fechar</Button>
          </>
        }
      >
        <div className="node-detail">
          <div className="node-detail__head">
            <span className={`node-card__type node-type--${node.type}`}>
              {nodeTypeLabel(node.type)}
            </span>
            <span className="dim" style={{ fontSize: 11 }}>
              {node.mentionCount} menç{node.mentionCount === 1 ? "ão" : "ões"} ·
              atualizado {formatDateTime(node.updatedAt)}
            </span>
          </div>

          <section className="node-detail__section">
            <h4 className="section-label">Resumo</h4>
            {editing ? (
              <>
                <textarea className="input input--area" rows={3} value={summary}
                          onChange={(event) => setSummary(event.target.value)} />
                <div className="inline" style={{ marginTop: 8 }}>
                  <Button size="sm" variant="primary" onClick={() => {
                    onUpdate(node.id, { summary });
                    setEditing(false);
                  }}>Guardar</Button>
                  <Button size="sm" onClick={() => { setSummary(node.summary); setEditing(false); }}>
                    Cancelar
                  </Button>
                </div>
              </>
            ) : (
              <p className="node-detail__body">
                {node.summary || <span className="dim">Sem resumo.</span>}
                <Button size="sm" variant="ghost" onClick={() => setEditing(true)}
                        style={{ marginLeft: 8 }}>Editar</Button>
              </p>
            )}
          </section>

          <section className="node-detail__section">
            <h4 className="section-label">Ligações ({edges.length})</h4>
            {edges.length === 0 ? (
              <p className="dim" style={{ fontSize: 12 }}>
                Ainda sem ligações. Uma ligação aparece quando duas coisas são
                referidas na mesma memória.
              </p>
            ) : (
              <div className="chip-row">
                {edges.map((edge) => {
                  const otherId = edge.source === node.id ? edge.target : edge.source;
                  const otherTitle = edge.source === node.id ? edge.targetTitle : edge.sourceTitle;
                  return (
                    <button type="button" className="link-chip" key={edge.id}
                            onClick={() => onOpenNode(otherId)}
                            title={`${node.title} ${relationLabel(edge.relation)} ${otherTitle}`}>
                      <span className="link-chip__relation">{relationLabel(edge.relation)}</span>
                      {otherTitle}
                    </button>
                  );
                })}
              </div>
            )}
          </section>

          <section className="node-detail__section">
            <h4 className="section-label">Memórias ({memories.length})</h4>
            {memories.length === 0 ? (
              <p className="dim" style={{ fontSize: 12 }}>Sem memórias associadas.</p>
            ) : (
              <ul className="node-detail__list">
                {memories.map((memory) => (
                  <li key={memory.id}>
                    {memory.text}
                    <span className="dim"> · {kindLabel(memory.kind)}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="node-detail__section">
            <h4 className="section-label">Conversas ({conversations.length})</h4>
            {conversations.length === 0 ? (
              <p className="dim" style={{ fontSize: 12 }}>
                Nenhuma conversa associada, ou a conversa de origem foi apagada.
              </p>
            ) : (
              <ul className="node-detail__list">
                {conversations.map((conversation) => (
                  <li key={conversation.id}>{conversation.title}</li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </Modal>

      <ConfirmDialog
        open={confirmDelete} danger title="Apagar este nó?"
        confirmLabel="Apagar"
        message={
          <>
            <strong>{node.title}</strong> e as suas {edges.length} ligações são
            apagados. As memórias associadas continuam guardadas.
          </>
        }
        onConfirm={() => { setConfirmDelete(false); onDelete(node.id); }}
        onCancel={() => setConfirmDelete(false)}
      />
    </>
  );
}

/* ======================================================================
   GRAPH
   ====================================================================

   WHY A HAND-WRITTEN LAYOUT AND NOT A LIBRARY

   d3-force, cytoscape and vis-network are all excellent and all far larger
   than the problem: a few hundred nodes, drawn once, pannable. The whole
   layout below is about sixty lines, ships nothing, and cannot break the
   Content-Security-Policy the desktop shell enforces.

   The simulation is deterministic — a fixed seed, a fixed iteration count, no
   animation loop — so the same graph draws the same way every time it is
   opened. A layout that reshuffles on each visit makes a knowledge graph
   impossible to learn.

   Above SIM_LIMIT nodes the O(n²) repulsion stops being free, so the layout
   falls back to a deterministic ring clustered by type. It is less pretty and
   it is instant, which is the right trade at that size.
   ==================================================================== */

type Point = { id: string; x: number; y: number };

const SIM_LIMIT = 220;
const SIM_ITERATIONS = 160;
const VIEW = 1000;

/** Deterministic pseudo-random in [0,1) from a string. No Math.random(). */
function seeded(id: string): number {
  let hash = 2166136261;
  for (let i = 0; i < id.length; i += 1) {
    hash ^= id.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return ((hash >>> 0) % 100000) / 100000;
}

function layout(nodes: KnowledgeNode[], edges: KnowledgeEdge[]): Map<string, Point> {
  const points: Point[] = nodes.map((node, index) => {
    const angle = (index / Math.max(1, nodes.length)) * Math.PI * 2;
    const radius = VIEW * 0.32 * (0.55 + seeded(node.id) * 0.45);
    return {
      id: node.id,
      x: VIEW / 2 + Math.cos(angle) * radius,
      y: VIEW / 2 + Math.sin(angle) * radius,
    };
  });
  const byId = new Map(points.map((point) => [point.id, point]));

  if (nodes.length > SIM_LIMIT || nodes.length < 2) return byId;

  const links = edges
    .map((edge) => [byId.get(edge.source), byId.get(edge.target)] as const)
    .filter((pair): pair is readonly [Point, Point] => Boolean(pair[0] && pair[1]));

  for (let step = 0; step < SIM_ITERATIONS; step += 1) {
    const cooling = 1 - step / SIM_ITERATIONS;
    // Repulsion: every pair pushes apart, weakly, so labels do not collide.
    for (let i = 0; i < points.length; i += 1) {
      for (let j = i + 1; j < points.length; j += 1) {
        const a = points[i];
        const b = points[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let distance = Math.hypot(dx, dy);
        if (distance < 0.01) {
          // Two nodes exactly on top of each other have no direction to
          // separate along; nudge them deterministically rather than with a
          // random jitter that would make the layout unreproducible.
          dx = (seeded(a.id) - 0.5) || 0.5;
          dy = (seeded(b.id) - 0.5) || 0.5;
          distance = Math.hypot(dx, dy) || 1;
        }
        const force = (9000 / (distance * distance)) * cooling;
        const ux = (dx / distance) * force;
        const uy = (dy / distance) * force;
        a.x += ux; a.y += uy;
        b.x -= ux; b.y -= uy;
      }
    }
    // Attraction along real edges, and a gentle pull to the centre so
    // disconnected nodes do not drift out of the viewport.
    for (const [a, b] of links) {
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const distance = Math.hypot(dx, dy) || 1;
      const force = ((distance - 150) * 0.012) * cooling;
      const ux = (dx / distance) * force;
      const uy = (dy / distance) * force;
      a.x += ux; a.y += uy;
      b.x -= ux; b.y -= uy;
    }
    for (const point of points) {
      point.x += (VIEW / 2 - point.x) * 0.004 * cooling;
      point.y += (VIEW / 2 - point.y) * 0.004 * cooling;
    }
  }
  return byId;
}

export function GraphPage({
  graph, types, loading, onOpenNode, onRefresh, overview,
}: {
  graph: KnowledgeGraphPayload | null;
  types: string[];
  loading: boolean;
  onOpenNode: (id: string) => void;
  onRefresh: (type: string) => void;
  overview: MemoryOverview | null;
}) {
  const [type, setType] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState({ x: 0, y: 0, scale: 1 });
  const dragging = useRef<{ x: number; y: number } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];

  const positions = useMemo(() => layout(nodes, edges), [nodes, edges]);

  const needle = query.trim().toLowerCase();
  const matches = useMemo(() => {
    if (!needle) return null;
    return new Set(nodes.filter((node) => node.title.toLowerCase().includes(needle))
      .map((node) => node.id));
  }, [nodes, needle]);

  const neighbours = useMemo(() => {
    if (!selected) return null;
    const set = new Set<string>([selected]);
    for (const edge of edges) {
      if (edge.source === selected) set.add(edge.target);
      if (edge.target === selected) set.add(edge.source);
    }
    return set;
  }, [selected, edges]);

  /* WHEEL ZOOM NEEDS A NON-PASSIVE LISTENER.
     React registers onWheel passively, so calling preventDefault there is
     ignored and the page scrolls behind the graph while it zooms. Attaching the
     listener directly, with { passive: false }, is the only way to own the
     gesture -- and there must be exactly ONE handler, or every notch zooms
     twice. */
  useEffect(() => {
    const element = svgRef.current;
    if (!element) return;
    const handler = (event: WheelEvent) => {
      event.preventDefault();
      setView((current) => {
        const next = current.scale * (event.deltaY < 0 ? 1.12 : 1 / 1.12);
        return { ...current, scale: Math.min(3.5, Math.max(0.35, next)) };
      });
    };
    element.addEventListener("wheel", handler, { passive: false });
    return () => element.removeEventListener("wheel", handler);
  }, []);

  useEffect(() => { onRefresh(type); }, [type, onRefresh]);

  if (loading && graph === null) {
    return <div className="page__inner"><Skeleton height={420} /></div>;
  }

  if (!nodes.length) {
    return (
      <div className="page__inner">
        <EmptyState
          title="Ainda não há nada para desenhar"
          hint="O grafo mostra os nós do Second Brain e as ligações entre eles. Guarda algumas memórias sobre coisas concretas e volta aqui."
        />
      </div>
    );
  }

  return (
    <div className="page__inner graph-page">
      <div className="memory-toolbar">
        <select className="input input--select" value={type} aria-label="Filtrar por tipo"
                onChange={(event) => setType(event.target.value)}>
          <option value="">Todos os tipos</option>
          {types.map((entry) => (
            <option key={entry} value={entry}>{nodeTypeLabel(entry)}</option>
          ))}
        </select>
        <SearchField id="graph-search" value={query} onChange={setQuery}
                     placeholder="Procurar um nó…" />
        <span className="stage__spacer" />
        <Button size="sm" onClick={() => { setView({ x: 0, y: 0, scale: 1 }); setSelected(null); }}>
          Repor vista
        </Button>
      </div>

      <div className="graph-canvas">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${VIEW} ${VIEW}`}
          className="graph-svg"
          role="img"
          aria-label={`Grafo com ${nodes.length} nós e ${edges.length} ligações`}
          onPointerDown={(event) => {
            dragging.current = { x: event.clientX, y: event.clientY };
            (event.target as Element).setPointerCapture?.(event.pointerId);
          }}
          onPointerMove={(event) => {
            if (!dragging.current) return;
            const dx = event.clientX - dragging.current.x;
            const dy = event.clientY - dragging.current.y;
            dragging.current = { x: event.clientX, y: event.clientY };
            setView((current) => ({ ...current, x: current.x + dx, y: current.y + dy }));
          }}
          onPointerUp={() => { dragging.current = null; }}
          onPointerLeave={() => { dragging.current = null; }}
        >
          <g transform={`translate(${view.x} ${view.y}) scale(${view.scale}) `
                        + `translate(${(1 - 1 / view.scale) * 0} 0)`}>
            {edges.map((edge) => {
              const a = positions.get(edge.source);
              const b = positions.get(edge.target);
              if (!a || !b) return null;
              const dimmed = neighbours
                && !(neighbours.has(edge.source) && neighbours.has(edge.target));
              return (
                <line key={edge.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                      className={`graph-edge${dimmed ? " is-dim" : ""}`}
                      strokeWidth={Math.min(3, 1 + edge.weight * 0.4)} />
              );
            })}
            {nodes.map((node) => {
              const point = positions.get(node.id);
              if (!point) return null;
              const radius = 9 + Math.min(10, node.mentionCount * 1.4);
              const dimmed = (matches && !matches.has(node.id))
                || (neighbours && !neighbours.has(node.id));
              return (
                <g key={node.id}
                   className={`graph-node${dimmed ? " is-dim" : ""}`
                              + `${selected === node.id ? " is-selected" : ""}`}
                   data-type={node.type}
                   transform={`translate(${point.x} ${point.y})`}
                   tabIndex={0}
                   role="button"
                   aria-label={`${node.title} — ${nodeTypeLabel(node.type)}`}
                   onClick={(event) => {
                     event.stopPropagation();
                     setSelected((current) => (current === node.id ? null : node.id));
                   }}
                   onDoubleClick={() => onOpenNode(node.id)}
                   onKeyDown={(event) => {
                     if (event.key === "Enter") onOpenNode(node.id);
                     if (event.key === " ") { event.preventDefault(); setSelected(node.id); }
                   }}>
                  <circle r={radius} className="graph-node__dot" />
                  <text y={radius + 15} className="graph-node__label">{node.title}</text>
                </g>
              );
            })}
          </g>
        </svg>

        <div className="graph-legend">
          {Array.from(new Set(nodes.map((node) => node.type))).slice(0, 8).map((entry) => (
            <span className="graph-legend__item" key={entry}>
              <span className={`graph-legend__dot node-type--${entry}`} />
              {nodeTypeLabel(entry)}
            </span>
          ))}
        </div>

        {selected && (
          <div className="graph-selected">
            <strong>{nodes.find((node) => node.id === selected)?.title}</strong>
            <Button size="sm" onClick={() => onOpenNode(selected)}>Abrir detalhe</Button>
          </div>
        )}
      </div>

      <div className="graph-foot">
        <span className="dim">
          {nodes.length} nós · {edges.length} ligações
          {graph?.truncated && ` · a mostrar os mais ligados de ${graph.total}`}
          {" · arrasta para mover, roda para ampliar, duplo-clique abre o detalhe"}
        </span>
      </div>

      <RetrievalFooter overview={overview} />
    </div>
  );
}
