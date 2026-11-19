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
from core.mobile_server import MobileServer


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
    return value


ADDRESSES = [{"address": "192.168.1.20", "kind": "lan"}]


def _qr(link: MobileLink) -> dict:
    info = link.start_pairing(47821, ADDRESSES)
    query = parse_qs(urlsplit(info["link"]).query)
    return {"info": info, "id": query["i"][0], "secret": b64d(query["s"][0]),
            "hosts": query["h"][0], "port": int(query["p"][0])}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _pair_call(link: MobileLink, qr: dict, op: str, **extra) -> dict:
    request_id = mobile_link.new_request_id()
    body = seal(qr["secret"], {"op": op, "id": request_id, "ts": _now_ms(), **extra},
                "nano-pair-v1|" + qr["id"])
    body["i"] = qr["id"]
    reply = link.handle_pair(body)
    return open_sealed(qr["secret"], reply, f"nano-pair-reply-v1|{qr['id']}|{request_id}")


def _paired(link: MobileLink) -> tuple[str, bytes]:
    qr = _qr(link)
    assert _pair_call(link, qr, "request", name="Pixel 9", platform="android")["state"] == "pending"
    assert link.decide_pairing(qr["id"], True)["ok"]
    reply = _pair_call(link, qr, "status")
    assert reply["state"] == "approved"
    return reply["deviceId"], b64d(reply["key"])


def _rpc(link: MobileLink, device_id: str, key: bytes, method: str, args=None,
         request_id: str | None = None, ts: int | None = None) -> dict:
    request_id = request_id or mobile_link.new_request_id()
    body = seal(key, {"id": request_id, "ts": ts if ts is not None else _now_ms(),
                      "m": method, "a": args or {}}, "nano-rpc-v1|" + device_id)
    body["d"] = device_id
    reply = link.handle_rpc(body)
    return open_sealed(key, reply, f"nano-rpc-reply-v1|{device_id}|{request_id}")


# ── sealing ────────────────────────────────────────────────────────────────

def test_seal_round_trip_and_tamper_detection():
    key = b"k" * 32
    sealed = seal(key, {"hello": "olá"}, "ctx")
    assert open_sealed(key, sealed, "ctx") == {"hello": "olá"}
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


# ── addresses ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("address,kind", [
    ("192.168.1.5", "lan"), ("10.0.0.2", "lan"), ("172.20.1.1", "lan"),
    ("100.101.102.103", "private_network"), ("127.0.0.1", None), ("169.254.3.3", None),
    ("8.8.8.8", None), ("::1", None), ("nonsense", None),
])
def test_only_home_and_private_network_addresses_are_offered(address, kind):
    assert mobile_link.classify_address(address) == kind


# ── pairing ────────────────────────────────────────────────────────────────

def test_pairing_needs_the_qr_and_a_yes_on_the_pc(link):
    qr = _qr(link)
    assert qr["hosts"] == "192.168.1.20" and qr["port"] == 47821
    assert len(qr["secret"]) == 32
    first = _pair_call(link, qr, "request", name="Pixel 9", platform="android")
    assert first["state"] == "pending" and "key" not in first
    assert link.pair_requests == [{"sessionId": qr["id"], "deviceName": "Pixel 9", "platform": "android"}]
    # Asking again does not get a key while the PC has not answered.
    assert _pair_call(link, qr, "status")["state"] == "pending"
    assert link.devices.list() == []
    assert link.decide_pairing(qr["id"], True)["state"] == "approved"
    reply = _pair_call(link, qr, "status")
    assert reply["state"] == "approved" and len(b64d(reply["key"])) == 32
    assert [d["name"] for d in link.devices.list()] == ["Pixel 9"]
    # The QR is spent.
    with pytest.raises(LinkError) as spent:
        _pair_call(link, qr, "status")
    assert spent.value.code == "pairing_expired"


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


# ── calls from a paired phone ──────────────────────────────────────────────

def test_paired_phone_can_call_registered_methods_only(link):
    device_id, key = _paired(link)
    reply = _rpc(link, device_id, key, "echo", {"text": "olá"})
    assert reply["ok"] and reply["r"] == {"echo": "olá"}
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
    with pytest.raises(LinkError) as full:
        link.decide_pairing(qr["id"], True)
    assert full.value.code == "too_many_devices"


# ── events ─────────────────────────────────────────────────────────────────

def test_events_are_only_kept_when_a_phone_is_paired(link):
    link.publish("on_stream_chunk", "m1", "olá")
    assert link.feed.last == 0
    _paired(link)
    link.publish("on_stream_chunk", "m1", "olá")
    link.publish("on_something_private", "x")
    feed = link.feed.read(0)
    assert [(e["name"], e["data"]) for e in feed["events"]] == [("on_stream_chunk", ["m1", "olá"])]


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


# ── over HTTP ──────────────────────────────────────────────────────────────

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
        body = seal(qr["secret"], {"op": "request", "id": request_id, "ts": _now_ms(), "name": "Pixel"},
                    "nano-pair-v1|" + qr["id"])
        body["i"] = qr["id"]
        status, reply, _ = _post(base + "/v1/pair", body)
        assert status == 200
        assert open_sealed(qr["secret"], reply, f"nano-pair-reply-v1|{qr['id']}|{request_id}")["state"] == "pending"
        link.decide_pairing(qr["id"], True)
        request_id = mobile_link.new_request_id()
        body = seal(qr["secret"], {"op": "status", "id": request_id, "ts": _now_ms()}, "nano-pair-v1|" + qr["id"])
        body["i"] = qr["id"]
        _, reply, _ = _post(base + "/v1/pair", body)
        approved = open_sealed(qr["secret"], reply, f"nano-pair-reply-v1|{qr['id']}|{request_id}")
        device_id, key = approved["deviceId"], b64d(approved["key"])

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
        status, reply, _ = _post(f"http://127.0.0.1:{server.port}/v1/rpc", b"x" * (300 * 1024))
        assert status == 413
    finally:
        server.stop()


def test_poll_can_start_from_now(link):
    device_id, key = _paired(link)
    link.publish("on_stream_chunk", "m0", "old")
    now = _rpc(link, device_id, key, "events.poll", {"after": -1})["r"]
    assert now == {"events": [], "last": 1, "reset": False}
