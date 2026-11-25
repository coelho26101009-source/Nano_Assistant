"""NANO on the phone: the PC side of the phone app, split out of core/main.py.

The phone app is a remote for this NANO (core/mobile_link.py explains the
pairing and the encryption, core/mobile_server.py the network side). The
methods below are the WHOLE surface a paired phone has: each is a thin,
typed wrapper over the same function the PC window calls, so a message sent
from the phone is answered by the same Brain, with the same memory, and every
tool it triggers goes through the same policy, guardrails and approvals.
Nothing here changes settings, keys, permissions or the emergency stop.

WHY A SEPARATE MODULE. core/main.py had grown to 5,500 lines (review of
2026-11-25). This area moved out as it was. Everything it shares with the
rest of the app (Brain, memory, conversations, the UI notifier) is read from
``core.main`` AT CALL TIME through ``_host``, so a test that replaces
``core.main.memory_stack`` or ``core.main._notify_ui`` still reaches this code,
and core/main.py re-exports every public name here, so ``core.main.MOBILE``
and the eel functions keep working unchanged. core/main.py registers itself as
``core.main`` before importing this module, so ``_host`` is the running app
even when it was started as ``python core/main.py``.
"""
from __future__ import annotations

import os
import re
import sys
import threading

import eel

if "core.main" not in sys.modules:
    # It is a part of core.main, which imports it halfway through its own
    # start-up; imported alone first, the two would each wait for the other.
    raise ImportError("core.main_phone is part of core.main: import core.main instead")

from core import i18n, mobile_link, mobile_relay, mobile_server, phone_camera, user_settings
from core import main as _host
from core import version as nano_version
from core.i18n import tr


def _mobile_pair_requested(info: dict) -> None:
    _host._notify_ui("on_mobile_pair_request", info)


MOBILE = mobile_link.MobileLink(on_pair_request=_mobile_pair_requested)
_MOBILE_SERVER: mobile_server.MobileServer | None = None
_MOBILE_LOCK = threading.Lock()
_SECRETISH = re.compile(r"secret|token|password|key|credential", re.I)


def _mobile_relay_wanted() -> bool:
    """Should the PC sit in its relay room right now? (core/mobile_relay.py)

    Only while phone access is on and listening, "Away from home" is on, and
    there is something to serve: a paired phone, or a QR code on the screen.
    """
    if not MOBILE.active:
        return False
    if user_settings.get("mobile_relay_enabled", True) is False:
        return False
    return MOBILE.devices.has_devices() or MOBILE.pairing_open()


_MOBILE_RELAY = mobile_relay.RelayClient(MOBILE, _mobile_relay_wanted)


def _mobile_port() -> int:
    try:
        port = int(os.getenv("NANO_MOBILE_PORT") or 0)
    except ValueError:
        port = 0
    return port if 1024 <= port <= 65535 else mobile_link.DEFAULT_PORT


def _mobile_confirm_listener(phase: str, request_id: str, message, meta) -> bool:
    """Show the PC's approval requests on the phone too, and take them down."""
    if phase == "resolved":
        # Both screens take the card down, whichever one answered it.
        _host._notify_ui("on_confirm_resolved", request_id)
        return True
    server = _MOBILE_SERVER
    if server is None or not server.running or not MOBILE.devices.has_devices():
        return False
    meta = dict(meta or {})
    args = meta.get("args") if isinstance(meta.get("args"), dict) else {}
    meta["args"] = {str(k): str(v)[:200] for k, v in args.items()
                    if not str(k).startswith("_") and not _SECRETISH.search(str(k))}
    for key in ("_task_id", "_pc_target"):
        meta.pop(key, None)
    MOBILE.publish("on_confirm_request", request_id, str(message or "")[:600], meta)
    return True


_host.guardrails.add_confirm_listener(_mobile_confirm_listener)




def _text(value, limit: int) -> str:
    return str(value if value is not None else "")[:limit]


def _m_hello() -> dict:
    try:
        name = (user_settings.get("display_name") or "").strip()
    except Exception:
        name = ""
    return {"pcName": mobile_link.pc_name(), "version": nano_version.display(),
            "language": i18n.current(), "displayName": name,
            "protocol": mobile_link.PROTOCOL, "methods": MOBILE.methods(),
            # What this PC understands beyond the methods: "camera" = chat.send
            # takes a photo, and camera requests arrive on the feed.
            "features": ["camera"]}


def _m_status() -> dict:
    try:
        route = (_host.describe_providers(stale_ok=True) or {}).get("route") or {}
    except Exception:
        route = {}
    return {"route": {"provider": route.get("provider"), "model": route.get("model"),
                      "usable": bool(route.get("usable")), "fallback": bool(route.get("fallback"))},
            "emergencyStop": bool(_host.get_emergency_stop_state().get("enabled"))}


def _m_chat_send(text: str = "", msgId: str = "", photo: dict | None = None) -> dict:  # noqa: N803 - wire name
    """A message from the phone, optionally with ONE photo from its camera.

    The photo is the person's own request (they pressed the camera button and
    the shutter): it goes through the same path as a picture attached on the
    PC -- described by a vision model, local first, and only the description is
    kept (core/attachments.py, core/phone_camera.py).
    """
    msg_id = _text(msgId, 64) or None
    if msg_id and not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", msg_id):
        msg_id = None
    attachments = None
    if photo is not None:
        if not isinstance(photo, dict) or not isinstance(photo.get("data"), str):
            return {"ok": False, "accepted": False, "error": "bad_attachments"}
        # A plain name on purpose: "foto da câmara" in the turn's text would
        # offer the model the camera tool, which would ask for ANOTHER photo.
        attachments = [{"name": tr("foto") + ".jpg", "kind": "image", "camera": True,
                        "mime": _text(photo.get("mime"), 32).lower(), "data": photo["data"]}]
    return _host.send_message(_text(text, 8000), msg_id, attachments)


def _m_camera_respond(requestId: str = "", declined: bool = False,  # noqa: N803 - wire names
                      data: str = "", mime: str = "") -> dict:
    """The phone's answer to NANO asking to see: one photo, or no."""
    return phone_camera.BROKER.respond(_text(requestId, 64), declined=declined is True,
                                       data=data if isinstance(data, str) else "",
                                       mime=_text(mime, 32))


def _m_chat_retry(text: str = "", msgId: str = "") -> dict:  # noqa: N803
    msg_id = _text(msgId, 64) or None
    if msg_id and not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", msg_id):
        msg_id = None
    return _host.retry_last_turn(_text(text, 8000), msg_id)


def _m_chat_history() -> dict:
    messages = _host.get_conversation_history() or []
    rows = [{"id": m.get("id"), "role": m.get("role"), "content": _text(m.get("content"), 20000),
             "timestamp": m.get("timestamp")}
            for m in messages if m.get("role") in ("user", "assistant")]
    return {"activeId": _host.memory_stack.active_conversation_id if _host.memory_stack.ready else None,
            "messages": rows[-100:]}


def _m_conversations_list(query: str = "") -> dict:
    result = _host.list_conversations(_text(query, 200), 60)
    rows = [{"id": t.get("id"), "title": t.get("title"), "updatedAt": t.get("updatedAt") or t.get("updated_at"),
             "pinned": bool(t.get("pinned")), "project": t.get("project") or ""}
            for t in result.get("conversations", [])]
    return {"ok": result.get("ok", False), "conversations": rows, "activeId": result.get("activeId")}


def _m_conversations_open(id: str = "") -> dict:  # noqa: A002 - wire name
    result = _host._from_phone(_host.open_conversation, _text(id, 80))
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error")}
    return {"ok": True, **_m_chat_history()}


def _m_conversations_create() -> dict:
    result = _host._from_phone(_host.create_conversation)
    return {"ok": bool(result.get("ok")), "activeId": (result.get("conversation") or {}).get("id")}


def _m_conversations_delete(id: str = "") -> dict:  # noqa: A002
    result = _host._from_phone(_host.delete_conversation, _text(id, 80))
    return {"ok": bool(result.get("ok")), "activeId": _host.memory_stack.active_conversation_id
            if _host.memory_stack.ready else None}


def _m_confirm(requestId: str = "", confirmed: bool = False) -> dict:  # noqa: N803
    return _host.confirm_action(_text(requestId, 64), confirmed is True)


def _m_agenda(days: int = 30) -> dict:
    try:
        days = max(1, min(90, int(days)))
    except (TypeError, ValueError):
        days = 30
    return _host.get_agenda(days)


def _m_agenda_add_reminder(text: str = "", when: str = "", repeat: str = "") -> dict:
    return _host.agenda_create_reminder(_text(text, 300), _text(when, 64), _text(repeat, 16) or None)


def _m_agenda_delete_reminder(id: int = 0) -> dict:  # noqa: A002
    return _host.agenda_delete_reminder(id)


def _m_agenda_add_event(title: str = "", start: str = "", end: str = "", location: str = "") -> dict:
    return _host.agenda_create_event(_text(title, 200), _text(start, 64), _text(end, 64) or None,
                               _text(location, 200))


def _m_agenda_delete_event(id: int = 0) -> dict:  # noqa: A002
    return _host.agenda_delete_event(id)


def _m_memory_list(query: str = "") -> dict:
    result = _host.list_memories(_text(query, 200), "", "active", 150)
    rows = [{"id": m.get("id"), "text": m.get("text"), "kind": m.get("kind"),
             "pinned": bool(m.get("pinned")), "updatedAt": m.get("updated_at") or m.get("updatedAt")}
            for m in result.get("memories", [])]
    return {"ok": result.get("ok", False), "memories": rows}


def _m_memory_add(text: str = "") -> dict:
    return _host.create_memory(_text(text, 1000), "fact", 3, [])


def _m_memory_delete(id: str = "") -> dict:  # noqa: A002
    return _host.delete_memory(_text(id, 80))


def _m_relay_info() -> dict | None:
    """Where this PC waits away from home, for a phone paired before it had a
    relay. Asked over the sealed link, so only a paired phone ever learns it."""
    if user_settings.get("mobile_relay_enabled", True) is False:
        return None
    room = _MOBILE_RELAY.room()
    return {"url": mobile_relay.relay_url(), "room": room} if room else None


for _name, _fn in {
    "hello": _m_hello,
    "relay.info": _m_relay_info,
    "status": _m_status,
    "chat.send": _m_chat_send,
    "chat.retry": _m_chat_retry,
    "chat.history": _m_chat_history,
    "conversations.list": _m_conversations_list,
    "conversations.open": _m_conversations_open,
    "conversations.create": _m_conversations_create,
    "conversations.delete": _m_conversations_delete,
    "confirm.respond": _m_confirm,
    "camera.respond": _m_camera_respond,
    "agenda.get": _m_agenda,
    "agenda.addReminder": _m_agenda_add_reminder,
    "agenda.deleteReminder": _m_agenda_delete_reminder,
    "agenda.addEvent": _m_agenda_add_event,
    "agenda.deleteEvent": _m_agenda_delete_event,
    "memory.list": _m_memory_list,
    "memory.add": _m_memory_add,
    "memory.delete": _m_memory_delete,
}.items():
    MOBILE.register(_name, _fn)


def _camera_ready() -> bool:
    server = _MOBILE_SERVER
    return bool(MOBILE.active and server is not None and server.running and MOBILE.devices.has_devices())


phone_camera.BROKER.configure(publish=MOBILE.publish, ready=_camera_ready,
                              seen_by=phone_camera._seen_by)


def _apply_mobile_access(enabled: bool) -> dict:
    """Start or stop the phone server. Returns the new status."""
    global _MOBILE_SERVER
    with _MOBILE_LOCK:
        if enabled:
            if _MOBILE_SERVER is None:
                _MOBILE_SERVER = mobile_server.MobileServer(MOBILE, _mobile_port())
            MOBILE.active = bool(_MOBILE_SERVER.start())
            if MOBILE.active:
                _MOBILE_RELAY.start()
        else:
            MOBILE.active = False
            MOBILE.cancel_pairing()
            phone_camera.BROKER.cancel()
            _MOBILE_RELAY.poke()          # it sees access is off and leaves
            if _MOBILE_SERVER is not None:
                _MOBILE_SERVER.stop()
            # Nothing said while access is off is kept for the phone, and what
            # was buffered before goes too.
            MOBILE.feed.clear()
    return get_mobile_status()


def _qr_svg(text: str) -> str:
    try:
        import segno
    except ImportError:
        return ""
    import io
    buffer = io.BytesIO()
    segno.make(text, error="m").save(buffer, kind="svg", scale=6, border=2,
                                     dark="#000000", light="#ffffff", xmldecl=False,
                                     svgns=True, nl=False)
    return buffer.getvalue().decode("utf-8")


def _mobile_missing_packages() -> list[str]:
    """Packages phone access needs that this Python does not have yet.

    An install updated by copying files keeps its old interpreter packages, so
    the panel names the command instead of failing at the first pairing.
    """
    import importlib.util
    return [name for name in ("cryptography", "segno") if importlib.util.find_spec(name) is None]


@eel.expose
def get_mobile_status() -> dict:
    """Settings → Phone: is access on, is it listening, which phones are paired."""
    server = _MOBILE_SERVER
    return {
        "ok": True,
        "missing": _mobile_missing_packages(),
        "enabled": user_settings.get("mobile_access_enabled", False) is True,
        "running": bool(server and server.running),
        "error": (server.error if server else "") or "",
        "port": server.port if server else _mobile_port(),
        "addresses": mobile_link.local_addresses(),
        "devices": MOBILE.devices.list(),
        "pcName": mobile_link.pc_name(),
        "relay": {**_MOBILE_RELAY.info(),
                  "enabled": user_settings.get("mobile_relay_enabled", True) is not False},
    }


@eel.expose
def set_mobile_access(enabled: bool) -> dict:
    if enabled is True and _mobile_missing_packages():
        return {**get_mobile_status(), "ok": False, "error": "missing_packages"}
    result = user_settings.set_value("mobile_access_enabled", enabled is True)
    if not result.get("ok"):
        return {"ok": False, "error": "invalid_value"}
    return _apply_mobile_access(enabled is True)


@eel.expose
def mobile_start_pairing() -> dict:
    """A fresh QR code. Only while phone access is on and listening."""
    server = _MOBILE_SERVER
    if server is None or not server.running:
        return {"ok": False, "error": "access_off"}
    relay = None
    room = _MOBILE_RELAY.room() if user_settings.get("mobile_relay_enabled", True) is not False else None
    if room:
        relay = {"url": mobile_relay.relay_url(), "room": room}
    result = MOBILE.start_pairing(server.port, relay=relay)
    result["qrSvg"] = _qr_svg(result["link"])
    _MOBILE_RELAY.poke()              # a QR on screen: be reachable for it
    return result


@eel.expose
def set_mobile_relay(enabled: bool) -> dict:
    """Settings → Phone → Away from home. On by default; off keeps NANO home-only."""
    result = user_settings.set_value("mobile_relay_enabled", enabled is True)
    if not result.get("ok"):
        return {"ok": False, "error": "invalid_value"}
    _MOBILE_RELAY.poke()
    return get_mobile_status()


@eel.expose
def mobile_pairing_state(session_id: str) -> dict:
    return MOBILE.pairing_state(str(session_id or ""))


@eel.expose
def mobile_decide_pairing(session_id: str, approve: bool) -> dict:
    """The yes or no on the PC. A phone can never answer this for itself."""
    return MOBILE.decide_pairing(str(session_id or ""), approve is True)


@eel.expose
def mobile_cancel_pairing() -> dict:
    MOBILE.cancel_pairing()
    return {"ok": True}


@eel.expose
def mobile_remove_device(device_id: str) -> dict:
    removed = MOBILE.devices.remove(str(device_id or ""))
    if removed and not MOBILE.devices.has_devices():
        # No phone left: the next pairing gets a fresh relay room, so a phone
        # removed today does not even know where to knock tomorrow.
        _MOBILE_RELAY.token.forget()
    _MOBILE_RELAY.poke()
    return {"ok": removed, "devices": MOBILE.devices.list()}
