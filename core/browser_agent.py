"""Browser agent primitives for public web navigation and research."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import quote_plus
from urllib.parse import urlparse

import httpx


def validate_public_http_url(url: str) -> tuple[bool, str]:
    raw = str(url or "").strip()
    if not raw:
        return False, "url_required"
    try:
        parsed = urlparse(raw)
    except Exception:
        return False, "invalid_url"
    if parsed.scheme.lower() not in {"http", "https"}:
        return False, "invalid_scheme"
    if not parsed.hostname:
        return False, "invalid_url"
    if parsed.username or parsed.password:
        return False, "credentials_not_allowed"

    host = parsed.hostname.strip()
    try:
        resolved_hosts = {
            info[4][0]
            for info in socket.getaddrinfo(host, parsed.port or None, type=socket.SOCK_STREAM)
        }
    except socket.gaierror:
        return False, "unresolvable_host"
    except Exception:
        return False, "invalid_url"

    if not resolved_hosts:
        return False, "unresolvable_host"

    for resolved in resolved_hosts:
        try:
            ip = ipaddress.ip_address(resolved)
        except ValueError:
            return False, "invalid_ip"
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, "private_or_reserved_destination"
    return True, ""


class BrowserProvider:
    """Minimal Brave/Chromium-compatible browser interface for safe, policy-gated browsing."""

    def __init__(self, browser_name: str = "brave"):
        self.browser_name = browser_name

    def open(self, url: str) -> dict:
        if not url:
            return {"success": False, "error": "url_required"}
        return {"success": True, "browser": self.browser_name, "url": url, "opened": True}

    def navigate(self, url: str) -> dict:
        return self.open(url)

    def detect_safety_level(self, action: str, *, url: str | None = None) -> str:
        action_lower = (action or "").lower()
        if any(token in action_lower for token in ("purchase", "payment", "checkout", "bank", "transfer")):
            return "critical"
        if any(token in action_lower for token in ("submit", "message", "publish", "login", "form")):
            return "user_impacting"
        return "safe"

    def verify_action(self, action: str, *, expected: str | None = None, page_text: str | None = None) -> bool:
        if not action:
            return False
        if expected and not page_text:
            return False
        if expected and page_text and expected.lower() in (page_text or "").lower():
            return True
        if not expected:
            return True
        return False


def search_web(query: str, engine: str = "duckduckgo") -> dict:
    encoded = quote_plus((query or "").strip())
    if not encoded:
        return {"success": False, "error": "query_required"}
    urls = {
        "duckduckgo": f"https://html.duckduckgo.com/html/?q={encoded}",
        "bing": f"https://www.bing.com/search?q={encoded}",
        "google": f"https://www.google.com/search?q={encoded}",
    }
    url = urls.get(engine.lower(), urls["duckduckgo"])
    try:
        response = httpx.get(url, timeout=20)
        response.raise_for_status()
        snippet = response.text[:8000]
        return {"success": True, "engine": engine, "url": url, "content": snippet}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def fetch_url(url: str) -> dict:
    ok, error = validate_public_http_url(url)
    if not ok:
        return {"success": False, "error": error}
    try:
        response = httpx.get(url, timeout=20)
        response.raise_for_status()
        return {"success": True, "url": url, "status_code": response.status_code, "content": response.text[:12000]}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
