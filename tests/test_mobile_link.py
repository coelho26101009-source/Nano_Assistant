"""NANO on the phone: pairing, sealing, replay, revocation and the HTTP server."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

from core import mobile_link, secret_store
from core.mobile_link import (DeviceStore, EventFeed, LinkError, MobileLink, b64d, b64e,
                              open_sealed, seal)
from core.mobile_server import MAX_BODY_BYTES, MobileServer


@pytest.fixture()
def store(tmp_path, monkeypatch):
    secrets: dict[str, str] = {}
    monkeypatch.setattr(secret_store, "get_secret", lambda name: secrets.get(name, ""))
    monkeypatch.setattr(secret_store, "set_secret",
                        lambda name, value: secrets.__setitem__(name, value) or True)
    monkeypatch.setattr(secret_store, "delete_secret",
                        lambda name: secrets.pop(name, None) or True)
    devices = DeviceStore(path=tmp_path / "mobile_devices.json", secret_name="test_keys")
    devices.secrets = secrets  # for assertions
    return devices


@pytest.fixture()
def link(store):
    requests: list[dict] = []
    value = MobileLink(devices=store, on_pair_request=requests.append)
    value.pair_requests = requests
    value.register("echo", lambda text="": {"echo": text})
    value.active = True          # what core/main.py sets once the server listens
    return value


class _Phone:
    """The phone's half of pairing protocol 2: a P-256 key that never leaves it.

    Mirrors mobile/src/lib/crypto.ts (newPairingKeys, openFromPc)."""

    def __init__(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.public = self.private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.pk = b64e(self.public)

    def open(self, salt: bytes, context: str, box: dict) -> dict:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64d(box["epk"]))
        shared = self.private.exchange(ec.ECDH(), peer)
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                   info=context.encode()).derive(shared)
        return json.loads(AESGCM(key).decrypt(b64d(box["n"]), b64d(box["c"]), context.encode()))


ADDRESSES = [{"address": "192.168.1.20", "kind": "lan"}]


def _qr(link: MobileLink) -> dict:
    info = link.start_pairing(47821, ADDRESSES)
    query = parse_qs(urlsplit(info["link"]).query)
    return {"info": info, "id": query["i"][0], "secret": b64d(query["s"][0]),
            "hosts": query["h"][0], "port": int(query["p"][0]), "version": int(query["v"][0]),
            "phone": _Phone()}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _pair_call(link: MobileLink, qr: dict, op: str, phone=None, **extra) -> dict:
    """One pairing request. ``phone=False`` leaves the public key out (protocol 1)."""
    phone = qr["phone"] if phone is None else phone
    request_id = mobile_link.new_request_id()
    payload = {"op": op, "id": request_id, "ts": _now_ms(), **extra}
    if phone is not False:
        payload["pk"] = phone.pk
    body = seal(qr["secret"], payload, "nano-pair-v1|" + qr["id"])
    body["i"] = qr["id"]
    reply = link.handle_pair(body)
    return open_sealed(qr["secret"], reply, f"nano-pair-reply-v1|{qr['id']}|{request_id}")


def _key_context(qr: dict, device_id: str) -> str:
    return f"nano-pair-key-v2|{qr['id']}|{device_id}"


def _paired(link: MobileLink) -> tuple[str, bytes]:
    qr = _qr(link)
    assert _pair_call(link, qr, "request", name="Pixel 9", platform="android")["state"] == "pending"
    assert link.decide_pairing(qr["id"], True)["ok"]
    reply = _pair_call(link, qr, "status")
    assert reply["state"] == "approved" and "key" not in reply
    opened = qr["phone"].open(qr["secret"], _key_context(qr, reply["deviceId"]), reply["box"])
    return reply["deviceId"], b64d(opened["key"])


def _rpc(link: MobileLink, device_id: str, key: bytes, method: str, args=None,
         request_id: str | None = None, ts: int | None = None) -> dict:
    request_id = request_id or mobile_link.new_request_id()
    body = seal(key, {"id": request_id, "ts": ts if ts is not None else _now_ms(),
                      "m": method, "a": args or {}}, "nano-rpc-v1|" + device_id)
    body["d"] = device_id
    reply = link.handle_rpc(body)
    return open_sealed(key, reply, f"nano-rpc-reply-v1|{device_id}|{request_id}")


# â”€â”€ sealing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_seal_round_trip_and_tamper_detection():
    key = b"k" * 32
    sealed = seal(key, {"hello": "olÃ¡"}, "ctx")
    assert open_sealed(key, sealed, "ctx") == {"hello": "olÃ¡"}
    with pytest.raises(LinkError) as wrong_context:
        open_sealed(key, sealed, "other")
    assert wrong_context.value.status == 401
    tampered = dict(sealed)
    raw = bytearray(b64d(sealed["c"]))
    raw[0] ^= 1
    tampered["c"] = b64e(bytes(raw))
    with pytest.raises(LinkError):
        open_sealed(key, tampered, "ctx")
    with pytest.raises(LinkError):
        open_sealed(b"x" * 32, sealed, "ctx")


def test_known_vector_matches_the_phone():
    """The phone (mobile/src/lib/crypto.ts) decrypts this same vector in its tests."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = bytes(range(32))
    nonce = bytes(range(12))
    plain = b'{"hello":"NANO"}'
    cipher = AESGCM(key).encrypt(nonce, plain, b"nano-rpc-v1|dev_test")
    assert b64e(cipher) == "PCC-fqmJrTm3Y9nK_6ZaECwJFt6kwHZ2ePOXwwvBLvY"
    assert open_sealed(key, {"n": b64e(nonce), "c": b64e(cipher)}, "nano-rpc-v1|dev_test") == {"hello": "NANO"}


# â”€â”€ addresses â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.parametrize("address,kind", [
    ("192.168.1.5", "lan"), ("10.0.0.2", "lan"), ("172.20.1.1", "lan"),
    ("100.101.102.103", "private_network"), ("127.0.0.1", None), ("169.254.3.3", None),
    ("8.8.8.8", None), ("::1", None), ("nonsense", None),
])
def test_only_home_and_private_network_addresses_are_offered(address, kind):
    assert mobile_link.classify_address(address) == kind


# â”€â”€ pairing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_pairing_needs_the_qr_and_a_yes_on_the_pc(link):
    qr = _qr(link)
    assert qr["hosts"] == "192.168.1.20" and qr["port"] == 47821
    assert len(qr["secret"]) == 32 and qr["version"] == mobile_link.PROTOCOL == 2
    first = _pair_call(link, qr, "request", name="Pixel 9", platform="android")
    code = mobile_link.pairing_code(qr["id"], qr["phone"].public)
    assert first["state"] == "pending" and "key" not in first and "box" not in first
    assert first["code"] == code and len(code) == 6 and code.isdigit()
    assert link.pair_requests == [{"sessionId": qr["id"], "deviceName": "Pixel 9",
                                   "platform": "android", "code": code}]
    assert link.pairing_state(qr["id"])["code"] == code
    # Asking again does not get a key while the PC has not answered.
    assert _pair_call(link, qr, "status")["state"] == "pending"
    assert link.devices.list() == []
    assert link.decide_pairing(qr["id"], True)["state"] == "approved"
    assert link.devices.list() == []          # created when the phone collects its key
    reply = _pair_call(link, qr, "status")
    assert reply["state"] == "approved" and "key" not in reply
    key = b64d(qr["phone"].open(qr["secret"], _key_context(qr, reply["deviceId"]), reply["box"])["key"])
    assert len(key) == 32
    assert [d["name"] for d in link.devices.list()] == ["Pixel 9"]
    assert link.pairing_state(qr["id"])["state"] == "connected"
    # The reply was lost on the way: the same phone asks again and gets the
    # same key, and no second device is created.
    again = _pair_call(link, qr, "status")
    assert again["state"] == "approved" and again["deviceId"] == reply["deviceId"]
    assert len(link.devices.list()) == 1
    # Any other phone is refused.
    with pytest.raises(LinkError) as other:
        _pair_call(link, qr, "status", phone=_Phone())
    assert other.value.code == "unauthorized"


def test_a_photo_of_the_qr_is_not_enough_to_get_the_key(link):
    """Protocol 1 sealed the key with the QR secret: anyone who photographed the
    code could read it off the network. Now it is sealed to the phone's key."""
    qr = _qr(link)
    _pair_call(link, qr, "request", name="Pixel 9")
    link.decide_pairing(qr["id"], True)
    reply = _pair_call(link, qr, "status")      # an eavesdropper with the QR can read this far
    from cryptography.exceptions import InvalidTag
    with pytest.raises(InvalidTag):
        _Phone().open(qr["secret"], _key_context(qr, reply["deviceId"]), reply["box"])


def test_a_phone_that_slips_in_first_shows_a_different_code(link):
    qr = _qr(link)
    intruder = _Phone()
    first = _pair_call(link, qr, "request", phone=intruder, name="Pixel 9")
    # The PC shows the intruder's code; the person's phone computes its own.
    assert first["code"] == mobile_link.pairing_code(qr["id"], intruder.public)
    assert first["code"] != mobile_link.pairing_code(qr["id"], qr["phone"].public)
    with pytest.raises(LinkError) as refused:
        _pair_call(link, qr, "request", name="Pixel 9")
    assert refused.value.code == "unauthorized"


def test_a_protocol_1_phone_is_told_to_update(link):
    qr = _qr(link)
    with pytest.raises(LinkError) as old:
        _pair_call(link, qr, "request", phone=False, name="old app")
    assert old.value.code == "upgrade_required" and old.value.status == 426
    assert link.pair_requests == []


def test_pairing_vector_matches_the_phone():
    """mobile/test/crypto.test.ts opens this box and derives this code."""
    from cryptography.hazmat.primitives.asymmetric import ec
    phone = ec.derive_private_key(
        0x1F2E3D4C5B6A79881F2E3D4C5B6A79881F2E3D4C5B6A79881F2E3D4C5B6A7988, ec.SECP256R1())
    ephemeral = ec.derive_private_key(
        0x0A0B0C0D0E0F10111213141516171819202122232425262728292A2B2C2D2E2F, ec.SECP256R1())
    public = b64d("BL18c7iLLptM7aYgIrLai-Exk6W1btwm5994QuJM0LXrBgWtp72oOsaiuA1-MUBA-kf_Frg7rIXO2wFEUbt85xo")
    from cryptography.hazmat.primitives import serialization
    assert phone.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint) == public
    box = mobile_link.seal_to_phone(public, bytes(range(32)), "nano-pair-key-v2|0123456789abcdef|dev123",
                                    {"key": b64e(bytes([7] * 32))}, _ephemeral=ephemeral,
                                    _nonce=bytes(range(12)))
    assert box == {
        "epk": "BLGX90HjurBl9wAmEKL1-QJ5YRZFBN_G4nZSmf5Efvk3tt8br3iBApnKASIXhdTaMCwp9HfzYAXuHfDsJ-B-sNM",
        "n": "AAECAwQFBgcICQoL",
        "c": "57ncfJbEqIvfOcEunXAl1WnbiTUyhvRNmiQnMyYiIpgIvNFkS9I6EJpKJMinU7FGu9foPycuETXAF8RjvQ2Bbu89lG_X",
    }
    assert mobile_link.pairing_code("0123456789abcdef", public) == "175500"


def test_a_no_on_the_pc_issues_nothing(link):
    qr = _qr(link)
    _pair_call(link, qr, "request", name="Stranger")
    assert link.decide_pairing(qr["id"], False)["state"] == "denied"
    assert _pair_call(link, qr, "status")["state"] == "denied"
    assert link.devices.list() == []


def test_the_pc_cannot_approve_before_a_phone_asked(link):
    qr = _qr(link)
    assert link.decide_pairing(qr["id"], True)["ok"] is False


def test_a_wrong_secret_is_refused(link):
    qr = _qr(link)
    qr["secret"] = b"\0" * 32
    with pytest.raises(LinkError) as refused:
        _pair_call(link, qr, "request", name="x")
    assert refused.value.code == "unauthorized"
    assert link.pair_requests == []


def test_the_qr_expires(link):
    clock = [time.time()]
    link.clock = lambda: clock[0]
    qr = _qr(link)
    clock[0] += mobile_link.PAIRING_TTL_SECONDS + 1
    with pytest.raises(LinkError) as expired:
        _pair_call(link, qr, "request", name="late")
    assert expired.value.code == "pairing_expired"


def test_a_new_qr_cancels_the_old_one(link):
    old = _qr(link)
    _qr(link)
    with pytest.raises(LinkError):
        _pair_call(link, old, "request", name="old")


def test_device_names_are_cleaned(link):
    qr = _qr(link)
    _pair_call(link, qr, "request", name="  Pixel\u0000\n  9 " + "x" * 200)
    state = link.pairing_state(qr["id"])
    assert state["deviceName"].startswith("Pixel 9")
    assert len(state["deviceName"]) <= mobile_link.MAX_DEVICE_NAME


# â”€â”€ calls from a paired phone â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_paired_phone_can_call_registered_methods_only(link):
    device_id, key = _paired(link)
    reply = _rpc(link, device_id, key, "echo", {"text": "olÃ¡"})
    assert reply["ok"] and reply["r"] == {"echo": "olÃ¡"}
    assert _rpc(link, device_id, key, "set_emergency_stop")["error"] == "unknown_method"
    assert _rpc(link, device_id, key, "__class__")["error"] == "unknown_method"
    assert _rpc(link, device_id, key, "echo", {"nope": 1})["error"] == "bad_arguments"


def test_replayed_request_is_refused(link):
    device_id, key = _paired(link)
    request_id = mobile_link.new_request_id()
    body = seal(key, {"id": request_id, "ts": _now_ms(), "m": "echo", "a": {}}, "nano-rpc-v1|" + device_id)
    body["d"] = device_id
    link.handle_rpc(dict(body))
    with pytest.raises(LinkError) as replay:
        link.handle_rpc(dict(body))
    assert replay.value.code == "replayed"


def test_old_request_is_refused(link):
    device_id, key = _paired(link)
    with pytest.raises(LinkError) as old:
        _rpc(link, device_id, key, "echo", ts=_now_ms() - 10 * 60 * 1000)
    assert old.value.code == "clock_skew"


def test_another_devices_key_does_not_work(link):
    device_id, _key = _paired(link)
    with pytest.raises(LinkError) as wrong:
        _rpc(link, device_id, b"\1" * 32, "echo")
    assert wrong.value.code == "unauthorized"
    with pytest.raises(LinkError):
        _rpc(link, "dev_unknown", b"\1" * 32, "echo")


def test_removed_phone_is_locked_out(link):
    device_id, key = _paired(link)
    assert link.devices.remove(device_id)
    with pytest.raises(LinkError) as removed:
        _rpc(link, device_id, key, "echo")
    assert removed.value.code == "unauthorized"


def test_phone_can_unpair_itself(link):
    device_id, key = _paired(link)
    assert _rpc(link, device_id, key, "device.unpair")["ok"]
    assert link.devices.list() == []


def test_keys_live_in_the_secret_store_not_the_device_file(link, store, tmp_path):
    device_id, key = _paired(link)
    metadata = (tmp_path / "mobile_devices.json").read_text(encoding="utf-8")
    assert device_id in metadata and b64e(key) not in metadata
    assert b64e(key) in store.secrets["test_keys"]


def test_device_limit(link):
    for _ in range(mobile_link.MAX_DEVICES):
        _paired(link)
    qr = _qr(link)
    _pair_call(link, qr, "request", name="one too many")
    assert link.decide_pairing(qr["id"], True) == {"ok": False, "error": "too_many_devices"}
    assert len(link.devices.list()) == mobile_link.MAX_DEVICES


# â”€â”€ events â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_events_are_only_kept_when_a_phone_is_paired(link):
    link.publish("on_stream_chunk", "m1", "olÃ¡")
    assert link.feed.last == 0
    _paired(link)
    link.publish("on_stream_chunk", "m1", "olÃ¡")
    link.publish("on_something_private", "x")
    feed = link.feed.read(0)
    assert [(e["name"], e["data"]) for e in feed["events"]] == [("on_stream_chunk", ["m1", "olÃ¡"])]


def test_poll_returns_new_events_and_waits(link):
    device_id, key = _paired(link)
    link.publish("on_stream_start", "m1", "q")
    first = _rpc(link, device_id, key, "events.poll", {"after": 0})["r"]
    assert first["last"] == 1 and first["events"][0]["name"] == "on_stream_start"
    threading.Timer(0.2, lambda: link.publish("on_stream_end", "m1", {"text": "a"})).start()
    started = time.monotonic()
    second = _rpc(link, device_id, key, "events.poll", {"after": first["last"], "wait": 5})["r"]
    assert second["events"][0]["name"] == "on_stream_end"
    assert time.monotonic() - started < 4


def test_feed_reports_a_gap():
    feed = EventFeed(capacity=3)
    for n in range(6):
        feed.publish("on_stream_chunk", n)
    result = feed.read(1)
    assert result["reset"] is True
    assert [e["seq"] for e in result["events"]] == [4, 5, 6]
    assert feed.read(100)["last"] in (0, 6)


def test_a_cursor_from_before_a_restart_is_reset():
    """A restarted NANO numbers events from 1 again. A phone whose cursor came
    from the old process must reload instead of skipping or misreading."""
    old = EventFeed()
    for n in range(3):
        old.publish("on_stream_chunk", n)
    cursor, epoch = old.read(0)["last"], old.epoch
    restarted = EventFeed()
    for n in range(5):
        restarted.publish("on_stream_chunk", n)
    assert restarted.epoch != epoch
    result = restarted.read(cursor, epoch=epoch)
    assert result["reset"] is True and result["events"] == []
    assert result["last"] == 5 and result["epoch"] == restarted.epoch
    assert restarted.read(5, epoch=restarted.epoch)["reset"] is False


def test_clearing_the_feed_drops_events_and_starts_a_new_epoch():
    feed = EventFeed()
    feed.publish("on_stream_chunk", "private")
    before = feed.epoch
    feed.clear()
    assert feed.read(0)["events"] == [] and feed.epoch != before


def test_nothing_is_buffered_while_phone_access_is_off(link):
    _paired(link)
    link.active = False
    link.publish("on_stream_chunk", "m1", "while off")
    assert link.feed.read(0)["events"] == []


# â”€â”€ over HTTP â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _post(url: str, payload, headers=None):
    data = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read()), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}"), dict(error.headers)


def test_http_server_end_to_end(link):
    server = MobileServer(link, 0, host="127.0.0.1")
    assert server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        qr = _qr(link)
        request_id = mobile_link.new_request_id()
        body = seal(qr["secret"], {"op": "request", "id": request_id, "ts": _now_ms(), "name": "Pixel",
                                   "pk": qr["phone"].pk},
                    "nano-pair-v1|" + qr["id"])
        body["i"] = qr["id"]
        status, reply, _ = _post(base + "/v1/pair", body)
        assert status == 200
        assert open_sealed(qr["secret"], reply, f"nano-pair-reply-v1|{qr['id']}|{request_id}")["state"] == "pending"
        link.decide_pairing(qr["id"], True)
        request_id = mobile_link.new_request_id()
        body = seal(qr["secret"], {"op": "status", "id": request_id, "ts": _now_ms(), "pk": qr["phone"].pk},
                    "nano-pair-v1|" + qr["id"])
        body["i"] = qr["id"]
        _, reply, _ = _post(base + "/v1/pair", body)
        approved = open_sealed(qr["secret"], reply, f"nano-pair-reply-v1|{qr['id']}|{request_id}")
        device_id = approved["deviceId"]
        key = b64d(qr["phone"].open(qr["secret"], _key_context(qr, device_id), approved["box"])["key"])

        request_id = mobile_link.new_request_id()
        body = seal(key, {"id": request_id, "ts": _now_ms(), "m": "echo", "a": {"text": "hi"}},
                    "nano-rpc-v1|" + device_id)
        body["d"] = device_id
        status, reply, headers = _post(base + "/v1/rpc", body, {"Origin": "http://localhost"})
        assert status == 200 and headers.get("Access-Control-Allow-Origin") == "http://localhost"
        assert open_sealed(key, reply, f"nano-rpc-reply-v1|{device_id}|{request_id}")["r"] == {"echo": "hi"}

        status, reply, headers = _post(base + "/v1/rpc", body, {"Origin": "https://evil.example"})
        assert status == 401 and reply == {"error": "replayed"}
        assert "Access-Control-Allow-Origin" not in headers

        assert _post(base + "/v1/other", {})[0] == 404
        assert _post(base + "/v1/rpc", b"not json")[0] == 400
        with pytest.raises(urllib.error.HTTPError) as get:
            urllib.request.urlopen(base + "/v1/rpc", timeout=5)
        assert get.value.code == 405
    finally:
        server.stop()
    assert not server.running


def test_http_server_throttles_repeated_failures(link):
    server = MobileServer(link, 0, host="127.0.0.1")
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}/v1/rpc"
        bogus = {"d": "dev_x", "n": b64e(b"\0" * 12), "c": b64e(b"\0" * 32)}
        codes = [_post(base, bogus)[0] for _ in range(22)]
        assert codes[0] == 401 and codes[-1] == 429
    finally:
        server.stop()


def test_oversized_body_is_refused(link):
    server = MobileServer(link, 0, host="127.0.0.1")
    server.start()
    try:
        # Announce an oversized body but send only a little of it. The server
        # must refuse on the Content-Length alone, before reading anything.
        # (Sending it all raced on Windows CI: the server answers and closes
        # while the client is still writing, and the client then sees
        # WinError 10053 instead of the 413.)
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            conn.putrequest("POST", "/v1/rpc")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(MAX_BODY_BYTES + 1))
            conn.endheaders(b"x" * 16)
            assert conn.getresponse().status == 413
        finally:
            conn.close()
    finally:
        server.stop()


def test_a_phone_that_hangs_up_mid_answer_is_not_an_error(link, monkeypatch, capsys):
    """A locked screen or a backgrounded app drops the connection before the
    answer goes out. That is normal on a phone and must not log a failure."""
    import logging
    from http.server import BaseHTTPRequestHandler

    def hung_up(self):
        raise ConnectionResetError(10054, "the phone went away")

    errors: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record):
            errors.append(record.getMessage())

    collector = Collect(level=logging.ERROR)
    server_logger = logging.getLogger("nano.mobile.server")
    server_logger.addHandler(collector)
    monkeypatch.setattr(BaseHTTPRequestHandler, "end_headers", hung_up)
    server = MobileServer(link, 0, host="127.0.0.1")
    server.start()
    try:
        with pytest.raises(Exception):
            _post(f"http://127.0.0.1:{server.port}/v1/rpc", b"{}")
        time.sleep(0.2)
        assert errors == []
        assert "Traceback" not in capsys.readouterr().err
    finally:
        server.stop()
        server_logger.removeHandler(collector)


def test_poll_can_start_from_now(link):
    device_id, key = _paired(link)
    link.publish("on_stream_chunk", "m0", "old")
    now = _rpc(link, device_id, key, "events.poll", {"after": -1})["r"]
    assert {k: now[k] for k in ("events", "last", "reset")} == {"events": [], "last": 1, "reset": False}
    assert now["epoch"] == link.feed.epoch


def test_stopping_the_server_also_closes_kept_alive_connections(link):
    """Turning phone access off must leave nothing answering. A phone holding a
    keep-alive connection used to keep getting answers after stop()."""
    import http.client
    device_id, key = _paired(link)
    server = MobileServer(link, 0, host="127.0.0.1")
    assert server.start()
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        def echo():
            request_id = mobile_link.new_request_id()
            body = seal(key, {"id": request_id, "ts": _now_ms(), "m": "echo", "a": {"text": "hi"}},
                        "nano-rpc-v1|" + device_id)
            body["d"] = device_id
            conn.request("POST", "/v1/rpc", body=json.dumps(body), headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, response.read()

        assert echo()[0] == 200                  # the connection stays open (HTTP/1.1)
        server.stop()
        with pytest.raises((ConnectionError, http.client.HTTPException, OSError)):
            status, _ = echo()
            if status:                           # anything answered at all is a failure
                raise AssertionError(f"served after stop: {status}")
    finally:
        conn.close()
        server.stop()


def test_a_bad_content_length_is_a_bad_request_not_a_crash(link):
    import http.client
    server = MobileServer(link, 0, host="127.0.0.1")
    server.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        try:
            conn.putrequest("POST", "/v1/rpc")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", "abc")
            conn.endheaders()
            assert conn.getresponse().status == 400
        finally:
            conn.close()
    finally:
        server.stop()
