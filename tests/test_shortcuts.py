"""Keyboard shortcuts and the "go to the end" button in the conversation.

Source checks only: the Chromium harnesses drive the real page, and these
keep the list the dialog advertises and the key handler from drifting apart.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def _shortcuts() -> list[list[str]]:
    source = (FRONTEND / "components" / "ShortcutsDialog.tsx").read_text(encoding="utf-8")
    return [re.findall(r'"([^"]+)"', keys) for keys in re.findall(r"keys: \[([^\]]*)\]", source)]


def test_every_listed_ctrl_shortcut_has_a_handler():
    handler = (FRONTEND / "pages" / "index.tsx").read_text(encoding="utf-8")
    listed = [keys for keys in _shortcuts() if keys[0] == "Ctrl"]
    assert listed, "the dialog lists no Ctrl shortcut"
    for keys in listed:
        key = keys[-1].lower()
        assert f'key === "{key}"' in handler, f"Ctrl+{keys[-1]} is listed but not handled"
        if "Shift" in keys:
            assert f'event.shiftKey && key === "{key}"' in handler


def test_question_mark_does_not_steal_typing():
    handler = (FRONTEND / "pages" / "index.tsx").read_text(encoding="utf-8")
    assert 'event.key === "?" && !typing' in handler


def test_the_conversation_only_follows_new_text_when_already_at_the_end():
    source = (FRONTEND / "components" / "Conversation.tsx").read_text(encoding="utf-8")
    assert "if (atEndRef.current) stick();" in source
    assert 'className="conversation__to-end"' in source


def test_voice_mode_follows_the_phase_and_never_a_fake_level():
    source = (FRONTEND / "components" / "VoiceMode.tsx").read_text(encoding="utf-8")
    assert "export function voiceModeState(" in source
    # The ring is driven by the turn's phase only; there is no audio level to read.
    for fake in ("getUserMedia", "AnalyserNode", "Math.random"):
        assert fake not in source
    shell = (FRONTEND / "pages" / "index.tsx").read_text(encoding="utf-8")
    # An approval asked during voice mode is shown as the dialog, above it.
    assert '!showFirstRun && !voiceModeOpen' in shell
