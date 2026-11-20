"""NANO on the phone: pairing, encryption and the event feed.

WHAT THIS IS
------------
The NANO phone app is a remote for the NANO that runs on this PC. It has no
brain of its own: it sends the person's message here, NANO answers it with the
same memory, conversations, agenda and tools, and the answer streams back. This
module is everything about that link that is not HTTP: who is paired, how a new
phone gets paired, how every message is sealed, which calls a phone may make,
and the feed of events (answer chunks, approval requests, reminders) the phone
reads. ``core.mobile_server`` puts it on the network.

THE SECURITY MODEL, PLAINLY
---------------------------
* OFF BY DEFAULT. Nothing listens on the network until the person turns on
  "Phone access" in Settings.
* PAIRING NEEDS TWO THINGS: the QR code on the PC screen AND a yes on the PC.
  The QR carries a one-time 256-bit secret that expires in five minutes. The
  phone proves it has it, sends a fresh public key of its own, then waits. The
  PC shows "<phone> wants to connect" with a six-digit code, the phone shows
  the same code, and nothing is issued until the person approves it ON THE PC
  -- the approval is an eel call, which only the local UI can make (see
  local_control_plane).
  The phone's key is delivered ENCRYPTED TO THAT PUBLIC KEY (ECDH P-256 +
  HKDF + AES-GCM), so only the phone that asked can read it. A photo of the
  QR -- even with a copy of every packet on the Wi-Fi -- is therefore not
  enough to get in: protocol 1 sealed the key with the QR secret alone, and
  anyone holding the QR who polled at the right moment could take it.
* EVERY MESSAGE IS SEALED. Once approved, the phone gets its own random
  256-bit key. Every request and every answer is AES-256-GCM encrypted and
  authenticated with it, bound to the device id, the request id and the
  direction. A request older or newer than five minutes is refused, and a
  request id is accepted once: a captured request cannot be replayed. This
  holds on a café Wi-Fi as well as at home, and it is why the link does not
  depend on TLS certificates the phone would have to be taught to trust.
* KEYS STAY IN THE OS STORE. Device keys live in ``core.secret_store`` (DPAPI
  on Windows). The list of phones (names, dates) is ordinary metadata.
* A NARROW SURFACE. A phone can call only the methods registered in
  ``METHODS`` -- chat, conversations, agenda, memory, approvals, status. It
  cannot change settings, keys, permissions or the emergency stop, and it gets
  no generic endpoint. Approvals it answers are the SAME approvals the PC
  shows: the policy, guardrails and audit trail are untouched, and a phone that
  says yes is exactly as accountable as a click on the PC.
* REVOCABLE. Removing a phone on the PC deletes its key; its next request
  fails.
* NOTHING ON THE INTERNET. The server listens on the local network. Away from
  home the person uses a private network such as Tailscale, which the QR
  already lists when the PC has one; no port is ever opened on the router.

What it is NOT: a defence against malware already running as the same user on
the PC. Such a process can read the secret store directly; see SECURITY.md.
"""
from __future__ import annotations

import base64
import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import quote

from core import secret_store
from core.app_paths import DATA_DIR
from core.atomic_storage import write_bytes

logger = logging.getLogger("nano.mobile")

#: 2 since 2026-11-20: the device key is sealed to the phone's own ECDH key
#: and both screens show a pairing code. Version 1 phones cannot pair.
PROTOCOL = 2
DEFAULT_PORT = 47821

#: How long a QR code can be used.
PAIRING_TTL_SECONDS = 300
#: How long a phone that proved the QR waits for the yes on the PC.
APPROVAL_TTL_SECONDS = 180
#: Accepted clock difference between the phone and the PC.
MAX_CLOCK_SKEW_SECONDS = 300
#: A request id is remembered this long, which is longer than the skew window:
#: a replay of a request still inside the window is always caught.
REPLAY_MEMORY_SECONDS = 2 * MAX_CLOCK_SKEW_SECONDS + 60
MAX_DEVICES = 8
MAX_DEVICE_NAME = 60
#: Events kept for phones to read. Old ones fall off; a phone that was away
#: longer than that simply resynchronises.
EVENT_BUFFER = 2000
EVENT_MAX_AGE_SECONDS = 15 * 60
#: Longest a feed read may wait for something new.
MAX_POLL_WAIT_SECONDS = 25

_SECRET_NAME = "mobile_device_keys"
_DEVICES_PATH = DATA_DIR / "mobile_devices.json"

_AAD_PAIR = "nano-pair-v1|"
_AAD_PAIR_REPLY = "nano-pair-reply-v1|"
_AAD_PAIR_KEY = "nano-pair-key-v2|"
_AAD_RPC = "nano-rpc-v1|"
_AAD_RPC_REPLY = "nano-rpc-reply-v1|"


class LinkError(Exception):
    """A refused request. ``code`` is safe to send back; it reveals nothing."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


# ── encoding and sealing ───────────────────────────────────────────────────

def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64d(text: str) -> bytes:
    if not isinstance(text, str) or len(text) > 2_000_000:
        raise LinkError("bad_request")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise LinkError("bad_request") from exc


def _aesgcm(key: bytes):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key)


def seal(key: bytes, payload: dict, aad: str) -> dict:
    """Encrypt and authenticate ``payload`` -> ``{"n": nonce, "c": ciphertext}``."""
    nonce = os.urandom(12)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"n": b64e(nonce), "c": b64e(_aesgcm(key).encrypt(nonce, data, aad.encode("utf-8")))}


def open_sealed(key: bytes, envelope: dict, aad: str) -> dict:
    """The inverse of ``seal``. Any tampering, wrong key or wrong context fails."""
    from cryptography.exceptions import InvalidTag
    if not isinstance(envelope, dict):
        raise LinkError("bad_request")
    nonce, cipher = b64d(envelope.get("n", "")), b64d(envelope.get("c", ""))
    if len(nonce) != 12 or len(cipher) < 16:
        raise LinkError("bad_request")
    try:
        plain = _aesgcm(key).decrypt(nonce, cipher, aad.encode("utf-8"))
    except InvalidTag as exc:
        raise LinkError("unauthorized", 401) from exc
    try:
        value = json.loads(plain.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LinkError("bad_request") from exc
    if not isinstance(value, dict):
        raise LinkError("bad_request")
    return value


def phone_public_key(text: Any) -> bytes:
    """The phone's ECDH P-256 public key (65 raw bytes), checked to be a real point."""
    raw = b64d(text) if isinstance(text, str) else b""
    if len(raw) != 65 or raw[0] != 4:
        raise LinkError("bad_request")
    from cryptography.hazmat.primitives.asymmetric import ec
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except ValueError as exc:
        raise LinkError("bad_request") from exc
    return raw


def pairing_code(session_id: str, phone_key: bytes) -> str:
    """Six digits both screens show, so the person can see it is THEIR phone.

    Derived from the session and the phone's public key: a phone that slipped
    in first with a photo of the QR would show a different code than the
    person's own phone, and the PC shows the code of the phone it is asking
    about.
    """
    import hashlib
    digest = hashlib.sha256(b"nano-pair-code-v2|" + session_id.encode("ascii") + b"|" + phone_key).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


def seal_to_phone(phone_key: bytes, salt: bytes, context: str, payload: dict,
                  _ephemeral=None, _nonce: bytes | None = None) -> dict:
    """Encrypt ``payload`` so only the holder of the phone's private key can read it.

    ECDH (P-256) between a fresh PC key and the phone's key, HKDF-SHA256 with
    the QR secret as salt and ``context`` as info, then AES-256-GCM with
    ``context`` as additional data. Returns ``{"epk", "n", "c"}``. The two
    underscored arguments exist for the shared test vector only.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), phone_key)
    mine = _ephemeral or ec.generate_private_key(ec.SECP256R1())
    shared = mine.exchange(ec.ECDH(), peer)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
               info=context.encode("utf-8")).derive(shared)
    nonce = _nonce or os.urandom(12)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    cipher = _aesgcm(key).encrypt(nonce, data, context.encode("utf-8"))
    public = mine.public_key().public_bytes(serialization.Encoding.X962,
                                            serialization.PublicFormat.UncompressedPoint)
    return {"epk": b64e(public), "n": b64e(nonce), "c": b64e(cipher)}


# ── network addresses the QR lists ─────────────────────────────────────────

_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


def classify_address(address: str) -> str | None:
    """"lan", "private_network" (Tailscale and similar) or None (not offered)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    if ip.version != 4 or ip.is_loopback or ip.is_link_local or ip.is_multicast:
        return None
    if ip in _TAILSCALE_NET:
        return "private_network"
    if ip.is_private:
        return "lan"
    return None


def local_addresses() -> list[dict]:
    """This PC's reachable IPv4 addresses: home network first, then Tailscale.

    Public addresses are never listed: NANO does not offer itself to the
    internet, and a phone told to use one would be told to use the router.
    """
    found: list[str] = []
    try:
        import psutil
        for entries in psutil.net_if_addrs().values():
            for entry in entries:
                if getattr(entry, "family", None) == socket.AF_INET:
                    found.append(str(entry.address))
    except Exception:
        logger.debug("psutil could not list interfaces", exc_info=True)
    if not found:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("10.255.255.255", 1))  # no packet is sent
                found.append(probe.getsockname()[0])
        except OSError:
            pass
    result: list[dict] = []
    for address in dict.fromkeys(found):
        kind = classify_address(address)
        if kind:
            result.append({"address": address, "kind": kind})
    result.sort(key=lambda row: 0 if row["kind"] == "lan" else 1)
    return result


def pc_name() -> str:
    try:
        name = socket.gethostname().strip()
    except OSError:
        name = ""
    return (name or "NANO PC")[:60]


# ── paired phones ──────────────────────────────────────────────────────────

def _clean_name(value: Any) -> str:
    text = " ".join(str(value or "").split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:MAX_DEVICE_NAME] or "Phone"


class DeviceStore:
    """Paired phones: keys in the secret store, names and dates in a file."""

    def __init__(self, path=_DEVICES_PATH, secret_name: str = _SECRET_NAME):
        self._path = path
        self._secret_name = secret_name
        self._lock = threading.RLock()
        self._keys_cache: dict[str, bytes] | None = None

    # keys
    def _keys(self) -> dict[str, bytes]:
        with self._lock:
            if self._keys_cache is None:
                raw = secret_store.get_secret(self._secret_name)
                keys: dict[str, bytes] = {}
                if raw:
                    try:
                        for device_id, value in json.loads(raw).items():
                            key = b64d(value)
                            if len(key) == 32:
                                keys[str(device_id)] = key
                    except (ValueError, LinkError, AttributeError):
                        logger.warning("Stored phone keys are unreadable; phones must pair again.")
                self._keys_cache = keys
            return self._keys_cache

    def _save_keys(self, keys: dict[str, bytes]) -> bool:
        payload = json.dumps({k: b64e(v) for k, v in keys.items()})
        ok = secret_store.set_secret(self._secret_name, payload) if keys else \
            secret_store.delete_secret(self._secret_name)
        if ok:
            self._keys_cache = dict(keys)
        return ok

    def has_devices(self) -> bool:
        return bool(self._keys())

    def key(self, device_id: str) -> bytes | None:
        return self._keys().get(str(device_id or ""))

    # metadata
    def _meta(self) -> dict[str, dict]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save_meta(self, meta: dict[str, dict]) -> None:
        try:
            write_bytes(self._path, json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"))
        except OSError:
            logger.warning("Could not save the list of phones", exc_info=True)

    def list(self) -> list[dict]:
        with self._lock:
            keys = self._keys()
            meta = self._meta()
            rows = []
            for device_id in keys:
                row = meta.get(device_id, {})
                rows.append({
                    "id": device_id,
                    "name": _clean_name(row.get("name")),
                    "platform": str(row.get("platform") or "")[:20],
                    "pairedAt": row.get("pairedAt"),
                    "lastSeen": row.get("lastSeen"),
                })
            rows.sort(key=lambda r: r.get("pairedAt") or 0)
            return rows

    def add(self, name: str, platform: str) -> tuple[str, bytes]:
        with self._lock:
            keys = dict(self._keys())
            if len(keys) >= MAX_DEVICES:
                raise LinkError("too_many_devices", 409)
            device_id = "dev_" + secrets.token_hex(8)
            key = os.urandom(32)
            keys[device_id] = key
            if not self._save_keys(keys):
                raise LinkError("store_failed", 500)
            meta = self._meta()
            meta[device_id] = {"name": _clean_name(name), "platform": str(platform or "")[:20],
                               "pairedAt": int(time.time()), "lastSeen": int(time.time())}
            self._save_meta(meta)
            return device_id, key

    def remove(self, device_id: str) -> bool:
        with self._lock:
            keys = dict(self._keys())
            if device_id not in keys:
                return False
            del keys[device_id]
            if not self._save_keys(keys):
                return False
            meta = self._meta()
            meta.pop(device_id, None)
            self._save_meta(meta)
            return True

    def remove_all(self) -> None:
        with self._lock:
            self._save_keys({})
            self._save_meta({})

    def touch(self, device_id: str) -> None:
        """Remember when a phone was last heard from (at most once a minute)."""
        with self._lock:
            meta = self._meta()
            row = meta.get(device_id)
            now = int(time.time())
            if row is not None and now - int(row.get("lastSeen") or 0) >= 60:
                row["lastSeen"] = now
                self._save_meta(meta)


# ── pairing ────────────────────────────────────────────────────────────────

@dataclass
class PairingSession:
    id: str
    secret: bytes
    created: float
    state: str = "waiting"          # waiting | pending | approved | denied | connected
    device_name: str = ""
    platform: str = ""
    requested_at: float = 0.0
    phone_key: bytes = b""
    code: str = ""
    device_id: str = ""
    delivered: bool = False
    #: The reply that carried the phone's key, kept while the session lives so
    #: a phone whose first copy was lost on the network can ask again. It is
    #: sealed to the phone's own public key: nobody else can open it.
    delivery: dict = field(default_factory=dict)

    def expired(self, now: float) -> bool:
        if self.state == "waiting":
            return now - self.created > PAIRING_TTL_SECONDS
        if self.state == "pending":
            return now - self.requested_at > APPROVAL_TTL_SECONDS
        return now - self.created > PAIRING_TTL_SECONDS + APPROVAL_TTL_SECONDS + 60


# ── the event feed ─────────────────────────────────────────────────────────

class EventFeed:
    """A bounded, numbered list of events every paired phone reads from."""

    def __init__(self, capacity: int = EVENT_BUFFER):
        self._events: deque[dict] = deque(maxlen=capacity)
        self._seq = 0
        self._cond = threading.Condition()
        #: Changes whenever the numbering starts again (a new NANO process, or
        #: the feed being cleared). A phone that knew another epoch resyncs.
        self.epoch = secrets.token_hex(4)

    def clear(self) -> None:
        """Forget every buffered event. Phones resynchronise on their next read."""
        with self._cond:
            self._events.clear()
            self.epoch = secrets.token_hex(4)
            self._cond.notify_all()

    @property
    def last(self) -> int:
        with self._cond:
            return self._seq

    def publish(self, name: str, payload: Any) -> int:
        with self._cond:
            self._seq += 1
            self._events.append({"seq": self._seq, "name": name, "data": payload,
                                 "at": time.time()})
            self._cond.notify_all()
            return self._seq

    def read(self, after: int, wait: float = 0.0, limit: int = 300,
             epoch: str | None = None) -> dict:
        """Events with ``seq > after``. Waits up to ``wait`` seconds for one.

        ``reset`` is true when the phone asked for events that already fell
        off the end, or when its cursor belongs to another epoch (NANO was
        restarted, or the feed was cleared): it then reloads its state instead
        of trusting a gap. A restart used to be invisible -- the cursor was
        quietly set back to 0 -- so a turn cut off by it left the phone
        showing "a pensar" with its buttons disabled.
        """
        deadline = time.monotonic() + max(0.0, min(float(wait), MAX_POLL_WAIT_SECONDS))
        with self._cond:
            if (epoch is not None and epoch != self.epoch) or after > self._seq:
                return {"events": [], "last": self._seq, "reset": True, "epoch": self.epoch}
            while True:
                cutoff = time.time() - EVENT_MAX_AGE_SECONDS
                while self._events and self._events[0]["at"] < cutoff:
                    self._events.popleft()
                fresh = [e for e in self._events if e["seq"] > after]
                if fresh or time.monotonic() >= deadline:
                    break
                self._cond.wait(timeout=max(0.0, deadline - time.monotonic()))
            oldest = self._events[0]["seq"] if self._events else self._seq + 1
            reset = after > 0 and after < oldest - 1
            fresh = fresh[:limit]
            if fresh:
                last = fresh[-1]["seq"]
            else:
                last = self._seq if reset else after
            return {"events": [{"seq": e["seq"], "name": e["name"], "data": e["data"]} for e in fresh],
                    "last": last, "reset": reset, "epoch": self.epoch}


# ── the link itself ────────────────────────────────────────────────────────

#: Events the phone needs. Everything else NANO tells its own window stays there.
FORWARDED_EVENTS = frozenset({
    "on_stream_start", "on_stream_status", "on_stream_chunk", "on_stream_end",
    "on_stream_error", "on_rate_limited", "on_confirm_request", "on_reminder_due",
    "on_proactive_notice", "on_work_mode_done", "on_voice_exchange",
    "on_confirm_resolved", "on_conversation_changed",
})


@dataclass
class MobileLink:
    devices: DeviceStore = field(default_factory=DeviceStore)
    feed: EventFeed = field(default_factory=EventFeed)
    clock: Callable[[], float] = time.time
    #: Called with the session when a phone asks to pair, so the PC can ask.
    on_pair_request: Callable[[dict], None] | None = None

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        #: True only while phone access is on. Events are buffered for phones
        #: only then: with access off, nothing NANO says is kept for them.
        self.active = False
        self._sessions: dict[str, PairingSession] = {}
        self._seen: dict[str, float] = {}
        self._methods: dict[str, Callable[..., Any]] = {}
        self._failures: dict[str, deque] = {}

    # methods
    def register(self, name: str, fn: Callable[..., Any]) -> None:
        self._methods[name] = fn

    def methods(self) -> list[str]:
        return sorted(self._methods)

    # rate limiting of failed requests, per network address
    def check_client(self, client: str) -> None:
        with self._lock:
            window = self._failures.get(client)
            now = self.clock()
            if window:
                while window and now - window[0] > 300:
                    window.popleft()
                if len(window) >= 20:
                    raise LinkError("too_many_attempts", 429)

    def note_failure(self, client: str) -> None:
        with self._lock:
            self._failures.setdefault(client, deque(maxlen=50)).append(self.clock())

    # pairing, PC side
    def start_pairing(self, port: int, addresses: list[dict] | None = None) -> dict:
        """A fresh QR. Any earlier unfinished pairing is cancelled."""
        with self._lock:
            self._sessions = {k: s for k, s in self._sessions.items()
                              if s.state == "approved" and not s.delivered}
            session = PairingSession(id=secrets.token_hex(8), secret=os.urandom(32),
                                     created=self.clock())
            self._sessions[session.id] = session
        hosts = addresses if addresses is not None else local_addresses()
        host_list = ",".join(row["address"] for row in hosts)
        name = pc_name()
        link = (f"nano://pair?v={PROTOCOL}&h={quote(host_list, safe=',')}&p={int(port)}"
                f"&i={session.id}&s={b64e(session.secret)}&n={quote(name)}")
        return {"ok": True, "sessionId": session.id, "link": link, "addresses": hosts,
                "port": int(port), "pcName": name, "expiresIn": PAIRING_TTL_SECONDS,
                "remoteReady": any(row["kind"] == "private_network" for row in hosts)}

    def cancel_pairing(self) -> None:
        with self._lock:
            self._sessions.clear()

    def pairing_state(self, session_id: str) -> dict:
        with self._lock:
            session = self._live_session(session_id)
            if session is None:
                return {"ok": True, "state": "expired"}
            return {"ok": True, "state": session.state, "deviceName": session.device_name,
                    "platform": session.platform, "code": session.code}

    def decide_pairing(self, session_id: str, approve: bool) -> dict:
        """The person's answer, ON THE PC. The only way a phone gets a key."""
        with self._lock:
            session = self._live_session(session_id)
            if session is None or session.state != "pending":
                return {"ok": False, "error": "no_pending_request"}
            if not approve:
                session.state = "denied"
                return {"ok": True, "state": "denied"}
            if len(self.devices.list()) >= MAX_DEVICES:
                return {"ok": False, "error": "too_many_devices"}
            # The device is created when the phone collects its key, not now:
            # a phone that gave up meanwhile must not leave a paired device
            # behind that nothing holds the key for.
            session.state = "approved"
            return {"ok": True, "state": "approved"}

    def _live_session(self, session_id: str) -> PairingSession | None:
        session = self._sessions.get(str(session_id or ""))
        if session is None:
            return None
        if session.expired(self.clock()):
            self._sessions.pop(session.id, None)
            return None
        return session

    # pairing, phone side (over the network)
    def handle_pair(self, body: dict) -> dict:
        """The phone proves it read the QR, says who it is, and asks for its key."""
        with self._lock:
            session = self._live_session(body.get("i") if isinstance(body, dict) else "")
            if session is None:
                raise LinkError("pairing_expired", 410)
            request = open_sealed(session.secret, body, _AAD_PAIR + session.id)
            self._check_fresh(request)
            op = request.get("op")
            if op not in ("request", "status"):
                raise LinkError("bad_request")
            if "pk" not in request:
                # A protocol-1 phone: it would take a key sealed with the QR
                # secret alone, which is exactly what version 2 stopped doing.
                raise LinkError("upgrade_required", 426)
            phone_key = phone_public_key(request.get("pk"))
            notify = None
            if op == "request" and session.state == "waiting":
                session.state = "pending"
                session.device_name = _clean_name(request.get("name"))
                session.platform = str(request.get("platform") or "")[:20]
                session.requested_at = self.clock()
                session.phone_key = phone_key
                session.code = pairing_code(session.id, phone_key)
                notify = {"sessionId": session.id, "deviceName": session.device_name,
                          "platform": session.platform, "code": session.code}
            elif phone_key != session.phone_key:
                # Not the phone this session is asking about. Answered with
                # nothing, and the session is left alone for the real one.
                raise LinkError("unauthorized", 401)
            reply: dict[str, Any] = {"state": session.state, "id": request.get("id"),
                                     "code": session.code}
            if session.state == "approved":
                device_id, key = self.devices.add(session.device_name, session.platform)
                context = f"{_AAD_PAIR_KEY}{session.id}|{device_id}"
                session.delivery = {
                    "deviceId": device_id, "pcName": pc_name(), "protocol": PROTOCOL,
                    "box": seal_to_phone(session.phone_key, session.secret, context,
                                         {"key": b64e(key)})}
                reply.update(session.delivery)
                session.device_id, session.delivered, session.state = device_id, True, "connected"
                logger.info("Phone paired: %s", device_id)
            elif session.state == "connected" and session.delivery:
                # The same phone asks again: the reply with its key was lost.
                reply.update({"state": "approved", **session.delivery})
            elif session.state == "denied":
                self._sessions.pop(session.id, None)
            sealed = seal(session.secret, reply, _AAD_PAIR_REPLY + session.id + "|" + str(request.get("id")))
        if notify and self.on_pair_request:
            try:
                self.on_pair_request(notify)
            except Exception:
                logger.debug("pair request notification failed", exc_info=True)
        return sealed

    # requests from paired phones
    def _check_fresh(self, request: dict) -> None:
        request_id = request.get("id")
        sent = request.get("ts")
        if not isinstance(request_id, str) or not (8 <= len(request_id) <= 64):
            raise LinkError("bad_request")
        if not isinstance(sent, (int, float)) or isinstance(sent, bool):
            raise LinkError("bad_request")
        now = self.clock()
        if abs(now - float(sent) / 1000.0) > MAX_CLOCK_SKEW_SECONDS:
            raise LinkError("clock_skew", 401)
        with self._lock:
            for key, at in list(self._seen.items()):
                if now - at > REPLAY_MEMORY_SECONDS:
                    del self._seen[key]
            if request_id in self._seen:
                raise LinkError("replayed", 401)
            self._seen[request_id] = now

    def handle_rpc(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise LinkError("bad_request")
        device_id = str(body.get("d") or "")
        key = self.devices.key(device_id)
        if key is None:
            raise LinkError("unauthorized", 401)
        request = open_sealed(key, body, _AAD_RPC + device_id)
        self._check_fresh(request)
        self.devices.touch(device_id)
        request_id = request["id"]
        method = request.get("m")
        args = request.get("a") or {}
        if not isinstance(args, dict):
            args = {}
        try:
            result = self.call(device_id, method, args)
            reply = {"id": request_id, "ok": True, "r": result}
        except LinkError as exc:
            reply = {"id": request_id, "ok": False, "error": exc.code}
        except TypeError:
            reply = {"id": request_id, "ok": False, "error": "bad_arguments"}
        except Exception:
            logger.exception("Phone call %r failed", method)
            reply = {"id": request_id, "ok": False, "error": "internal_error"}
        # A device removed while its request ran still gets its answer sealed
        # with the key it used; its next request is refused.
        return seal(key, reply, _AAD_RPC_REPLY + device_id + "|" + request_id)

    def call(self, device_id: str, method: Any, args: dict) -> Any:
        if method == "events.poll":
            after = args.get("after", 0)
            wait = args.get("wait", 0)
            epoch = args.get("epoch")
            if after == -1:      # "start from now": the phone just opened
                return {"events": [], "last": self.feed.last, "reset": False,
                        "epoch": self.feed.epoch}
            if not isinstance(after, int) or isinstance(after, bool) or after < 0:
                after = 0
            if not isinstance(wait, (int, float)) or isinstance(wait, bool):
                wait = 0
            return self.feed.read(after, wait, epoch=epoch if isinstance(epoch, str) else None)
        if method == "device.unpair":
            self.devices.remove(device_id)
            return {"ok": True}
        fn = self._methods.get(method) if isinstance(method, str) else None
        if fn is None:
            raise LinkError("unknown_method")
        clean = {str(k): v for k, v in args.items() if isinstance(k, str) and k.isidentifier()}
        return fn(**clean)

    # events
    def publish(self, name: str, *args: Any) -> None:
        if name not in FORWARDED_EVENTS or not self.active:
            return
        if not self.devices.has_devices():
            return
        try:
            json.dumps(args, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return
        self.feed.publish(name, json.loads(json.dumps(list(args), ensure_ascii=False, default=str)))


def new_request_id() -> str:
    return uuid.uuid4().hex


__all__ = [
    "DEFAULT_PORT", "DeviceStore", "EventFeed", "FORWARDED_EVENTS", "LinkError",
    "MobileLink", "PROTOCOL", "b64d", "b64e", "classify_address", "local_addresses",
    "new_request_id", "open_sealed", "pairing_code", "pc_name", "phone_public_key",
    "seal", "seal_to_phone",
]
