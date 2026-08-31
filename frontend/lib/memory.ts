/**
 * Types for the Memória section: long-term memories and the Second Brain.
 *
 * These mirror what core/long_term_memory.py and core/knowledge_graph.py
 * return, field for field. They are types only — every call goes through
 * `call()` in lib/backend.ts, which is the single typed surface between the
 * renderer and Python. There is no query builder here, no filter expression,
 * and nothing that assembles a request the backend has not already named as an
 * operation.
 */

/** How a memory came to exist. Shown to the user, because it changes trust. */
export type MemoryOrigin = "explicit" | "inferred" | "manual";

/**
 * `candidate` is the important one. Nano proposes an inferred memory as a
 * candidate: it is listed, it is NOT put in the model's context, and it becomes
 * active only when the user promotes it. An assistant that silently promotes
 * its own guesses will eventually state one back as fact.
 */
export type MemoryStatus = "active" | "candidate" | "archived";

export type Memory = {
  id: string;
  text: string;
  kind: string;
  origin: MemoryOrigin | string;
  trust: string;
  status: MemoryStatus | string;
  confidence: number;
  importance: number;
  pinned: boolean;
  legacyKey: string | null;
  tags: string[];
  sourceConversationId: string | null;
  sourceMessageId: number | null;
  createdAt: string;
  updatedAt: string;
  lastUsedAt: string | null;
  useCount: number;
  /** Only present on search results. */
  score?: number;
};

export type MemoryStats = {
  total: number;
  active: number;
  candidates: number;
  archived: number;
  byKind: Record<string, number>;
};

export type KnowledgeNode = {
  id: string;
  slug: string;
  title: string;
  type: string;
  summary: string;
  body: string;
  tags: string[];
  pinned: boolean;
  mentionCount: number;
  origin: string;
  createdAt: string;
  updatedAt: string;
  score?: number;
};

export type KnowledgeEdge = {
  id: string;
  source: string;
  target: string;
  relation: string;
  weight: number;
  sourceTitle?: string;
  targetTitle?: string;
  sourceType?: string;
  targetType?: string;
};

export type KnowledgeGraphPayload = {
  ok?: boolean;
  nodes: KnowledgeNode[];
  edges: KnowledgeEdge[];
  /** True when the store holds more nodes than this slice returned. */
  truncated: boolean;
  total: number;
  totalEdges?: number;
  focus?: string | null;
  types?: string[];
};

export type KnowledgeStats = {
  nodes: number;
  edges: number;
  byType: Record<string, number>;
};

/**
 * What the retrieval layer is really doing.
 *
 * Rendered verbatim in the UI. Nano says "SQLite FTS5 (BM25)" or "correspondência
 * textual simples" because those are the two things that actually run — never
 * "semantic search", which would describe a capability this build does not have.
 */
export type RetrievalStats = {
  mode: "fts5" | "text" | string;
  engine: string;
  entries: number;
  byKind: Record<string, number>;
};

export type MemoryOverview = {
  profile: Record<string, any>;
  facts: { key: string; value: string }[];
  memories: Memory[];
  kinds: string[];
  stats: MemoryStats;
  knowledge: KnowledgeStats;
  retrieval: RetrievalStats;
  conversationCount: number;
  messageCount: number;
  ready: boolean;
  migration: { from?: number; to?: number; ok: boolean; error?: string | null };
  longTermEnabled: boolean;
  captureEnabled: boolean;
  documentsSupported: boolean;
  documentsNote: string;
};

/** Portuguese labels for the memory categories the backend defines. */
export const KIND_LABEL: Record<string, string> = {
  preference: "Preferência",
  fact: "Facto",
  hardware: "Hardware",
  software: "Software",
  project: "Projeto",
  goal: "Objetivo",
  decision: "Decisão",
  person: "Pessoa",
  other: "Outro",
};

export const NODE_TYPE_LABEL: Record<string, string> = {
  person: "Pessoa",
  project: "Projeto",
  topic: "Tema",
  game: "Jogo",
  software: "Software",
  device: "Dispositivo",
  goal: "Objetivo",
  preference: "Preferência",
  decision: "Decisão",
  note: "Nota",
};

export const RELATION_LABEL: Record<string, string> = {
  related_to: "relacionado com",
  part_of: "faz parte de",
  uses: "usa",
  prefers: "prefere",
  works_on: "trabalha em",
  decided: "decidiu",
  mentioned_in: "mencionado em",
  depends_on: "depende de",
};

export const ORIGIN_LABEL: Record<string, string> = {
  explicit: "pedido pelo utilizador",
  manual: "criado pelo utilizador",
  inferred: "inferido pelo Nano",
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}

export function nodeTypeLabel(type: string): string {
  return NODE_TYPE_LABEL[type] ?? type;
}

export function relationLabel(relation: string): string {
  return RELATION_LABEL[relation] ?? relation;
}

export function originLabel(origin: string): string {
  return ORIGIN_LABEL[origin] ?? origin;
}
