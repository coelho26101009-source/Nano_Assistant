"""Handing a URL to the user's default browser. Nothing more than that.

THIS IS NOT BROWSER AUTOMATION. Nano opens an address and stops; it does not
read the page, click anything, fill anything in, or know what happened next.
Browsing belongs to a later phase with its own consent model, and keeping the
two apart is what stops "open YouTube" from quietly growing into "act as me on
the web".

THE SCHEME IS THE SECURITY BOUNDARY. ShellExecuteExW will happily open whatever
protocol it is given: `file:` reads the disk, `javascript:` runs code in the
browser, `data:` smuggles a document inline, and `ms-*` reaches deep into
Windows. So the scheme is checked against a two-item allow-list before anything
else -- http and https, and nothing may be added to that list by phrasing.

A bare `github.com` is accepted and becomes `https://github.com`, because that
is what a person means; the upgrade is to HTTPS, never to HTTP.
"""
from __future__ import annotations

import logging
from urllib.parse import quote_plus, urlparse, urlunparse

from core.pc_control import winapi
from core.pc_control.results import PCControlError, clamp_text

logger = logging.getLogger("nano.pc_control.web")

#: The only two schemes that may reach the shell from here.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Named explicitly so the refusal message can be specific, and so a test can
#: assert each one is actually refused rather than trusting the allow-list.
DANGEROUS_SCHEMES = frozenset({
    "file", "javascript", "data", "vbscript", "shell", "ms-settings",
    "ms-appinstaller", "search-ms", "ftp", "smb", "about", "chrome",
    "res", "mailto", "tel", "callto", "ldap", "jar", "view-source",
})

MAX_URL_LENGTH = 2048
MAX_QUERY_LENGTH = 300

#: Search engines Nano may build a URL for. The engine is an enum, not a
#: template the caller supplies.
SEARCH_ENGINES: dict[str, str] = {
    "duckduckgo": "https://duckduckgo.com/?q={query}",
    "google": "https://www.google.com/search?q={query}",
    "bing": "https://www.bing.com/search?q={query}",
    "youtube": "https://www.youtube.com/results?search_query={query}",
}
DEFAULT_ENGINE = "duckduckgo"


def normalise_url(value) -> str:
    """Validate a URL and return the exact string that will be opened.

    Returns the normalised URL or raises. Nothing here concatenates the value
    into a larger string, so there is no interpolation for a crafted URL to
    break out of -- the result is handed to ShellExecuteExW as one typed
    argument.
    """
    if not isinstance(value, str):
        raise PCControlError("invalid_input", "O endereço tem de ser texto.")
    raw = value.strip()
    if not raw:
        raise PCControlError("invalid_input", "É preciso indicar o endereço.")
    if len(raw) > MAX_URL_LENGTH:
        raise PCControlError("invalid_input", "Esse endereço é demasiado longo.")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw):
        raise PCControlError("invalid_input", "O endereço contém caracteres inválidos.")

    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise PCControlError("invalid_input", "Esse endereço não é válido.") from exc

    scheme = (parsed.scheme or "").lower()
    if not scheme:
        # "github.com" -- a host with no scheme. Upgrade to HTTPS and re-parse,
        # so the rest of the validation sees exactly what will be opened.
        if "/" in raw.split("?", 1)[0].split("#", 1)[0].split("/", 1)[0]:
            raise PCControlError("invalid_input", "Esse endereço não é válido.")
        parsed = urlparse(f"https://{raw}")
        scheme = "https"
    if scheme in DANGEROUS_SCHEMES or scheme not in ALLOWED_SCHEMES:
        raise PCControlError(
            "blocked",
            f"O Nano só abre endereços http e https; '{scheme}:' não é permitido.",
            scheme=scheme)
    if not parsed.hostname:
        raise PCControlError("invalid_input", "Falta o endereço do site.")
    if parsed.username or parsed.password:
        raise PCControlError(
            "blocked", "O Nano não abre endereços que contenham credenciais.")

    return urlunparse(parsed)


def open_url(value) -> dict:
    """Open one validated http(s) address in the default browser."""
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "Só funciona no Windows.")
    url = normalise_url(value)
    try:
        winapi.shell_execute(url)
    except OSError as exc:
        logger.warning("could not open url: %s", exc)
        raise PCControlError("failed", "O Windows não conseguiu abrir esse endereço.") from exc
    return {"url": clamp_text(url, MAX_URL_LENGTH), "host": urlparse(url).hostname}


def build_search_url(query, engine: str | None = None) -> tuple[str, str, str]:
    """(url, engine, query) for a bounded search. The query is percent-encoded."""
    if not isinstance(query, str):
        raise PCControlError("invalid_input", "A pesquisa tem de ser texto.")
    text = query.strip()
    if not text:
        raise PCControlError("invalid_input", "É preciso dizer o que procurar.")
    if len(text) > MAX_QUERY_LENGTH:
        raise PCControlError(
            "invalid_input",
            f"A pesquisa é demasiado longa (máximo {MAX_QUERY_LENGTH} caracteres).")

    key = str(engine or DEFAULT_ENGINE).strip().lower()
    if key not in SEARCH_ENGINES:
        raise PCControlError("invalid_input",
                             f"'{engine}' não é um motor de pesquisa conhecido.",
                             allowed=sorted(SEARCH_ENGINES))
    # quote_plus encodes every reserved character, so nothing in the query can
    # add a parameter, change the path, or alter the host.
    return SEARCH_ENGINES[key].format(query=quote_plus(text)), key, text


def search(query, engine: str | None = None) -> dict:
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "Só funciona no Windows.")
    url, key, text = build_search_url(query, engine)
    # Validated again on the way out: the builder and the opener agree on one
    # definition of an acceptable URL, and neither trusts the other.
    url = normalise_url(url)
    try:
        winapi.shell_execute(url)
    except OSError as exc:
        logger.warning("could not open search: %s", exc)
        raise PCControlError("failed", "O Windows não conseguiu abrir a pesquisa.") from exc
    return {"query": clamp_text(text, MAX_QUERY_LENGTH), "engine": key,
            "url": clamp_text(url, MAX_URL_LENGTH)}


__all__ = [
    "ALLOWED_SCHEMES",
    "DANGEROUS_SCHEMES",
    "DEFAULT_ENGINE",
    "MAX_QUERY_LENGTH",
    "MAX_URL_LENGTH",
    "SEARCH_ENGINES",
    "build_search_url",
    "normalise_url",
    "open_url",
    "search",
]
