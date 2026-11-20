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
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import {
  Badge, Button, ConfirmDialog, EmptyState, Field, Modal, Panel,
  Skeleton, StatusIndicator, Tabs, formatDateTime,
} from "./ui";
import type {
  KnowledgeEdge, KnowledgeGraphPayload, KnowledgeNode, Memory, MemoryOverview,
} from "../lib/memory";
import { kindLabel, nodeTypeLabel, originLabel, relationLabel } from "../lib/memory";
import { t } from "../lib/i18n";
import { tj } from "../lib/tj";

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
      {suggestion ? t("sugestão") : originLabel(String(memory.origin))}
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
    <p className="dim memory-footnote">{tj("Pesquisa: {0} · {1} entradas indexadas · tudo neste computador, nada é enviado para fora.", { 0: <strong>{retrieval.engine}</strong>, 1: retrieval.entries })}</p>
  );
}

/* ======================================================================
   MEMÓRIAS
   ==================================================================== */

type MemoryScope = "active" | "candidate" | "archived";

const SCOPES: { id: MemoryScope; label: string }[] = [
  { id: "active", label: t("Ativas") },
  { id: "candidate", label: t("Sugestões") },
  { id: "archived", label: t("Arquivadas") },
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
  const [viewMode, setViewMode] = useState<"all" | "pinned" | "recent">("all");
  const [inspectingId, setInspectingId] = useState<string | null>(null);
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
  const inspecting = memories.find((memory) => memory.id === inspectingId) ?? null;

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return memories
      .filter((memory) => memory.status === scope)
      .filter((memory) => !kind || memory.kind === kind)
      .filter((memory) => viewMode !== "pinned" || memory.pinned)
      .filter((memory) => !needle
        || memory.text.toLowerCase().includes(needle)
        || memory.tags.some((tag) => tag.toLowerCase().includes(needle)))
      .sort((a, b) => viewMode === "recent"
        ? Date.parse(b.updatedAt) - Date.parse(a.updatedAt)
        : 0);
  }, [memories, scope, kind, query, viewMode]);

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
        <Panel title={t("Memória indisponível")}>
          <StatusIndicator state={migrationState}
                           label={t("A base de dados não pôde ser migrada")} />
          <p className="muted" style={{ fontSize: 13, marginTop: 10, lineHeight: 1.7 }}>{t("O NANO continua a responder, mas não guarda conversas nem memórias nesta sessão. Nada foi apagado — a base de dados anterior está intacta.")}</p>
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
          <span className="stat-chip">{tj("{0} ativas", { 0: <strong>{counts.active}</strong> })}</span>
          <span className="stat-chip">{tj("{0} sugestões", { 0: <strong>{counts.candidate}</strong> })}</span>
          <span className="stat-chip">
            {tj("{0} nós no Second Brain", { 0: <strong>{overview?.knowledge?.nodes ?? 0}</strong> })}</span>
          <span className="stat-chip">
            {tj("{0} conversas", { 0: <strong>{overview?.conversationCount ?? 0}</strong> })}
          </span>
        </div>
        <span className="stage__spacer" />
        <Button size="sm" variant="primary" onClick={() => setCreating(true)}>{t("Adicionar memória")}</Button>
      </div>

      {!overview?.longTermEnabled && (
        <div className="notice notice--warn">
          <strong>{t("A memória de longo prazo está desligada.")}</strong>{' '}{t("O NANO não guarda nem consulta nada entre conversas. O que já está guardado continua aqui.")}<Button size="sm" onClick={onOpenSettings}>{t("Abrir definições")}</Button>
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
        <select className="input input--select" value={kind} aria-label={t("Filtrar por tipo")}
                onChange={(event) => setKind(event.target.value)}>
          <option value="">{t("Todos os tipos")}</option>
          {kinds.map((entry) => (
            <option key={entry} value={entry}>{kindLabel(entry)}</option>
          ))}
        </select>
        <SearchField id="memory-search" value={query} onChange={setQuery}
                     placeholder={t("Procurar nas memórias…")} />
      </div>

      <div className="memory-views" aria-label={t("Vista das memórias")}>
        <button className="chip" type="button" aria-pressed={viewMode === "all"}
                onClick={() => setViewMode("all")}>{t("Todas")}</button>
        <button className="chip" type="button" aria-pressed={viewMode === "recent"}
                onClick={() => setViewMode("recent")}>{t("Recentes")}</button>
        <button className="chip" type="button" aria-pressed={viewMode === "pinned"}
                onClick={() => setViewMode("pinned")}>{t("Fixadas")}</button>
        <span className="memory-views__count" role="status">{t("{0} nesta vista", { 0: rows.length })}</span>
      </div>

      {scope === "candidate" && counts.candidate > 0 && (
        <p className="dim memory-note">{tj("Estas são coisas que o NANO {0} durante as conversas. Não são usadas nas respostas enquanto não as guardares.", { 0: <em>{t("reparou")}</em> })}</p>
      )}

      {rows.length === 0 ? (
        <EmptyState
          title={query || kind || viewMode === "pinned" ? t("Nada corresponde a esse filtro") : t("Nada guardado aqui")}
          hint={scope === "active"
            ? t("Diz “lembra-te que…” numa conversa, ou adiciona uma memória à mão.")
            : scope === "candidate"
              ? t("O NANO ainda não sugeriu nada. Sugere pouco, de propósito.")
              : t("Memórias arquivadas aparecem aqui.")}
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
                    <span className="origin-chip origin-chip--pinned" title={t("Sempre no contexto")}>
                      <Glyph d={PIN} size={11} /> {t("fixada")}
                    </span>
                  )}
                  {memory.tags.map((tag) => (
                    <span className="tag-chip" key={tag}>{tag}</span>
                  ))}
                  <span className="dim">{t("Atualizada {0}", { 0: formatDateTime(memory.updatedAt) })}
                    {memory.useCount > 0 && t(" · usada {0}×", { 0: memory.useCount })}
                  </span>
                </div>
              </div>
              <div className="memory-card__actions">
                <Button size="sm" onClick={() => setInspectingId(memory.id)}>{t("Detalhes")}</Button>
                {memory.status === "candidate" ? (
                  <Button size="sm" variant="primary"
                          onClick={() => onUpdate(memory.id, { status: "active" })}
                          title={t("Passar a memória permanente")}>{t("Guardar")}</Button>
                ) : (
                  <Button size="sm" icon
                          aria-label={memory.pinned ? t("Deixar de fixar") : t("Fixar")}
                          title={memory.pinned
                            ? t("Deixar de incluir sempre no contexto")
                            : t("Incluir sempre no contexto")}
                          className={memory.pinned ? "is-active" : ""}
                          onClick={() => onUpdate(memory.id, { pinned: !memory.pinned })}>
                    <Glyph d={PIN} size={14} />
                  </Button>
                )}
                <Button size="sm"
                        onClick={() => { setDraft(memory.text); setEditing(memory); }}>{t("Editar")}</Button>
                <Button size="sm" variant="danger" onClick={() => setConfirmDelete(memory)}>{t("Apagar")}</Button>
              </div>
            </article>
          ))}
        </div>
      )}

      {profileEntries.length > 0 && (
        <>
          <h3 className="page-title" style={{ marginTop: "var(--sp-8)" }}>{t("Perfil")}</h3>
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
        <Button size="sm" variant="danger" onClick={() => setConfirmClear(true)}>{t("Esquecer tudo")}</Button>
        <span className="dim" style={{ fontSize: 11 }}>{t("Apaga todas as memórias. As conversas não são afetadas.")}</span>
      </div>

      <RetrievalFooter overview={overview} />

      <Modal open={Boolean(inspecting)} onClose={() => setInspectingId(null)}
             title={t("Detalhes da memória")} width="narrow"
             footer={<Button onClick={() => setInspectingId(null)}>{t("Fechar")}</Button>}>
        {inspecting && (
          <div className="memory-detail">
            <p className="memory-detail__text">{inspecting.text}</p>
            <dl className="kv">
              <dt>{t("Tipo")}</dt><dd>{kindLabel(inspecting.kind)}</dd>
              <dt>{t("Origem")}</dt><dd>{originLabel(String(inspecting.origin))}</dd>
              <dt>{t("Estado")}</dt><dd>{inspecting.status === "candidate" ? t("Sugestão") : inspecting.status === "active" ? t("Ativa") : t("Arquivada")}</dd>
              <dt>{t("Criada")}</dt><dd>{formatDateTime(inspecting.createdAt)}</dd>
              <dt>{t("Atualizada")}</dt><dd>{formatDateTime(inspecting.updatedAt)}</dd>
              {inspecting.lastUsedAt && <><dt>{t("Último uso")}</dt><dd>{formatDateTime(inspecting.lastUsedAt)}</dd></>}
              <dt>{t("Utilizações")}</dt><dd>{inspecting.useCount}</dd>
              <dt>{t("Importância")}</dt><dd>{inspecting.importance}</dd>
              <dt>{t("Contexto")}</dt><dd>{inspecting.pinned ? t("Sempre incluída") : t("Recuperada quando relevante")}</dd>
              {inspecting.sourceConversationId && <><dt>{t("Conversa de origem")}</dt><dd className="mono">{inspecting.sourceConversationId}</dd></>}
              {inspecting.sourceMessageId != null && <><dt>{t("Mensagem de origem")}</dt><dd>#{inspecting.sourceMessageId}</dd></>}
            </dl>
          </div>
        )}
      </Modal>

      <Modal
        open={Boolean(editing)} onClose={() => setEditing(null)} title={t("Editar memória")}
        width="narrow"
        footer={
          <>
            <Button onClick={() => setEditing(null)}>{t("Cancelar")}</Button>
            <Button variant="primary" onClick={() => {
              if (editing && draft.trim()) onUpdate(editing.id, { text: draft.trim() });
              setEditing(null);
            }}>{t("Guardar")}</Button>
          </>
        }
      >
        <Field label={t("Texto da memória")}
               hint={t("Escrito como o NANO vai lê-lo. Chaves e palavras-passe são recusadas.")}>
          <textarea className="input input--area" rows={4} value={draft}
                    onChange={(event) => setDraft(event.target.value)} />
        </Field>
      </Modal>

      <Modal
        open={creating} onClose={() => setCreating(false)} title={t("Nova memória")}
        width="narrow"
        footer={
          <>
            <Button onClick={() => setCreating(false)}>{t("Cancelar")}</Button>
            <Button variant="primary" onClick={() => {
              if (newText.trim()) onCreate(newText.trim(), newKind, 4);
              setNewText("");
              setCreating(false);
            }}>{t("Guardar")}</Button>
          </>
        }
      >
        <Field label={t("O que queres que o NANO saiba")}
               hint={t("Uma frase. Ex.: “A minha placa gráfica é uma GTX 1660 Ti.”")}>
          <textarea className="input input--area" rows={3} value={newText}
                    onChange={(event) => setNewText(event.target.value)} />
        </Field>
        <Field label={t("Tipo")}>
          <select className="input input--select" value={newKind}
                  onChange={(event) => setNewKind(event.target.value)}>
            {kinds.map((entry) => (
              <option key={entry} value={entry}>{kindLabel(entry)}</option>
            ))}
          </select>
        </Field>
      </Modal>

      <ConfirmDialog
        open={Boolean(confirmDelete)} danger title={t("Apagar esta memória?")}
        confirmLabel={t("Apagar")}
        message={<>{tj("O NANO deixa de saber {0}.", { 0: <strong>{confirmDelete?.text}</strong> })}</>}
        onConfirm={() => { if (confirmDelete) onDelete(confirmDelete.id); setConfirmDelete(null); }}
        onCancel={() => setConfirmDelete(null)}
      />

      <ConfirmDialog
        open={confirmClear} danger title={t("Esquecer tudo?")}
        confirmLabel={t("Esquecer tudo")}
        message={t("Todas as memórias de longo prazo são apagadas deste computador. As conversas e o Second Brain não são afetados.")}
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
          <span className="stat-chip">{tj("{0} nós", { 0: <strong>{stats?.nodes ?? 0}</strong> })}</span>
          <span className="stat-chip">{tj("{0} ligações", { 0: <strong>{stats?.edges ?? 0}</strong> })}</span>
        </div>
        <span className="stage__spacer" />
        <Button size="sm" variant="primary" onClick={() => setCreating(true)}>{t("Novo nó")}</Button>
      </div>

      <div className="memory-toolbar">
        <select className="input input--select" value={type} aria-label={t("Filtrar por tipo")}
                onChange={(event) => setType(event.target.value)}>
          <option value="">{t("Todos os tipos")}</option>
          {types.map((entry) => (
            <option key={entry} value={entry}>{nodeTypeLabel(entry)}</option>
          ))}
        </select>
        <span className="stage__spacer" />
        <SearchField id="knowledge-search" value={query} onChange={setQuery}
                     placeholder={t("Procurar no Second Brain…")} />
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title={query || type ? t("Nada corresponde a esse filtro") : t("O Second Brain está vazio")}
          hint={
            t("Os nós nascem das memórias que nomeiam alguma coisa concreta — um dispositivo, um programa, um projeto, uma pessoa. Guarda uma memória dessas, ou cria um nó à mão.")
          }
        />
      ) : (
        <div className="node-grid">
          {rows.map((node) => (
            <button type="button" className="node-card" key={node.id}
                    onClick={() => onOpenNode(node.id)} title={t("Abrir {0}", { 0: node.title })}>
              <span className={`node-card__type node-type--${node.type}`}>
                {nodeTypeLabel(node.type)}
              </span>
              <span className="node-card__title">{node.title}</span>
              {node.summary && <span className="node-card__summary">{node.summary}</span>}
              <span className="node-card__foot">
                {node.mentionCount > 1 && <span>{t("{0} menções", { 0: node.mentionCount })}</span>}
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
        open={creating} onClose={() => setCreating(false)} title={t("Novo nó")} width="narrow"
        footer={
          <>
            <Button onClick={() => setCreating(false)}>{t("Cancelar")}</Button>
            <Button variant="primary" onClick={() => {
              if (title.trim()) onCreate(title.trim(), nodeType, summary.trim());
              setTitle(""); setSummary(""); setCreating(false);
            }}>{t("Criar")}</Button>
          </>
        }
      >
        <Field label={t("Nome")} hint={t("Como lhe chamas. Ex.: “Projeto NANO”, “GTX 1660 Ti”.")}>
          <input className="input" value={title} maxLength={90}
                 onChange={(event) => setTitle(event.target.value)} />
        </Field>
        <Field label={t("Tipo")}>
          <select className="input input--select" value={nodeType}
                  onChange={(event) => setNodeType(event.target.value)}>
            {types.map((entry) => (
              <option key={entry} value={entry}>{nodeTypeLabel(entry)}</option>
            ))}
          </select>
        </Field>
        <Field label={t("Resumo")} hint={t("Opcional. Uma linha que explique o que isto é.")}>
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
            <Button variant="danger" onClick={() => setConfirmDelete(true)}>{t("Apagar nó")}</Button>
            <span className="stage__spacer" />
            <Button onClick={onClose}>{t("Fechar")}</Button>
          </>
        }
      >
        <div className="node-detail">
          <div className="node-detail__head">
            <span className={`node-card__type node-type--${node.type}`}>
              {nodeTypeLabel(node.type)}
            </span>
            <span className="dim" style={{ fontSize: 11 }}>
              {t(node.mentionCount === 1 ? "{0} menção · atualizado {1}" : "{0} menções · atualizado {1}", { 0: node.mentionCount, 1: formatDateTime(node.updatedAt) })}
            </span>
          </div>

          <section className="node-detail__section">
            <h4 className="section-label">{t("Resumo")}</h4>
            {editing ? (
              <>
                <textarea className="input input--area" rows={3} value={summary}
                          onChange={(event) => setSummary(event.target.value)} />
                <div className="inline" style={{ marginTop: 8 }}>
                  <Button size="sm" variant="primary" onClick={() => {
                    onUpdate(node.id, { summary });
                    setEditing(false);
                  }}>{t("Guardar")}</Button>
                  <Button size="sm" onClick={() => { setSummary(node.summary); setEditing(false); }}>{t("Cancelar")}</Button>
                </div>
              </>
            ) : (
              <p className="node-detail__body">
                {node.summary || <span className="dim">{t("Sem resumo.")}</span>}
                <Button size="sm" variant="ghost" onClick={() => setEditing(true)}
                        style={{ marginLeft: 8 }}>{t("Editar")}</Button>
              </p>
            )}
          </section>

          <section className="node-detail__section">
            <h4 className="section-label">{t("Ligações ({0})", { 0: edges.length })}</h4>
            {edges.length === 0 ? (
              <p className="dim" style={{ fontSize: 12 }}>{t("Ainda sem ligações. Uma ligação aparece quando duas coisas são referidas na mesma memória.")}</p>
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
            <h4 className="section-label">{t("Memórias ({0})", { 0: memories.length })}</h4>
            {memories.length === 0 ? (
              <p className="dim" style={{ fontSize: 12 }}>{t("Sem memórias associadas.")}</p>
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
            <h4 className="section-label">{t("Conversas ({0})", { 0: conversations.length })}</h4>
            {conversations.length === 0 ? (
              <p className="dim" style={{ fontSize: 12 }}>{t("Nenhuma conversa associada, ou a conversa de origem foi apagada.")}</p>
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
        open={confirmDelete} danger title={t("Apagar este nó?")}
        confirmLabel={t("Apagar")}
        message={
          <>
            {tj("{0} e as suas {1} ligações são apagados. As memórias associadas continuam guardadas.", { 0: <strong>{node.title}</strong>, 1: edges.length })}</>
        }
        onConfirm={() => { setConfirmDelete(false); onDelete(node.id); }}
        onCancel={() => setConfirmDelete(false)}
      />
    </>
  );
}

/* ======================================================================
   GRAPH — the Second Brain in 3D
   ====================================================================

   WHY A HAND-WRITTEN 3D VIEW AND NOT A LIBRARY

   three.js, d3-force-3d and friends are all excellent and all far larger than
   the problem: at most a few hundred nodes, drawn as SVG. The layout and the
   camera below are a couple of hundred lines, ship nothing, and cannot break
   the Content-Security-Policy the desktop shell enforces. Staying in SVG also
   keeps every node a real, focusable, labelled element, which a WebGL canvas
   would not be.

   THE LAYOUT IS STILL DETERMINISTIC. A fixed seed and a fixed iteration count
   place every node at the same point in 3D space every time the view opens,
   so the brain can be learned. Only the CAMERA moves: a slow turn while
   nobody is touching it, an orbit when the background is dragged, a smooth
   glide when a search finds one node. `data-x/y/z` on each node carry its
   place in that space; the tests read them, because screen positions change
   as the brain turns.

   MOTION IS OPTIONAL AND CHEAP. The animation loop runs only while something
   is actually moving and stops by itself; it never runs with
   prefers-reduced-motion (then the brain stands still and every change is
   instant), and it switches itself off on a PC that cannot keep up. The turn
   always has a visible pause button.

   Above SIM_LIMIT nodes the O(n²) repulsion stops being free, so the layout
   falls back to a sphere banded by type. It is less pretty and it is instant,
   which is the right trade at that size.
   ==================================================================== */

type Vec3 = { id: string; x: number; y: number; z: number };
type Projected = { sx: number; sy: number; f: number; z: number };

const SIM_LIMIT = 220;
const SIM_ITERATIONS = 160;
const VIEW = 1000;
/** Radius of the brain, in layout units. */
const SPREAD = 330;
/** Perspective: smaller is stronger. */
const FOCAL = 1250;
const ZOOM_MIN = 0.45;
const ZOOM_MAX = 3.2;
const START_YAW = -0.5;
const START_PITCH = 0.32;
const START_ZOOM = 1.12;
/** One full turn in about fifty seconds: alive, never busy. */
const SPIN_PER_MS = 0.00012;
const MAX_PULSES = 24;

/** Deterministic pseudo-random in [0,1) from a string. No Math.random(). */
function seeded(id: string): number {
  let hash = 2166136261;
  for (let i = 0; i < id.length; i += 1) {
    hash ^= id.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return ((hash >>> 0) % 100000) / 100000;
}

/** Points spread evenly over a sphere (the golden-angle spiral). */
function spherePoint(index: number, count: number, radius: number) {
  const y = 1 - (2 * (index + 0.5)) / Math.max(1, count);
  const ring = Math.sqrt(Math.max(0, 1 - y * y));
  const angle = index * 2.399963229728653;
  return { x: Math.cos(angle) * ring * radius, y: y * radius, z: Math.sin(angle) * ring * radius };
}

function layout3d(nodes: KnowledgeNode[], edges: KnowledgeEdge[]): Map<string, Vec3> {
  // Large graphs: a sphere banded by type, so each kind of thing has its own
  // latitude. Small ones start from the same spiral and then relax.
  const ordered = nodes.length > SIM_LIMIT
    ? [...nodes].sort((a, b) => (a.type === b.type ? 0 : a.type < b.type ? -1 : 1))
    : nodes;
  const points: Vec3[] = ordered.map((node, index) => {
    const p = spherePoint(index, ordered.length, SPREAD * (0.6 + seeded(node.id) * 0.4));
    return { id: node.id, ...p };
  });
  const byId = new Map(points.map((point) => [point.id, point]));
  if (nodes.length > SIM_LIMIT || nodes.length < 2) return byId;

  const links = edges
    .map((edge) => [byId.get(edge.source), byId.get(edge.target)] as const)
    .filter((pair): pair is readonly [Vec3, Vec3] => Boolean(pair[0] && pair[1]));

  for (let step = 0; step < SIM_ITERATIONS; step += 1) {
    const cooling = 1 - step / SIM_ITERATIONS;
    // Repulsion: every pair pushes apart, weakly, so labels do not collide.
    for (let i = 0; i < points.length; i += 1) {
      for (let j = i + 1; j < points.length; j += 1) {
        const a = points[i];
        const b = points[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let dz = a.z - b.z;
        let distance = Math.hypot(dx, dy, dz);
        if (distance < 0.01) {
          // Two nodes on top of each other have no direction to separate
          // along; nudge them deterministically, never with a random jitter.
          dx = (seeded(a.id) - 0.5) || 0.5;
          dy = (seeded(b.id) - 0.5) || 0.5;
          dz = 0.5;
          distance = Math.hypot(dx, dy, dz) || 1;
        }
        const force = (12000 / (distance * distance)) * cooling;
        const ux = (dx / distance) * force;
        const uy = (dy / distance) * force;
        const uz = (dz / distance) * force;
        a.x += ux; a.y += uy; a.z += uz;
        b.x -= ux; b.y -= uy; b.z -= uz;
      }
    }
    // Attraction along real edges, and a gentle pull to the centre so
    // disconnected nodes do not drift off into space.
    for (const [a, b] of links) {
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dz = b.z - a.z;
      const distance = Math.hypot(dx, dy, dz) || 1;
      const force = ((distance - 130) * 0.014) * cooling;
      const ux = (dx / distance) * force;
      const uy = (dy / distance) * force;
      const uz = (dz / distance) * force;
      a.x += ux; a.y += uy; a.z += uz;
      b.x -= ux; b.y -= uy; b.z -= uz;
    }
    for (const point of points) {
      point.x -= point.x * 0.006 * cooling;
      point.y -= point.y * 0.006 * cooling;
      point.z -= point.z * 0.006 * cooling;
    }
  }

  // Centre the cloud and scale it to SPREAD, so a graph of six nodes and a
  // graph of two hundred both fill the same view.
  const centre = points.reduce((acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y, z: acc.z + p.z }),
    { x: 0, y: 0, z: 0 });
  centre.x /= points.length; centre.y /= points.length; centre.z /= points.length;
  let furthest = 1;
  for (const p of points) {
    p.x -= centre.x; p.y -= centre.y; p.z -= centre.z;
    furthest = Math.max(furthest, Math.hypot(p.x, p.y, p.z));
  }
  const fit = SPREAD / furthest;
  for (const p of points) { p.x *= fit; p.y *= fit; p.z *= fit; }
  return byId;
}

/* The camera. Yaw turns around the vertical axis, pitch tilts, and the target
   is the point in the middle of the view. Everything that animates eases
   towards a "...To" value, so a glide and an instant jump are the same code. */
type Camera = {
  yaw: number; pitch: number; zoom: number; zoomTo: number;
  tx: number; ty: number; tz: number; txTo: number; tyTo: number; tzTo: number;
  spinVelocity: number; tiltVelocity: number; intro: number;
};

const freshCamera = (): Camera => ({
  yaw: START_YAW, pitch: START_PITCH, zoom: START_ZOOM, zoomTo: START_ZOOM,
  tx: 0, ty: 0, tz: 0, txTo: 0, tyTo: 0, tzTo: 0,
  spinVelocity: 0, tiltVelocity: 0, intro: 1,
});

function project(p: { x: number; y: number; z: number }, cam: Camera, grow = 1): Projected {
  const dx = p.x * grow - cam.tx;
  const dy = p.y * grow - cam.ty;
  const dz = p.z * grow - cam.tz;
  const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw);
  const cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
  const x1 = dx * cy - dz * sy;
  const z1 = dx * sy + dz * cy;
  const y2 = dy * cp - z1 * sp;
  const z2 = dy * sp + z1 * cp;
  const f = FOCAL / Math.max(FOCAL * 0.25, FOCAL + z2);
  return { sx: VIEW / 2 + x1 * f * cam.zoom, sy: VIEW / 2 + y2 * f * cam.zoom, f, z: z2 };
}

/** A movement on screen (viewBox units) as a movement in the brain's space. */
function unproject(ux: number, uy: number, f: number, cam: Camera) {
  const cx = ux / (f * cam.zoom);
  const cyv = uy / (f * cam.zoom);
  const cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
  const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw);
  const dy = cyv * cp;
  const z1 = -cyv * sp;
  return { dx: cx * cy + z1 * sy, dy, dz: -cx * sy + z1 * cy };
}

const round = (value: number) => Math.round(value * 100) / 100;
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));
const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);

/** useLayoutEffect in the browser; the static export renders once on the
    server, where React warns about it and it would do nothing anyway. */
const useBrowserLayoutEffect = typeof window !== "undefined" ? useLayoutEffect : useEffect;

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);
  return reduced;
}

/** The floor rings under the brain: a depth cue, drawn once per frame. */
const FLOOR_RINGS = [SPREAD * 1.1, SPREAD * 0.62];
const FLOOR_Y = SPREAD * 0.92;
const FLOOR_STEPS = 56;

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
  const reducedMotion = usePrefersReducedMotion();
  const [type, setType] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  /* The turn is ON by default and OFF with reduced motion; the person can
     still switch it on there, because then it is their choice. */
  const [spinChoice, setSpinChoice] = useState<boolean | null>(null);
  const spinning = spinChoice ?? !reducedMotion;
  /* MANUAL POSITIONS, laid over the computed ones (in 3D).
     Dragging a node moves it here and the simulation is not re-run, so a graph
     the user has arranged by hand stays arranged. It is per-view state and not
     persisted: writing layout coordinates to the database would make the graph
     a document, and the next derived node would arrive with nowhere to go. */
  const [moved, setMoved] = useState<Record<string, { x: number; y: number; z: number }>>({});
  const [order, setOrder] = useState<string[]>([]);
  const svgRef = useRef<SVGSVGElement | null>(null);

  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];
  const nodeTitles = useMemo(() => new Map(nodes.map((node) => [node.id, node.title])), [nodes]);

  const computed = useMemo(() => layout3d(nodes, edges), [nodes, edges]);
  const positions = useMemo(() => {
    if (!Object.keys(moved).length) return computed;
    const merged = new Map(computed);
    for (const [id, point] of Object.entries(moved)) {
      if (merged.has(id)) merged.set(id, { id, ...point });
    }
    return merged;
  }, [computed, moved]);

  const needle = query.trim().toLowerCase();
  const matches = useMemo(() => {
    if (!needle) return null;
    return new Set(nodes.filter((node) => node.title.toLowerCase().includes(needle))
      .map((node) => node.id));
  }, [nodes, needle]);

  /* THE FOCUS is whatever the user is pointing at, then whatever they selected.
     Hover is transient and selection is sticky, so hover wins while it lasts —
     that is what makes sweeping the pointer across a graph a way to READ it. */
  const focus = hovered ?? selected;
  const selectedNode = nodes.find((node) => node.id === selected);
  const selectedLinks = selected ? edges.filter((edge) =>
    edge.source === selected || edge.target === selected) : [];

  const neighbours = useMemo(() => {
    if (!focus) return null;
    const set = new Set<string>([focus]);
    for (const edge of edges) {
      if (edge.source === focus) set.add(edge.target);
      if (edge.target === focus) set.add(edge.source);
    }
    return set;
  }, [focus, edges]);

  /* Signals travelling along the focused node's links: the "thinking" look.
     Only with motion allowed, and capped so a hub cannot flood the view. */
  const pulseEdges = useMemo(() => {
    if (!focus || reducedMotion) return [];
    return edges.filter((edge) => edge.source === focus || edge.target === focus).slice(0, MAX_PULSES);
  }, [focus, edges, reducedMotion]);

  /* Nodes are drawn far-to-near so a near sphere covers a far one. The order
     is recomputed as the brain turns, but applied at most a few times a
     second: a React re-render per frame would be the expensive part. */
  const ordered = useMemo(() => {
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const seen = new Set<string>();
    const list: KnowledgeNode[] = [];
    for (const id of order) {
      const node = byId.get(id);
      if (node && !seen.has(id)) { list.push(node); seen.add(id); }
    }
    for (const node of nodes) if (!seen.has(node.id)) list.push(node);
    return list;
  }, [nodes, order]);

  /* ------------------------------------------------------------------ engine
     Everything the animation loop reads lives in ONE mutable object, so the
     loop never closes over a stale render. Each render refreshes it below. */
  const engine = useRef({
    cam: freshCamera(),
    positions: new Map<string, Vec3>(),
    nodeIds: [] as string[],
    edges: [] as KnowledgeEdge[],
    pulses: [] as KnowledgeEdge[],
    focus: null as string | null,
    proj: new Map<string, Projected>(),
    nodeEls: new Map<string, SVGGElement>(),
    parts: new WeakMap<SVGGElement, { body: Element | null; label: Element | null }>(),
    edgeEls: new Map<string, SVGLineElement>(),
    pulseEls: new Map<string, SVGCircleElement>(),
    floorEls: [] as (SVGPolylineElement | null)[],
    spin: true,
    reduced: false,
    pointerInside: false,
    resumeAt: 0,
    dragging: false,
    frame: null as number | null,
    last: 0,
    slowFrames: 0,
    countedFrames: 0,
    orderKey: "",
    orderAt: 0,
    setOrder: (_ids: string[]) => {},
    onTooSlow: () => {},
  });
  const E = engine.current;
  E.positions = positions;
  E.nodeIds = nodes.map((node) => node.id);
  E.edges = edges;
  E.pulses = pulseEdges;
  E.focus = focus;
  E.spin = spinning;
  E.reduced = reducedMotion;
  E.setOrder = setOrder;
  E.onTooSlow = () => setSpinChoice(false);

  const draw = (now: number) => {
    const cam = E.cam;
    const grow = easeOut(cam.intro);
    const proj = new Map<string, Projected>();
    for (const id of E.nodeIds) {
      const point = E.positions.get(id);
      if (point) proj.set(id, project(point, cam, grow));
    }
    E.proj = proj;

    for (const [id, el] of E.nodeEls) {
      const p = proj.get(id);
      if (!p) continue;
      const scale = clamp(p.f * cam.zoom, 0.3, 3.4);
      el.setAttribute("transform", `translate(${round(p.sx)} ${round(p.sy)}) scale(${round(scale)})`);
      // Fog: far spheres are fainter, so depth reads at a glance.
      const fog = clamp(0.5 + (p.f - 0.8) * 1.6, 0.5, 1);
      let parts = E.parts.get(el);
      if (!parts) {
        parts = { body: el.querySelector(".graph-node__body"), label: el.querySelector(".graph-node__label") };
        E.parts.set(el, parts);
      }
      parts.body?.setAttribute("opacity", String(round(fog)));
      parts.label?.setAttribute("fill-opacity", String(round(clamp(0.45 + (p.f - 0.9) * 3, 0.45, 1))));
    }
    for (const edge of E.edges) {
      const el = E.edgeEls.get(edge.id);
      const a = proj.get(edge.source);
      const b = proj.get(edge.target);
      if (!el || !a || !b) continue;
      el.setAttribute("x1", String(round(a.sx))); el.setAttribute("y1", String(round(a.sy)));
      el.setAttribute("x2", String(round(b.sx))); el.setAttribute("y2", String(round(b.sy)));
      el.setAttribute("stroke-opacity", String(round(clamp(0.25 + ((a.f + b.f) / 2 - 0.8) * 1.6, 0.22, 1))));
    }
    E.pulses.forEach((edge, index) => {
      const el = E.pulseEls.get(edge.id);
      if (!el || !E.focus) return;
      const from = proj.get(E.focus);
      const to = proj.get(edge.source === E.focus ? edge.target : edge.source);
      if (!from || !to) return;
      const t = ((now / 1500) + index * 0.29) % 1;
      el.setAttribute("cx", String(round(from.sx + (to.sx - from.sx) * t)));
      el.setAttribute("cy", String(round(from.sy + (to.sy - from.sy) * t)));
      el.setAttribute("opacity", String(round(Math.sin(Math.PI * t) * 0.9)));
    });
    FLOOR_RINGS.forEach((radius, ring) => {
      const el = E.floorEls[ring];
      if (!el) return;
      const pts: string[] = [];
      for (let i = 0; i <= FLOOR_STEPS; i += 1) {
        const angle = (i / FLOOR_STEPS) * Math.PI * 2;
        const p = project({ x: Math.cos(angle) * radius, y: FLOOR_Y, z: Math.sin(angle) * radius }, cam, grow);
        pts.push(`${round(p.sx)},${round(p.sy)}`);
      }
      el.setAttribute("points", pts.join(" "));
    });

    // Far-to-near order, applied a few times a second at most.
    const depthOrder = [...proj.entries()].sort((a, b) => b[1].z - a[1].z).map(([id]) => id);
    const key = depthOrder.join("|");
    if (key !== E.orderKey && now - E.orderAt > 140) {
      E.orderKey = key;
      E.orderAt = now;
      E.setOrder(depthOrder);
    }
  };

  /** Advance the camera by dt ms. Returns true while something still moves. */
  const advance = (dt: number, now: number) => {
    const cam = E.cam;
    let busy = false;
    if (cam.intro < 1) {
      cam.intro = E.reduced ? 1 : Math.min(1, cam.intro + dt / 1100);
      busy = true;
    }
    const k = E.reduced ? 1 : 1 - Math.exp(-dt / 170);
    const glide = (from: number, to: number) => from + (to - from) * k;
    cam.tx = glide(cam.tx, cam.txTo); cam.ty = glide(cam.ty, cam.tyTo); cam.tz = glide(cam.tz, cam.tzTo);
    cam.zoom = glide(cam.zoom, cam.zoomTo);
    if (Math.abs(cam.tx - cam.txTo) + Math.abs(cam.ty - cam.tyTo) + Math.abs(cam.tz - cam.tzTo) > 0.3
        || Math.abs(cam.zoom - cam.zoomTo) > 0.002) busy = true;

    // A flick of the background keeps turning for a moment, then settles.
    if (!E.dragging && (Math.abs(cam.spinVelocity) > 0.00002 || Math.abs(cam.tiltVelocity) > 0.00002)) {
      cam.yaw += cam.spinVelocity * dt;
      cam.pitch = clamp(cam.pitch + cam.tiltVelocity * dt, -1.25, 1.25);
      const decay = Math.pow(0.9, dt / 16);
      cam.spinVelocity *= decay;
      cam.tiltVelocity *= decay;
      busy = true;
    }

    const idle = !E.pointerInside && !E.dragging && !E.focus;
    if (E.spin && idle) {
      if (now >= E.resumeAt) cam.yaw += SPIN_PER_MS * dt;
      busy = true;
    }
    if (E.pulses.length) busy = true;
    return busy;
  };

  const tick = (now: number) => {
    E.frame = null;
    const dt = Math.min(64, Math.max(0, now - E.last));
    E.last = now;
    const busy = advance(dt, now);
    draw(now);
    /* A PC THAT CANNOT KEEP UP gets a still brain rather than a stuttering
       one: if most of ~3 seconds of frames took longer than 45 ms, the turn
       switches itself off (the button shows it, and can switch it back on). */
    if (busy && E.spin) {
      E.countedFrames += 1;
      if (dt > 45) E.slowFrames += 1;
      if (E.countedFrames >= 90) {
        if (E.slowFrames > 60) E.onTooSlow();
        E.countedFrames = 0;
        E.slowFrames = 0;
      }
    }
    if (busy) E.frame = requestAnimationFrame(tick);
  };

  const kick = () => {
    if (E.frame !== null || typeof requestAnimationFrame === "undefined") return;
    E.last = performance.now();
    E.frame = requestAnimationFrame(tick);
  };

  // Every render re-applies the current camera to the (possibly new) DOM, and
  // restarts the loop if something should be moving.
  useBrowserLayoutEffect(() => {
    draw(typeof performance !== "undefined" ? performance.now() : 0);
    kick();
  });

  useEffect(() => () => {
    if (E.frame !== null) cancelAnimationFrame(E.frame);
    E.frame = null;
  }, [E]);

  // The brain "grows" out of its centre when it first appears with content.
  const hasNodes = nodes.length > 0;
  useEffect(() => {
    if (!hasNodes) return;
    E.cam.intro = E.reduced ? 1 : 0;
    kick();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasNodes]);

  /** Client pixels -> viewBox units. The SVG keeps its aspect ratio, so the
      viewBox fits the SHORTER side of the box. */
  const toViewUnits = (dx: number, dy: number) => {
    const box = svgRef.current?.getBoundingClientRect();
    const side = box ? Math.min(box.width, box.height) : 0;
    const fit = side ? VIEW / side : 1;
    return { ux: dx * fit, uy: dy * fit };
  };

  /** Glide the camera so this node sits in the middle of the view. */
  const centreOn = (id: string) => {
    const point = positions.get(id);
    if (!point) return;
    E.cam.txTo = point.x; E.cam.tyTo = point.y; E.cam.tzTo = point.z;
    E.cam.zoomTo = Math.max(E.cam.zoomTo, 1.25);
    kick();
  };

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
      const cam = engine.current.cam;
      cam.zoomTo = clamp(cam.zoomTo * (event.deltaY < 0 ? 1.12 : 1 / 1.12), ZOOM_MIN, ZOOM_MAX);
      kick();
    };
    element.addEventListener("wheel", handler, { passive: false });
    return () => element.removeEventListener("wheel", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasNodes]);

  /* SEARCH FOCUSES THE MATCH instead of only dimming everything else.
     Typing a name and being left to hunt for the one undimmed dot in a graph
     you have turned away from is not a search. A single match is selected and
     the camera glides to it; several matches stay highlighted, because gliding
     to an arbitrary one of them would be a guess. */
  useEffect(() => {
    if (!matches || matches.size !== 1) return;
    const [id] = [...matches];
    setSelected(id);
    centreOn(id);
    // Only when the query changes: re-centring on every render would fight the
    // user's own turning.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needle]);

  useEffect(() => { onRefresh(type); }, [type, onRefresh]);

  /* A node that is no longer in the payload cannot stay selected or displaced:
     a type filter, or a deleted node, would otherwise leave the detail panel
     naming something the graph no longer contains. */
  useEffect(() => {
    const alive = new Set(nodes.map((node) => node.id));
    setSelected((current) => (current && !alive.has(current) ? null : current));
    setMoved((current) => {
      const kept = Object.fromEntries(
        Object.entries(current).filter(([id]) => alive.has(id)));
      return Object.keys(kept).length === Object.keys(current).length ? current : kept;
    });
  }, [nodes]);

  /* Gestures. Dragging the background turns the brain; dragging a node moves
     that node in the plane of the screen. A press that moved is not a click. */
  const orbiting = useRef<{ x: number; y: number; t: number } | null>(null);
  const draggingNode = useRef<{ id: string; x: number; y: number } | null>(null);
  const travelled = useRef(0);

  const endGesture = () => {
    orbiting.current = null;
    draggingNode.current = null;
    E.dragging = false;
    if (E.reduced) { E.cam.spinVelocity = 0; E.cam.tiltVelocity = 0; }
    kick();
  };

  const resetView = () => {
    const intro = E.cam.intro;
    const next = freshCamera();
    if (E.reduced) {
      E.cam = { ...next, intro };
    } else {
      // Glide back instead of jumping: only the "...To" values are reset.
      Object.assign(E.cam, {
        txTo: 0, tyTo: 0, tzTo: 0, zoomTo: START_ZOOM, spinVelocity: 0, tiltVelocity: 0,
        yaw: E.cam.yaw, pitch: START_PITCH,
      });
    }
    setSelected(null);
    // "Repor vista" resets the VIEW and the arrangement. Leaving hand-moved
    // nodes where they were would make the button a partial undo, which is
    // the least predictable kind.
    setMoved({});
    kick();
  };

  if (loading && graph === null) {
    return <div className="page__inner"><Skeleton height={420} /></div>;
  }

  if (!nodes.length) {
    return (
      <div className="page__inner">
        <EmptyState
          title={t("Ainda não há nada para desenhar")}
          hint={t("O grafo mostra os nós do Second Brain e as ligações entre eles. Guarda algumas memórias sobre coisas concretas e volta aqui.")}
        />
      </div>
    );
  }

  return (
    <div className="page__inner graph-page">
      <div className="memory-toolbar">
        <select className="input input--select" value={type} aria-label={t("Filtrar por tipo")}
                onChange={(event) => setType(event.target.value)}>
          <option value="">{t("Todos os tipos")}</option>
          {types.map((entry) => (
            <option key={entry} value={entry}>{nodeTypeLabel(entry)}</option>
          ))}
        </select>
        <SearchField id="graph-search" value={query} onChange={setQuery}
                     placeholder={t("Procurar um nó…")} />
        <span className="stage__spacer" />
        <Button size="sm" aria-pressed={spinning} className="graph-spin"
                onClick={() => { setSpinChoice(!spinning); E.resumeAt = 0; kick(); }}>
          {spinning ? t("Parar rotação") : t("Rodar")}
        </Button>
        <Button size="sm" onClick={resetView}>{t("Repor vista")}</Button>
      </div>

      <div className="graph-canvas"
           onPointerEnter={() => { E.pointerInside = true; }}
           onPointerLeave={() => {
             E.pointerInside = false;
             // A short beat before the turn resumes, so leaving for a moment
             // does not set the brain spinning under the returning pointer.
             E.resumeAt = (typeof performance !== "undefined" ? performance.now() : 0) + 1200;
             kick();
           }}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${VIEW} ${VIEW}`}
          className="graph-svg"
          data-focused={focus ? "true" : undefined}
          role="img"
          aria-label={t("Cérebro em 3D com {0} nós e {1} ligações", { 0: nodes.length, 1: edges.length })}
          onPointerDown={(event) => {
            // Empty space: turn the brain. A node starts its own drag in its
            // handler and stops the event there, so the two never both run.
            orbiting.current = { x: event.clientX, y: event.clientY, t: event.timeStamp };
            travelled.current = 0;
            E.dragging = true;
            E.cam.spinVelocity = 0; E.cam.tiltVelocity = 0;
            (event.target as Element).setPointerCapture?.(event.pointerId);
          }}
          onPointerMove={(event) => {
            const node = draggingNode.current;
            if (node) {
              const { ux, uy } = toViewUnits(event.clientX - node.x, event.clientY - node.y);
              travelled.current += Math.hypot(event.clientX - node.x, event.clientY - node.y);
              draggingNode.current = { ...node, x: event.clientX, y: event.clientY };
              const f = E.proj.get(node.id)?.f ?? 1;
              const { dx, dy, dz } = unproject(ux, uy, f, E.cam);
              setMoved((current) => {
                const base = current[node.id] ?? positions.get(node.id);
                if (!base) return current;
                return { ...current, [node.id]: { x: base.x + dx, y: base.y + dy, z: base.z + dz } };
              });
              return;
            }
            const orbit = orbiting.current;
            if (!orbit) return;
            const dx = event.clientX - orbit.x;
            const dy = event.clientY - orbit.y;
            travelled.current += Math.hypot(dx, dy);
            const elapsed = Math.max(8, event.timeStamp - orbit.t);
            orbiting.current = { x: event.clientX, y: event.clientY, t: event.timeStamp };
            E.cam.yaw += dx * 0.0065;
            E.cam.pitch = clamp(E.cam.pitch + dy * 0.0065, -1.25, 1.25);
            E.cam.spinVelocity = (dx * 0.0065) / elapsed;
            E.cam.tiltVelocity = (dy * 0.0065) / elapsed;
            draw(performance.now());
          }}
          onPointerUp={endGesture}
          onPointerCancel={endGesture}
          onClick={() => {
            // A plain click on empty space lets go of the selection; the end
            // of a turn does not.
            if (travelled.current < 4) setSelected(null);
          }}
          onKeyDown={(event) => {
            // The keyboard turns the brain too, from any focused node.
            const cam = E.cam;
            const steps: Record<string, () => void> = {
              ArrowLeft: () => { cam.yaw -= 0.18; },
              ArrowRight: () => { cam.yaw += 0.18; },
              ArrowUp: () => { cam.pitch = clamp(cam.pitch - 0.14, -1.25, 1.25); },
              ArrowDown: () => { cam.pitch = clamp(cam.pitch + 0.14, -1.25, 1.25); },
              "+": () => { cam.zoomTo = clamp(cam.zoomTo * 1.15, ZOOM_MIN, ZOOM_MAX); },
              "=": () => { cam.zoomTo = clamp(cam.zoomTo * 1.15, ZOOM_MIN, ZOOM_MAX); },
              "-": () => { cam.zoomTo = clamp(cam.zoomTo / 1.15, ZOOM_MIN, ZOOM_MAX); },
            };
            const action = steps[event.key];
            if (!action) return;
            event.preventDefault();
            action();
            draw(performance.now());
            kick();
          }}
        >
          <defs>
            {/* Lit spheres: a bright spot up and to the left, a deep shade on
                the far side. One gradient per colour family. */}
            {([
              ["blue", "#f4f8ff", "#7fb0ff", "#1d4fa8"],
              ["teal", "#effffd", "#5fd6c8", "#0f6b66"],
              ["amber", "#fff8ea", "#f4be5c", "#8a5a0c"],
              ["green", "#f0fff4", "#6fd69a", "#17693a"],
              ["grey", "#f6f8fb", "#a9b6c8", "#3d4a5c"],
              ["selected", "#ffffff", "#a8ccff", "#2f6fe4"],
            ] as const).map(([name, light, mid, dark]) => (
              <radialGradient key={name} id={`nano-sphere-${name}`} cx="34%" cy="28%" r="78%">
                <stop offset="0%" stopColor={light} />
                <stop offset="42%" stopColor={mid} />
                <stop offset="100%" stopColor={dark} />
              </radialGradient>
            ))}
          </defs>

          <g className="graph-floor" aria-hidden="true">
            {FLOOR_RINGS.map((radius, ring) => (
              <polyline key={radius} fill="none"
                        ref={(el) => { E.floorEls[ring] = el; }} />
            ))}
          </g>

          <g className="graph-edges">
            {edges.map((edge) => {
              if (!positions.get(edge.source) || !positions.get(edge.target)) return null;
              // An edge is LIT when it touches the focused node, dimmed when a
              // focus exists and it does not.
              const lit = Boolean(focus)
                && (edge.source === focus || edge.target === focus);
              const dimmed = Boolean(focus) && !lit;
              return (
                <line key={edge.id} data-source={edge.source} data-target={edge.target}
                      ref={(el) => { if (el) E.edgeEls.set(edge.id, el); else E.edgeEls.delete(edge.id); }}
                      className={`graph-edge${dimmed ? " is-dim" : ""}${lit ? " is-lit" : ""}`}
                      strokeWidth={Math.min(3, 1 + edge.weight * 0.4)}>
                  <title>{`${edge.sourceTitle ?? nodeTitles.get(edge.source) ?? edge.source} — ${relationLabel(edge.relation)} — ${edge.targetTitle ?? nodeTitles.get(edge.target) ?? edge.target}`}</title>
                </line>
              );
            })}
          </g>

          <g className="graph-pulses" aria-hidden="true">
            {pulseEdges.map((edge) => (
              <circle key={edge.id} r={3.2} className="graph-pulse" opacity={0}
                      ref={(el) => { if (el) E.pulseEls.set(edge.id, el); else E.pulseEls.delete(edge.id); }} />
            ))}
          </g>

          {ordered.map((node) => {
            const point = positions.get(node.id);
            if (!point) return null;
            const radius = 16 + Math.min(13, node.mentionCount * 2);
            const dimmed = (matches && !matches.has(node.id))
              || (neighbours && !neighbours.has(node.id));
            const near = Boolean(neighbours && neighbours.has(node.id))
              || Boolean(matches && matches.has(node.id));
            return (
              <g key={node.id}
                 ref={(el) => { if (el) E.nodeEls.set(node.id, el); else E.nodeEls.delete(node.id); }}
                 className={`graph-node${dimmed ? " is-dim" : ""}${near ? " is-near" : ""}`
                            + `${selected === node.id ? " is-selected" : ""}`
                            + `${focus === node.id ? " is-focus" : ""}`}
                 data-type={node.type} data-id={node.id}
                 data-x={round(point.x)} data-y={round(point.y)} data-z={round(point.z)}
                 tabIndex={0}
                 role="button"
                 aria-label={`${node.title} — ${nodeTypeLabel(node.type)}`}
                 onPointerDown={(event) => {
                   // The node owns this gesture. Without stopPropagation the
                   // brain turns at the same time as the node moves.
                   event.stopPropagation();
                   travelled.current = 0;
                   E.dragging = true;
                   draggingNode.current = { id: node.id, x: event.clientX, y: event.clientY };
                   (event.currentTarget as Element).setPointerCapture?.(event.pointerId);
                 }}
                 onPointerEnter={() => setHovered(node.id)}
                 onPointerLeave={() => setHovered((current) => (current === node.id ? null : current))}
                 onFocus={() => setHovered(node.id)}
                 onBlur={() => setHovered((current) => (current === node.id ? null : current))}
                 onClick={(event) => {
                   event.stopPropagation();
                   if (travelled.current >= 4) return;
                   setSelected((current) => (current === node.id ? null : node.id));
                 }}
                 onDoubleClick={() => onOpenNode(node.id)}
                 onKeyDown={(event) => {
                   if (event.key === "Enter") onOpenNode(node.id);
                   if (event.key === " ") { event.preventDefault(); setSelected(node.id); }
                 }}>
                <g className="graph-node__body">
                  {selected === node.id && <circle r={radius + 9} className="graph-node__halo" />}
                  <circle r={radius} className="graph-node__dot" />
                  <circle r={radius} className="graph-node__rim" />
                </g>
                <text y={radius + 16} className="graph-node__label">{node.title}</text>
                <title>{t("{0} · {1} · {2} menções", { 0: node.title, 1: nodeTypeLabel(node.type), 2: node.mentionCount })}</title>
              </g>
            );
          })}
        </svg>

        <div className="graph-legend">
          {Array.from(new Set(nodes.map((node) => node.type))).slice(0, 8).map((entry) => (
            <span className="graph-legend__item" key={entry}>
              <span className={`graph-legend__dot node-type--${entry}`} />
              {nodeTypeLabel(entry)}
            </span>
          ))}
        </div>

        {selectedNode && (
          <div className="graph-selected">
            <span className="graph-selected__type">{nodeTypeLabel(selectedNode.type)}</span>
            <strong>{selectedNode.title}</strong>
            {selectedNode.summary && <span className="graph-selected__summary">{selectedNode.summary}</span>}
            <span className="graph-selected__links">
              {selectedLinks.length} {selectedLinks.length === 1 ? t("ligação real") : t("ligações reais")}
            </span>
            {selectedLinks.slice(0, 3).map((edge) => (
              <span className="graph-selected__link" key={edge.id}>
                {relationLabel(edge.relation)} · {edge.source === selectedNode.id
                  ? edge.targetTitle ?? nodeTitles.get(edge.target) ?? edge.target
                  : edge.sourceTitle ?? nodeTitles.get(edge.source) ?? edge.source}
              </span>
            ))}
            <span className="graph-selected__actions">
              <Button size="sm" onClick={() => centreOn(selectedNode.id)}>{t("Centrar")}</Button>
              <Button size="sm" onClick={() => onOpenNode(selectedNode.id)}>{t("Abrir detalhe")}</Button>
            </span>
          </div>
        )}
      </div>

      <div className="graph-foot">
        {/* A SEARCH THAT FINDS NOTHING HAS TO SAY SO.
            Matching dims every node that is not a hit, so a query with no hits
            fades the WHOLE graph and looks exactly like a view that broke.
            The count is stated instead: it is the only difference between
            "nothing here is called that" and "something went wrong". */}
        {needle ? (
          <span className={matches && matches.size ? "dim" : ""}
                role="status" aria-live="polite">
            {matches && matches.size
              ? t(matches.size === 1 ? "{0} de {1} nós corresponde a “{2}”" : "{0} de {1} nós correspondem a “{2}”",
                  { 0: matches.size, 1: nodes.length, 2: query.trim() })
              : t("Nenhum nó corresponde a “{0}”", { 0: query.trim() })}
          </span>
        ) : (
          <span className="dim">
            {t("{0} nós · {1} ligações", { 0: nodes.length, 1: edges.length })}{graph?.truncated && t(" · a mostrar os mais ligados de {0}", { 0: graph.total })}
            {t(" · arrasta o fundo para rodar o cérebro, arrasta um nó para o recolocar, roda para ampliar, setas rodam com o teclado, duplo-clique abre o detalhe")}
          </span>
        )}
      </div>

      <RetrievalFooter overview={overview} />
    </div>
  );
}
