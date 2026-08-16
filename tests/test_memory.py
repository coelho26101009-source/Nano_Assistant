import core.memory as memory_module
from core.memory import MemoryEngine


def test_memory_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_module, "DB_PATH", tmp_path / "helios.db")
    engine = MemoryEngine()
    try:
        engine.save_message("user", "Olá HELIOS")
        engine.save_message("assistant", "Olá! Como posso ajudar?")
        history = engine.get_recent_messages(10)
        assert [item["role"] for item in history] == ["user", "assistant"]
        assert engine.count_messages() == 2
    finally:
        engine.close()


def test_local_rag_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_module, "DB_PATH", tmp_path / "helios.db")
    engine = MemoryEngine()
    try:
        assert engine.index_document(
            "doc1",
            "O HELIOS pode funcionar com um modelo local e guardar memória no computador.",
            {"filename": "manual.txt"},
        )
        results = engine.search_documents("modelo local", 3)
        assert results
        assert "modelo local" in results[0]["text"]
        assert results[0]["metadata"]["filename"] == "manual.txt"
    finally:
        engine.close()
