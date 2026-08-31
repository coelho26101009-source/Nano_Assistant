"""Versioned schema for the Nano memory database.

ONE DATABASE, MIGRATED — NEVER REPLACED
---------------------------------------
Nano has always stored its conversation in ``helios.db`` (the name predates the
rename and is deliberately kept: ``core.data_migration`` copies that exact file
between data directories, and renaming it would strand every existing install).
This module adds the threads / long-term-memory / Second Brain tables to the
*same* file, in place, with a versioned migration.

Nothing is dropped and nothing is rewritten destructively. The two legacy tables
``messages`` and ``preferences`` keep their shape and their rows; ``messages``
gains columns, and the pre-existing rows are back-filled into real conversation
threads rather than discarded.

IDEMPOTENCE IS THE CONTRACT
---------------------------
``apply`` is safe to run on every start and safe to run twice in a row: each
step is guarded by ``PRAGMA user_version`` *and* written so that re-running it
by hand would be a no-op anyway (``CREATE TABLE IF NOT EXISTS``, ``INSERT OR
IGNORE``, a column check before every ``ALTER TABLE``). A half-applied migration
is not possible: each version runs inside one transaction, and the version
number is bumped in that same transaction.

WHY THE BACK-FILL SPLITS ON SILENCE
-----------------------------------
Before this schema there was no thread id, so the UI derived "conversations"
from gaps in the message timestamps (45 minutes of silence started a new one).
Users have been looking at that list for a long time. The back-fill applies the
same rule, so the conversations they already recognise survive the upgrade with
their titles and their order intact, instead of collapsing into one enormous
thread called "legacy".
"""
from __future__ import annotations

import logging
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("nano.memory_schema")

#: Bump this when a new step is added to ``_STEPS``.
SCHEMA_VERSION = 2

#: The silence that separates two conversations in the back-fill. Identical to
#: SESSION_GAP_MS in frontend/lib/conversations.ts, which is the rule the rail
#: has been showing the user all along.
LEGACY_SESSION_GAP = timedelta(minutes=45)

#: A back-filled thread stops absorbing messages at this size even if the
#: silence rule never fires. Without it, a log written by a script (or a very
#: long uninterrupted day) could produce one thread with thousands of messages,
#: which is not a conversation anyone can open.
LEGACY_MAX_MESSAGES = 400


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    """A stable, sortable-enough identifier. Prefixed so a stray id is legible."""
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (table,)
    ).fetchone()
    return row is not None


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """ALTER TABLE ADD COLUMN, but only when the column is genuinely missing."""
    if column in _columns(conn, table):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def title_from_text(text: str, *, fallback: str = "Conversa") -> str:
    """The first line of a message, trimmed to something a rail row can hold."""
    line = re.sub(r"\s+", " ", str(text or "")).strip()
    if not line:
        return fallback
    return line if len(line) <= 52 else line[:51].rstrip() + "…"


# ---------------------------------------------------------------------- v2

def _create_v2_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id              TEXT PRIMARY KEY,
            title           TEXT NOT NULL,
            title_source    TEXT NOT NULL DEFAULT 'auto',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            last_message_at TEXT,
            message_count   INTEGER NOT NULL DEFAULT 0,
            archived        INTEGER NOT NULL DEFAULT 0,
            metadata        TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversations_recent "
        "ON conversations(archived, last_message_at DESC)"
    )

    # messages keeps every legacy row and every legacy column.
    _add_column(conn, "messages", "conversation_id", "TEXT")
    _add_column(conn, "messages", "message_uid", "TEXT")
    _add_column(conn, "messages", "trust", "TEXT NOT NULL DEFAULT 'USER'")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_conversation "
        "ON messages(conversation_id, id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_uid ON messages(message_uid) "
        "WHERE message_uid IS NOT NULL"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            conversation_id  TEXT PRIMARY KEY
                             REFERENCES conversations(id) ON DELETE CASCADE,
            summary          TEXT NOT NULL DEFAULT '',
            covered_through  INTEGER NOT NULL DEFAULT 0,
            covered_messages INTEGER NOT NULL DEFAULT 0,
            generator        TEXT NOT NULL DEFAULT 'extractive',
            updated_at       TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_facts (
            id                TEXT PRIMARY KEY,
            conversation_id   TEXT NOT NULL
                              REFERENCES conversations(id) ON DELETE CASCADE,
            text              TEXT NOT NULL,
            kind              TEXT NOT NULL DEFAULT 'fact',
            trust             TEXT NOT NULL DEFAULT 'USER',
            source_message_id INTEGER,
            created_at        TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversation_facts_thread "
        "ON conversation_facts(conversation_id, created_at)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_facts_unique "
        "ON conversation_facts(conversation_id, kind, text)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id                     TEXT PRIMARY KEY,
            text                   TEXT NOT NULL,
            normalized             TEXT NOT NULL,
            kind                   TEXT NOT NULL DEFAULT 'fact',
            origin                 TEXT NOT NULL DEFAULT 'explicit',
            trust                  TEXT NOT NULL DEFAULT 'USER',
            status                 TEXT NOT NULL DEFAULT 'active',
            confidence             REAL NOT NULL DEFAULT 0.9,
            importance             INTEGER NOT NULL DEFAULT 3,
            pinned                 INTEGER NOT NULL DEFAULT 0,
            legacy_key             TEXT,
            tags                   TEXT NOT NULL DEFAULT '[]',
            source_conversation_id TEXT,
            source_message_id      INTEGER,
            created_at             TEXT NOT NULL,
            updated_at             TEXT NOT NULL,
            last_used_at           TEXT,
            use_count              INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_normalized ON memories(normalized)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(status, updated_at DESC)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind, status)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_source ON memories(source_conversation_id)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_nodes (
            id            TEXT PRIMARY KEY,
            slug          TEXT NOT NULL UNIQUE,
            title         TEXT NOT NULL,
            type          TEXT NOT NULL DEFAULT 'topic',
            summary       TEXT NOT NULL DEFAULT '',
            body          TEXT NOT NULL DEFAULT '',
            tags          TEXT NOT NULL DEFAULT '[]',
            pinned        INTEGER NOT NULL DEFAULT 0,
            mention_count INTEGER NOT NULL DEFAULT 1,
            origin        TEXT NOT NULL DEFAULT 'derived',
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_nodes_type ON knowledge_nodes(type, updated_at DESC)"
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_edges (
            id         TEXT PRIMARY KEY,
            source_id  TEXT NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
            target_id  TEXT NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
            relation   TEXT NOT NULL DEFAULT 'related_to',
            weight     REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source_id, target_id, relation)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_edges_source ON knowledge_edges(source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_edges_target ON knowledge_edges(target_id)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_links (
            id         TEXT PRIMARY KEY,
            node_id    TEXT NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
            kind       TEXT NOT NULL,
            ref_id     TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(node_id, kind, ref_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_links_ref ON knowledge_links(kind, ref_id)")

    # The retrieval index. Rows here are DERIVED and disposable: every one of
    # them can be rebuilt from the tables above, which is why a failure to
    # create it degrades retrieval instead of breaking the database.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS retrieval_entries (
            entry_id   TEXT PRIMARY KEY,
            kind       TEXT NOT NULL,
            scope      TEXT NOT NULL DEFAULT '',
            title      TEXT NOT NULL DEFAULT '',
            body       TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            metadata   TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_retrieval_kind ON retrieval_entries(kind, scope)")


def _backfill_legacy_messages(conn: sqlite3.Connection) -> int:
    """Give every pre-existing message a thread, split the way the UI split them.

    Returns the number of conversations created. Messages that already carry a
    conversation_id are left alone, so running this twice creates nothing.
    """
    rows = conn.execute(
        "SELECT id, role, content, timestamp FROM messages "
        "WHERE conversation_id IS NULL ORDER BY id"
    ).fetchall()
    if not rows:
        return 0

    created = 0
    current_id: str | None = None
    current_first: str | None = None
    previous_at: datetime | None = None
    in_thread = 0
    pending_title: str | None = None

    for message_id, role, content, timestamp in rows:
        at = _parse_time(timestamp) or datetime.now(timezone.utc)
        gap = previous_at is None or (at - previous_at) > LEGACY_SESSION_GAP
        if current_id is None or gap or in_thread >= LEGACY_MAX_MESSAGES:
            current_id = new_id("conv")
            current_first = at.isoformat()
            in_thread = 0
            pending_title = None
            conn.execute(
                "INSERT INTO conversations (id, title, title_source, created_at, updated_at,"
                " last_message_at, message_count, archived, metadata)"
                " VALUES (?,?,?,?,?,?,?,0,?)",
                (current_id, "Conversa", "auto", current_first, current_first,
                 current_first, 0, '{"imported": true}'),
            )
            created += 1

        if pending_title is None and role == "user" and (content or "").strip():
            pending_title = title_from_text(content)
            conn.execute("UPDATE conversations SET title=? WHERE id=?",
                         (pending_title, current_id))

        conn.execute(
            "UPDATE messages SET conversation_id=?, message_uid=COALESCE(message_uid, ?)"
            " WHERE id=?",
            (current_id, f"legacy_{message_id}", message_id),
        )
        conn.execute(
            "UPDATE conversations SET message_count = message_count + 1,"
            " last_message_at=?, updated_at=? WHERE id=?",
            (at.isoformat(), at.isoformat(), current_id),
        )
        previous_at = at
        in_thread += 1

    # A run of tool/system rows with no user text still deserves a name.
    conn.execute(
        "UPDATE conversations SET title='Conversa importada'"
        " WHERE title='Conversa' AND json_extract(metadata, '$.imported') = 1"
    )
    return created


def _import_legacy_facts(conn: sqlite3.Connection) -> int:
    """Copy `fact:<key>` preferences into long-term memory, without deleting them.

    The preferences rows STAY. ``memory.set_fact`` / ``get_facts`` still read and
    write them, plugins still call them, and an older Nano opening this database
    would still find its facts exactly where it left them. This is a copy into
    the richer store, not a move out of the old one.
    """
    if not _table_exists(conn, "preferences"):
        return 0
    rows = conn.execute(
        "SELECT key, value FROM preferences WHERE key LIKE 'fact:%' ORDER BY key"
    ).fetchall()
    stamp = _now()
    imported = 0
    for key, raw in rows:
        name = str(key)[len("fact:"):].strip()
        if not name:
            continue
        value = str(raw or "").strip().strip('"')
        text = f"{name}: {value}" if value else name
        normalized = re.sub(r"\s+", " ", text).strip().lower()
        if not normalized:
            continue
        cursor = conn.execute(
            "INSERT OR IGNORE INTO memories"
            " (id, text, normalized, kind, origin, trust, status, confidence,"
            "  importance, pinned, legacy_key, tags, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("mem"), text, normalized, "fact", "explicit", "USER", "active",
             0.9, 3, 0, name, '["importado"]', stamp, stamp),
        )
        imported += cursor.rowcount or 0
    return imported


def _apply_v2(conn: sqlite3.Connection) -> dict:
    _create_v2_tables(conn)
    conversations = _backfill_legacy_messages(conn)
    facts = _import_legacy_facts(conn)
    return {"conversations_created": conversations, "facts_imported": facts}


#: (target version, description, step). Ordered, applied in sequence.
_STEPS: tuple[tuple[int, str, object], ...] = (
    (2, "conversation threads, long-term memory and the knowledge graph", _apply_v2),
)


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def apply(conn: sqlite3.Connection) -> dict:
    """Bring the database up to SCHEMA_VERSION. Safe to call on every start.

    Never raises for a step that fails: Nano must still open, and a database
    that could not be migrated is reported rather than fatal. The caller checks
    ``ok`` and degrades the memory features that need the new tables.
    """
    report: dict = {"ok": True, "from": current_version(conn), "to": None,
                    "applied": [], "error": None}
    version = report["from"]

    # A database written by a NEWER Nano must not be "migrated" backwards.
    if version > SCHEMA_VERSION:
        report["to"] = version
        report["ok"] = False
        report["error"] = "database_newer_than_this_build"
        logger.warning(
            "helios.db is at schema v%d but this build knows v%d; leaving it alone.",
            version, SCHEMA_VERSION)
        return report

    for target, description, step in _STEPS:
        if version >= target:
            continue
        try:
            with conn:  # one transaction per version, version bump included
                detail = step(conn) or {}
                conn.execute(f"PRAGMA user_version = {int(target)}")
            version = target
            report["applied"].append({"version": target, "description": description,
                                      **detail})
            logger.info("helios.db migrated to v%d (%s): %s", target, description, detail)
        except Exception as exc:  # noqa: BLE001 - migration must not stop startup
            report["ok"] = False
            report["error"] = f"v{target}: {exc}"
            logger.exception("Falha a migrar helios.db para a versão %d", target)
            break

    report["to"] = version
    return report


__all__ = [
    "LEGACY_SESSION_GAP",
    "SCHEMA_VERSION",
    "apply",
    "current_version",
    "new_id",
    "title_from_text",
]
