"""Interface language: every visible sentence has an English translation.

The Portuguese sentence is the key on both sides (``t("…")`` in the frontend,
``tr("…")`` in the backend), so a sentence added without an English entry
would quietly show in Portuguese to an English user. These tests fail first.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from core import i18n

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
STRING = r'"((?:[^"\\\n]|\\.)*)"'


def _frontend_sources() -> list[Path]:
    files = [*FRONTEND.glob("components/*.tsx"), *FRONTEND.glob("pages/*.tsx"),
             *FRONTEND.glob("lib/*.ts"), *FRONTEND.glob("lib/*.tsx")]
    return [f for f in files if f.name not in {"i18n.ts", "tj.tsx"}]


def _frontend_keys() -> dict[str, str]:
    """Every literal passed to t() or tj(), mapped to the file it is in."""
    keys: dict[str, str] = {}
    direct = re.compile(r"\btj?\(\s*" + STRING)
    ternary = re.compile(r"\btj?\(\s*[^\"'()]*?\?\s*" + STRING + r"\s*:\s*" + STRING)
    for path in _frontend_sources():
        text = path.read_text(encoding="utf-8")
        for match in direct.finditer(text):
            keys.setdefault(json.loads(f'"{match.group(1)}"'), path.name)
        for match in ternary.finditer(text):
            for group in (1, 2):
                keys.setdefault(json.loads(f'"{match.group(group)}"'), path.name)
    return keys


def _frontend_english() -> dict[str, str]:
    text = (FRONTEND / "lib" / "locales" / "en.ts").read_text(encoding="utf-8")
    entry = re.compile(r"^\s*" + STRING + r":\s*" + STRING + r",\s*$", re.M)
    return {json.loads(f'"{k}"'): json.loads(f'"{v}"') for k, v in entry.findall(text)}


def _placeholders(text: str) -> list[str]:
    return sorted(re.findall(r"\{[^{}]*\}", text))


# ================================================================ frontend


def test_the_frontend_has_a_translation_table():
    assert len(_frontend_english()) > 800


def test_every_frontend_sentence_has_an_english_entry():
    english = _frontend_english()
    missing = {key: where for key, where in _frontend_keys().items() if key not in english}
    assert not missing, f"t() literals with no entry in lib/locales/en.ts: {missing}"


def test_the_english_table_holds_no_dead_entries():
    used = set(_frontend_keys())
    dead = [key for key in _frontend_english() if key not in used]
    assert not dead, f"entries in lib/locales/en.ts that no t() uses any more: {dead}"


def test_frontend_translations_keep_their_placeholders():
    wrong = {k: v for k, v in _frontend_english().items() if _placeholders(k) != _placeholders(v)}
    assert not wrong, wrong


def test_the_frontend_uses_no_browser_storage_for_the_language():
    source = (FRONTEND / "lib" / "i18n.ts").read_text(encoding="utf-8")
    assert "localStorage" not in source and "sessionStorage" not in source


# ================================================================= backend


def _backend_keys() -> set[str]:
    keys: set[str] = set()

    def add(node: ast.AST) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            keys.add(node.value)
        elif isinstance(node, ast.IfExp):
            add(node.body)
            add(node.orelse)

    for path in (ROOT / "core").rglob("*.py"):
        if "locales" in path.parts or path.name == "i18n.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "tr" and node.args):
                add(node.args[0])

    # Tables translated where they are served rather than where they are written.
    from core import capability_catalogue, confirmation

    keys.update(confirmation.ACTION_LABELS.values())
    keys.update(confirmation.SCOPE_LABELS.values())
    keys.add("Esta execução")
    for category in (*capability_catalogue.CATEGORIES, capability_catalogue._OTHER):
        keys.update((category.label, category.hint))
    return keys


def test_every_backend_sentence_has_an_english_entry():
    from core.locales.en import EN

    missing = sorted(key for key in _backend_keys() if key not in EN)
    assert not missing, f"tr() sentences with no entry in core/locales/en.py: {missing}"


def test_backend_translations_keep_their_placeholders():
    from core.locales.en import EN

    wrong = {k: v for k, v in EN.items() if _placeholders(k) != _placeholders(v)}
    assert not wrong, wrong


@pytest.fixture
def english():
    i18n.set_override("en")
    try:
        yield
    finally:
        i18n.set_override(None)


def test_tr_translates_and_fills_placeholders_in_english(english):
    assert i18n.tr("Modo Cloud: apenas o {name} é usado.", name="Groq") == "Cloud mode: only Groq is used."


def test_tr_keeps_the_portuguese_in_portuguese():
    i18n.set_override("pt-PT")
    try:
        assert i18n.tr("Modo Cloud: apenas o {name} é usado.", name="Groq") == "Modo Cloud: apenas o Groq é usado."
    finally:
        i18n.set_override(None)


def test_a_sentence_with_no_entry_falls_back_to_the_portuguese(english):
    assert i18n.tr("uma frase que não existe na tabela") == "uma frase que não existe na tabela"


def test_approval_cards_follow_the_language(english):
    from core import confirmation

    card = confirmation.describe("pc.window.close", {"_pc_target": "window:Bloco de Notas"})
    assert card["action"] == "CLOSE WINDOW"
    assert card["scope"] == "This window only"


# =========================================================== which language


@pytest.mark.parametrize("tag, expected", [
    ("pt_PT.UTF-8", "pt-PT"), ("pt-BR", "pt-PT"), ("en_US.UTF-8", "en"),
    ("de_DE.UTF-8", None), ("C", None), ("", None),
])
def test_os_language_tags_map_onto_a_supported_language(tag, expected):
    assert i18n._normalise(tag) == expected


def test_an_unsupported_system_language_falls_back_to_english(monkeypatch):
    for name in ("NANO_LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.setenv(name, "de_DE.UTF-8")
    monkeypatch.setattr(i18n, "_windows_ui_language", lambda: None)
    monkeypatch.setattr(i18n._locale, "getlocale", lambda: ("de_DE", "UTF-8"))
    assert i18n.system_language() == "en"


def test_english_is_the_default_language():
    assert i18n.DEFAULT == "en"


def test_the_reply_language_follows_the_user_not_the_interface(english):
    line = i18n.reply_language_instruction()
    assert "same language the user writes in" in line


def test_settings_carry_the_language_and_it_can_be_changed():
    source = (ROOT / "core" / "main.py").read_text(encoding="utf-8")
    body = source.split("def get_settings(")[1].split("\n@eel.expose")[0]
    assert '"language": i18n.describe()' in body
    assert "def set_language(" in source and "def get_language(" in source


def test_the_erase_phrase_is_accepted_in_every_language():
    """The dialog asks for the translated phrase; the backend must accept it."""
    english = _frontend_english()["APAGAR TUDO"]
    source = (ROOT / "core" / "main.py").read_text(encoding="utf-8")
    line = next(l for l in source.splitlines() if l.startswith("ERASE_ALL_CONFIRMATIONS"))
    assert f'"{english}"' in line
