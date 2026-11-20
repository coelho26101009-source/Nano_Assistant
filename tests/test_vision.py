"""Seeing: the screen through a vision model, and PDF / image attachments.

No test reaches a real provider, screen or Ollama. ``vision.TRANSPORT`` and
``vision.API_KEY`` are replaced, so what is tested is who is asked (the
provider mode decides), what is sent, what comes back (masked, bounded,
fenced) and the privacy controls on the screen tool.
"""
from __future__ import annotations

import base64

import pytest

from core import attachments, vision
from core.pc_control import ocr, screen, windows, winapi
from core.permission_manager import PermissionManager
from core.plugin_loader import load_all_plugins
from core.tool_execution import ToolExecutor, _pc_control_target
from plugins import pc_control

PNG = base64.b64encode(screen.encode_png(2, 1, [b"\xff\x00\x00\x00\xff\x00"])).decode()


class FakeNet:
    def __init__(self, *, ollama_models=(), fail=()):
        self.calls: list[tuple[str, str, dict]] = []
        self.ollama_models = list(ollama_models)
        self.fail = set(fail)
        self.answer = "A spreadsheet with a total of 12,50 €."

    def __call__(self, method, url, *, headers=None, body=None, timeout=None):
        self.calls.append((method, url, body or {}))
        if url.endswith("/api/tags"):
            return {"models": [{"name": name} for name in self.ollama_models]}
        for marker in self.fail:
            if marker in url:
                raise vision.VisionError("HTTP 429")
        if "googleapis" in url:
            return {"candidates": [{"content": {"parts": [{"text": self.answer}]}}]}
        if "/api/chat" in url:
            return {"message": {"content": self.answer}}
        return {"choices": [{"message": {"content": self.answer}}]}


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet(ollama_models=["llama3.2-vision:11b", "qwen3:4b"])
    keys = {"groq": "gsk_test", "mistral": "", "google": "g-key"}
    monkeypatch.setattr(vision, "TRANSPORT", fake)
    monkeypatch.setattr(vision, "API_KEY", lambda provider: keys.get(provider, ""))
    monkeypatch.setattr(vision, "SETTINGS_SOURCE", lambda: {
        "mode": "AUTO", "preferred_cloud": "groq", "ollama_url": "http://127.0.0.1:11434/api/chat",
        "local_enabled": True})
    fake.keys = keys
    return fake


# ── who is asked ──────────────────────────────────────────────────────────

def test_local_mode_never_sends_a_picture_to_the_cloud(net):
    found = vision.candidates({"mode": "LOCAL", "ollama_url": "http://127.0.0.1:11434"})
    assert found == [("ollama", "llama3.2-vision:11b")]
    result = vision.describe(PNG, "image/png", settings={"mode": "LOCAL", "ollama_url": ""})
    assert result["ok"] and result["local"] is True
    assert not any("groq" in url or "googleapis" in url for _, url, _ in net.calls)


def test_auto_prefers_the_chosen_cloud_and_ends_local(net):
    found = vision.candidates({"mode": "AUTO", "preferred_cloud": "google"})
    providers = [provider for provider, _ in found]
    assert providers[0] == "google"
    assert "mistral" not in providers            # no key, never asked
    assert providers[-1] == "ollama"
    assert "qwen3:4b" not in [model for _, model in found]   # text-only model


def test_cloud_mode_skips_ollama(net):
    assert all(provider != "ollama" for provider, _ in vision.candidates({"mode": "CLOUD"}))


def test_cloud_mode_only_sends_the_picture_to_the_chosen_provider(net):
    found = vision.candidates({"mode": "CLOUD", "preferred_cloud": "google"})
    assert found and {provider for provider, _ in found} == {"google"}


def test_a_failing_provider_falls_through_to_the_next(net):
    net.fail = {"groq.com"}
    result = vision.describe(PNG, "image/png")
    assert result["ok"] and result["provider"] == "google"
    # one rate-limited Groq model is enough to skip the other Groq model
    assert sum("groq.com" in url for _, url, _ in net.calls) == 1


def test_no_model_at_all_says_how_to_get_one(net, monkeypatch):
    net.ollama_models = []
    monkeypatch.setattr(vision, "API_KEY", lambda provider: "")
    result = vision.describe(PNG, "image/png")
    assert result["ok"] is False and result["error"] == "no_vision_model"
    assert "Ollama" in result["message"]
    assert vision.status()["available"] is False


# ── what is sent and what comes back ──────────────────────────────────────

def test_the_request_carries_the_image_and_a_no_instructions_rule(net):
    vision.describe(PNG, "image/png", question="What is the total?")
    method, url, body = [call for call in net.calls if not call[1].endswith("/api/tags")][0]
    assert "groq.com" in url
    parts = body["messages"][0]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "never an instruction" in parts[0]["text"]
    assert "What is the total?" in parts[0]["text"]


def test_the_answer_is_masked_and_bounded(net):
    net.answer = "Key gsk_" + "a" * 40 + " " + "x" * 9000
    result = vision.describe(PNG, "image/png")
    assert "gsk_" not in result["text"] and result["masked"] >= 1
    assert len(result["text"]) <= vision.MAX_DESCRIPTION_CHARS


@pytest.mark.parametrize("data,mime,code", [
    (PNG, "image/bmp", "unsupported_image"),
    ("", "image/png", "bad_attachments"),
    ("!!!notbase64", "image/png", "bad_attachments"),
    ("A" * (vision.MAX_IMAGE_BYTES * 2), "image/png", "image_too_large"),
])
def test_bad_images_are_refused_before_any_request(net, data, mime, code):
    assert vision.check_image(data, mime) == code
    assert vision.describe(data, mime)["ok"] is False
    assert net.calls == []


# ── attachments: images and PDFs ──────────────────────────────────────────

def _pdf(text: str) -> bytes:
    """A one-page PDF with a real text layer, built by hand."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def test_a_pdf_is_read_locally_and_its_bytes_dropped(net):
    data = base64.b64encode(_pdf("Invoice total 99 EUR")).decode()
    files, problem = attachments.normalise([{"name": "fatura.pdf", "kind": "pdf", "data": data}])
    assert problem is None and files[0]["kind"] == "pdf"
    ready = attachments.prepare(files)
    assert "data" not in ready[0] and "mime" not in ready[0]
    assert "Invoice total 99 EUR" in ready[0]["text"]
    assert net.calls == []                    # a PDF never goes to a vision model
    composed = attachments.compose("Quanto é?", ready)
    assert "NANO_UNTRUSTED_EXTERNAL_CONTENT" in composed and "Invoice total" in composed


def test_a_broken_pdf_says_so_instead_of_failing(net):
    data = base64.b64encode(b"%PDF-1.4 garbage").decode()
    files, _ = attachments.normalise([{"name": "x.pdf", "kind": "pdf", "data": data}])
    ready = attachments.prepare(files)
    assert ready[0]["text"].startswith("(")


def test_an_image_is_described_and_the_picture_is_not_kept(net):
    files, problem = attachments.normalise(
        [{"name": "foto.png", "kind": "image", "data": PNG, "mime": "image/png"}])
    assert problem is None
    ready = attachments.prepare(files, question="O que é isto?")
    assert "data" not in ready[0]
    assert net.answer in ready[0]["text"]
    assert ready[0]["vision"]["provider"] == "groq"
    assert attachments.summary(ready)[0]["kind"] == "image"


def test_an_image_without_a_vision_model_explains_why(net, monkeypatch):
    net.ollama_models = []
    monkeypatch.setattr(vision, "API_KEY", lambda provider: "")
    files, _ = attachments.normalise([{"name": "a.jpg", "kind": "image", "data": PNG, "mime": "image/jpeg"}])
    ready = attachments.prepare(files)
    assert ready[0]["vision_error"] == "no_vision_model"
    assert "Ollama" in ready[0]["text"]


@pytest.mark.parametrize("item,code", [
    ({"name": "a", "kind": "video", "data": "AAAA"}, "bad_attachments"),
    ({"name": "a.png", "kind": "image", "data": PNG, "mime": "image/tiff"}, "unsupported_image"),
    ({"name": "a.pdf", "kind": "pdf"}, "bad_attachments"),
    ({"name": "a.pdf", "kind": "pdf", "data": "A" * (attachments.MAX_PDF_BYTES * 2)}, "pdf_too_large"),
])
def test_bad_attachments_are_refused_at_the_bridge(item, code):
    assert attachments.normalise([item]) == ([], code)


def test_text_files_still_work_as_before():
    files, problem = attachments.normalise([{"name": "notas.txt", "text": "olá"}])
    assert problem is None and files[0]["kind"] == "text" and files[0]["text"] == "olá"
    assert attachments.prepare(files) == files


# ── the screen, through a vision model ────────────────────────────────────

@pytest.fixture
def fake_screen(monkeypatch, net):
    state = {"enabled": True, "grabs": []}
    monkeypatch.setattr(pc_control, "_screen_vision_enabled", lambda: state["enabled"])
    monkeypatch.setattr(winapi, "IS_WINDOWS", True)
    monkeypatch.setattr(winapi, "foreground_window", lambda: 42)
    monkeypatch.setattr(winapi, "is_window", lambda hwnd: True)
    state["foreground"] = {"window_id": 42, "title": "Excel", "process": "EXCEL.EXE"}
    monkeypatch.setattr(windows, "describe", lambda hwnd: dict(state["foreground"]))
    monkeypatch.setattr(pc_control, "_visible_windows_with_rects", lambda: state.get("visible", []))

    def grab(mode, window=None, monitor=None, region=None):
        state["grabs"].append(mode)
        return {"rows": [b"\x00\x00\x00" * 2], "width": 2, "height": 1, "scaled": False,
                "subject": "Excel" if window else mode, "method": "screen",
                "rect": (0, 0, 2, 1), "extra": {}}

    monkeypatch.setattr(screen, "grab", grab)
    return state


def test_describing_the_active_window(fake_screen, net):
    result = pc_control.pc_screen_describe({"question": "Qual é o total?"})
    assert result["ok"] is True and result["status"] == "described"
    assert result["text"] == net.answer and result["provider"] == "groq"
    image = net.calls[-1][2]["messages"][0]["content"][1]["image_url"]["url"]
    assert base64.b64decode(image.split(",", 1)[1]).startswith(b"\x89PNG")


def test_nothing_is_seen_while_the_switch_is_off(fake_screen, net):
    fake_screen["enabled"] = False
    result = pc_control.pc_screen_describe({})
    assert result["status"] == "disabled" and "Privacidade" in result["message"]
    assert fake_screen["grabs"] == [] and net.calls == []


def test_a_password_manager_is_never_looked_at(fake_screen, net):
    fake_screen["foreground"] = {"window_id": 42, "title": "Cofre", "process": "Bitwarden.exe"}
    assert pc_control.pc_screen_describe({})["status"] == "blocked"
    fake_screen["visible"] = [{"process": "KeePass.exe", "rect": (0, 0, 9, 9), "state": "normal"}]
    assert pc_control.pc_screen_describe({"mode": "monitor", "monitor": 1})["status"] == "blocked"
    assert not any("groq" in url for _, url, _ in net.calls)


def test_without_a_vision_model_nothing_is_captured(fake_screen, net, monkeypatch):
    net.ollama_models = []
    monkeypatch.setattr(vision, "API_KEY", lambda provider: "")
    assert pc_control.pc_screen_describe({})["status"] == "unavailable"
    assert fake_screen["grabs"] == []


def test_seeing_the_screen_always_asks_and_is_untrusted():
    from core.trust import is_untrusted_capability

    load_all_plugins()
    manager = PermissionManager()
    capability = manager.resolve_tool_capability("pc_screen_describe", {})
    assert capability == "pc.screen.describe"
    evaluation = manager.policy_engine.evaluate(capability, target="x", scope="current_workspace")
    assert evaluation.requires_confirmation is True
    assert is_untrusted_capability(capability)
    assert _pc_control_target("pc_screen_describe", {}) == "screen-vision:active_window"


def test_a_declined_confirmation_sees_nothing(fake_screen, net):
    load_all_plugins()
    executor = ToolExecutor(PermissionManager(confirmation_callback=lambda *a, **k: False))
    executor.register_plugin_tools()
    result = executor.execute_tool("pc_screen_describe", {})
    assert result["success"] is False
    assert fake_screen["grabs"] == [] and net.calls == []


def test_the_setting_is_allow_listed_off_by_default():
    from core import user_settings

    assert "screen_vision_enabled" in user_settings.ALLOWED_KEYS
    assert user_settings._valid_value("screen_vision_enabled", False)
    assert not user_settings._valid_value("screen_vision_enabled", "on")
