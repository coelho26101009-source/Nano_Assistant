"""The old name (HELIOS) is gone, and an old install's data still comes across."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core import memory as memory_module

ROOT = Path(__file__).resolve().parent.parent


def _make_db(path: Path, text: str) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TABLE messages (content TEXT)")
        conn.execute("INSERT INTO messages VALUES (?)", (text,))


def test_an_old_helios_database_is_renamed_with_its_side_files(tmp_path):
    _make_db(tmp_path / "helios.db", "olá do passado")
    (tmp_path / "helios.db-wal").write_bytes(b"")
    target = tmp_path / "nano.db"

    assert memory_module.adopt_legacy_database(target) is True
    assert target.exists() and (tmp_path / "nano.db-wal").exists()
    assert not (tmp_path / "helios.db").exists()
    with sqlite3.connect(str(target)) as conn:
        assert conn.execute("SELECT content FROM messages").fetchone()[0] == "olá do passado"


def test_an_existing_nano_database_is_never_overwritten(tmp_path):
    _make_db(tmp_path / "helios.db", "old")
    _make_db(tmp_path / "nano.db", "new")

    assert memory_module.adopt_legacy_database(tmp_path / "nano.db") is False
    with sqlite3.connect(str(tmp_path / "nano.db")) as conn:
        assert conn.execute("SELECT content FROM messages").fetchone()[0] == "new"
    assert (tmp_path / "helios.db").exists()


def test_nothing_to_adopt_is_a_no_op(tmp_path):
    assert memory_module.adopt_legacy_database(tmp_path / "nano.db") is False


def test_the_database_is_called_nano():
    assert memory_module.DB_PATH.name == "nano.db"


def test_indexed_documents_can_be_listed():
    engine = memory_module.MemoryEngine()
    engine.index_document("manual", "Como ligar a máquina de café. " * 60,
                          {"filename": "manual.pdf"})
    docs = engine.list_documents()
    assert {"id": "manual", "filename": "manual.pdf"} in docs
    assert sum(1 for d in docs if d["id"] == "manual") == 1


def test_the_old_name_only_survives_in_migration_code():
    allowed = {"core/memory.py", "core/memory_schema.py", "core/data_migration.py"}
    offenders = []
    for folder in ("core", "plugins", "electron/lib", "frontend/components",
                   "frontend/lib", "frontend/pages"):
        for path in (ROOT / folder).rglob("*"):
            if (path.suffix not in {".py", ".js", ".ts", ".tsx"}
                    or "node_modules" in path.parts or "__pycache__" in path.parts):
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel not in allowed and "helios" in path.read_text(encoding="utf-8").lower():
                offenders.append(rel)
    assert (ROOT / "electron" / "main.js").read_text(encoding="utf-8").lower().count("helios") == 0
    assert offenders == []
