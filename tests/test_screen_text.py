"""Screen text (local OCR) and region/monitor captures: the privacy controls.

No test here touches a real screen or a real OCR engine. ``screen.grab``,
``ocr.engine_status`` and ``ocr.RECOGNIZER`` are replaced, so what is tested
is the control flow: the opt-in switch, the sensitive-app refusals, masking,
bounds, target binding and the permission rule.
"""
from __future__ import annotations

import pytest

from core.pc_control import ocr, screen, windows, winapi
from core.pc_control.results import PCControlError
from core.permission_manager import PermissionManager
from core.plugin_loader import load_all_plugins
from core.tool_execution import ToolExecutor, _pc_control_target
from plugins import pc_control

MONITORS = [
    {"number": 1, "primary": True, "bounds": [0, 0, 1920, 1080], "width": 1920, "height": 1080},
    {"number": 2, "primary": False, "bounds": [1920, 0, 3840, 1080], "width": 1920, "height": 1080},
]


# ── region geometry ───────────────────────────────────────────────────────

def test_a_region_is_clipped_to_the_monitor_it_starts_on():
    x, y, w, h, clamped = screen.clamp_region({"x": 1800, "y": 100, "width": 400, "height": 50}, MONITORS)
    assert (x, y, w, h, clamped) == (1800, 100, 120, 50, True)
    assert screen.clamp_region({"x": 2000, "y": 0, "width": 100, "height": 100}, MONITORS)[:4] == (2000, 0, 100, 100)


@pytest.mark.parametrize("region", [
    {"x": 0, "y": 0, "width": 2, "height": 100},
    {"x": -50, "y": 0, "width": 100, "height": 100},
    {"x": "a", "y": 0, "width": 100, "height": 100},
    {"x": True, "y": 0, "width": 100, "height": 100},
    {"x": float("nan"), "y": 0, "width": 100, "height": 100},
    {},
])
def test_bad_regions_are_refused(region):
    with pytest.raises(PCControlError):
        screen.clamp_region(region, MONITORS)


def test_region_and_monitor_grants_bind_to_the_exact_area():
    assert _pc_control_target("pc_screenshot_capture", {"mode": "monitor", "monitor": 2}) == "screen:monitor:2"
    a = _pc_control_target("pc_screenshot_capture", {"mode": "region", "x": 0, "y": 0, "width": 10, "height": 10})
    b = _pc_control_target("pc_screenshot_capture", {"mode": "region", "x": 0, "y": 0, "width": 11, "height": 10})
    assert a != b
    assert _pc_control_target("pc_screen_read_text", {}) == "screen-text:active_window"
    assert _pc_control_target("pc_screen_read_text", {"mode": "monitor", "monitor": 1}) == "screen-text:monitor:1"


# ── masking ───────────────────────────────────────────────────────────────

def test_secrets_cards_and_ibans_are_masked():
    text = ("Chave gsk_" + "a" * 30 + "\npassword: hunter2hunter2\n"
            "Cartão 4111 1111 1111 1111 e encomenda 1234 5678 9012 3456\n"
            "IBAN PT50 0002 0123 1234 5678 9015 4")
    masked, count = ocr.mask_sensitive(text)
    assert "gsk_" not in masked and "hunter2" not in masked
    assert "4111" not in masked and "[cartão oculto]" in masked
    assert "1234 5678 9012 3456" in masked  # not Luhn-valid: an order number, kept
    assert "PT50" not in masked and "[IBAN oculto]" in masked
    assert count >= 4


def test_sensitive_apps_are_recognised():
    for name in ("KeePassXC.exe", "1Password", "bitwarden.EXE", "LockApp.exe"):
        assert ocr.is_blocked_process(name)
    assert not ocr.is_blocked_process("WINWORD.EXE")
    windows_on_screen = [
        {"process": "notepad.exe", "rect": (0, 0, 500, 500)},
        {"process": "KeePass.exe", "rect": (1000, 0, 400, 400), "visible": True, "state": "normal"},
        {"process": "Bitwarden.exe", "rect": (0, 0, 400, 400), "state": "minimized"},
    ]
    assert ocr.blocked_window_in((0, 0, 900, 900), windows_on_screen) is None
    assert ocr.blocked_window_in((900, 0, 300, 300), windows_on_screen)["process"] == "KeePass.exe"


# ── the tool, end to end with fakes ───────────────────────────────────────

@pytest.fixture
def fake_screen(monkeypatch):
    state = {"enabled": True, "grabs": [], "texts": ["Olá mundo\nTotal: 12,50 €"]}
    monkeypatch.setattr(pc_control, "_screen_text_enabled", lambda: state["enabled"])
    monkeypatch.setattr(winapi, "IS_WINDOWS", True)
    monkeypatch.setattr(ocr, "engine_status", lambda: {"available": True, "reason": "", "message": ""})
    monkeypatch.setattr(winapi, "foreground_window", lambda: 42)
    monkeypatch.setattr(winapi, "is_window", lambda hwnd: True)
    state["foreground"] = {"window_id": 42, "title": "Fatura.pdf", "process": "msedge.exe"}
    monkeypatch.setattr(windows, "describe", lambda hwnd: dict(state["foreground"]))
    monkeypatch.setattr(pc_control, "_visible_windows_with_rects", lambda: state.get("visible", []))

    def grab(mode, window=None, monitor=None, region=None):
        state["grabs"].append((mode, window, monitor, region))
        return {"rows": [b"\x00\x00\x00" * 4] * 2, "width": 4, "height": 2, "scaled": False,
                "subject": "Fatura.pdf" if window else f"{mode}", "method": "screen",
                "rect": (0, 0, 4, 2), "extra": {}}

    monkeypatch.setattr(screen, "grab", grab)
    monkeypatch.setattr(ocr, "RECOGNIZER", lambda bgra, w, h: state["texts"].pop(0))
    return state


def test_reading_the_active_window_returns_masked_bounded_text(fake_screen):
    fake_screen["texts"] = ["Pagar com 4111 1111 1111 1111\n" + "x" * 10000]
    result = pc_control.pc_screen_read_text({})
    assert result["ok"] is True and result["status"] == "read"
    assert "4111" not in result["text"] and result["masked"] == 1
    assert result["truncated"] is True and len(result["text"]) <= ocr.MAX_TEXT_CHARS
    assert fake_screen["grabs"][0][0] == "window"


def test_nothing_is_read_while_the_switch_is_off(fake_screen):
    fake_screen["enabled"] = False
    result = pc_control.pc_screen_read_text({})
    assert result["ok"] is False and result["status"] == "disabled"
    assert "Privacidade" in result["message"]
    assert fake_screen["grabs"] == []


def test_a_password_manager_is_refused_before_any_capture(fake_screen):
    fake_screen["foreground"] = {"window_id": 42, "title": "Cofre", "process": "KeePassXC.exe"}
    result = pc_control.pc_screen_read_text({})
    assert result["status"] == "blocked"
    assert fake_screen["grabs"] == []


def test_a_password_manager_on_a_monitor_is_refused_before_ocr(fake_screen):
    fake_screen["visible"] = [{"process": "1Password.exe", "rect": (0, 0, 100, 100), "state": "normal"}]
    result = pc_control.pc_screen_read_text({"mode": "monitor", "monitor": 1})
    assert result["status"] == "blocked"
    assert fake_screen["texts"], "OCR must not have run"


def test_without_the_engine_the_tool_says_so(fake_screen, monkeypatch):
    monkeypatch.setattr(ocr, "engine_status", lambda: {
        "available": False, "reason": "missing_dependency", "message": "Falta o motor"})
    result = pc_control.pc_screen_read_text({})
    assert result["status"] == "unavailable" and fake_screen["grabs"] == []


def test_an_unknown_mode_is_refused(fake_screen):
    assert pc_control.pc_screen_read_text({"mode": "desktop"})["status"] == "invalid_input"


def test_engine_status_off_windows_is_honest(monkeypatch):
    monkeypatch.setattr(winapi, "IS_WINDOWS", False)
    status = ocr.engine_status.__wrapped__() if hasattr(ocr.engine_status, "__wrapped__") else ocr.engine_status()
    assert status["available"] is False and status["reason"] == "unsupported_platform"


# ── policy ────────────────────────────────────────────────────────────────

def test_reading_screen_text_always_asks_and_is_untrusted():
    from core.trust import is_untrusted_capability

    load_all_plugins()
    manager = PermissionManager()
    capability = manager.resolve_tool_capability("pc_screen_read_text", {})
    assert capability == "pc.screen.read_text"
    evaluation = manager.policy_engine.evaluate(capability, target="x", scope="current_workspace")
    assert evaluation.requires_confirmation is True
    assert is_untrusted_capability(capability)


def test_a_declined_confirmation_reads_nothing(fake_screen):
    load_all_plugins()
    executor = ToolExecutor(PermissionManager(confirmation_callback=lambda *a, **k: False))
    executor.register_plugin_tools()
    result = executor.execute_tool("pc_screen_read_text", {})
    assert result["success"] is False
    assert fake_screen["grabs"] == []


def test_the_setting_is_allow_listed_and_boolean():
    from core import user_settings

    assert "screen_text_enabled" in user_settings.ALLOWED_KEYS
    assert user_settings._valid_value("screen_text_enabled", True)
    assert not user_settings._valid_value("screen_text_enabled", "yes")
