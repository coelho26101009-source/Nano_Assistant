"""The Second Brain: entities Nano knows about, and how they connect.

WHAT A NODE IS FOR
------------------
A memory is a sentence. A node is the *thing the sentence is about*, so that
"a minha placa gráfica é uma GTX 1660 Ti", "o Fortnite corre a 60 fps" and "vou
usar o Ollama neste PC" stop being three unrelated strings and become three
statements attached to a machine, a game and a tool that the user can look at,
navigate and correct.

THE RESTRAINT IS THE DESIGN
---------------------------
The failure mode of a knowledge graph built by an assistant is not too few
nodes, it is thousands of useless ones: a node per noun, an edge per
co-occurrence, and a graph that renders as a hairball nobody opens twice. So:

* nodes are created only from ACTIVE long-term memories and explicit user
  action — never from raw message text, never from a passing mention;
* an edge is written only when two nodes appear in the SAME memory, which is
  real evidence of a relationship rather than a statistical shadow of one;
* ``mention_count`` records how often the evidence recurred, so the UI can rank
  by what actually matters instead of showing everything at equal weight;
* every graph read is bounded by node count and by edge count, so a large store
  degrades into "the most connected part of the graph" rather than into a
  browser that stops responding.

EDGES CARRY NO AUTHORITY
------------------------
Like memories, nodes are text. Nothing in this module can grant a permission,
and no relation type here is consulted by the policy engine. ``depends_on`` is a
note about the user's world, not a capability.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Sequence

from core import text_normalize
from core.memory_schema import new_id
from core.retrieval import RetrievalIndex

logger = logging.getLogger("nano.knowledge")

#: Node types. Extensible by design — an unknown type is stored as given rather
#: than coerced, so a future extractor is not blocked by this list — but these
#: are the ones the UI offers a filter and an icon for.
NODE_TYPES: tuple[str, ...] = (
    "person", "project", "topic", "game", "software", "device",
    "goal", "preference", "decision", "note",
)

#: Relation vocabulary. `related_to` is the honest default: inventing a specific
#: relation from weak evidence is worse than admitting the connection is generic.
RELATIONS: tuple[str, ...] = (
    "related_to", "part_of", "uses", "prefers", "works_on",
    "decided", "mentioned_in", "depends_on",
)

DEFAULT_RELATION = "related_to"

#: Ceilings for the graph endpoint. A view that cannot be drawn is not a view.
MAX_GRAPH_NODES = 300
MAX_GRAPH_EDGES = 900
MAX_LIST_LIMIT = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tags(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        parts = []
    out: list[str] = []
    for tag in parts:
        clean = text_normalize.shorten(str(tag).strip().lstrip("#"), 32)
        if clean and clean not in out:
            out.append(clean)
        if len(out) >= 8:
            break
    return out


class KnowledgeGraph:
    """Nodes, edges and their links back to memories and conversations."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock,
                 index: RetrievalIndex | None = None):
        self.conn = conn
        self._lock = lock
        self.index = index

    # -------------------------------------------------------------- nodes

    def upsert_node(self, title: str, *, node_type: str = "topic", summary: str = "",
                    body: str = "", tags: Any = None, origin: str = "derived",
                    bump: bool = True) -> dict | None:
        """Create the node, or recognise the one that is already there.

        Identity is the slug of the title, not the title itself, so "Nano
        Project", "nano project" and "Nano  Project" are one node rather than
        three. That is the single most important line of defence against a
        graph that grows a near-duplicate every time the user rephrases.
        """
        clean_title = text_normalize.shorten(str(title or "").strip(), 90)
        if not clean_title:
            return None
        slug = text_normalize.slugify(clean_title)
        stamp = _now()
        try:
            with self._lock:
                existing = self.conn.execute(
                    "SELECT id, mention_count FROM knowledge_nodes WHERE slug=?", (slug,)
                ).fetchone()
                if existing:
                    node_id = existing[0]
                    fields = ["updated_at=?"]
                    params: list = [stamp]
                    if bump:
                        fields.append("mention_count = mention_count + 1")
                    if summary:
                        fields.append("summary=?")
                        params.append(text_normalize.shorten(summary, 400))
                    if body:
                        fields.append("body=?")
                        params.append(str(body)[:4000])
                    params.append(node_id)
                    self.conn.execute(
                        f"UPDATE knowledge_nodes SET {', '.join(fields)} WHERE id=?", params)
                else:
                    node_id = new_id("node")
                    self.conn.execute(
                        "INSERT INTO knowledge_nodes (id, slug, title, type, summary, body,"
                        " tags, pinned, mention_count, origin, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?,0,1,?,?,?)",
                        (node_id, slug, clean_title,
                         str(node_type or "topic"),
                         text_normalize.shorten(summary, 400), str(body)[:4000],
                         json.dumps(_tags(tags), ensure_ascii=False),
                         str(origin), stamp, stamp))
                self.conn.commit()
        except sqlite3.Error:
            logger.exception("Falha a criar/atualizar o nó '%s'", clean_title)
            return None

        node = self.get_node(node_id)
        if node:
            self._index(node)
        return node

    def get_node(self, node_id: str) -> dict | None:
        if not node_id:
            return None
        try:
            with self._lock:
                row = self.conn.execute(
                    f"SELECT {_NODE_COLUMNS} FROM knowledge_nodes WHERE id=?",
                    (str(node_id),)).fetchone()
        except sqlite3.Error:
            return None
        return _row_to_node(row) if row else None

    def node_by_title(self, title: str) -> dict | None:
        slug = text_normalize.slugify(title)
        if not slug:
            return None
        try:
            with self._lock:
                row = self.conn.execute(
                    f"SELECT {_NODE_COLUMNS} FROM knowledge_nodes WHERE slug=?",
                    (slug,)).fetchone()
        except sqlite3.Error:
            return None
        return _row_to_node(row) if row else None

    def list_nodes(self, *, limit: int = 120, node_type: str | None = None,
                   query: str = "", tag: str = "") -> list[dict]:
        limit = max(1, min(int(limit), MAX_LIST_LIMIT))
        clauses: list[str] = []
        params: list = []
        if node_type:
            clauses.append("type=?")
            params.append(str(node_type))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            with self._lock:
                rows = self.conn.execute(
                    f"SELECT {_NODE_COLUMNS} FROM knowledge_nodes{where}"
                    " ORDER BY pinned DESC, mention_count DESC, updated_at DESC LIMIT ?",
                    [*params, limit]).fetchall()
        except sqlite3.Error:
            logger.exception("Falha a listar nós do Second Brain")
            return []
        nodes = [_row_to_node(row) for row in rows]
        needle = text_normalize.normalize(query)
        if needle:
            nodes = [n for n in nodes if needle in text_normalize.normalize(
                f"{n['title']} {n['summary']} {' '.join(n['tags'])}")]
        tag_needle = text_normalize.normalize(tag)
        if tag_needle:
            nodes = [n for n in nodes
                     if any(tag_needle == text_normalize.normalize(t) for t in n["tags"])]
        return nodes

    def update_node(self, node_id: str, *, title: str | None = None,
                    node_type: str | None = None, summary: str | None = None,
                    body: str | None = None, tags: Any = None,
                    pinned: bool | None = None) -> dict:
        node = self.get_node(node_id)
        if node is None:
            return {"ok": False, "error": "unknown_node"}
        fields: list[str] = []
        params: list = []
        if title is not None:
            clean = text_normalize.shorten(str(title).strip(), 90)
            if not clean:
                return {"ok": False, "error": "empty_title"}
            slug = text_normalize.slugify(clean)
            with self._lock:
                clash = self.conn.execute(
                    "SELECT id FROM knowledge_nodes WHERE slug=? AND id<>?",
                    (slug, str(node_id))).fetchone()
            if clash:
                return {"ok": False, "error": "duplicate_node",
                        "detail": "já existe um nó com este nome"}
            fields += ["title=?", "slug=?"]
            params += [clean, slug]
        if node_type is not None:
            fields.append("type=?")
            params.append(str(node_type))
        if summary is not None:
            fields.append("summary=?")
            params.append(text_normalize.shorten(summary, 400))
        if body is not None:
            fields.append("body=?")
            params.append(str(body)[:4000])
        if tags is not None:
            fields.append("tags=?")
            params.append(json.dumps(_tags(tags), ensure_ascii=False))
        if pinned is not None:
            fields.append("pinned=?")
            params.append(1 if pinned else 0)
        if not fields:
            return {"ok": False, "error": "nothing_to_update"}
        fields.append("updated_at=?")
        params += [_now(), str(node_id)]
        try:
            with self._lock:
                self.conn.execute(
                    f"UPDATE knowledge_nodes SET {', '.join(fields)} WHERE id=?", params)
                self.conn.commit()
        except sqlite3.Error as exc:
            logger.exception("Falha a atualizar o nó %s", node_id)
            return {"ok": False, "error": "write_failed", "detail": str(exc)}
        updated = self.get_node(node_id)
        if updated:
            self._index(updated)
        return {"ok": True, "node": updated}

    def delete_node(self, node_id: str) -> dict:
        """Remove a node and every edge and link that referenced it.

        Explicit deletes rather than relying on ``ON DELETE CASCADE`` alone:
        the cascade only fires while ``PRAGMA foreign_keys`` is on, and a graph
        with an edge pointing at a node that no longer exists is a graph that
        renders a line into empty space.
        """
        if self.get_node(node_id) is None:
            return {"ok": False, "error": "unknown_node"}
        try:
            with self._lock:
                edges = self.conn.execute(
                    "DELETE FROM knowledge_edges WHERE source_id=? OR target_id=?",
                    (str(node_id), str(node_id))).rowcount or 0
                links = self.conn.execute(
                    "DELETE FROM knowledge_links WHERE node_id=?", (str(node_id),)
                ).rowcount or 0
                self.conn.execute("DELETE FROM knowledge_nodes WHERE id=?", (str(node_id),))
                self.conn.commit()
        except sqlite3.Error as exc:
            logger.exception("Falha a apagar o nó %s", node_id)
            return {"ok": False, "error": "delete_failed", "detail": str(exc)}
        if self.index is not None:
            self.index.remove(f"node:{node_id}")
        return {"ok": True, "id": node_id, "edges": edges, "links": links}

    def clear(self) -> dict:
        try:
            with self._lock:
                row = self.conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()
                total = int(row[0]) if row else 0
                self.conn.execute("DELETE FROM knowledge_edges")
                self.conn.execute("DELETE FROM knowledge_links")
                self.conn.execute("DELETE FROM knowledge_nodes")
                self.conn.commit()
        except sqlite3.Error as exc:
            return {"ok": False, "error": "delete_failed", "detail": str(exc)}
        removed = self.index.clear_kind("node") if self.index is not None else 0
        return {"ok": True, "removed": total, "indexEntries": removed}

    # -------------------------------------------------------------- edges

    def link(self, source_id: str, target_id: str, *, relation: str = DEFAULT_RELATION,
             weight: float = 1.0) -> dict:
        """Connect two nodes. Self-links and dangling ends are refused."""
        if not source_id or not target_id or source_id == target_id:
            return {"ok": False, "error": "invalid_edge"}
        if self.get_node(source_id) is None or self.get_node(target_id) is None:
            return {"ok": False, "error": "unknown_node"}
        relation = relation if relation in RELATIONS else DEFAULT_RELATION
        stamp = _now()
        try:
            with self._lock:
                self.conn.execute(
                    "INSERT INTO knowledge_edges (id, source_id, target_id, relation,"
                    " weight, created_at, updated_at) VALUES (?,?,?,?,?,?,?)"
                    " ON CONFLICT(source_id, target_id, relation) DO UPDATE SET"
                    "  weight = knowledge_edges.weight + 0.5, updated_at=excluded.updated_at",
                    (new_id("edge"), str(source_id), str(target_id), relation,
                     float(weight), stamp, stamp))
                self.conn.commit()
        except sqlite3.Error as exc:
            logger.exception("Falha a ligar %s -> %s", source_id, target_id)
            return {"ok": False, "error": "write_failed", "detail": str(exc)}
        return {"ok": True, "source": source_id, "target": target_id, "relation": relation}

    def unlink(self, source_id: str, target_id: str, relation: str | None = None) -> dict:
        params: list = [str(source_id), str(target_id)]
        clause = "source_id=? AND target_id=?"
        if relation:
            clause += " AND relation=?"
            params.append(str(relation))
        try:
            with self._lock:
                removed = self.conn.execute(
                    f"DELETE FROM knowledge_edges WHERE {clause}", params).rowcount or 0
                self.conn.commit()
        except sqlite3.Error as exc:
            return {"ok": False, "error": "delete_failed", "detail": str(exc)}
        return {"ok": True, "removed": removed}

    def edges_for(self, node_id: str, *, limit: int = 60) -> list[dict]:
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT e.id, e.source_id, e.target_id, e.relation, e.weight,"
                    "       s.title, t.title, s.type, t.type"
                    "  FROM knowledge_edges e"
                    "  JOIN knowledge_nodes s ON s.id = e.source_id"
                    "  JOIN knowledge_nodes t ON t.id = e.target_id"
                    " WHERE e.source_id=? OR e.target_id=?"
                    " ORDER BY e.weight DESC LIMIT ?",
                    (str(node_id), str(node_id), max(1, min(int(limit), 200)))).fetchall()
        except sqlite3.Error:
            return []
        return [
            {"id": r[0], "source": r[1], "target": r[2], "relation": r[3],
             "weight": float(r[4] or 1.0), "sourceTitle": r[5], "targetTitle": r[6],
             "sourceType": r[7], "targetType": r[8]}
            for r in rows
        ]

    # ------------------------------------------------------- links to data

    def attach(self, node_id: str, kind: str, ref_id: str) -> bool:
        """Record that a node is evidenced by a memory or a conversation."""
        if kind not in {"memory", "conversation"} or not node_id or not ref_id:
            return False
        try:
            with self._lock:
                cursor = self.conn.execute(
                    "INSERT OR IGNORE INTO knowledge_links (id, node_id, kind, ref_id,"
                    " created_at) VALUES (?,?,?,?,?)",
                    (new_id("link"), str(node_id), str(kind), str(ref_id), _now()))
                self.conn.commit()
            return bool(cursor.rowcount)
        except sqlite3.Error:
            logger.exception("Falha a ligar o nó %s a %s:%s", node_id, kind, ref_id)
            return False

    def links_for(self, node_id: str) -> dict:
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT kind, ref_id FROM knowledge_links WHERE node_id=?"
                    " ORDER BY created_at DESC LIMIT 200", (str(node_id),)).fetchall()
        except sqlite3.Error:
            return {"memory": [], "conversation": []}
        grouped: dict[str, list[str]] = {"memory": [], "conversation": []}
        for kind, ref_id in rows:
            grouped.setdefault(str(kind), []).append(str(ref_id))
        return grouped

    def nodes_for_ref(self, kind: str, ref_id: str) -> list[dict]:
        """Which nodes a given memory or conversation contributed to."""
        try:
            with self._lock:
                rows = self.conn.execute(
                    f"SELECT {_NODE_COLUMNS_QUALIFIED} FROM knowledge_nodes n"
                    "  JOIN knowledge_links l ON l.node_id = n.id"
                    " WHERE l.kind=? AND l.ref_id=?"
                    " ORDER BY n.mention_count DESC LIMIT 50",
                    (str(kind), str(ref_id))).fetchall()
        except sqlite3.Error:
            logger.exception("Falha a listar nós de %s:%s", kind, ref_id)
            return []
        return [_row_to_node(row) for row in rows]

    def prune_links(self, kind: str, ref_ids: Sequence[str]) -> int:
        """Drop links whose target no longer exists (a deleted memory or chat)."""
        ids = [str(value) for value in ref_ids if value]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        try:
            with self._lock:
                removed = self.conn.execute(
                    f"DELETE FROM knowledge_links WHERE kind=? AND ref_id IN ({marks})",
                    [str(kind), *ids]).rowcount or 0
                self.conn.commit()
            return removed
        except sqlite3.Error:
            return 0

    # -------------------------------------------------------------- graph

    def graph(self, *, limit: int = 120, node_type: str | None = None,
              focus_id: str | None = None, depth: int = 1) -> dict:
        """A bounded slice of the graph, ready to draw.

        With a `focus_id`, returns that node's neighbourhood to `depth` hops.
        Without one, returns the most-connected nodes — which is the part of the
        graph worth looking at, and keeps the first render fast on a large store.
        """
        limit = max(1, min(int(limit), MAX_GRAPH_NODES))
        if focus_id:
            ids = self._neighbourhood(focus_id, depth=depth, limit=limit)
        else:
            ids = [node["id"] for node in self.list_nodes(limit=limit, node_type=node_type)]
        if not ids:
            return {"nodes": [], "edges": [], "truncated": False, "total": self.stats()["nodes"]}

        marks = ",".join("?" * len(ids))
        try:
            with self._lock:
                node_rows = self.conn.execute(
                    f"SELECT {_NODE_COLUMNS} FROM knowledge_nodes WHERE id IN ({marks})",
                    ids).fetchall()
                edge_rows = self.conn.execute(
                    "SELECT id, source_id, target_id, relation, weight FROM knowledge_edges"
                    f" WHERE source_id IN ({marks}) AND target_id IN ({marks})"
                    " ORDER BY weight DESC LIMIT ?", [*ids, *ids, MAX_GRAPH_EDGES]).fetchall()
        except sqlite3.Error:
            logger.exception("Falha a construir o grafo")
            return {"nodes": [], "edges": [], "truncated": False, "total": 0}

        stats = self.stats()
        return {
            "nodes": [_row_to_node(row) for row in node_rows],
            "edges": [{"id": r[0], "source": r[1], "target": r[2], "relation": r[3],
                       "weight": float(r[4] or 1.0)} for r in edge_rows],
            "truncated": stats["nodes"] > len(node_rows),
            "total": stats["nodes"],
            "totalEdges": stats["edges"],
            "focus": focus_id or None,
        }

    def _neighbourhood(self, node_id: str, *, depth: int, limit: int) -> list[str]:
        frontier = {str(node_id)}
        seen = set(frontier)
        for _ in range(max(1, min(int(depth), 3))):
            if not frontier or len(seen) >= limit:
                break
            marks = ",".join("?" * len(frontier))
            try:
                with self._lock:
                    rows = self.conn.execute(
                        "SELECT source_id, target_id FROM knowledge_edges"
                        f" WHERE source_id IN ({marks}) OR target_id IN ({marks})"
                        " LIMIT ?", [*frontier, *frontier, MAX_GRAPH_EDGES]).fetchall()
            except sqlite3.Error:
                break
            neighbours = {str(value) for row in rows for value in row} - seen
            seen |= neighbours
            frontier = neighbours
        return list(seen)[:limit]

    # ------------------------------------------------------------- search

    def search(self, query: str, *, limit: int = 5) -> list[dict]:
        if not str(query or "").strip():
            return []
        results: list[dict] = []
        if self.index is not None:
            for hit in self.index.search(query, kinds=["node"], limit=max(1, int(limit)) * 2):
                node_id = hit.metadata.get("nodeId") or hit.entry_id.split(":", 1)[-1]
                node = self.get_node(str(node_id))
                if node:
                    node["score"] = round(hit.score, 4)
                    results.append(node)
        if not results:
            wanted = text_normalize.token_set(query)
            for node in self.list_nodes(limit=120):
                if wanted & text_normalize.token_set(f"{node['title']} {node['summary']}"):
                    node["score"] = round(
                        text_normalize.overlap_score(query, f"{node['title']} {node['summary']}"), 4)
                    results.append(node)
        results.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        return results[:max(1, int(limit))]

    def stats(self) -> dict:
        try:
            with self._lock:
                nodes = int(self.conn.execute(
                    "SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0])
                edges = int(self.conn.execute(
                    "SELECT COUNT(*) FROM knowledge_edges").fetchone()[0])
                by_type = dict(self.conn.execute(
                    "SELECT type, COUNT(*) FROM knowledge_nodes GROUP BY type").fetchall())
        except sqlite3.Error:
            return {"nodes": 0, "edges": 0, "byType": {}}
        return {"nodes": nodes, "edges": edges,
                "byType": {str(k): int(v) for k, v in by_type.items()}}

    def _index(self, node: dict) -> bool:
        if self.index is None:
            return False
        body = " ".join(part for part in (node.get("summary"), node.get("body")) if part)
        return self.index.upsert(
            f"node:{node['id']}", kind="node", scope="", title=node["title"],
            body=body or node["title"], created_at=node.get("createdAt") or _now(),
            metadata={"nodeId": node["id"], "type": node.get("type")})


_NODE_COLUMNS = (
    "id, slug, title, type, summary, body, tags, pinned, mention_count, origin,"
    " created_at, updated_at"
)

#: The same columns, qualified. Required wherever knowledge_nodes is joined to a
#: table that also has `id` and `created_at` -- SQLite would otherwise refuse the
#: query as ambiguous, and the join is the whole point of those reads.
_NODE_COLUMNS_QUALIFIED = ", ".join(
    f"n.{column.strip()}" for column in _NODE_COLUMNS.split(","))


def _row_to_node(row) -> dict:
    try:
        tags = json.loads(row[6]) if row[6] else []
    except (ValueError, TypeError):
        tags = []
    return {
        "id": row[0],
        "slug": row[1],
        "title": row[2],
        "type": row[3],
        "summary": row[4] or "",
        "body": row[5] or "",
        "tags": tags if isinstance(tags, list) else [],
        "pinned": bool(row[7]),
        "mentionCount": int(row[8] or 0),
        "origin": row[9] or "derived",
        "createdAt": row[10],
        "updatedAt": row[11],
    }


__all__ = [
    "DEFAULT_RELATION",
    "MAX_GRAPH_EDGES",
    "MAX_GRAPH_NODES",
    "NODE_TYPES",
    "RELATIONS",
    "KnowledgeGraph",
]
