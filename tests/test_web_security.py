import pytest

from core.guardrails import GuardrailsEngine
from core.browser_agent import validate_public_http_url
from plugins.web_vision import _validate_url


@pytest.mark.asyncio
async def test_private_browser_targets_are_rejected():
    ok, _ = await _validate_url("http://127.0.0.1:8080/admin")
    assert ok is False


@pytest.mark.asyncio
async def test_non_http_schemes_are_rejected():
    ok, _ = await _validate_url("file:///C:/Windows/win.ini")
    assert ok is False


@pytest.mark.asyncio
async def test_public_url_is_accepted(monkeypatch):
    def fake_getaddrinfo(host, port, type=0):
        return [(None, None, None, None, ("93.184.216.34", 0))]

    import socket

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    ok, _ = await _validate_url("https://example.com")
    assert ok is True


def test_browser_mutations_require_confirmation():
    guard = GuardrailsEngine()
    assert guard.requires_confirmation("web_interact", {"action": "click"}) is True
    assert guard.requires_confirmation("web_interact", {"action": "type"}) is True
    assert guard.requires_confirmation("web_interact", {"action": "scroll"}) is False


def test_public_http_urls_are_allowed(monkeypatch):
    def fake_getaddrinfo(host, port, type=0):
        if host == "example.com":
            return [(None, None, None, None, ("93.184.216.34", 0))]
        if host == "www.example.com":
            return [(None, None, None, None, ("93.184.216.34", 0))]
        raise AssertionError(f"unexpected host {host}")

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    assert validate_public_http_url("http://example.com")[0] is True
    assert validate_public_http_url("https://www.example.com/path?q=1")[0] is True


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "http://127.0.0.1",
        "http://[::1]",
        "http://192.168.1.10",
        "http://10.0.0.5",
        "http://172.16.10.20",
        "file:///C:/Windows/win.ini",
        "nota-url",
    ],
)
def test_private_and_malformed_urls_are_blocked(monkeypatch, url):
    def fake_getaddrinfo(host, port, type=0):
        mapping = {
            "localhost": "127.0.0.1",
            "127.0.0.1": "127.0.0.1",
            "192.168.1.10": "192.168.1.10",
            "10.0.0.5": "10.0.0.5",
            "172.16.10.20": "172.16.10.20",
        }
        if host in mapping:
            return [(None, None, None, None, (mapping[host], 0))]
        if host == "::1":
            return [(None, None, None, None, ("::1", 0))]
        raise socket.gaierror(1, "unresolvable")

    import socket

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    ok, _ = validate_public_http_url(url)
    assert ok is False
