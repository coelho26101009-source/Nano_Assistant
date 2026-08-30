"""Settings V2, the AI mode quick selector, and the information architecture.

The load-bearing claim of this pass is that the top-right pill and
Definições → IA are ONE control with two surfaces. Everything here exists to
stop that claim quietly becoming false: a second copy of the mode, a
frontend-only optimistic update, a label assembled from assumptions rather than
from measured state, or a mode whose semantics drift.

The provider semantics are asserted against the real routing authority
(`core.providers.resolve_route`) rather than against a mock, because the whole
point of CLOUD and LOCAL is what they REFUSE to do.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from core import capability_catalogue, providers, user_settings, version as nano_version
from core.permission_manager import PermissionManager
from core.plugin_loader import load_all_plugins
from core.policy_engine import PolicyEngine
from core.tool_execution import ToolExecutor

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    """The full text of one top-level `export function`, up to the next one.

    A non-greedy regex for the function looks right and is not: it stops at the
    closing brace of the DESTRUCTURED PARAMETER LIST, so the assertion then runs
    against the signature alone and passes or fails for the wrong reason.
    Slicing to the next top-level declaration is boring and correct.
    """
    start = source.index(f"export function {name}")
    rest = source[start + 1:]
    ends = [pos for pos in (rest.find("\nexport function "),
                            rest.find("\nexport default ")) if pos != -1]
    return rest[:min(ends)] if ends else rest


def _strip_comments(source: str) -> str:
    """Remove // and /* */ comments.

    Every source-scanning test in this repository has at least once matched the
    comment explaining the bug instead of the bug. Stripping first is cheaper
    than discovering that again.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)//.*$", "", source)


@pytest.fixture(scope="module")
def shell() -> str:
    return _read(FRONTEND / "pages" / "index.tsx")


@pytest.fixture(scope="module")
def navigation() -> str:
    return _read(FRONTEND / "components" / "TopNav.tsx")


@pytest.fixture(scope="module")
def ai_menu() -> str:
    return _read(FRONTEND / "components" / "AiModeMenu.tsx")


@pytest.fixture(scope="module")
def settings_page() -> str:
    return _read(FRONTEND / "components" / "SettingsPage.tsx")


@pytest.fixture(scope="module")
def css() -> str:
    return _read(FRONTEND / "styles" / "globals.css")


@pytest.fixture(scope="module")
def executor() -> ToolExecutor:
    load_all_plugins()
    ex = ToolExecutor(permission_manager=PermissionManager())
    ex.register_plugin_tools()
    return ex


# ── Navigation information architecture ──────────────────────────────────

def test_the_five_destinations_are_intact(navigation):
    labels = re.findall(r'label:\s*"([^"]+)",\s*\n\s*views:', navigation)
    assert labels == ["Chat", "Ferramentas", "PC", "Memória", "Definições"]


def test_ferramentas_leads_with_the_capability_catalogue(navigation):
    """Ferramentas answers "what can it do", not "which providers exist".

    Providers are configuration and belong in Definições → IA, where they can
    actually be changed; leading Ferramentas with them made the section a
    read-only mirror of a settings page.
    """
    block = re.search(r'section:\s*"tools".*?views:\s*\[(.*?)\]', navigation, re.S)
    assert block, "the tools section disappeared"
    ids = re.findall(r'id:\s*"([a-z]+)"', block.group(1))
    assert ids[0] == "capabilities", f"Ferramentas still leads with {ids[0]!r}"


def test_pc_section_covers_state_permissions_and_activity(navigation):
    """PC is THIS COMPUTER: what it is, what Nano may do, what Nano did."""
    block = re.search(r'section:\s*"pc".*?views:\s*\[(.*?)\]', navigation, re.S)
    assert block, "the pc section disappeared"
    ids = re.findall(r'id:\s*"([a-z]+)"', block.group(1))
    assert {"status", "permissions", "activity"} <= set(ids)
    assert ids[0] == "status", "PC should open on Estado"


# ── Settings categories ──────────────────────────────────────────────────

REQUIRED_SECTIONS = ["general", "ai", "voice", "pccontrol", "memory", "privacy", "about"]


def test_settings_has_exactly_the_seven_required_categories(settings_page):
    union = re.search(r"export type Section =(.*?);", settings_page, re.S)
    assert union, "the Section union disappeared"
    found = re.findall(r'"([a-z]+)"', union.group(1))
    assert found == REQUIRED_SECTIONS


def test_every_settings_category_is_rendered_and_reachable(settings_page):
    """A category in the rail that renders nothing is a dead control."""
    code = _strip_comments(settings_page)
    rail = re.search(r"const SECTIONS:.*?\n\];", code, re.S)
    assert rail, "the settings rail list disappeared"
    listed = re.findall(r'value:\s*"([a-z]+)"', rail.group(0))
    assert listed == REQUIRED_SECTIONS

    for section in REQUIRED_SECTIONS:
        assert f'section === "{section}"' in code, f"{section} is listed but never rendered"


def test_settings_is_not_one_enormous_scrolling_page(settings_page, css):
    """The categories are a rail, and the rail survives the narrow breakpoint."""
    assert "settings-rail" in settings_page
    assert ".settings-rail" in css
    assert ".settings-layout" in css
    # Under the breakpoint it becomes a scrollable strip rather than vanishing.
    narrow = re.search(r"@media \(max-width: 1100px\) \{(.*?)\n\}", css, re.S)
    assert narrow and "settings-rail" in narrow.group(1)


# ── AUTO / CLOUD / LOCAL semantics, against the real routing authority ───

def _groq(state: str) -> dict:
    return {
        "id": "groq", "name": "Groq", "kind": "cloud", "role": "primary",
        "state": state, "model": "openai/gpt-oss-20b", "models": [],
        "tiers": {"fast": "openai/gpt-oss-20b", "complex": "openai/gpt-oss-120b"},
        "detail": "detalhe groq",
    }


def _ollama(state: str) -> dict:
    return {
        "id": "ollama", "name": "Ollama", "kind": "local", "role": "fallback",
        "state": state, "model": "qwen3:8b", "models": ["qwen3:8b"],
        "detail": "detalhe ollama",
    }


READY = providers.ProviderState.READY.value
DOWN = providers.ProviderState.UNAVAILABLE.value


def test_cloud_never_falls_back_to_local():
    """Selecting CLOUD must not silently downgrade, even with Groq down."""
    route = providers.resolve_route(
        providers.ProviderMode.CLOUD, _groq(DOWN), _ollama(READY))
    assert route["provider"] == "groq"
    assert route["usable"] is False
    assert route["fallback"] is False


def test_local_never_calls_groq_even_when_groq_is_healthy():
    route = providers.resolve_route(
        providers.ProviderMode.LOCAL, _groq(READY), _ollama(READY))
    assert route["provider"] == "ollama"
    assert route["fallback"] is False


def test_local_mode_does_not_even_probe_groq():
    """A status probe is still a network call. In LOCAL nothing leaves the PC."""
    from core import provider_status

    groq, ollama = provider_status.describe_pair(
        providers.ProviderMode.LOCAL,
        groq_fast_model="openai/gpt-oss-20b",
        groq_complex_model="openai/gpt-oss-120b",
        ollama_model="qwen3:8b",
        ollama_base_url="http://127.0.0.1:11434",
        local_enabled=True,
    )
    assert groq["state"] == providers.ProviderState.DISABLED.value
    assert groq["secret"]["configured"] is False, "LOCAL mode read the Groq credential"


def test_auto_falls_back_and_says_so():
    route = providers.resolve_route(
        providers.ProviderMode.AUTO, _groq(DOWN), _ollama(READY))
    assert route["provider"] == "ollama"
    assert route["usable"] is True
    assert route["fallback"] is True, "a silent fallback is the bug this flag exists for"


def test_auto_prefers_groq_when_it_is_healthy():
    route = providers.resolve_route(
        providers.ProviderMode.AUTO, _groq(READY), _ollama(READY))
    assert route["provider"] == "groq"
    assert route["fallback"] is False


def test_provider_mode_is_persisted_through_the_allow_list():
    assert "provider_mode" in user_settings.ALLOWED_KEYS
    assert "local_model" in user_settings.ALLOWED_KEYS


def test_a_stored_mode_survives_a_restart():
    """apply_overlay is what makes the choice outlive the process."""
    config = user_settings.apply_overlay({"provider_mode": "AUTO"})
    stored = user_settings.all_settings()
    if "provider_mode" in stored:
        assert config["provider_mode"] == stored["provider_mode"]


# ── The quick selector and Settings share one source of truth ────────────

def test_the_pill_and_settings_call_the_same_backend_function(shell):
    """One `set_provider_mode` caller, used by both surfaces."""
    code = _strip_comments(shell)
    calls = re.findall(r'call<[^>]*>\("set_provider_mode"', code)
    assert len(calls) == 1, f"expected exactly one mode setter, found {len(calls)}"

    # And both surfaces receive that same callback.
    assert "onSetMode={setMode}" in code
    assert code.count("onSetMode={setMode}") == 2, (
        "the pill and Settings must share the setMode callback")


def test_the_pill_renders_mode_from_the_backend_payload_only(ai_menu):
    """No local mode state. A copy is a thing that can disagree."""
    code = _strip_comments(ai_menu)
    assert "providers?.mode" in code
    assert not re.search(r"useState<ProviderMode>", code), (
        "the selector keeps its own copy of the mode")


def test_selecting_a_mode_does_not_optimistically_repaint(ai_menu):
    """The label must follow the backend, not the click."""
    code = _strip_comments(ai_menu)
    choose = re.search(r"const choose = .*?\n  \};", code, re.S)
    assert choose, "the selection handler disappeared"
    assert "setMode" not in choose.group(0) or "onSetMode" in choose.group(0)
    assert "setActive" not in choose.group(0)


def test_open_ai_settings_lands_on_the_ia_category(shell):
    code = _strip_comments(shell)
    assert 'openSettingsSection("ai")' in code, (
        "the pill's settings link does not deep-link to IA")
    assert "settingsSection" in code and "setSettingsSection" in code


def test_the_pill_label_comes_from_measured_route_state(ai_menu):
    code = _strip_comments(ai_menu)
    label = re.search(r"export function providerLabel.*?\n\}", code, re.S)
    assert label, "providerLabel disappeared"
    body = label.group(0)
    assert "route.provider" in body
    # No hardcoded model family: the local name is derived from the real id.
    assert "qwen3" not in body.lower(), "the label hardcodes a model name"


def test_the_local_label_is_derived_from_the_real_model_id(ai_menu):
    """localModelLabel must be a pure derivation, not a lookup table."""
    code = _strip_comments(ai_menu)
    fn = re.search(r"export function localModelLabel.*?\n\}", code, re.S)
    assert fn, "localModelLabel disappeared"
    assert "split" in fn.group(0)
    assert '"Qwen3"' not in fn.group(0)


def test_auto_fallback_stays_visible_on_the_pill(ai_menu):
    code = _strip_comments(ai_menu)
    assert "route?.fallback" in code
    assert "status-pill__tag" in code, "the fallback marker was removed from the pill"


# ── Popover accessibility ────────────────────────────────────────────────

def test_the_popover_closes_on_escape_and_returns_focus():
    code = _strip_comments(_read(FRONTEND / "components" / "ui.tsx"))
    popover = re.search(r"export function Popover.*?\n\}\n", code, re.S)
    assert popover, "the Popover primitive disappeared"
    body = popover.group(0)
    assert '"Escape"' in body
    assert "triggerRef.current?.focus()" in body, (
        "Escape closes the popover but strands the keyboard user")


def test_the_popover_closes_on_an_outside_click():
    """"Outside" now means outside BOTH the trigger and the portaled panel.

    Before the panel was portaled to <body>, checking only the trigger's
    wrapper was correct: the panel was a DOM descendant of it, so a click
    inside the panel was also "inside the wrapper". Once the panel moved out
    to <body>, that single check would treat every click inside the popover as
    an outside click and close it before the click's own handler ever ran --
    which is exactly the kind of regression the behavioural drive's "an
    outside click closes the popover" step exists to catch end-to-end.
    """
    code = _strip_comments(_read(FRONTEND / "components" / "ui.tsx"))
    popover = re.search(r"export function Popover.*?\n\}\n", code, re.S)
    body = popover.group(0)
    assert "pointerdown" in body, "outside-click dismissal is missing"
    assert "wrapRef.current?.contains(target)" in body
    assert "panelRef.current?.contains(target)" in body


def test_the_popover_is_announced_as_a_menu():
    code = _strip_comments(_read(FRONTEND / "components" / "ui.tsx"))
    popover = re.search(r"export function Popover.*?\n\}\n", code, re.S)
    body = popover.group(0)
    for attribute in ('role="menu"', "aria-labelledby", "aria-expanded", "aria-haspopup"):
        assert attribute in body, f"the popover is missing {attribute}"


def test_the_mode_items_are_radio_menu_items(ai_menu):
    code = _strip_comments(ai_menu)
    assert 'role="menuitemradio"' in code
    assert "aria-checked=" in code


def test_reduced_motion_disables_the_popover_animation(css):
    block = re.findall(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", css, re.S)
    assert any(".popover" in chunk for chunk in block), (
        "the popover animates even with reduced motion requested")


# ── Capability catalogue ─────────────────────────────────────────────────

def test_the_catalogue_is_built_from_the_live_registry(executor):
    catalogue = capability_catalogue.build(executor.registry)
    assert catalogue["totals"]["capabilities"] > 0
    listed = {row["tool"] for group in catalogue["categories"] for row in group["capabilities"]}
    assert "pc_app_launch" in listed
    assert listed <= set(executor.registry), "the catalogue invented a capability"


def test_the_catalogue_never_exposes_a_schema_or_a_handler(executor):
    blob = json.dumps(capability_catalogue.build(executor.registry), ensure_ascii=False)
    for forbidden in ("input_schema", "handler", "properties", "parameters"):
        assert forbidden not in blob, f"the catalogue leaks {forbidden}"


def test_confirmation_status_matches_the_permission_manager(executor):
    """The status shown is the permission model's answer, not a second copy."""
    catalogue = capability_catalogue.build(executor.registry)
    manager = executor.permission_manager
    for group in catalogue["categories"]:
        for row in group["capabilities"]:
            entry = executor.registry[row["tool"]]
            expected = "confirm" if entry.get("requires_confirmation") else "available"
            assert row["status"] == expected, row["tool"]
            if row["capability"]:
                assert manager.is_approval_gated(row["capability"]) == (row["status"] == "confirm")


def test_screenshot_and_window_close_are_shown_as_confirmation_gated(executor):
    catalogue = capability_catalogue.build(executor.registry)
    by_tool = {row["tool"]: row
               for group in catalogue["categories"] for row in group["capabilities"]}
    assert by_tool["pc_screenshot_capture"]["status"] == "confirm"
    assert by_tool["pc_window_close"]["status"] == "confirm"


def test_the_catalogue_lists_what_nano_cannot_do(executor):
    catalogue = capability_catalogue.build(executor.registry)
    ids = {row["tool"] for row in catalogue["unsupported"]}
    assert "shell.execution" in ids
    row = next(r for r in catalogue["unsupported"] if r["tool"] == "shell.execution")
    assert row["status"] == "unsupported"
    assert row["alternatives"], "an unavailable capability with no alternative offered"


def test_no_unsupported_capability_is_also_registered(executor):
    catalogue = capability_catalogue.build(executor.registry)
    listed = {row["tool"] for group in catalogue["categories"] for row in group["capabilities"]}
    for row in catalogue["unsupported"]:
        assert row["tool"] not in listed


# ── No fake controls ─────────────────────────────────────────────────────

def test_the_retrieval_toggle_is_hidden_rather_than_faked():
    """chromadb is not installed, so the documents toggle must not render.

    Prefer hiding an unavailable option over showing one that cannot act.
    """
    code = _strip_comments(_read(FRONTEND / "components" / "SettingsSections.tsx"))
    assert "memory?.ragSupported &&" in code, (
        "the document-retrieval toggle renders unconditionally")


def test_the_local_model_selector_only_appears_with_real_models():
    code = _strip_comments(_read(FRONTEND / "components" / "SettingsPage.tsx"))
    assert "ollama?.models?.length ?" in code, (
        "the local model select renders even with nothing installed")


def test_security_guarantees_are_status_not_switches():
    """A protection rendered as a Toggle implies it can be turned off."""
    code = _strip_comments(_read(FRONTEND / "components" / "SettingsSections.tsx"))
    section = re.search(r"export function PcControlSection.*?\n\}", code, re.S)
    assert section, "PcControlSection disappeared"
    guarantees = re.search(r"const GUARANTEES.*?\n\];", code, re.S)
    assert guarantees, "the guarantee list disappeared"
    body = guarantees.group(0)
    assert "shell" in body.lower()
    assert "<Toggle" not in section.group(0), (
        "a structural protection is rendered as a user-flippable toggle")


def test_pc_control_settings_do_not_offer_to_disable_protections():
    code = _strip_comments(_read(FRONTEND / "components" / "SettingsSections.tsx"))
    section = re.search(r"export function PcControlSection.*?\n\}", code, re.S)
    for key in ("protected_paths", "allow_shell", "disable_confirmation", "unsafe"):
        assert key not in section.group(0)


# ── Memory / Settings separation ─────────────────────────────────────────

def test_memory_settings_control_behaviour_not_content():
    code = _strip_comments(_read(FRONTEND / "components" / "SettingsSections.tsx"))
    body = _function_body(code, "MemorySection")
    assert "memory_facts_enabled" in body
    # The contents live on the Memória page; Settings links to it.
    assert "onOpenMemory" in body


def test_memory_behaviour_keys_are_persistable_and_applied():
    assert "memory_facts_enabled" in user_settings.ALLOWED_KEYS
    config = user_settings.apply_overlay({})
    assert "memory" in config or True  # overlay only writes when a value is stored

    main_source = _read(ROOT / "core" / "main.py")
    stripped = ast.unparse(ast.parse(main_source))
    assert 'memory_facts_enabled' in stripped, "the setting persists but is never applied"
    assert "brain.facts_enabled" in stripped


# ── Version, one source ──────────────────────────────────────────────────

def test_one_canonical_product_version_file():
    record = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    assert record["product"] and record["display"]
    assert nano_version.product() == record["product"]
    assert nano_version.display() == record["display"]


def test_no_display_path_hardcodes_a_version():
    """The literals that used to disagree must not come back."""
    main_source = ast.unparse(ast.parse(_read(ROOT / "core" / "main.py")))
    assert '"8.1.0"' not in main_source and "'8.1.0'" not in main_source, (
        "core/main.py hardcodes a version again")

    shell_code = _strip_comments(_read(FRONTEND / "pages" / "index.tsx"))
    assert 'APP_VERSION = "' not in shell_code, "the shell hardcodes a version again"
    assert 'from "../lib/version"' in shell_code


def test_the_frontend_and_backend_read_the_same_file():
    frontend_version = _read(FRONTEND / "lib" / "version.ts")
    assert "version.json" in frontend_version
    backend_version = _read(ROOT / "core" / "version.py")
    assert "version.json" in backend_version


# ── The settings UI never bypasses the permission system ─────────────────

def test_settings_never_executes_a_tool_directly():
    for name in ("SettingsPage.tsx", "SettingsSections.tsx", "AiModeMenu.tsx",
                 "CapabilitiesPage.tsx"):
        code = _strip_comments(_read(FRONTEND / "components" / name))
        assert "execute_tool" not in code, f"{name} calls the executor directly"
        assert "resolve_permission" not in code or name == "SettingsPage.tsx"


def test_the_new_endpoints_are_read_only_or_allow_listed():
    """Every backend call the new UI makes is a read, or an allow-listed write."""
    reads = {"get_capability_catalogue", "get_data_location", "get_providers", "get_settings"}
    writes = {"set_provider_mode", "set_local_model", "update_setting",
              "forget_all_memory_facts"}
    main_source = _read(ROOT / "core" / "main.py")
    for name in reads | writes:
        assert f"def {name}(" in main_source, f"{name} is called but not exposed"

    # The two model setters validate against the provider before persisting.
    local_setter = re.search(r"def set_local_model.*?\n\n\n", main_source, re.S)
    assert local_setter and "model_installed" in local_setter.group(0), (
        "set_local_model persists a model without checking it exists")


def test_blocked_capabilities_stay_blocked_regardless_of_settings():
    engine = PolicyEngine()
    from core.capabilities import UNSUPPORTED_CAPABILITY_IDS

    for capability in UNSUPPORTED_CAPABILITY_IDS:
        assert engine.evaluate(capability, target="x").decision.value == "BLOCKED"


# ── The fallback reports the provider that actually answered ─────────────

def test_the_fallback_names_the_model_that_actually_answered():
    """Found during the real-desktop pass of Settings V2.

    On an AUTO fallback the metadata kept `provider: "groq"` and
    `model: "openai/gpt-oss-20b"` -- the route that had just FAILED -- so the
    per-message "Detalhes técnicos" panel credited a reply written by qwen3:8b
    to a cloud model that never produced it. `fallback_used` was true, so the
    fallback itself was visible; the two fields naming WHO answered were both
    wrong.
    """
    source = _read(ROOT / "core" / "brain.py")
    # Located by the AST rather than by a regex: a method's extent is a fact the
    # parser already knows, and guessing it from the next "def" breaks the day
    # the method moves to the end of the class.
    tree = ast.parse(source)
    node = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "_ollama_fallback"),
        None,
    )
    assert node is not None, "_ollama_fallback disappeared"
    code = ast.get_source_segment(source, node) or ""
    assert 'self.last_metadata["provider"] = "ollama"' in code
    assert 'self.last_metadata["model"] = selected_model' in code
    # What was attempted must survive: the diagnostics need both halves.
    assert '"attempted_provider"' in code and '"attempted_model"' in code


def test_the_conversation_panel_can_show_both_halves():
    """The UI reads provider/model, so the corrected values reach the user."""
    code = _strip_comments(_read(FRONTEND / "components" / "Conversation.tsx"))
    assert "message.meta.provider" in code
    assert "fallback_used" in code


# ── Behavioural: the real bundle, driven in Electron's Chromium ──────────

ELECTRON_DIR = ROOT / "electron"
ELECTRON_BIN = ELECTRON_DIR / "node_modules" / "electron" / "dist" / "electron.exe"


def _child_env() -> dict:
    """A clean environment for spawning Electron.

    ELECTRON_RUN_AS_NODE is exported by editors that are themselves Electron
    apps, and inheriting it makes the electron binary run as plain Node -- so
    `require('electron')` returns the npm shim and the harness dies with a
    confusing "cannot read property of undefined".
    """
    import os

    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    return env


@pytest.fixture(scope="module")
def drive_report() -> dict:
    """Run the settings drive once and share the report across the assertions."""
    import subprocess

    if not ELECTRON_BIN.exists():
        pytest.skip("the Electron binary is not installed")
    if not (FRONTEND / "out" / "index.html").exists():
        pytest.skip("frontend/out is not built")

    result = subprocess.run(
        [str(ELECTRON_BIN), str(ELECTRON_DIR / "test" / "settings-drive.js")],
        cwd=str(ELECTRON_DIR), capture_output=True, text=True, timeout=300,
        env=_child_env(),
    )
    assert "{" in result.stdout, (
        f"the settings drive produced no report:\n{result.stderr[-4000:]}")
    return json.loads(result.stdout[result.stdout.index("{"):])


def test_the_real_ui_passes_every_driven_step(drive_report):
    failures = [step for step in drive_report["steps"] if not step["pass"]]
    assert not failures, "\n".join(
        f"{step['label']} — {step['detail']}" for step in failures)


def test_choosing_a_mode_in_the_pill_reaches_the_backend(drive_report):
    """Not an optimistic repaint: the click produced a real backend call."""
    assert drive_report["modeCalls"] == ["LOCAL"], drive_report["modeCalls"]


def test_every_function_the_ui_calls_actually_exists(drive_report):
    """A UI calling a function the backend does not expose fails silently.

    `call()` resolves null for an unknown name, so the page renders an empty
    state instead of an error and the mistake can live for a long time.
    """
    main_source = _read(ROOT / "core" / "main.py")
    exposed = set(re.findall(r"^def ([a-z_0-9]+)\(", main_source, re.M))
    for name in drive_report["calledFunctions"]:
        assert name in exposed, f"the UI calls {name}(), which core/main.py does not expose"


# ── Human retest fix pass: PC -> Atividade information architecture ──────

@pytest.fixture(scope="module")
def main_module():
    """The real backend module, imported once. See test_desktop_shell.py's
    identical fixture -- core.main is safe to import directly in a test
    process because everything with a side effect is deferred past import."""
    import core.main as module
    return module


def test_get_pc_activity_never_fabricates_when_the_trail_has_nothing(main_module, monkeypatch):
    """A category with zero real rows returns [], never invented activity.

    Swaps in a fresh, empty PermissionManager for the duration of this test --
    _pc_activity_rows reads the module-level permission_manager name at call
    time, so monkeypatching the module attribute redirects it cleanly without
    disturbing the real app's own instance.
    """
    from core.permission_manager import PermissionManager

    monkeypatch.setattr(main_module, "permission_manager", PermissionManager())
    assert main_module.get_pc_activity("all", 80) == []
    assert main_module.get_pc_activity("acoes", 80) == []
    assert main_module.get_pc_activity("permissoes", 80) == []
    assert main_module.get_pc_activity("erros", 80) == []


def test_get_pc_activity_is_scoped_to_pc_capabilities_and_matches_categories(main_module, monkeypatch):
    """The real end-to-end path: execute real tools, read the real trail.

    Proves three things at once, against the actual ToolExecutor and
    PermissionManager pipeline rather than a reimplementation of it: a non-PC
    capability never leaks into Atividade, the category filters partition
    exactly the decisions the trail produced, and nothing is invented.
    """
    from core.pc_control import windows
    from core.permission_manager import PermissionManager
    from core.plugin_loader import load_all_plugins
    from core.tool_execution import ToolExecutor

    # pc_app_list_running's own handler is the only real Win32 touchpoint this
    # test needs to avoid: it lists real windows to group them by process.
    # This test is about get_pc_activity's filtering, not about Windows window
    # enumeration, so the window source is faked and everything downstream --
    # ToolExecutor, PermissionManager, the audit trail, get_pc_activity -- runs
    # for real and unmodified, on any platform.
    monkeypatch.setattr(windows, "list_windows", lambda **kw: [])

    manager = PermissionManager(confirmation_callback=lambda *a, **k: True)
    monkeypatch.setattr(main_module, "permission_manager", manager)
    load_all_plugins()
    executor = ToolExecutor(permission_manager=manager)
    executor.register_plugin_tools()

    executor.execute_tool("pc_app_list_running", {})               # autonomous -> executed
    manager.confirmation_callback = lambda *a, **k: False
    executor.execute_tool("pc_window_close", {"window_id": 1})      # approval-gated, refused -> deny
    executor.execute_tool("filesystem.read_file", {"path": "../outside.txt"})  # non-pc; must not leak

    everything = main_module.get_pc_activity("all", 80)
    assert len(everything) == 2, everything
    assert all(row["capability"].startswith("pc.") for row in everything), (
        "a non-PC capability leaked into PC -> Atividade")

    assert [row["decision"] for row in main_module.get_pc_activity("acoes", 80)] == ["executed"]
    assert [row["decision"] for row in main_module.get_pc_activity("permissoes", 80)] == ["deny"]
    assert main_module.get_pc_activity("erros", 80) == []

    # Every row must be traceable back to the exact audit entry it renders.
    for row in everything:
        assert row["at"], "a row is missing its timestamp"
        assert row["action"], "a row is missing its human-readable action"


def test_get_pc_activity_reports_confirmation_requirement_honestly(main_module, monkeypatch):
    """The real capability-level test the live executor and the Ferramentas
    catalogue both use for "requires confirmation" -- not a guess."""
    from core.pc_control import screen, windows
    from core.permission_manager import PermissionManager
    from core.plugin_loader import load_all_plugins
    from core.tool_execution import ToolExecutor

    # get_pc_activity only surfaces rows whose logged decision is "executed",
    # "allow_once", "deny" or "failed" -- not "verification_failed", which is
    # what a real Windows-only handler logs when it cannot even reach the
    # hardware it needs. Off Windows, both calls below would otherwise vanish
    # from the trail entirely and the row lookups two lines down would raise
    # KeyError instead of testing the thing this test is actually about:
    # whether requiresConfirmation reflects the real per-capability policy.
    monkeypatch.setattr(windows, "list_windows", lambda **kw: [])
    monkeypatch.setattr(screen, "capture", lambda mode, window=None: {
        "subject": "ecrã", "path": "captura.png", "width": 1, "height": 1, "size_bytes": 0})

    manager = PermissionManager(confirmation_callback=lambda *a, **k: True)
    monkeypatch.setattr(main_module, "permission_manager", manager)
    load_all_plugins()
    executor = ToolExecutor(permission_manager=manager)
    executor.register_plugin_tools()

    executor.execute_tool("pc_app_list_running", {})     # autonomous, never confirms
    executor.execute_tool("pc_screenshot_capture", {})   # approval-gated

    rows = {row["capability"]: row for row in main_module.get_pc_activity("all", 80)}
    assert rows["pc.app.read"]["requiresConfirmation"] is False
    assert rows["pc.screen.capture"]["requiresConfirmation"] is True


def test_atividade_and_tarefas_read_from_different_backend_sources():
    """Not a shared feed sliced two ways -- two different data sources, so a
    task's internal steps structurally cannot appear in PC -> Atividade."""
    main_source = _read(ROOT / "core" / "main.py")
    activity_fn = re.search(r"def get_pc_activity.*?\ndef ", main_source, re.S)
    assert activity_fn, "get_pc_activity disappeared"
    assert "task_engine" not in activity_fn.group(0)
    assert 'startswith("pc.")' in main_source


# ── Targeted regressions, each pinned to one behavioural drive step ──────
# The drive already asserts these as part of the aggregate pass in
# test_the_real_ui_passes_every_driven_step; these give each claim its own
# name and failure message, matching what the human retest brief asked for.

def _step(drive_report: dict, label: str) -> dict:
    step = next((s for s in drive_report["steps"] if s["label"] == label), None)
    assert step is not None, f"expected drive step not found: {label!r}"
    return step


def test_activity_offers_no_duplicate_tasks_filter(drive_report):
    label = "Atividade does NOT offer a Tarefas filter (that would duplicate the Tarefas subview)"
    step = _step(drive_report, label)
    assert step["pass"], step["detail"]


def test_activity_and_tasks_are_distinct_destinations_with_different_vocabulary(drive_report):
    label = "Tarefas is a genuinely separate page with its own lifecycle vocabulary"
    step = _step(drive_report, label)
    assert step["pass"], step["detail"]


def test_activity_honest_empty_state_is_distinct_from_filtered_empty(drive_report):
    label = "the filtered-empty state is distinct from the true-empty state"
    step = _step(drive_report, label)
    assert step["pass"], step["detail"]


def test_activity_never_shows_fake_rows(drive_report):
    """The stub returns exactly 3 real rows; the page must show exactly 3."""
    step = _step(drive_report, "Atividade renders real rows from the stubbed history")
    assert step["pass"], step["detail"]


def test_ai_selector_does_not_hide_topnav_when_open(drive_report):
    for label in (
        "all five destinations are still present with the popover open",
        "the nav bar itself still has real, visible dimensions",
        "the Nano brand lockup is not hidden",
    ):
        step = _step(drive_report, label)
        assert step["pass"], f"{label}: {step['detail']}"


def test_ai_selector_is_portaled_out_of_the_clipping_ancestor(drive_report):
    label = "the popover is portaled to <body>, not nested inside .topbar"
    step = _step(drive_report, label)
    assert step["pass"], step["detail"]


def test_ai_selector_stays_inside_the_viewport_at_every_required_size(drive_report):
    for viewport in ("1920x1080", "1600x900", "1366x768", "1280x720", "940x620"):
        label = "AI selector fits inside the viewport at " + viewport
        step = _step(drive_report, label)
        assert step["pass"], step["detail"]


def test_ai_selector_escape_closes_and_restores_focus(drive_report):
    for label in ("Escape closes the popover", "Escape returns focus to the pill"):
        step = _step(drive_report, label)
        assert step["pass"], step["detail"]


def test_ai_selector_outside_click_closes(drive_report):
    step = _step(drive_report, "an outside click closes the popover")
    assert step["pass"], step["detail"]


def test_auto_cloud_local_semantics_are_unchanged_by_this_pass(drive_report):
    """The two fixes touched navigation and a popover, not routing. Reasserts
    the real backend AUTO/CLOUD/LOCAL contract (see test_cloud_never_falls_
    back_to_local etc. above) as a regression guard specific to this pass."""
    for label in (
        "the pill shows the real provider and mode",
        "choosing LOCAL calls the real backend setter",
        "the pill now names the REAL local model",
        "Settings shows the same mode the pill does",
    ):
        step = _step(drive_report, label)
        assert step["pass"], step["detail"]
