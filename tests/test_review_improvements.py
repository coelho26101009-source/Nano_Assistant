"""Improvements from the 2026-11-25 total review ("o que devia melhorar").

Each block here was written to fail on the code before the change.
"""
from __future__ import annotations

import pytest

from core.permission_manager import PermissionManager
from core.tool_execution import ToolExecutor


# ------------------------------------------------- coding helper and scripts

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("NANO_WORKSPACE_ROOT", str(root))
    return root


@pytest.fixture
def executor(tmp_path):
    manager = PermissionManager(policy_store_path=tmp_path / "policies.json")
    asked: list = []

    def approve(*args):
        asked.append(args)
        return True

    manager.confirmation_callback = approve
    tool_executor = ToolExecutor(permission_manager=manager)
    tool_executor.asked = asked
    return tool_executor


@pytest.mark.parametrize("name", ["run.bat", "Setup.PS1", "go.vbs", "x.lnk", "tool.py", "a.exe"])
def test_scripts_outside_the_project_are_refused_without_asking(workspace, executor, tmp_path, name):
    outside = tmp_path / "Desktop"
    outside.mkdir()
    target = outside / name

    result = executor.execute_tool("filesystem.write_file", {"path": str(target), "content": "echo hi"})

    assert result["success"] is False
    assert result["status"] == "permission_denied"
    assert not target.exists()
    assert executor.asked == []           # refused before anybody was asked


@pytest.mark.parametrize("name", ["run.bat.", "run.bat ", "run.bat::$DATA", "Run.Bat. . "])
def test_windows_name_tricks_do_not_hide_a_script(name):
    from core.tool_execution import _executable_extensions, _windows_suffix
    assert _windows_suffix(name) == ".bat"
    assert _windows_suffix(name) in _executable_extensions()


@pytest.mark.parametrize("name,suffix", [("notes.txt", ".txt"), (".bashrc", ""), ("README", "")])
def test_plain_names_keep_their_suffix(name, suffix):
    from core.tool_execution import _windows_suffix
    assert _windows_suffix(name) == suffix


def test_plain_text_outside_the_project_still_works_with_approval(workspace, executor, tmp_path):
    outside = tmp_path / "Desktop"
    outside.mkdir()
    target = outside / "notes.txt"

    result = executor.execute_tool("filesystem.write_file", {"path": str(target), "content": "ok"})

    assert result["success"] is True
    assert target.read_text(encoding="utf-8") == "ok"


def test_the_coding_helper_may_still_write_scripts_inside_the_project(workspace, executor):
    result = executor.execute_tool("filesystem.write_file",
                                   {"path": "scripts/build.py", "content": "print('ok')"})

    assert result["success"] is True
    assert (workspace / "scripts" / "build.py").exists()


# ------------------------------------------- pinned addresses (DNS rebinding)

import asyncio
import http.server
import threading

import httpcore
import httpx

from core import safe_http


class _Recorder(httpcore.NetworkBackend):
    """Stands in for real sockets: records where NANO tried to connect."""

    def __init__(self):
        self.inner = httpcore.SyncBackend()
        self.hosts: list[str] = []

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.hosts.append(host)
        return self.inner.connect_tcp(host, port, timeout=timeout,
                                      local_address=local_address, socket_options=socket_options)

    def sleep(self, seconds):
        self.inner.sleep(seconds)


@pytest.fixture
def local_server():
    seen: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append(self.headers.get("Host", ""))
            body = b"router admin page"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], seen
    finally:
        server.shutdown()


def test_a_name_that_rebinds_to_a_private_address_is_refused_at_connect():
    answers = iter([["93.184.216.34"], ["192.168.1.1"]])

    def rebinding_dns(host, port):
        addresses = next(answers)
        if not all(safe_http.is_public_ip(a) for a in addresses):
            raise safe_http.PrivateDestination("private_or_reserved_destination")
        return addresses

    recorder = _Recorder()
    backend = safe_http.PinnedBackend(inner=recorder, resolver=rebinding_dns)
    # The first lookup (the up-front check) said public; the connection makes
    # its own, which now says private, and is refused before any socket opens.
    assert rebinding_dns("evil.example", 80) == ["93.184.216.34"]
    with safe_http.public_client(backend=backend) as client:
        with pytest.raises(httpx.ConnectError):
            client.get("http://evil.example/")
    assert recorder.hosts == []


def test_the_connection_goes_to_the_address_that_was_checked(local_server):
    port, seen = local_server
    lookups: list[str] = []

    def dns(host, port_):
        lookups.append(host)
        return ["127.0.0.1"]               # pretend this is the public answer

    recorder = _Recorder()
    backend = safe_http.PinnedBackend(inner=recorder, resolver=dns)
    with safe_http.public_client(backend=backend) as client:
        response = client.get(f"http://public.example:{port}/")

    assert response.status_code == 200
    assert lookups == ["public.example"]   # looked up once, at connect time
    assert recorder.hosts == ["127.0.0.1"]  # connected to the checked address
    assert seen == [f"public.example:{port}"]  # the site still sees its own name


def test_real_lookups_refuse_loopback_and_private_names():
    with safe_http.public_client(backend=safe_http.PinnedBackend()) as client:
        with pytest.raises(httpx.ConnectError):
            client.get("http://localhost:9/")
        with pytest.raises(httpx.ConnectError):
            client.get("http://10.0.0.1:9/")


def test_the_async_client_is_pinned_too():
    def private(host, port):
        raise safe_http.PrivateDestination("private_or_reserved_destination")

    async def run():
        backend = safe_http.AsyncPinnedBackend(resolver=private)
        async with safe_http.public_async_client(backend=backend) as client:
            await client.get("https://example.com/")

    with pytest.raises(httpx.ConnectError):
        asyncio.run(run())


@pytest.mark.parametrize("address,public", [
    ("8.8.8.8", True), ("2606:4700:4700::1111", True),
    ("127.0.0.1", False), ("192.168.1.1", False), ("10.1.2.3", False),
    ("100.100.100.100", False), ("::ffff:127.0.0.1", False), ("169.254.1.1", False),
    ("fe80::1", False), ("not-an-ip", False),
])
def test_public_address_rule(address, public):
    assert safe_http.is_public_ip(address) is public


_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
               "http_proxy", "https_proxy", "all_proxy", "no_proxy")


def test_no_proxy_alone_keeps_the_pinning(monkeypatch):
    for name in _PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    assert safe_http._proxied() is False
    with safe_http.public_client() as client:
        assert isinstance(client._transport, safe_http._PinnedTransport)


def test_a_real_proxy_falls_back_to_plain_httpx(monkeypatch):
    for name in _PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    assert safe_http._proxied() is True


def test_every_tool_that_reads_the_web_uses_a_pinned_client():
    import inspect

    from core import browser_agent, research, tool_execution, weather
    from plugins import smart_life

    for module in (browser_agent, tool_execution, smart_life, weather):
        source = inspect.getsource(module)
        assert "httpx.get(" not in source and "httpx.AsyncClient(" not in source, module.__name__
        assert "httpx.Client(" not in source, module.__name__
    assert "public_async_client" in inspect.getsource(research._client)


def _host_of(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).hostname or ""


class _FakeRoute:
    def __init__(self, url):
        self.request = type("R", (), {"url": url})()
        self.outcome = None

    async def fallback(self):
        self.outcome = "continued"

    async def abort(self, reason=""):
        self.outcome = f"aborted:{reason}"


def test_the_headless_browser_refuses_requests_to_private_addresses(monkeypatch):
    from plugins import web_vision

    monkeypatch.setattr(web_vision, "_HOST_VERDICTS", {})
    monkeypatch.setattr(web_vision, "validate_public_http_url",
                        lambda url: (_host_of(url) == "public.example", ""))

    async def run(url):
        route = _FakeRoute(url)
        await web_vision._guard_request(route)
        return route.outcome

    assert asyncio.run(run("https://public.example/page")) == "continued"
    assert asyncio.run(run("http://192.168.1.1/admin")).startswith("aborted")
    assert asyncio.run(run("file:///C:/Windows/win.ini")).startswith("aborted")


class _FakeSocket:
    def __init__(self, url):
        self.url = url
        self.outcome = None

    def connect_to_server(self):
        self.outcome = "connected"
        return self

    async def close(self, code=None, reason=None):
        self.outcome = f"closed:{code}"


def test_the_headless_browser_refuses_websockets_to_private_addresses(monkeypatch):
    from plugins import web_vision

    monkeypatch.setattr(web_vision, "_HOST_VERDICTS", {})
    checked: list[str] = []

    def verdict(url):
        checked.append(url)
        return _host_of(url) == "public.example", ""

    monkeypatch.setattr(web_vision, "validate_public_http_url", verdict)

    async def run(url):
        socket = _FakeSocket(url)
        await web_vision._guard_web_socket(socket)
        return socket.outcome

    assert asyncio.run(run("wss://public.example/live")) == "connected"
    assert asyncio.run(run("ws://192.168.1.1/")) == "closed:1008"
    assert checked == ["https://public.example/live", "http://192.168.1.1/"]


def test_the_headless_browser_blocks_service_workers_and_guards_websockets():
    import inspect

    from plugins import web_vision
    source = inspect.getsource(web_vision._get_page)
    assert 'service_workers="block"' in source
    assert "route_web_socket" in source


# ------------------------------------------- the fast local model for short answers

from types import SimpleNamespace

from core import brain as brain_module
from core import ollama_service


def _brain_stub(fast="qwen3:1.7b", main="qwen3:8b"):
    stub = SimpleNamespace(ollama_fast_model=fast, ollama_model=main,
                           ollama_url="http://127.0.0.1:11434/api/chat")
    stub._FAST_LOCAL_TASKS = brain_module.Brain._FAST_LOCAL_TASKS
    stub.pick = lambda message, tools=None, continue_turn=False: brain_module.Brain._local_model_for(
        stub, message, tools, continue_turn=continue_turn)
    return stub


@pytest.fixture
def installed(monkeypatch):
    models = ["qwen3:8b", "qwen3:1.7b"]
    monkeypatch.setattr(ollama_service.STATUS, "peek",
                        lambda *a, **k: {"installed": list(models), "ollamaUp": True})
    return models


def test_short_answers_use_the_fast_model(installed):
    stub = _brain_stub()
    assert stub.pick("olá, tudo bem?") == "qwen3:1.7b"
    assert stub.pick("o que é um byte?") == "qwen3:1.7b"


def test_tools_long_work_and_tool_turns_keep_the_main_model(installed):
    stub = _brain_stub()
    assert stub.pick("abre o spotify", tools=[{"name": "pc_app_open"}]) == "qwen3:8b"
    assert stub.pick("analisa este código e corrige o erro") == "qwen3:8b"
    assert stub.pick("olá", continue_turn=True) == "qwen3:8b"


def test_no_fast_model_or_one_that_is_not_installed_changes_nothing(installed):
    assert _brain_stub(fast="").pick("olá") == "qwen3:8b"
    installed.remove("qwen3:1.7b")
    assert _brain_stub().pick("olá") == "qwen3:8b"


def test_the_setting_is_allowed_and_overlaid(tmp_path, monkeypatch):
    from core import user_settings
    assert "local_fast_model" in user_settings.ALLOWED_KEYS
    assert user_settings._valid_value("local_fast_model", "qwen3:1.7b")
    assert user_settings._valid_value("local_fast_model", "")
    assert not user_settings._valid_value("local_fast_model", 3)
    assert not user_settings._valid_value("local_fast_model", "a\nb")
    monkeypatch.setattr(user_settings, "all_settings", lambda: {"local_fast_model": "qwen3:1.7b"})
    config: dict = {}
    user_settings.apply_overlay(config)
    assert config["local"]["fast_model"] == "qwen3:1.7b"


def test_turning_the_fast_model_off_wins_over_a_choice_still_being_checked(monkeypatch):
    import threading

    import core.main as main

    saved: list = []
    monkeypatch.setattr(main.user_settings, "set_value",
                        lambda key, value: saved.append((key, value)) or {"ok": True})
    monkeypatch.setattr(main.brain, "local_enabled", True)
    monkeypatch.setattr(main.brain, "ollama_fast_model", "")
    release = threading.Event()
    checking = threading.Event()

    def slow_status(*args, **kwargs):
        checking.set()
        release.wait(5)
        return {"state": "READY", "ollamaUp": True, "installed": ["qwen3:1.7b"]}

    monkeypatch.setattr(main, "_off_hub", slow_status)
    outcome: dict = {}
    worker = threading.Thread(target=lambda: outcome.update(main.set_local_fast_model("qwen3:1.7b")))
    worker.start()
    assert checking.wait(5)
    assert main.set_local_fast_model("")["ok"] is True     # "Desligado", chosen later
    release.set()
    worker.join(5)

    assert outcome.get("error") == "superseded"
    assert saved == [("local_fast_model", "")]
    assert main.brain.ollama_fast_model == ""
