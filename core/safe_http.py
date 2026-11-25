"""HTTP clients for public web pages, with the checked address PINNED.

THE PROBLEM THIS SOLVES (DNS REBINDING)
---------------------------------------
``core.browser_agent.validate_public_http_url`` looks a host name up and
refuses it when any address is private (the router, the PC itself, a
Tailscale device). The request that followed then looked the name up AGAIN.
A hostile site can answer the first lookup with a public address and the
second, a moment later, with ``192.168.1.1``: the check passed, and the page
NANO read was the router's. That is called DNS rebinding.

THE FIX
-------
The clients here resolve the name ONCE, at the moment the connection is
opened, refuse the connection when any address is not public, and connect to
the address they checked. There is no second lookup to race. TLS still
verifies the certificate against the host NAME (httpcore passes the original
host as ``server_hostname``), so nothing about HTTPS changes. Every redirect
opens its connection through the same door, so every hop is checked too.

A configured proxy (HTTPS_PROXY and friends) is the one case this cannot
help: the proxy does its own lookup. Then the clients fall back to the plain
httpx behaviour, and the up-front check in ``validate_public_http_url`` is all
there is, exactly as before.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any

import httpcore
import httpx

from core import http_tls


class PrivateDestination(httpcore.ConnectError):
    """The host resolved to an address that is not on the public internet."""


def is_public_ip(text: str) -> bool:
    """True only for a global unicast address (the rule of validate_public_http_url)."""
    try:
        ip = ipaddress.ip_address(str(text).split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped                     # ::ffff:127.0.0.1 is 127.0.0.1
    # ``is_global`` also refuses the 100.64.0.0/10 shared range, where
    # Tailscale puts the person's own devices.
    return not (
        ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved
        or ip.is_multicast or ip.is_unspecified or not ip.is_global
    )


def resolve_public(host: str, port: int | None) -> list[str]:
    """Every address ``host`` has, or PrivateDestination if any is not public.

    ALL of them must be public, not just the first: a name with one public
    and one private address could be connected to either.
    """
    try:
        infos = socket.getaddrinfo(host, port or None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise httpcore.ConnectError(f"unresolvable_host: {host}") from exc
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise httpcore.ConnectError(f"unresolvable_host: {host}")
    for address in addresses:
        if not is_public_ip(address):
            raise PrivateDestination("private_or_reserved_destination")
    return addresses


class PinnedBackend(httpcore.NetworkBackend):
    """httpcore's normal sockets, but only to addresses checked a moment ago."""

    def __init__(self, inner: httpcore.NetworkBackend | None = None, resolver=resolve_public):
        self._inner = inner or httpcore.SyncBackend()
        self._resolve = resolver

    def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                    local_address: str | None = None, socket_options=None):
        last: Exception | None = None
        for address in self._resolve(host, port):
            try:
                return self._inner.connect_tcp(address, port, timeout=timeout,
                                               local_address=local_address,
                                               socket_options=socket_options)
            except httpcore.ConnectError as exc:
                last = exc
        raise last or httpcore.ConnectError(f"unreachable: {host}")

    def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):
        raise httpcore.ConnectError("unix sockets are not public addresses")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class AsyncPinnedBackend(httpcore.AsyncNetworkBackend):
    """The asyncio twin of PinnedBackend. The lookup runs on a worker thread."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None, resolver=resolve_public):
        self._inner = inner or httpcore.AnyIOBackend()
        self._resolve = resolver

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                          local_address: str | None = None, socket_options=None):
        import anyio
        addresses = await anyio.to_thread.run_sync(self._resolve, host, port)
        last: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(address, port, timeout=timeout,
                                                     local_address=local_address,
                                                     socket_options=socket_options)
            except httpcore.ConnectError as exc:
                last = exc
        raise last or httpcore.ConnectError(f"unreachable: {host}")

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):
        raise httpcore.ConnectError("unix sockets are not public addresses")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def _proxied() -> bool:
    """Would httpx send http(s) through a proxy from the environment?"""
    try:
        from httpx._utils import get_environment_proxies
        proxies = get_environment_proxies()
    except Exception:
        return False
    # NO_PROXY adds entries like ``all://*localhost`` with the value None:
    # those say where NOT to use a proxy, so they must not count as one.
    return any(value and key.startswith(("http://", "https://", "all://"))
               for key, value in proxies.items())


class _PinnedTransport(httpx.HTTPTransport):
    def __init__(self, backend: httpcore.NetworkBackend | None = None) -> None:
        context = http_tls.shared_context()
        super().__init__(verify=context, trust_env=False)
        self._pool = httpcore.ConnectionPool(
            ssl_context=context, http1=True, http2=False,
            network_backend=backend or PinnedBackend())


class _AsyncPinnedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        context = http_tls.shared_context()
        super().__init__(verify=context, trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=context, http1=True, http2=False,
            network_backend=backend or AsyncPinnedBackend())


def public_client(*, backend: httpcore.NetworkBackend | None = None, **kwargs: Any) -> httpx.Client:
    """An ``httpx.Client`` that only ever connects to public addresses."""
    if backend is None and _proxied():
        return httpx.Client(verify=http_tls.shared_context(), **kwargs)
    return httpx.Client(transport=_PinnedTransport(backend), **kwargs)


def public_async_client(*, backend: httpcore.AsyncNetworkBackend | None = None,
                        **kwargs: Any) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` that only ever connects to public addresses."""
    if backend is None and _proxied():
        return httpx.AsyncClient(verify=http_tls.shared_context(), **kwargs)
    return httpx.AsyncClient(transport=_AsyncPinnedTransport(backend), **kwargs)


__all__ = ["AsyncPinnedBackend", "PinnedBackend", "PrivateDestination", "is_public_ip",
           "public_async_client", "public_client", "resolve_public"]
