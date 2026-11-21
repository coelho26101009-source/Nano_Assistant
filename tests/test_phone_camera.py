"""Seeing through the phone's camera (core/phone_camera.py).

What matters: the PC can only ASK; nothing happens without the phone's answer;
a no, a timeout or phone access going off all end the request with nothing
seen; a photo is described (local vision first) and its bytes are dropped; the
result is untrusted content; and the phone link carries the new events and the
bigger sealed body a photo needs.
"""
from __future__ import annotations

import base64
import threading
import time

from urllib.parse import urlsplit

import pytest

from core import attachments, mobile_link, mobile_server, phone_camera, vision
from core.permission_manager import PermissionManager
from core.plugin_loader import load_all_plugins
from core.tool_execution import ToolExecutor, _PLUGIN_TIMEOUTS
from core.trust import is_untrusted_capability

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9").decode()


class Phone:
    """Records what the broker publishes to the phone feed."""

    def __init__(self):
        self.events: list[tuple] = []
        self.asked = threading.Event()

    def publish(self, name, *args):
        self.events.append((name, *args))
        if name == "on_camera_request":
            self.asked.set()

    def request_id(self) -> str:
        assert self.asked.wait(2)
        return next(e[1] for e in self.events if e[0] == "on_camera_request")


@pytest.fixture
def phone():
    return Phone()


@pytest.fixture
def broker(phone):
    return phone_camera.CameraBroker(publish=phone.publish, ready=lambda: True,
                                     seen_by=lambda: {"provider": "ollama", "local": True})


def _went_to_groq(sent) -> bool:
    return any(urlsplit(url).hostname == "api.groq.com" for url, _ in sent)


def _ask_in_background(broker, question="o que é isto?", timeout=5):
    out: dict = {}
    thread = threading.Thread(target=lambda: out.update(broker.request(question, timeout=timeout)))
    thread.start()
    return thread, out


# ── the broker ────────────────────────────────────────────────────────────

def test_without_a_phone_nothing_is_asked(phone):
    broker = phone_camera.CameraBroker(publish=phone.publish, ready=lambda: False)
    assert broker.request("x", timeout=1) == {"ok": False, "error": "no_phone"}
    assert phone.events == []


def test_the_phone_is_asked_with_the_question_and_where_it_is_seen(broker, phone):
    thread, out = _ask_in_background(broker, "  o que   é isto? ")
    request_id = phone.request_id()
    name, rid, info = phone.events[0]
    assert name == "on_camera_request" and rid == request_id
    assert info["question"] == "o que é isto?"
    assert info["seenBy"] == {"local": True, "provider": "ollama"}
    assert broker.respond(request_id, declined=False, data=JPEG, mime="image/jpeg") == {"ok": True}
    thread.join(2)
    assert out == {"ok": True, "data": JPEG, "mime": "image/jpeg"}
    assert ("on_camera_closed", request_id, "answered") in phone.events
    assert broker.pending_id is None


def test_a_no_on_the_phone_sees_nothing(broker, phone):
    thread, out = _ask_in_background(broker)
    request_id = phone.request_id()
    assert broker.respond(request_id, declined=True)["ok"]
    thread.join(2)
    assert out == {"ok": False, "error": "declined"}
    assert ("on_camera_closed", request_id, "declined") in phone.events


def test_no_answer_in_time_is_a_no_and_the_card_goes(broker, phone):
    out = broker.request("x", timeout=1)
    assert out == {"ok": False, "error": "timeout"}
    request_id = phone.events[0][1]
    assert ("on_camera_closed", request_id, "expired") in phone.events
    # A late photo is refused: nobody is waiting for it any more.
    assert broker.respond(request_id, declined=False, data=JPEG, mime="image/jpeg")["error"] == "no_request"


def test_only_the_open_request_can_be_answered_and_only_once(broker, phone):
    thread, out = _ask_in_background(broker)
    request_id = phone.request_id()
    assert broker.respond("someone-elses", declined=False, data=JPEG, mime="image/jpeg")["error"] == "no_request"
    assert broker.respond(request_id, declined=True)["ok"]
    assert broker.respond(request_id, declined=False, data=JPEG, mime="image/jpeg")["error"] in {
        "already_answered", "no_request"}
    thread.join(2)
    assert out["error"] == "declined"


def test_a_bad_photo_is_refused_and_the_request_stays_open(broker, phone):
    thread, out = _ask_in_background(broker)
    request_id = phone.request_id()
    assert broker.respond(request_id, declined=False, data=JPEG, mime="text/html")["error"] == "unsupported_image"
    assert broker.respond(request_id, declined=False, data="", mime="image/jpeg")["ok"] is False
    assert broker.pending_id == request_id
    assert broker.respond(request_id, declined=False, data=JPEG, mime="image/jpeg")["ok"]
    thread.join(2)
    assert out["ok"] is True


def test_turning_phone_access_off_cancels_the_request(broker, phone):
    thread, out = _ask_in_background(broker)
    request_id = phone.request_id()
    broker.cancel()
    thread.join(2)
    assert out == {"ok": False, "error": "cancelled"}
    assert ("on_camera_closed", request_id, "cancelled") in phone.events


def test_a_newer_request_replaces_the_older_card(broker, phone):
    first, first_out = _ask_in_background(broker, "primeiro")
    first_id = phone.request_id()
    phone.asked.clear()
    second, second_out = _ask_in_background(broker, "segundo")
    deadline = time.monotonic() + 2
    while broker.pending_id in (None, first_id) and time.monotonic() < deadline:
        time.sleep(0.01)
    first.join(2)
    assert first_out == {"ok": False, "error": "replaced"}
    assert ("on_camera_closed", first_id, "replaced") in phone.events
    broker.respond(broker.pending_id, declined=True)
    second.join(2)
    assert second_out["error"] == "declined"


# ── the tool ──────────────────────────────────────────────────────────────

@pytest.fixture
def local_vision(monkeypatch):
    sent = []

    def transport(method, url, *, headers=None, body=None, timeout=None):
        sent.append((url, body or {}))
        if url.endswith("/api/tags"):
            return {"models": [{"name": "moondream:latest"}]}
        if "/api/chat" in url:
            return {"message": {"content": "Uma caneca azul com café."}}
        return {"choices": [{"message": {"content": "cloud saw it"}}]}

    monkeypatch.setattr(vision, "TRANSPORT", transport)
    monkeypatch.setattr(vision, "API_KEY", lambda provider: "gsk_test" if provider == "groq" else "")
    monkeypatch.setattr(vision, "SETTINGS_SOURCE", lambda: {
        "mode": "AUTO", "preferred_cloud": "groq", "ollama_url": "http://127.0.0.1:11434",
        "local_enabled": True})
    vision._TAGS_CACHE.clear()
    return sent


def test_auto_mode_looks_at_a_camera_photo_on_the_pc_first(local_vision):
    assert vision.candidates()[0][0] == "groq"                      # unchanged for the screen
    assert vision.candidates(prefer_local=True)[0] == ("ollama", "moondream:latest")
    assert all(p != "ollama" for p, _ in vision.candidates({"mode": "CLOUD"}, prefer_local=True))


def test_look_describes_the_photo_and_keeps_nothing(broker, phone, local_vision):
    out: dict = {}
    thread = threading.Thread(target=lambda: out.update(phone_camera.look("o que é isto?", broker)))
    thread.start()
    request_id = phone.request_id()
    broker.respond(request_id, declined=False, data=JPEG, mime="image/jpeg")
    thread.join(5)
    assert out["ok"] and out["status"] == "seen" and out["local"] is True
    assert out["text"] == "Uma caneca azul com café."
    assert "data" not in out and JPEG not in str(out)
    assert not _went_to_groq(local_vision)


def test_look_says_why_when_the_person_says_no(broker, phone, local_vision):
    out: dict = {}
    thread = threading.Thread(target=lambda: out.update(phone_camera.look("x", broker)))
    thread.start()
    broker.respond(phone.request_id(), declined=True)
    thread.join(5)
    assert out["ok"] is False and out["status"] == "declined"
    assert not any("/api/chat" in url for url, _ in local_vision)


def test_without_a_vision_model_the_phone_is_not_even_asked(broker, phone, monkeypatch):
    monkeypatch.setattr(vision, "candidates", lambda *a, **k: [])
    out = phone_camera.look("x", broker)
    assert out["ok"] is False and out["status"] == "unavailable"
    assert phone.events == []


def test_the_tool_is_registered_untrusted_and_waits_long_enough():
    load_all_plugins()
    manager = PermissionManager()
    capability = manager.resolve_tool_capability("phone_camera_look", {})
    assert capability == "phone.camera.look"
    assert is_untrusted_capability(capability)
    # The yes is given on the phone, every time; the PC does not ask again.
    evaluation = manager.policy_engine.evaluate(capability, target="x", scope="current_workspace")
    assert evaluation.requires_confirmation is False
    executor = ToolExecutor(PermissionManager(confirmation_callback=lambda *a, **k: False))
    executor.register_plugin_tools()
    tool = executor.registry["phone_camera_look"]
    assert tool["timeout"] == _PLUGIN_TIMEOUTS["phone_camera_look"] > phone_camera.REQUEST_TTL_SECONDS + 60


def test_asking_for_the_camera_offers_the_tool():
    from core import model_selection as ms

    tools = [{"type": "function", "function": {"name": "phone_camera_look", "parameters": {"type": "object"}}},
             {"type": "function", "function": {"name": "pc_app_launch", "parameters": {"type": "object"}}}]
    for text in ("O que é isto? Olha pela câmara", "olha para isto pelo telemóvel", "look at this through my phone"):
        assert ms.classify(text) == ms.TaskClass.ACTION, text
        assert [t["function"]["name"] for t in ms.select_tools(text, tools)] == ["phone_camera_look"], text
    # A plain question typed on the PC stays a question.
    assert ms.select_tools("o que é um buraco negro?", tools) == []
    # A photo the person already sent does not make NANO ask for another one.
    turn = attachments.compose("O que é isto?", [{"name": "foto.jpg", "text": "Uma caneca."}])
    assert "phone_camera_look" not in [t["function"]["name"] for t in ms.select_tools(turn, tools)]


# ── the phone's own photo, in a message ───────────────────────────────────

def test_a_camera_photo_in_a_message_prefers_local_vision(local_vision):
    files, problem = attachments.normalise([{"name": "foto.jpg", "kind": "image", "camera": True,
                                             "mime": "image/jpeg", "data": JPEG}])
    assert problem is None and files[0]["camera"] is True
    out = attachments.prepare(files, question="o que é isto?")
    assert "data" not in out[0] and "camera" not in out[0]
    assert "Uma caneca azul" in out[0]["text"]
    assert not _went_to_groq(local_vision)


def test_an_ordinary_picture_still_follows_the_mode(local_vision):
    files, _ = attachments.normalise([{"name": "a.jpg", "kind": "image", "camera": "yes",
                                       "mime": "image/jpeg", "data": JPEG}])
    assert "camera" not in files[0]
    attachments.prepare(files)
    assert _went_to_groq(local_vision)


# ── the link ──────────────────────────────────────────────────────────────

def test_the_phone_feed_carries_camera_events():
    assert {"on_camera_request", "on_camera_closed"} <= mobile_link.FORWARDED_EVENTS


def test_a_sealed_photo_fits_in_one_request():
    """The phone keeps a photo under 120 KB of JPEG (mobile/src/lib/camera.ts):
    sealed, it fits one request on the Wi-Fi and through the relay."""
    import json

    from core import mobile_relay
    from core.mobile_link import seal
    photo = base64.b64encode(b"\xff" * (120 * 1024)).decode()
    envelope = seal(b"k" * 32, {"id": "x" * 32, "ts": 1_700_000_000_000, "m": "chat.send",
                                "a": {"text": "O que é isto?" * 20, "msgId": "y" * 32,
                                      "photo": {"data": photo, "mime": "image/jpeg"}}}, "aad")
    body = json.dumps({"d": "dev_" + "0" * 16, **envelope}, separators=(",", ":"))
    assert len(body.encode()) < mobile_server.MAX_BODY_BYTES
    assert len(body) < mobile_relay.MAX_BODY_BYTES
    assert len(body) + 200 < mobile_relay.MAX_FRAME
