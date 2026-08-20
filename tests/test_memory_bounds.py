"""Guards against the unbounded-memory bug that made Nano unusable on 16 GB.

The bug: ContextEngine.build_context() embedded the *full* dicts of the 5 most
recent tasks, and that snapshot was then persisted into the metadata of the new
task. Each task therefore contained the metadata of the previous five, which
already contained theirs. Metadata doubled per task (810 B -> 2 KB -> 4 KB ->
10 KB -> 20 KB -> 40 KB ...), the task database reached 1.5 GB from 36 tasks,
and the Command Center re-read and JSON-parsed those rows every three seconds,
driving the Python process to several gigabytes of RSS.

These tests assert the property that was violated — metadata stays bounded as
tasks accumulate — rather than any particular implementation detail.
"""
from __future__ import annotations

import json

import pytest

from core.context_engine import ContextEngine, _summarize_task
from core.task_engine import MAX_METADATA_BYTES, TaskEngine, _encode_metadata


class _StubMemory:
    def get_user_profile(self):
        return {"name": "tester"}

    def search_memory(self, query, limit=5):
        return []


@pytest.fixture
def engine(tmp_path):
    return TaskEngine(db_path=tmp_path / "tasks.db")


@pytest.fixture
def context(engine):
    return ContextEngine(_StubMemory(), engine)


def test_context_snapshot_carries_only_scalar_task_fields(engine, context):
    """The snapshot must never contain another task's metadata or result."""
    engine.create_task("primeira", description="algo", metadata={"plan": {"steps": ["a"]}})
    snapshot = context.build_context("nova tarefa").to_dict()

    assert snapshot["active_tasks"], "expected the existing task in context"
    for task in snapshot["active_tasks"]:
        assert "metadata" not in task, "context re-embeds task metadata (the recursion bug)"
        assert "result" not in task, "context re-embeds task results"
        assert set(task) <= {"id", "title", "status", "task_type", "progress", "priority"}


def test_task_metadata_plateaus_instead_of_compounding(engine, context):
    """The regression itself.

    Some growth is expected while the 5-task context window fills up. What must
    never happen is compounding: once the window is full, each further task must
    cost the same as the last. Before the fix these sizes doubled every task.
    """
    sizes: list[int] = []
    for index in range(12):
        snapshot = context.build_context(f"pedido {index}")
        task = engine.create_task(
            f"tarefa {index}",
            description="teste",
            metadata={"plan": {"steps": ["a", "b"]}, "context": snapshot.to_dict()},
        )
        stored = engine.get_task(task["id"])["metadata"]
        sizes.append(len(json.dumps(stored, ensure_ascii=False)))

    # Once the context window is saturated the size must be essentially constant.
    plateau = sizes[6:]
    assert max(plateau) - min(plateau) < 200, (
        f"metadata is still compounding after the window filled: {plateau}"
    )
    # And the whole series must stay small in absolute terms.
    assert max(sizes) < 32_000, f"a single task's metadata is too large: {max(sizes)} bytes"
    # Exponential growth over 12 tasks would put the last far beyond this.
    assert sizes[-1] < sizes[0] * 10, f"metadata grew {sizes[0]} -> {sizes[-1]} bytes"


def test_summarize_task_drops_unbounded_fields():
    summary = _summarize_task({
        "id": "abc", "title": "t", "status": "QUEUED", "task_type": "general",
        "progress": 0, "priority": 5,
        "metadata": {"context": {"huge": "x" * 100_000}},
        "result": {"steps": ["x" * 100_000]},
    })
    assert "metadata" not in summary
    assert "result" not in summary
    assert summary["id"] == "abc"


def test_long_titles_are_bounded_in_the_summary():
    summary = _summarize_task({"title": "t" * 5000, "id": "x", "status": "QUEUED"})
    assert len(summary["title"]) <= 120


def test_metadata_encoder_caps_oversized_blobs():
    """Even if a caller passes something huge, it must not reach the database."""
    encoded = _encode_metadata({"plan": {"steps": ["a"]}, "junk": "x" * (MAX_METADATA_BYTES * 2)})
    assert len(encoded.encode("utf-8")) <= MAX_METADATA_BYTES * 2

    parsed = json.loads(encoded)
    assert parsed["_metadata_truncated"] is True
    assert parsed["plan"] == {"steps": ["a"]}, "small, useful keys must survive"
    assert "omitido" in parsed["junk"], "the oversized value must be replaced, not truncated"


def test_metadata_encoder_leaves_normal_metadata_untouched():
    original = {"plan": {"steps": ["a", "b"]}, "recommended_agent": "CodingAgent"}
    assert json.loads(_encode_metadata(original)) == original


def test_command_center_task_rows_exclude_heavy_blobs():
    """The 3-second poll must not ship metadata/result for every task."""
    import core.main as main

    row = main._summarize_task_row({
        "id": "abc", "title": "t", "status": "QUEUED", "progress": 0,
        "metadata": {"plan": {"task_type": "research"}, "context": {"huge": "x" * 50_000}},
        "result": {"steps": ["x" * 50_000]},
    })
    assert "metadata" not in row
    assert "result" not in row
    assert row["has_result"] is True
    assert row["task_kind"] == "research", "the cheap summary should survive"
