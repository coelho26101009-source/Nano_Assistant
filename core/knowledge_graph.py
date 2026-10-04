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

WHO OWNS WHAT: DERIVED STATE FOLLOWS ITS SOURCE
-----------------------------------------------
The graph holds two kinds of state, and forgetting depends on telling them
apart.

* ``origin='manual'`` -- a node the user created, or one whose content they
  edited, and an edge drawn through :meth:`KnowledgeGraph.link`. It is the
  user's own data: it survives without any memory behind it, and derivation
  never writes over its summary.
* ``origin='derived'`` -- a node or edge that exists BECAUSE active memories
  imply it. It is a cache, not a copy: :meth:`KnowledgeGraph.sync_derived`
  makes it equal to what the active memories support, so a derived node lives
  exactly as long as one of them names it, carries the text of one of them,
  counts them rather than the times it was re-derived, and goes when the last
  one is deleted, archived, demoted or edited away. Its evidence is the
  ``knowledge_links`` rows of kind ``memory``.

A manual node can still have memory evidence -- a memory may name what the user
created -- and the sync keeps that evidence exact without touching the text.

When the user deletes a node, renames one, or removes an edge that memories
still imply, ``knowledge_suppressions`` records which memory's derivation they
removed, so rebuilding from the memories does not undo them. It holds a memory
id and a slug, never text, and goes with the memory (``ON DELETE CASCADE``).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

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
    "related_to", "part_of", "uses", "has", "prefers", "works_on",
    "decided", "mentioned_in", "depends_on",
)

DEFAULT_RELATION = "related_to"

#: Relations whose direction carries meaning. A `uses` edge from A to B says
#: something a `uses` edge from B to A does not, so those two are different
#: edges. `related_to` and `mentioned_in` are symmetric, and storing both
#: directions of a symmetric relation is how a graph grows two lines where the
#: evidence supports one.
SYMMETRIC_RELATIONS: frozenset[str] = frozenset({"related_to"})

#: Ceilings for the graph endpoint. A view that cannot be drawn is not a view.
MAX_GRAPH_NODES = 300
MAX_GRAPH_EDGES = 900
MAX_LIST_LIMIT = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def node_target(slug: str) -> str:
    """The suppression key for a node one memory would derive."""
    return f"node:{slug}"


def edge_target(source_slug: str, relation: str, target_slug: str) -> str:
    """The suppression key for an edge one memory would derive.

    In slug space, not id space: a node that is deleted and later re-derived
    gets a new id, and the suppression must still recognise it.
    """
    if relation in SYMMETRIC_RELATIONS and target_slug < source_slug:
        source_slug, target_slug = target_slug, source_slug
    return f"edge:{source_slug}|{relation}|{target_slug}"


@dataclass
class DerivedNode:
    """One node the active memories support, as the sync must leave it."""

    title: str
    node_type: str
    summary: str
    #: Supporting memory ids, the representative one first.
    evidence: list[str] = field(default_factory=list)
    #: Source conversations of that evidence. Filtered to live ones on apply.
    conversations: set[str] = field(default_factory=set)


@dataclass
class DerivedGraph:
    """Everything the active memories support, keyed by slug.

    ``edges`` maps ``(source slug, target slug, relation)`` to the number of
    distinct memories behind the edge. ``targets`` lists, per active memory,
    every node and edge it derives BEFORE suppressions are applied, which is
    how a suppression that no longer matches anything is recognised as stale.
    """

    nodes: dict[str, DerivedNode] = field(default_factory=dict)
    edges: dict[tuple[str, str, str], int] = field(default_factory=dict)
    targets: dict[str, set[str]] = field(default_factory=dict)


def derived_edge_weight(support: int) -> float:
    """1.0 for one memory, +0.5 for each further one: the scale ``link`` uses."""
    return 1.0 + 0.5 * (max(1, int(support)) - 1)


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
                    body: str = "", tags: Any = None, origin: str = "manual",
                    bump: bool = True) -> dict | None:
        """Create the node, or recognise the one that is already there.

        Identity is the slug of the title, not the title itself, so "Nano
        Project", "nano project" and "Nano  Project" are one node rather than
        three. That is the single most important line of defence against a
        graph that grows a near-duplicate every time the user rephrases.

        A call here is an explicit act, so the node is ``manual`` unless the
        caller says otherwise; derivation goes through :meth:`sync_derived`.
        Creating by hand a node that memories derived ADOPTS it -- and the
        memory's sentence it carried does not become the user's summary. The
        reverse never happens: ``origin='derived'`` writes nothing over a node
        the user owns.
        """
        clean_title = text_normalize.shorten(str(title or "").strip(), 90)
        if not clean_title:
            return None
        slug = text_normalize.slugify(clean_title)
        stamp = _now()
        try:
            with self._lock:
                existing = self.conn.execute(
                    "SELECT id, origin FROM knowledge_nodes WHERE slug=?", (slug,)
                ).fetchone()
                if existing:
                    node_id = existing[0]
                    owned = existing[1] == "manual"
                    writes_content = origin == "manual" or not owned
                    fields = ["updated_at=?"]
                    params: list = [stamp]
                    if bump:
                        fields.append("mention_count = mention_count + 1")
                    if origin == "manual" and not owned:
                        fields.append("origin='manual'")
                        if not summary:
                            fields.append("summary=''")
                    if summary and writes_content:
                        fields.append("summary=?")
                        params.append(text_normalize.shorten(summary, 400))
                    if body and writes_content:
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
        """Edit a node from the Second Brain.

        Changing what a DERIVED node says -- its title, summary or body -- makes
        it the user's (``origin='manual'``): what they typed must outlive the
        memory that first produced the node, and a later sync must not
        overwrite it. The memory's sentence does not come along: when the edit
        leaves the summary alone, the derived copy is cleared. Saving the text
        unchanged is not an edit. Type, tags and pinning are presentation and
        leave the node derived.

        A rename also records that the memories behind the node no longer
        derive its OLD name, or rebuilding from them would bring it back as a
        second node beside the renamed one.
        """
        node = self.get_node(node_id)
        if node is None:
            return {"ok": False, "error": "unknown_node"}
        fields: list[str] = []
        params: list = []
        renamed_from: str | None = None
        content_changed = False
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
            content_changed = clean != node["title"]
            if slug != node["slug"]:
                renamed_from = node["slug"]
        if node_type is not None:
            fields.append("type=?")
            params.append(str(node_type))
        if summary is not None:
            clean_summary = text_normalize.shorten(summary, 400)
            fields.append("summary=?")
            params.append(clean_summary)
            content_changed = content_changed or clean_summary != node["summary"]
        if body is not None:
            fields.append("body=?")
            params.append(str(body)[:4000])
            content_changed = content_changed or str(body)[:4000] != node["body"]
        if tags is not None:
            fields.append("tags=?")
            params.append(json.dumps(_tags(tags), ensure_ascii=False))
        if pinned is not None:
            fields.append("pinned=?")
            params.append(1 if pinned else 0)
        if not fields:
            return {"ok": False, "error": "nothing_to_update"}
        if content_changed and node["origin"] != "manual":
            fields.append("origin='manual'")
            if summary is None:
                fields.append("summary=''")
        fields.append("updated_at=?")
        params += [_now(), str(node_id)]
        try:
            with self._lock:
                if renamed_from is not None:
                    self._suppress(self._evidence(node_id), node_target(renamed_from))
                self.conn.execute(
                    f"UPDATE knowledge_nodes SET {', '.join(fields)} WHERE id=?", params)
                self.conn.commit()
        except sqlite3.Error as exc:
            self.conn.rollback()
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

        The memories that still name the node are recorded as suppressed for
        it, so the next sync does not derive it straight back from them. A
        memory that names it AFTER the deletion is new evidence and may.
        """
        node = self.get_node(node_id)
        if node is None:
            return {"ok": False, "error": "unknown_node"}
        try:
            with self._lock:
                self._suppress(self._evidence(node_id), node_target(node["slug"]))
                edges = self.conn.execute(
                    "DELETE FROM knowledge_edges WHERE source_id=? OR target_id=?",
                    (str(node_id), str(node_id))).rowcount or 0
                links = self.conn.execute(
                    "DELETE FROM knowledge_links WHERE node_id=?", (str(node_id),)
                ).rowcount or 0
                self.conn.execute("DELETE FROM knowledge_nodes WHERE id=?", (str(node_id),))
                self.conn.commit()
        except sqlite3.Error as exc:
            self.conn.rollback()
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
        """Connect two nodes. Self-links and dangling ends are refused.

        A call here is an explicit act, so the edge is ``manual`` and survives
        any sync; derived edges are written by :meth:`sync_derived` alone.
        Linking over a derived edge makes it the user's.

        TWO RULES THAT KEEP THE EDGE SET HONEST, both enforced here so no caller
        has to remember them.

        1. A SYMMETRIC relation is stored in ONE canonical direction, chosen by
           node id. Without this, "Groq e Ollama" and "Ollama e Groq" -- the same
           fact said twice -- produce two edges between the same pair, and the
           graph reports twice the connections it has evidence for.

        2. A GENERIC relation never lands on top of a specific one. If the store
           already knows "Projeto Nano --uses--> Ollama", a later co-occurrence
           must not add "Projeto Nano --related_to--> Ollama" beside it: that is
           the same fact, drawn twice, stated worse the second time. A specific
           relation may still be added next to a generic one, because that is
           new information arriving.
        """
        if not source_id or not target_id or source_id == target_id:
            return {"ok": False, "error": "invalid_edge"}
        if self.get_node(source_id) is None or self.get_node(target_id) is None:
            return {"ok": False, "error": "unknown_node"}
        relation = relation if relation in RELATIONS else DEFAULT_RELATION
        if relation in SYMMETRIC_RELATIONS and str(target_id) < str(source_id):
            source_id, target_id = target_id, source_id
        if relation == DEFAULT_RELATION and self._specific_edge_exists(source_id, target_id):
            return {"ok": True, "source": source_id, "target": target_id,
                    "relation": relation, "skipped": "already_specific"}
        stamp = _now()
        try:
            with self._lock:
                self.conn.execute(
                    "INSERT INTO knowledge_edges (id, source_id, target_id, relation,"
                    " weight, created_at, updated_at, origin)"
                    " VALUES (?,?,?,?,?,?,?,'manual')"
                    " ON CONFLICT(source_id, target_id, relation) DO UPDATE SET"
                    "  weight = knowledge_edges.weight + 0.5, updated_at=excluded.updated_at,"
                    "  origin = 'manual'",
                    (new_id("edge"), str(source_id), str(target_id), relation,
                     float(weight), stamp, stamp))
                self.conn.commit()
        except sqlite3.Error as exc:
            logger.exception("Falha a ligar %s -> %s", source_id, target_id)
            return {"ok": False, "error": "write_failed", "detail": str(exc)}
        return {"ok": True, "source": source_id, "target": target_id, "relation": relation}

    def _specific_edge_exists(self, source_id: str, target_id: str) -> bool:
        """Whether these two nodes are already joined by a NON-generic relation.

        Direction-agnostic: "A uses B" already explains the pair, and adding
        "B related_to A" would draw the same connection a second time from the
        other end.
        """
        try:
            with self._lock:
                row = self.conn.execute(
                    "SELECT 1 FROM knowledge_edges"
                    " WHERE relation<>? AND ((source_id=? AND target_id=?)"
                    "                     OR (source_id=? AND target_id=?)) LIMIT 1",
                    (DEFAULT_RELATION, str(source_id), str(target_id),
                     str(target_id), str(source_id))).fetchone()
        except sqlite3.Error:
            return False
        return row is not None

    def dedupe_symmetric_edges(self) -> int:
        """Collapse mirrored copies of a symmetric relation onto one row.

        WHY CANONICALISING NEW WRITES IS NOT ENOUGH. :meth:`link` now stores a
        symmetric relation in one direction chosen by node id, so "Groq e
        Ollama" and "Ollama e Groq" can no longer become two edges. That rule
        only governs writes made from now on, and a database written by an
        earlier build already holds both rows -- so the install that actually
        has the problem is the only one the rule does not help. It is the same
        gap ``MemoryStack.reconcile_knowledge`` exists to close for nodes.

        NOTHING IS LOST. The two rows assert the identical fact about the
        identical pair; one of them is a second drawing of the first. The
        surviving row keeps the HIGHER of the two weights rather than their
        sum: weight is evidence counted by repetition, and a pair that was
        counted twice because it was stored twice has not been mentioned twice.

        A row that is merely pointing the wrong way, with no twin, is turned
        round rather than deleted. If either twin was drawn by the user, the
        survivor is the user's.

        Returns how many rows were removed, so a caller can log a measured
        number instead of announcing a cleanup it did not verify.
        """
        if not SYMMETRIC_RELATIONS:
            return 0
        placeholders = ",".join("?" * len(SYMMETRIC_RELATIONS))
        removed = 0
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT id, source_id, target_id, relation, weight, origin"
                    f" FROM knowledge_edges WHERE relation IN ({placeholders})",
                    tuple(sorted(SYMMETRIC_RELATIONS))).fetchall()
                groups: dict[tuple[str, str, str], list] = {}
                for row in rows:
                    left, right = str(row[1]), str(row[2])
                    key = (min(left, right), max(left, right), str(row[3]))
                    groups.setdefault(key, []).append(row)

                stamp = _now()
                for (left, right, _relation), members in groups.items():
                    # Delete the extra rows FIRST: the surviving row is about to
                    # take the canonical direction, which would collide with a
                    # twin still holding it.
                    keeper = next((m for m in members if str(m[1]) == left), members[0])
                    for member in members:
                        if member[0] != keeper[0]:
                            self.conn.execute(
                                "DELETE FROM knowledge_edges WHERE id=?", (member[0],))
                            removed += 1
                    weight = max(float(member[4] or 0.0) for member in members)
                    origin = ("manual" if any(member[5] == "manual" for member in members)
                              else keeper[5])
                    if len(members) > 1 or str(keeper[1]) != left:
                        self.conn.execute(
                            "UPDATE knowledge_edges SET source_id=?, target_id=?,"
                            " weight=?, origin=?, updated_at=? WHERE id=?",
                            (left, right, weight, origin, stamp, keeper[0]))
                self.conn.commit()
        except sqlite3.Error:
            self.conn.rollback()
            logger.exception("Falha a colapsar ligações simétricas duplicadas")
            return 0
        if removed:
            logger.info("Second Brain: %d ligação(ões) duplicada(s) colapsada(s)", removed)
        return removed

    def unlink(self, source_id: str, target_id: str, relation: str | None = None) -> dict:
        """Remove an edge. A derived one stays removed for the memories behind it.

        Any memory linked to BOTH ends is one that could derive the edge, so
        each of them is suppressed for it; suppressing one that never did is
        harmless, missing one that did would let the next sync draw it again.
        """
        params: list = [str(source_id), str(target_id)]
        clause = "e.source_id=? AND e.target_id=?"
        if relation:
            clause += " AND e.relation=?"
            params.append(str(relation))
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT e.id, e.relation, e.origin, s.slug, t.slug"
                    "  FROM knowledge_edges e"
                    "  LEFT JOIN knowledge_nodes s ON s.id = e.source_id"
                    "  LEFT JOIN knowledge_nodes t ON t.id = e.target_id"
                    f" WHERE {clause}", params).fetchall()
                shared = set(self._evidence(source_id)) & set(self._evidence(target_id))
                for edge_id, edge_relation, origin, source_slug, target_slug in rows:
                    if origin != "manual" and source_slug and target_slug:
                        self._suppress(shared, edge_target(source_slug, edge_relation,
                                                           target_slug))
                    self.conn.execute("DELETE FROM knowledge_edges WHERE id=?", (edge_id,))
                self.conn.commit()
        except sqlite3.Error as exc:
            self.conn.rollback()
            return {"ok": False, "error": "delete_failed", "detail": str(exc)}
        return {"ok": True, "removed": len(rows)}

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

    # ------------------------------------------------------- derived state

    def _evidence(self, node_id: str) -> list[str]:
        """Memories recorded as evidence for a node. The caller holds the lock."""
        return [str(row[0]) for row in self.conn.execute(
            "SELECT ref_id FROM knowledge_links WHERE node_id=? AND kind='memory'",
            (str(node_id),))]

    def _suppress(self, memory_ids: Iterable[str], target: str) -> None:
        """Record that these memories must not derive ``target`` again.

        Runs inside the caller's transaction, under the caller's lock. Only a
        memory that exists is recorded: the row is deleted with the memory, so
        it can never outlive what it refers to.
        """
        stamp = _now()
        for memory_id in sorted({str(value) for value in memory_ids if value}):
            self.conn.execute(
                "INSERT OR IGNORE INTO knowledge_suppressions (memory_id, target, created_at)"
                " SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM memories WHERE id=?)",
                (memory_id, target, stamp, memory_id))

    def suppressions(self) -> dict[str, set[str]]:
        """Every recorded suppression, grouped by memory id.

        A read failure propagates: deriving without them would rebuild exactly
        what the user deleted, so the caller must not proceed as if there were
        none.
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT memory_id, target FROM knowledge_suppressions").fetchall()
        grouped: dict[str, set[str]] = {}
        for memory_id, target in rows:
            grouped.setdefault(str(memory_id), set()).add(str(target))
        return grouped

    def sync_derived(self, desired: DerivedGraph, *, grow: bool = True) -> dict:
        """Make the derived half of the graph exactly what ``desired`` says.

        One transaction, under the store's lock from the first read to the last
        index write: no other writer can change the graph between what was read
        and what is written, no reader sees a node and its index disagree, and
        a failure rolls back to the graph as it was.

        * A derived node ``desired`` does not name is deleted, with its edges,
          links and index entry. A manual node is kept and only loses evidence.
        * A derived node's summary is its representative evidence's text, and
          every mention count is the number of memories behind the node (plus
          one for a manual node, the user's own mention). Recomputed, never
          incremented, so a second pass changes nothing.
        * Memory and conversation links equal the evidence; a conversation that
          no longer exists is not linked again.
        * Derived edges equal ``desired``, weighted by their evidence; manual
          edges are never touched.
        * A suppression an active memory no longer matches is dropped.

        ``grow=False`` creates nothing new -- no node, no edge. That is how
        forgetting still runs while long-term memory is switched off.

        Nothing is written when nothing differs, timestamps included.
        """
        report = dict.fromkeys(
            ("nodesCreated", "nodesRemoved", "nodesUpdated", "edgesCreated",
             "edgesRemoved", "edgesUpdated", "linksAdded", "linksRemoved",
             "suppressionsDropped"), 0)
        stamp = _now()
        reindex: set[str] = set()
        removed: list[str] = []
        with self._lock:
            try:
                ids = self._sync_nodes(desired, grow, stamp, report, reindex, removed)
                self._sync_edges(desired, ids, grow, stamp, report)
                self._drop_stale_suppressions(desired, report)
                self.conn.commit()
            except sqlite3.Error:
                self.conn.rollback()
                logger.exception("Falha a sincronizar o Second Brain com as memórias")
                return {"ok": False, "error": "sync_failed"}
            if self.index is not None and (removed or reindex):
                for node_id in removed:
                    self.index.remove(f"node:{node_id}", commit=False)
                for node_id in sorted(reindex):
                    node = self.get_node(node_id)
                    if node:
                        self._index(node, commit=False)
                self._commit_index()
        return {"ok": True, **report}

    def _sync_nodes(self, desired: DerivedGraph, grow: bool, stamp: str, report: dict,
                    reindex: set[str], removed: list[str]) -> dict[str, str]:
        """Bring nodes and their links in line. Returns slug -> id for edges."""
        rows = self.conn.execute(
            "SELECT id, slug, summary, origin, mention_count FROM knowledge_nodes").fetchall()
        links: dict[tuple[str, str], set[str]] = {}
        for node_id, kind, ref_id in self.conn.execute(
                "SELECT node_id, kind, ref_id FROM knowledge_links"):
            links.setdefault((str(node_id), str(kind)), set()).add(str(ref_id))
        live = self._live_conversations(
            {ref for node in desired.nodes.values() for ref in node.conversations})

        ids: dict[str, str] = {}
        for node_id, slug, summary, origin, mentions in rows:
            want = desired.nodes.get(slug)
            manual = origin == "manual"
            if want is None and not manual:
                self.conn.execute("DELETE FROM knowledge_edges WHERE source_id=? OR target_id=?",
                                  (node_id, node_id))
                self.conn.execute("DELETE FROM knowledge_links WHERE node_id=?", (node_id,))
                self.conn.execute("DELETE FROM knowledge_nodes WHERE id=?", (node_id,))
                removed.append(node_id)
                report["nodesRemoved"] += 1
                continue
            ids[slug] = node_id
            evidence = set(want.evidence) if want else set()
            conversations = (want.conversations & live) if want else set()
            self._set_links(node_id, "memory", links.get((node_id, "memory"), set()),
                            evidence, stamp, report)
            self._set_links(node_id, "conversation",
                            links.get((node_id, "conversation"), set()),
                            conversations, stamp, report)
            new_summary = summary if manual else want.summary
            new_mentions = len(evidence) + (1 if manual else 0)
            if new_summary != summary or new_mentions != int(mentions or 0):
                self.conn.execute(
                    "UPDATE knowledge_nodes SET summary=?, mention_count=?, updated_at=?"
                    " WHERE id=?", (new_summary, new_mentions, stamp, node_id))
                report["nodesUpdated"] += 1
                if new_summary != summary:
                    reindex.add(node_id)

        if grow:
            for slug, want in desired.nodes.items():
                if slug in ids:
                    continue
                node_id = new_id("node")
                self.conn.execute(
                    "INSERT INTO knowledge_nodes (id, slug, title, type, summary, body, tags,"
                    " pinned, mention_count, origin, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,'','[]',0,?,'derived',?,?)",
                    (node_id, slug, want.title, want.node_type, want.summary,
                     len(want.evidence), stamp, stamp))
                self._set_links(node_id, "memory", set(), set(want.evidence), stamp, report)
                self._set_links(node_id, "conversation", set(), want.conversations & live,
                                stamp, report)
                ids[slug] = node_id
                reindex.add(node_id)
                report["nodesCreated"] += 1
        return ids

    def _set_links(self, node_id: str, kind: str, have: set[str], want: set[str],
                   stamp: str, report: dict) -> None:
        for ref_id in sorted(have - want):
            self.conn.execute(
                "DELETE FROM knowledge_links WHERE node_id=? AND kind=? AND ref_id=?",
                (node_id, kind, ref_id))
            report["linksRemoved"] += 1
        for ref_id in sorted(want - have):
            self.conn.execute(
                "INSERT OR IGNORE INTO knowledge_links (id, node_id, kind, ref_id, created_at)"
                " VALUES (?,?,?,?,?)", (new_id("link"), node_id, kind, ref_id, stamp))
            report["linksAdded"] += 1

    def _live_conversations(self, conversation_ids: set[str]) -> set[str]:
        """Which of these conversations still exist. A deleted one is not evidence."""
        ids = sorted(str(value) for value in conversation_ids if value)
        live: set[str] = set()
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            marks = ",".join("?" * len(chunk))
            live.update(str(row[0]) for row in self.conn.execute(
                f"SELECT id FROM conversations WHERE id IN ({marks})", chunk))
        return live

    def _sync_edges(self, desired: DerivedGraph, ids: dict[str, str], grow: bool,
                    stamp: str, report: dict) -> None:
        """Make derived edges equal what the memories support. Manual ones stay."""
        rows = self.conn.execute(
            "SELECT id, source_id, target_id, relation, weight, origin FROM knowledge_edges"
        ).fetchall()
        manual = {(str(r[1]), str(r[2]), str(r[3])) for r in rows if r[5] == "manual"}
        derived = {(str(r[1]), str(r[2]), str(r[3])): (r[0], float(r[4] or 0.0))
                   for r in rows if r[5] != "manual"}

        wanted: dict[tuple[str, str, str], int] = {}
        for (left, right, relation), support in desired.edges.items():
            source, target = ids.get(left), ids.get(right)
            if not source or not target or source == target:
                continue
            # The same canonical direction ``link`` stores.
            if relation in SYMMETRIC_RELATIONS and str(target) < str(source):
                source, target = target, source
            key = (source, target, relation)
            wanted[key] = max(wanted.get(key, 0), int(support))
        # And the same rule: a generic relation never lands on top of a
        # specific one for the same pair, whoever drew the specific one.
        specific = {frozenset(key[:2]) for key in (*wanted, *manual)
                    if key[2] != DEFAULT_RELATION}
        wanted = {key: support for key, support in wanted.items()
                  if not (key[2] == DEFAULT_RELATION and frozenset(key[:2]) in specific)}

        for key, (edge_id, _weight) in derived.items():
            if key not in wanted:
                self.conn.execute("DELETE FROM knowledge_edges WHERE id=?", (edge_id,))
                report["edgesRemoved"] += 1
        for key, support in wanted.items():
            if key in manual:
                continue
            weight = derived_edge_weight(support)
            if key in derived:
                edge_id, current = derived[key]
                if current != weight:
                    self.conn.execute(
                        "UPDATE knowledge_edges SET weight=?, updated_at=? WHERE id=?",
                        (weight, stamp, edge_id))
                    report["edgesUpdated"] += 1
            elif grow:
                self.conn.execute(
                    "INSERT INTO knowledge_edges (id, source_id, target_id, relation, weight,"
                    " created_at, updated_at, origin) VALUES (?,?,?,?,?,?,?,'derived')",
                    (new_id("edge"), key[0], key[1], key[2], weight, stamp, stamp))
                report["edgesCreated"] += 1

    def _drop_stale_suppressions(self, desired: DerivedGraph, report: dict) -> None:
        """Forget a suppression once its memory no longer derives the target.

        An edit that stops naming the entity has nothing left to suppress, and
        keeping the row would keep a fragment of the old sentence (its slug).
        Archived and candidate memories keep theirs: restoring one must not
        bring back what the user deleted.
        """
        for memory_id, target in self.conn.execute(
                "SELECT memory_id, target FROM knowledge_suppressions").fetchall():
            targets = desired.targets.get(str(memory_id))
            if targets is not None and str(target) not in targets:
                self.conn.execute(
                    "DELETE FROM knowledge_suppressions WHERE memory_id=? AND target=?",
                    (memory_id, target))
                report["suppressionsDropped"] += 1
        # The foreign key already does this while it is enforced; this covers
        # a connection that has it switched off.
        report["suppressionsDropped"] += self.conn.execute(
            "DELETE FROM knowledge_suppressions"
            " WHERE memory_id NOT IN (SELECT id FROM memories)").rowcount or 0

    def repair_index(self) -> dict:
        """Make every node's retrieval entry match the node, and drop the rest.

        Both index tables are checked -- the row ``search`` returns and the FTS
        row it MATCHes against -- because a stale FTS row still selects a node
        by text the node no longer has. An entry already correct is not
        rewritten, so a repair over a consistent index writes nothing.
        """
        if self.index is None:
            return {"updated": 0, "removed": 0}
        updated = removed = 0
        with self._lock:
            try:
                nodes = [_row_to_node(row) for row in self.conn.execute(
                    f"SELECT {_NODE_COLUMNS} FROM knowledge_nodes").fetchall()]
                entries = {str(row[0]): (str(row[1] or ""), str(row[2] or ""), row[3])
                           for row in self.conn.execute(
                               "SELECT entry_id, title, body, metadata FROM retrieval_entries"
                               " WHERE kind='node'").fetchall()}
                fts: dict[str, list[tuple[str, str]]] = {}
                if self.index.fts_available:
                    for entry_id, title, body in self.conn.execute(
                            "SELECT entry_id, title, body FROM retrieval_fts"
                            " WHERE entry_id LIKE 'node:%'").fetchall():
                        fts.setdefault(str(entry_id), []).append(
                            (str(title or ""), str(body or "")))
            except sqlite3.Error:
                logger.exception("Falha a verificar o índice do Second Brain")
                return {"updated": 0, "removed": 0}
            alive: set[str] = set()
            for node in nodes:
                entry_id = f"node:{node['id']}"
                alive.add(entry_id)
                title, body, metadata = _index_fields(node)
                stored = entries.get(entry_id)
                current = (stored is not None and stored[:2] == (title, body)
                           and _json_dict(stored[2]) == metadata)
                if current and self.index.fts_available:
                    current = fts.get(entry_id) == [(title, body)]
                if not current:
                    self._index(node, commit=False)
                    updated += 1
            for entry_id in sorted((set(entries) | set(fts)) - alive):
                self.index.remove(entry_id, commit=False)
                removed += 1
            if updated or removed:
                self._commit_index()
        return {"updated": updated, "removed": removed}

    def _commit_index(self) -> None:
        """Commit a batch of index writes. The index is derived: a failure costs
        retrieval until the next repair, never data, so it is logged, not raised."""
        try:
            self.conn.commit()
        except sqlite3.Error:
            self.conn.rollback()
            logger.exception("Falha a gravar o índice do Second Brain")

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

    def _index(self, node: dict, *, commit: bool = True) -> bool:
        if self.index is None:
            return False
        title, body, metadata = _index_fields(node)
        return self.index.upsert(
            f"node:{node['id']}", kind="node", scope="", title=title, body=body,
            created_at=node.get("createdAt") or _now(), metadata=metadata, commit=commit)


def _index_fields(node: dict) -> tuple[str, str, dict]:
    """(title, body, metadata) of a node's retrieval entry, as the index stores them."""
    body = " ".join(part for part in (node.get("summary"), node.get("body")) if part)
    return (str(node["title"]).strip(), str(body or node["title"]).strip(),
            {"nodeId": node["id"], "type": node.get("type")})


def _json_dict(raw: Any) -> dict:
    try:
        value = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


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
    "DerivedGraph",
    "DerivedNode",
    "KnowledgeGraph",
    "derived_edge_weight",
    "edge_target",
    "node_target",
]
