"""Seeing through the phone's camera -- only with the person's yes, each time.

WHAT THIS IS
------------
"O que é isto?" with the phone pointed at something. Two ways in:

* THE PERSON STARTS IT on the phone: the camera button in the phone's chat
  takes one photo and sends it with the message (``chat.send`` with a photo,
  see core/main.py). Pressing the button and then the shutter IS the request;
  nothing else asks.
* NANO ASKS FOR IT (the ``phone_camera_look`` tool, e.g. the person said "olha
  para isto pelo telemóvel" to the PC). This module sends the request to the
  paired phone, which shows a card: "NANO wants to see through your camera:
  <question>" with "Recusar" and "Abrir a câmara". Only a tap there opens the
  camera, the live picture shows a red "Câmara ligada" sign the whole time, and
  only the shutter sends a photo. No yes within ``REQUEST_TTL_SECONDS`` is a no.

WHAT CAN NEVER HAPPEN
---------------------
* The camera is never opened by the PC. The PC can only ASK; the phone decides,
  on the phone, every single time. There is no "always allow".
* Nothing is recorded. One still photo per yes, no video, no audio. The phone
  keeps no copy (it lives in memory until it is sent), and the PC keeps the
  bytes only until the vision model has answered: what is stored in the
  conversation is the written description, exactly like an attached picture.
* The photo travels inside the phone link's AES-256-GCM envelope
  (core/mobile_link.py), so the Wi-Fi never sees it.
* Which model looks at it follows the person's AI mode (core/vision.py), with a
  local vision model FIRST whenever one is installed, so in AUTO a photo of the
  person's home stays on the PC when it can. LOCAL never leaves the PC. The
  card on the phone says where the photo will be seen before the person says
  yes.
* What the vision model says is untrusted content (a photo of a note can say
  "ignore your instructions"): the capability is in
  core.trust.UNTRUSTED_OUTPUT_CAPABILITIES, so it reaches the chat model fenced.
"""
from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("nano.phone_camera")

#: How long the phone has to say yes and take the photo.
REQUEST_TTL_SECONDS = 90
#: The longest question shown on the phone's card.
MAX_QUESTION_CHARS = 300


@dataclass
class _Pending:
    id: str
    question: str
    event: threading.Event = field(default_factory=threading.Event)
    answered: bool = False
    declined: bool = False
    #: Ended without an answer: "replaced" by a newer request or "cancelled".
    ended: str = ""
    data: str = ""
    mime: str = ""


class CameraBroker:
    """At most one camera request at a time, between a tool and the phone.

    ``publish(name, *args)`` puts an event on the phone feed. ``ready()`` says
    whether a phone can hear it right now (phone access on, a phone paired).
    """

    def __init__(self, publish: Callable[..., None] | None = None,
                 ready: Callable[[], bool] | None = None,
                 seen_by: Callable[[], dict] | None = None):
        self.publish = publish or (lambda *_args: None)
        self.ready = ready or (lambda: False)
        self.seen_by = seen_by or (lambda: {})
        self._lock = threading.Lock()
        self._pending: _Pending | None = None

    def configure(self, *, publish: Callable[..., None], ready: Callable[[], bool],
                  seen_by: Callable[[], dict] | None = None) -> None:
        self.publish, self.ready = publish, ready
        if seen_by is not None:
            self.seen_by = seen_by

    @property
    def pending_id(self) -> str | None:
        with self._lock:
            return self._pending.id if self._pending else None

    def request(self, question: str, timeout: float = REQUEST_TTL_SECONDS) -> dict:
        """Ask the phone for one photo and wait for it. Blocking.

        Returns ``{"ok": True, "data", "mime"}`` or ``{"ok": False, "error"}``
        with error ``no_phone`` | ``declined`` | ``timeout``.
        """
        if not self.ready():
            return {"ok": False, "error": "no_phone"}
        question = " ".join(str(question or "").split())[:MAX_QUESTION_CHARS]
        pending = _Pending(id=secrets.token_hex(8), question=question)
        with self._lock:
            previous, self._pending = self._pending, pending
        if previous is not None:
            # A newer question replaces an unanswered one: the older card goes.
            previous.ended = "replaced"
            previous.event.set()
            self._safe_publish("on_camera_closed", previous.id, "replaced")
        try:
            where = self.seen_by() or {}
        except Exception:
            where = {}
        self._safe_publish("on_camera_request", pending.id,
                           {"question": question, "expiresIn": int(timeout),
                            "seenBy": {"local": bool(where.get("local")),
                                       "provider": str(where.get("provider") or "")[:20]}})
        answered = pending.event.wait(max(1.0, float(timeout)))
        with self._lock:
            if self._pending is pending:
                self._pending = None
        if not answered:
            self._safe_publish("on_camera_closed", pending.id, "expired")
            return {"ok": False, "error": "timeout"}
        if not pending.answered:
            return {"ok": False, "error": pending.ended or "cancelled"}
        if pending.declined:
            return {"ok": False, "error": "declined"}
        data, mime = pending.data, pending.mime
        pending.data = ""
        # What the card promised: "seen on your PC" binds where it is looked at.
        return {"ok": True, "data": data, "mime": mime, "local": bool(where.get("local"))}

    def respond(self, request_id: str, *, declined: bool, data: Any = "", mime: Any = "") -> dict:
        """The phone's answer: a photo, or no. Only for the request still open."""
        from core import vision
        with self._lock:
            pending = self._pending
            if pending is None or pending.id != str(request_id or ""):
                return {"ok": False, "error": "no_request"}
            if pending.answered:
                return {"ok": False, "error": "already_answered"}
            if not declined:
                mime = str(mime or "").lower()
                problem = vision.check_image(data if isinstance(data, str) else "", mime)
                if problem:
                    # The card stays open: the phone may try again.
                    return {"ok": False, "error": problem}
                pending.data, pending.mime = data, mime
            pending.declined = bool(declined)
            pending.answered = True
            pending.event.set()
        # Every phone takes the card down, whichever one answered.
        self._safe_publish("on_camera_closed", pending.id, "declined" if declined else "answered")
        return {"ok": True}

    def cancel(self) -> None:
        """Phone access went off (or NANO is closing): the open request is a no."""
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is not None:
            pending.ended = "cancelled"
            pending.event.set()
            self._safe_publish("on_camera_closed", pending.id, "cancelled")

    def _safe_publish(self, name: str, *args: Any) -> None:
        try:
            self.publish(name, *args)
        except Exception:
            logger.debug("Camera event %s failed", name, exc_info=True)


#: The one broker. core/main.py connects it to the phone link at start-up.
BROKER = CameraBroker()


def _seen_by() -> dict:
    """Where a photo would be looked at right now (local first)."""
    from core import vision
    found = vision.candidates(prefer_local=True)
    if not found:
        return {}
    provider, _model = found[0]
    return {"provider": provider, "local": provider == "ollama"}


def look(question: str, broker: CameraBroker | None = None) -> dict:
    """The ``phone_camera_look`` tool: ask the phone, then describe the photo.

    The photo's bytes live only inside this call.
    """
    from core import vision
    broker = broker or BROKER
    question = " ".join(str(question or "").split())[:MAX_QUESTION_CHARS]
    if not vision.candidates(prefer_local=True):
        return {"ok": False, "success": False, "status": "unavailable",
                "error": "no_vision_model", "message": vision.no_model_message()}
    shot = broker.request(question)
    if not shot.get("ok"):
        error = shot.get("error") or "failed"
        messages = {
            "no_phone": ("Não há nenhum telemóvel ligado ao NANO agora. Liga o acesso do "
                         "telemóvel no PC e abre a app NANO no telemóvel."),
            "declined": "O utilizador recusou mostrar a câmara. Não insistas; pergunta de outra forma.",
            "timeout": ("O telemóvel não respondeu a tempo (ninguém abriu a câmara). "
                        "Diz ao utilizador que pode tocar no botão da câmara na app NANO."),
            "replaced": "Este pedido foi substituído por outro mais recente.",
            "cancelled": "O acesso do telemóvel foi desligado; o pedido foi cancelado.",
        }
        return {"ok": False, "success": False, "status": error, "error": error,
                "message": messages.get(error, "Não foi possível ver pela câmara.")}
    # The phone's card said where the photo would be seen. When it said "no
    # teu PC", only the local model may look: if it fails, the answer is that
    # it failed, never a quiet upload to a cloud model the person was not told
    # about.
    result = vision.describe(shot.pop("data"), shot.get("mime") or "image/jpeg",
                             question=question or None, prefer_local=True,
                             local_only=bool(shot.get("local")))
    if not result.get("ok"):
        return {"ok": False, "success": False,
                "status": "unavailable" if result.get("error") == "no_vision_model" else "failed",
                "error": result.get("error") or "vision_failed", "message": result.get("message") or ""}
    where = "no teu PC" if result.get("local") else f"com {result.get('provider')}"
    return {"ok": True, "success": True, "status": "seen",
            "message": f"Vi pela câmara do telemóvel ({where}). A foto não foi guardada.",
            "text": result["text"], "provider": result.get("provider"),
            "model": result.get("model"), "local": bool(result.get("local")),
            "masked": result.get("masked", 0)}


__all__ = ["BROKER", "CameraBroker", "MAX_QUESTION_CHARS", "REQUEST_TTL_SECONDS", "look"]
