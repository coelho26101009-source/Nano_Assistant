"""NANO on the phone away from home: the PC's side of the relay.

The relay itself is relay/src/index.js (a Cloudflare Worker). These tests run
the PC's real RelayClient and MobileLink against a small stand-in relay that
follows the same wire rules, and a phone made of the same sealing code the
app uses. Set NANO_RELAY_E2E_URL (for example to a local `wrangler dev`) to
run the end-to-end test against the real Worker instead.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402

from core import mobile_relay, secret_store  # noqa: E402
from core.mobile_link import DeviceStore, MobileLink, b64d, b64e, open_sealed, seal  # noqa: E402
from core.mobile_relay import (RelayClient, RelayToken, clean_relay_url, room_for_token,  # noqa: E402
                               websocket_url)


# ── shared vector with relay/test/protocol.test.js ─────────────────────────

def test_room_name_matches_the_relay():
    assert room_for_token(bytes(range(32))) == "os4_ZZLglcyzI3aTrp6Q2vQecsaZEc8pR0_If--cIc8"


@pytest.mark.parametrize("value, expected", [
    ("https://nano-relay.example.workers.dev", "https://nano-relay.example.workers.dev"),
    ("https://nano-relay.example.workers.dev/", "https://nano-relay.example.workers.dev"),
    ("http://127.0.0.1:8787", "http://127.0.0.1:8787"),
    ("http://relay.example.com", ""),           # plain http only for this computer
    ("https://user:pw@relay.example.com", ""),
    ("https://relay.example.com/path", ""),
    ("https://relay.example.com?x=1", ""),
    ("ftp://relay.example.com", ""),
    ("", ""),
])
def test_only_https_relays_are_used(value, expected):
    assert clean_relay_url(value) == expected


def test_websocket_url():
    assert websocket_url("https://r.example", "pc", "abc") == "wss://r.example/v1/pc/abc"
    assert websocket_url("http://127.0.0.1:8787", "phone", "abc") == "ws://127.0.0.1:8787/v1/phone/abc"


def test_relay_is_off_until_deployed(monkeypatch):
    monkeypatch.delenv(mobile_relay.RELAY_URL_ENV, raising=False)
    monkeypatch.setattr(mobile_relay, "DEFAULT_RELAY_URL", "")
    assert mobile_relay.relay_url() == ""
    monkeypatch.setenv(mobile_relay.RELAY_URL_ENV, "http://evil.example")
    assert mobile_relay.relay_url() == ""


# ── fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture()
def secrets(monkeypatch):
    values: dict[str, str] = {}
    monkeypatch.setattr(secret_store, "get_secret", lambda name: values.get(name, ""))
    monkeypatch.setattr(secret_store, "set_secret",
                        lambda name, value: values.__setitem__(name, value) or True)
    monkeypatch.setattr(secret_store, "delete_secret", lambda name: values.pop(name, None) or True)
    return values


@pytest.fixture()
def link(tmp_path, secrets):
    devices = DeviceStore(path=tmp_path / "mobile_devices.json", secret_name="test_keys")
    value = MobileLink(devices=devices)
    value.register("echo", lambda text="": {"echo": text})
    value.active = True
    return value


def test_token_lives_in_the_secret_store_and_can_be_forgotten(secrets):
    token = RelayToken("t")
    room = token.room()
    assert len(room) == 43 and "t" in secrets
    assert RelayToken("t").room() == room            # same token after a restart
    token.forget()
    assert "t" not in secrets
    assert token.room() != room                      # a fresh room next time


def _frame(kind: str, body: dict, q: int = 1) -> dict:
    return {"t": kind, "q": q, "c": "0" * 32, "b": body}


def test_dispatch_refuses_when_not_wanted(link, secrets):
    client = RelayClient(link, wanted=lambda: False, token=RelayToken("t"), url=lambda: "https://r.example")
    assert client.dispatch(_frame("rpc", {"d": "x", "n": "", "c": ""})) == (503, {"error": "access_off"})


def test_dispatch_answers_unknown_devices_with_nothing(link, secrets):
    client = RelayClient(link, wanted=lambda: True, token=RelayToken("t"), url=lambda: "https://r.example")
    status, body = client.dispatch(_frame("rpc", {"d": "dev_nobody", "n": "", "c": ""}))
    assert (status, body) == (401, {"error": "unauthorized"})


def test_dispatch_rate_limits_a_relay_connection(link, secrets):
    client = RelayClient(link, wanted=lambda: True, token=RelayToken("t"), url=lambda: "https://r.example")
    for _ in range(20):
        client.dispatch(_frame("rpc", {"d": "dev_nobody"}))
    assert client.dispatch(_frame("rpc", {"d": "dev_nobody"}))[0] == 429


def test_frames_are_checked():
    good = {"t": "rpc", "q": 1, "c": "a" * 32, "b": {}}
    assert mobile_relay._valid_frame(good) == good
    for bad in ({**good, "t": "admin"}, {**good, "q": -1}, {**good, "q": True}, {**good, "c": "x"},
                {**good, "b": []}, [], None):
        assert mobile_relay._valid_frame(bad) is None


# ── a stand-in relay with the Worker's rules ───────────────────────────────

class FakeRelay:
    """relay/src/index.js in a few lines of aiohttp, on 127.0.0.1."""

    def __init__(self):
        self.pc: web.WebSocketResponse | None = None
        self.phones: dict[str, web.WebSocketResponse] = {}
        self.seen: list[str] = []      # everything that passed through, for the "cannot read" check
        self.url = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    async def _pc(self, request):
        room = request.match_info["room"]
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        try:
            ok = room_for_token(b64d(token)) == room
        except Exception:
            ok = False
        if not ok:
            return web.Response(status=401, text="unauthorized")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if self.pc is not None:
            await self.pc.close()
        self.pc = ws
        async for message in ws:
            self.seen.append(message.data)
            frame = json.loads(message.data)
            phone = self.phones.get(frame["c"])
            if phone is not None:
                await phone.send_str(json.dumps({"q": frame["q"], "s": frame["s"], "b": frame["b"]}))
        if self.pc is ws:
            self.pc = None
        return ws

    async def _phone(self, request):
        if self.pc is None:
            return web.Response(status=503, text="pc_offline")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        conn = os.urandom(16).hex()
        self.phones[conn] = ws
        async for message in ws:
            if message.data == "ping":
                await ws.send_str("pong")
                continue
            self.seen.append(message.data)
            frame = json.loads(message.data)
            if self.pc is None:
                await ws.send_str(json.dumps({"q": frame["q"], "s": 503, "b": {"error": "pc_offline"}}))
                continue
            await self.pc.send_str(json.dumps({"t": frame["t"], "q": frame["q"], "c": conn, "b": frame["b"]}))
        self.phones.pop(conn, None)
        return ws

    def _run(self):
        async def main():
            app = web.Application()
            app.router.add_get("/v1/pc/{room}", self._pc)
            app.router.add_get("/v1/phone/{room}", self._phone)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            self.url = f"http://127.0.0.1:{port}"
            self._loop = asyncio.get_running_loop()
            self._ready.set()
            while True:
                await asyncio.sleep(3600)
        asyncio.run(main())

    def start(self):
        self._thread.start()
        assert self._ready.wait(10)
        return self


@pytest.fixture()
def relay_url():
    real = os.getenv("NANO_RELAY_E2E_URL")
    if real:
        return real.rstrip("/")
    return FakeRelay().start().url


def _wait(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class _Phone:
    """The app's half of pairing and the sealed calls, through the relay."""

    def __init__(self, url: str, room: str):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.pk = b64e(self.private.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        self.target = websocket_url(url, "phone", room)
        self.q = 0

    async def send(self, kind: str, body: dict) -> tuple[int, dict]:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.target) as ws:
                self.q += 1
                await ws.send_str(json.dumps({"t": kind, "q": self.q, "b": body}))
                message = await asyncio.wait_for(ws.receive(), 10)
                frame = json.loads(message.data)
                assert frame["q"] == self.q
                return frame["s"], frame["b"]

    def open_box(self, salt: bytes, context: str, box: dict) -> dict:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64d(box["epk"]))
        shared = self.private.exchange(ec.ECDH(), peer)
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=context.encode()).derive(shared)
        return json.loads(AESGCM(key).decrypt(b64d(box["n"]), b64d(box["c"]), context.encode()))


def test_pair_and_talk_through_the_relay(link, secrets, relay_url):
    """The whole away-from-home path: QR, pairing approved ON THE PC, sealed
    calls, replay refused, removal honoured, and off means off."""
    wanted = {"on": True}
    client = RelayClient(link, wanted=lambda: wanted["on"], token=RelayToken("relay_t"),
                         url=lambda: relay_url)
    room = client.room()
    info = link.start_pairing(47821, [], relay={"url": relay_url, "room": room})
    assert info["relay"] is True and info["remoteReady"] is True
    query = parse_qs(urlsplit(info["link"]).query)
    assert query["r"] == [relay_url] and query["rr"] == [room]
    session_id, secret = query["i"][0], b64d(query["s"][0])

    client.start()
    try:
        assert _wait(lambda: client.state == "connected"), client.info()
        phone = _Phone(relay_url, room)

        def pair_call(op: str) -> dict:
            request_id = os.urandom(8).hex()
            body = seal(secret, {"op": op, "id": request_id, "ts": int(time.time() * 1000),
                                 "pk": phone.pk, "name": "Pixel", "platform": "android"},
                        "nano-pair-v1|" + session_id)
            body["i"] = session_id
            status, reply = asyncio.run(phone.send("pair", body))
            assert status == 200, reply
            return open_sealed(secret, reply, f"nano-pair-reply-v1|{session_id}|{request_id}")

        assert pair_call("request")["state"] == "pending"
        # Nothing is issued until the person says yes on the PC.
        assert pair_call("status")["state"] == "pending"
        assert link.decide_pairing(session_id, True)["ok"]
        reply = pair_call("status")
        assert reply["state"] == "approved"
        device_id = reply["deviceId"]
        key = b64d(phone.open_box(secret, f"nano-pair-key-v2|{session_id}|{device_id}", reply["box"])["key"])

        def rpc(method: str, args: dict, request_id: str | None = None) -> tuple[int, dict]:
            request_id = request_id or os.urandom(8).hex()
            body = seal(key, {"id": request_id, "ts": int(time.time() * 1000), "m": method, "a": args},
                        "nano-rpc-v1|" + device_id)
            body["d"] = device_id
            status, reply = asyncio.run(phone.send("rpc", body))
            if status != 200:
                return status, reply
            return status, open_sealed(key, reply, f"nano-rpc-reply-v1|{device_id}|{request_id}")

        status, reply = rpc("echo", {"text": "olá do café"}, "req_once_123")
        assert status == 200 and reply["r"] == {"echo": "olá do café"}
        # The same request again (a copy taken on the way) is refused.
        assert rpc("echo", {"text": "again"}, "req_once_123") == (401, {"error": "replayed"})

        # Removed on the PC: the phone's next call fails.
        link.devices.remove(device_id)
        assert rpc("echo", {"text": "x"})[0] == 401

        # Off means off: the PC leaves the room.
        wanted["on"] = False
        client.poke()
        assert _wait(lambda: client.state == "off"), client.info()
    finally:
        client.stop()


def test_relay_only_ever_sees_sealed_text(link, secrets):
    relay = FakeRelay().start()
    client = RelayClient(link, wanted=lambda: True, token=RelayToken("relay_t"), url=lambda: relay.url)
    device_id, key = link.devices.add("Pixel", "android")
    client.start()
    try:
        assert _wait(lambda: client.state == "connected")
        phone = _Phone(relay.url, client.room())
        request_id = os.urandom(8).hex()
        body = seal(key, {"id": request_id, "ts": int(time.time() * 1000), "m": "echo",
                          "a": {"text": "segredo-muito-pessoal"}}, "nano-rpc-v1|" + device_id)
        body["d"] = device_id
        status, reply = asyncio.run(phone.send("rpc", body))
        assert status == 200
        assert open_sealed(key, reply, f"nano-rpc-reply-v1|{device_id}|{request_id}")["r"] == {
            "echo": "segredo-muito-pessoal"}
        assert relay.seen and not any("segredo" in frame for frame in relay.seen)
    finally:
        client.stop()


def test_a_wrong_token_is_refused_at_the_door(link, secrets):
    relay = FakeRelay().start()
    token = RelayToken("relay_t")
    token.get()
    # Another PC claims this PC's room with its own token: the room name no
    # longer matches, and the relay refuses it.
    room = token.room()
    other = RelayToken("other")
    other.room = lambda: room  # type: ignore[method-assign]
    client = RelayClient(link, wanted=lambda: True, token=other, url=lambda: relay.url)

    async def attempt():
        async with aiohttp.ClientSession() as session:
            try:
                async with session.ws_connect(websocket_url(relay.url, "pc", room),
                                              headers={"Authorization": f"Bearer {b64e(other.get())}"}):
                    return 101
            except aiohttp.WSServerHandshakeError as exc:
                return exc.status
    assert asyncio.run(attempt()) == 401
    client.stop()


def test_a_phone_cannot_reach_a_pc_that_is_not_there(link, secrets):
    relay = FakeRelay().start()
    phone = _Phone(relay.url, RelayToken("relay_t").room())
    with pytest.raises(aiohttp.WSServerHandshakeError):
        asyncio.run(phone.send("rpc", {"d": "x"}))
