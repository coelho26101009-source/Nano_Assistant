import pytest

from core.guardrails import GuardrailsEngine
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
async def test_public_url_is_accepted():
    ok, _ = await _validate_url("https://example.com")
    assert ok is True


def test_browser_mutations_require_confirmation():
    guard = GuardrailsEngine()
    assert guard.requires_confirmation("web_interact", {"action": "click"}) is True
    assert guard.requires_confirmation("web_interact", {"action": "type"}) is True
    assert guard.requires_confirmation("web_interact", {"action": "scroll"}) is False
