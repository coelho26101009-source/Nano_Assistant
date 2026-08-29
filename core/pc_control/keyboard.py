"""Typing and key presses -- powerful, and deliberately narrow.

THREE RULES, AND THEY ARE THE WHOLE DESIGN.

1. THERE IS NO KEY SEQUENCE ARGUMENT. Nothing here accepts a string that is
   parsed into keystrokes, and nothing accepts a scan code. Text is sent as
   Unicode CHARACTERS; a chord is chosen from a fixed table below by name. A
   key or combination that is not in the table cannot be expressed, so the
   tool surface cannot be widened by phrasing.

2. TYPING IS ALWAYS AIMED. Every typing and editing action names the window it
   is for, that window is focused, and the OS is asked whether the focus change
   ACTUALLY happened. If Windows refused the foreground change, nothing is
   typed -- because the alternative is typing somebody's sentence into whatever
   happened to be in front, which is how an assistant sends half a message to
   the wrong chat.

   The aim also has to survive the approval dialog. Nano's own confirmation
   window takes the foreground while the user reads it, so "type into the
   foreground window" would resolve to Nano itself. That is why there is no
   implicit foreground target here: the caller names the window, the name is
   resolved strictly, and the focus happens after approval, inside the handler.

3. NANO NEVER TYPES A SECRET IT LOOKED UP. There is no path from the secret
   store to this module -- it is not imported, and no tool the model can call
   returns a stored credential. What can be typed is text the model composed or
   the user dictated, and the confirmation card shows that text in full before
   anything is sent.
"""
from __future__ import annotations

import logging
import time

from core.pc_control import winapi
from core.pc_control.results import PCControlError, clamp_text

logger = logging.getLogger("nano.pc_control.keyboard")

#: A bound on one typing action. Long enough for a paragraph or a search box,
#: short enough that a runaway argument cannot hold the input queue for a
#: minute or fill somebody's document.
MAX_TEXT_CHARS = 2000

#: Events are injected in batches so a long string does not monopolise the
#: input queue; the pause is what lets the receiving application keep up.
_BATCH_CHARS = 120
_BATCH_PAUSE = 0.012

VK_RETURN = 0x0D
VK_TAB = 0x09
VK_CONTROL = 0x11
VK_MENU = 0x12          # Alt
VK_SHIFT = 0x10
VK_LWIN = 0x5B

#: Every key the model may press, by name. Whole-word names, never codes.
KEY_ALLOWLIST: dict[str, int] = {
    "enter": VK_RETURN,
    "escape": 0x1B,
    "tab": VK_TAB,
    "backspace": 0x08,
    "delete": 0x2E,
    "space": 0x20,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "home": 0x24,
    "end": 0x23,
    "page_up": 0x21,
    "page_down": 0x22,
}

#: Keys whose effect can destroy the user's content rather than move a cursor.
#: They are still allowed, but they resolve to a higher-risk capability so the
#: policy layer asks first. See PermissionManager.resolve_tool_capability.
DESTRUCTIVE_KEYS = frozenset({"delete", "backspace"})

#: Every chord the model may send. The tuple is (virtual key, modifiers,
#: requires_target, human label). `requires_target` is False only for the two
#: gestures that act on the DESKTOP rather than on a window -- everything else
#: edits something, and editing needs an aimed target.
HOTKEY_ALLOWLIST: dict[str, tuple[int, tuple[int, ...], bool, str]] = {
    "copy":        (ord("C"), (VK_CONTROL,), True,  "Ctrl+C"),
    "paste":       (ord("V"), (VK_CONTROL,), True,  "Ctrl+V"),
    "cut":         (ord("X"), (VK_CONTROL,), True,  "Ctrl+X"),
    "select_all":  (ord("A"), (VK_CONTROL,), True,  "Ctrl+A"),
    "undo":        (ord("Z"), (VK_CONTROL,), True,  "Ctrl+Z"),
    "redo":        (ord("Y"), (VK_CONTROL,), True,  "Ctrl+Y"),
    "save":        (ord("S"), (VK_CONTROL,), True,  "Ctrl+S"),
    "find":        (ord("F"), (VK_CONTROL,), True,  "Ctrl+F"),
    "address_bar": (ord("L"), (VK_CONTROL,), True,  "Ctrl+L"),
    "switch_window": (VK_TAB, (VK_MENU,),    False, "Alt+Tab"),
    "show_desktop":  (ord("D"), (VK_LWIN,),  False, "Win+D"),
}

#: Media transport keys. Global by nature: they are routed by Windows to
#: whichever application currently owns media playback, which is why they take
#: no target.
MEDIA_KEYS: dict[str, tuple[int, str]] = {
    "play_pause": (0xB3, "reproduzir/pausar"),
    "next":       (0xB0, "faixa seguinte"),
    "previous":   (0xB1, "faixa anterior"),
    "stop":       (0xB2, "parar"),
}


def validate_text(value) -> str:
    """A bounded, printable line of text. Control characters are refused.

    Newline and tab survive because they are ordinary parts of typed text; a
    newline becomes a real Enter press further down, since KEYEVENTF_UNICODE
    for U+000A does nothing in most applications. Everything else in the C0
    range is refused rather than stripped -- silently altering what gets typed
    would make the confirmation card a lie about what was approved.
    """
    if not isinstance(value, str):
        raise PCControlError("invalid_input", "O texto a escrever tem de ser texto.")
    if not value:
        raise PCControlError("invalid_input", "Não há texto para escrever.")
    if len(value) > MAX_TEXT_CHARS:
        raise PCControlError(
            "invalid_input",
            f"O texto é demasiado longo ({len(value)} caracteres; o máximo é "
            f"{MAX_TEXT_CHARS}).")
    forbidden = [character for character in value
                 if ord(character) < 0x20 and character not in "\n\t\r"]
    if forbidden:
        raise PCControlError("invalid_input",
                             "O texto contém caracteres de controlo que o Nano não escreve.")
    return value


def validate_key(name) -> tuple[str, int]:
    key = str(name or "").strip().lower()
    if key not in KEY_ALLOWLIST:
        raise PCControlError(
            "invalid_input",
            f"'{name}' não é uma tecla que o Nano possa carregar.",
            allowed=sorted(KEY_ALLOWLIST))
    return key, KEY_ALLOWLIST[key]


def validate_hotkey(name) -> tuple[str, tuple[int, tuple[int, ...], bool, str]]:
    key = str(name or "").strip().lower()
    if key not in HOTKEY_ALLOWLIST:
        raise PCControlError(
            "invalid_input",
            f"'{name}' não é um atalho que o Nano possa usar.",
            allowed=sorted(HOTKEY_ALLOWLIST))
    return key, HOTKEY_ALLOWLIST[key]


def validate_media_action(name) -> tuple[str, int, str]:
    key = str(name or "").strip().lower()
    if key not in MEDIA_KEYS:
        raise PCControlError(
            "invalid_input",
            f"'{name}' não é um comando de reprodução conhecido.",
            allowed=sorted(MEDIA_KEYS))
    code, label = MEDIA_KEYS[key]
    return key, code, label


def focus_and_verify(hwnd: int) -> dict:
    """Bring a window forward and check with the OS that it worked.

    Windows refuses SetForegroundWindow from a process that does not already
    own the foreground. That refusal is the whole reason this function returns
    a fact rather than raising on success: the caller must be able to STOP,
    because sending keystrokes at an unverified target is the dangerous case.
    """
    winapi.focus_window(hwnd)
    for _ in range(10):
        time.sleep(0.04)
        if winapi.foreground_window() == hwnd:
            return {"focused": True, "title": clamp_text(winapi.window_title(hwnd), 200)}
    return {
        "focused": False,
        "title": clamp_text(winapi.window_title(hwnd), 200),
        "detail": ("O Windows não deixou trazer essa janela para a frente, "
                   "por isso o Nano não escreveu nada."),
    }


def _require_windows() -> None:
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "O teclado virtual só funciona no Windows.")


def type_text(text: str) -> dict:
    """Type ``text`` into whatever currently holds focus.

    The CALLER is responsible for having focused the right window and verified
    it; this function only reports how much Windows accepted, so that a partial
    injection is visible rather than rounded up to "done".
    """
    _require_windows()
    sent = 0
    expected = 0
    for line_index, line in enumerate(text.replace("\r\n", "\n").split("\n")):
        if line_index:
            expected += 1
            sent += 1 if winapi.press_chord(VK_RETURN) else 0
        for start in range(0, len(line), _BATCH_CHARS):
            chunk = line[start:start + _BATCH_CHARS]
            if not chunk:
                continue
            expected += len(chunk)
            accepted = winapi.type_unicode(chunk)
            # SendInput returns EVENTS accepted; each character is a down and
            # an up pair (two per UTF-16 unit), so it is halved back into
            # characters to keep the reported number meaningful.
            sent += accepted // 2
            time.sleep(_BATCH_PAUSE)
    return {"characters": len(text), "sent": sent, "expected": expected,
            "complete": sent >= expected}


def press_key(name) -> dict:
    _require_windows()
    key, code = validate_key(name)
    accepted = winapi.press_chord(code)
    return {"key": key, "sent": bool(accepted)}


def press_hotkey(name) -> dict:
    _require_windows()
    key, (code, modifiers, _requires_target, label) = validate_hotkey(name)
    accepted = winapi.press_chord(code, modifiers)
    return {"hotkey": key, "label": label, "sent": bool(accepted)}


def press_media(name) -> dict:
    _require_windows()
    key, code, label = validate_media_action(name)
    accepted = winapi.press_chord(code)
    return {"action": key, "label": label, "sent": bool(accepted)}


MAX_SCROLL_CLICKS = 20


def _clicks(value) -> int:
    if isinstance(value, bool) or value is None:
        raise PCControlError("invalid_input", "O número de voltas não é um número.")
    try:
        amount = int(value)
    except (TypeError, ValueError):
        raise PCControlError("invalid_input", "O número de voltas não é um número.") from None
    if amount == 0:
        raise PCControlError("invalid_input", "O número de voltas não pode ser zero.")
    if abs(amount) > MAX_SCROLL_CLICKS:
        raise PCControlError(
            "invalid_input",
            f"O máximo são {MAX_SCROLL_CLICKS} voltas de cada vez.")
    return amount


def scroll_magnitude(value) -> int:
    """A validated, positive number of clicks. The DIRECTION is a separate enum.

    Keeping the two apart is why the tool has no signed-integer argument the
    caller could use to mean something other than "how far".
    """
    return abs(_clicks(value))


def scroll(clicks, *, horizontal: bool = False) -> dict:
    """Scroll a bounded number of wheel clicks. No coordinates, ever."""
    _require_windows()
    amount = _clicks(clicks)
    accepted = winapi.scroll_wheel(amount, horizontal)
    return {"clicks": amount, "horizontal": bool(horizontal), "sent": bool(accepted)}


__all__ = [
    "DESTRUCTIVE_KEYS",
    "HOTKEY_ALLOWLIST",
    "KEY_ALLOWLIST",
    "MAX_SCROLL_CLICKS",
    "MAX_TEXT_CHARS",
    "MEDIA_KEYS",
    "focus_and_verify",
    "press_hotkey",
    "press_key",
    "press_media",
    "scroll",
    "scroll_magnitude",
    "type_text",
    "validate_hotkey",
    "validate_key",
    "validate_media_action",
    "validate_text",
]
