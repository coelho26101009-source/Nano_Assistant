"""Personalisation: name, theme and Modo avançado live in the backend settings.

The frontend uses no browser storage, so before this the theme and the
reduce-motion switch reset on every launch. They are now saved through
``update_setting`` and read back once, on open, through ``get_preferences``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core import user_settings

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(user_settings, "_PATH", tmp_path / "user_settings.json")
    monkeypatch.setattr(user_settings, "_cache", None)
    return user_settings


def _endpoint(name: str, settings):
    """Run one bridge endpoint without importing (and starting) core.main."""
    tree = ast.parse((ROOT / "core" / "main.py").read_text(encoding="utf-8-sig"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    namespace = {"user_settings": settings}
    exec(compile(ast.Module(body=[node], type_ignores=[]), name, "exec"), namespace)
    return namespace[name]


def test_preferences_default_to_the_system_theme_and_no_name(settings):
    assert _endpoint("get_preferences", settings)() == {
        "theme": "system", "reduceMotion": False, "displayName": "", "advancedMode": False,
    }


def test_preferences_survive_a_restart(settings, monkeypatch):
    get_preferences = _endpoint("get_preferences", settings)
    assert settings.set_value("theme", "light")["ok"]
    assert settings.set_value("display_name", "Ana Silva")["ok"]
    assert settings.set_value("advanced_mode", True)["ok"]
    assert settings.set_value("reduce_motion", True)["ok"]
    monkeypatch.setattr(settings, "_cache", None)
    assert get_preferences() == {
        "theme": "light", "reduceMotion": True, "displayName": "Ana Silva", "advancedMode": True,
    }


@pytest.mark.parametrize("value", ["x" * 41, "linha\nnova", 42, None])
def test_a_bad_name_is_refused(settings, value):
    assert settings.set_value("display_name", value)["ok"] is False


@pytest.mark.parametrize("key, value", [("advanced_mode", "yes"), ("theme", "blue")])
def test_bad_preference_values_are_refused(settings, key, value):
    assert settings.set_value(key, value)["ok"] is False


def test_developer_pages_are_behind_modo_avancado():
    source = (FRONTEND / "pages" / "index.tsx").read_text(encoding="utf-8")
    assert 'new Set<ViewId>(["agents", "integrations"])' in source
    assert "visibleTabs.map(" in source


def test_technical_details_only_show_in_modo_avancado():
    source = (FRONTEND / "components" / "Conversation.tsx").read_text(encoding="utf-8")
    assert "{advanced && !isUser && !message.streaming && message.meta?.model && (" in source


def test_the_approval_card_and_the_dialog_show_the_same_facts():
    """One body, two frames: the chat card cannot drift from the dialog."""
    source = (FRONTEND / "components" / "Confirm.tsx").read_text(encoding="utf-8")
    assert source.count("<ConfirmBody meta={meta} />") == 2
