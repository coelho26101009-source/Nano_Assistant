"""Contract tests for the Nano V1 product shell.

These pin the promises the interface makes to the user, each one traced to a
real defect observed in the running app:

  * the sidebar badge read 68 because it counted every task ever created;
  * the inspector showed a CANCELLED task as the "current" one;
  * the layout clipped at 100% zoom, so the window had to be zoomed out;
  * raw `/think` control tokens reached the chat bubble;
  * controls existed that did nothing when clicked.

They read source rather than render it: there is no JS test runner in this
project, and a contract that holds in the shipped file is the one that matters.
The layout claims are the exception: those are measured for real, in real
Chromium, by electron/test/render-check.js.

The V2 shell replaced the left navigation rail with a top bar of five sections,
turned the left column into the conversation list, and dissolved the fixed
inspector column into PC > Estado. The FILES these tests read moved with it
(Sidebar.tsx -> TopNav.tsx; the inspector column -> ContextPanels). Every
promise they pin is unchanged, and several are now stricter.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "frontend"
COMPONENTS = FRONTEND / "components"
PAGES = FRONTEND / "pages"
CSS = FRONTEND / "styles" / "globals.css"

# Every view the sidebar can reach. The shell must handle all of them.
VIEWS = (
    "chat", "tasks", "activity", "permissions",
    "agents", "memory", "integrations", "status", "settings",
)


def _strip_comments(source: str) -> str:
    """Remove // and /* */ comments so prose about a bug is not read as the bug."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tsx_files() -> list[Path]:
    return sorted(COMPONENTS.glob("*.tsx")) + sorted(PAGES.glob("*.tsx"))


@pytest.fixture(scope="module")
def shell() -> str:
    return _read(PAGES / "index.tsx")


@pytest.fixture(scope="module")
def navigation() -> str:
    """The navigation model. It was Sidebar.tsx; it is TopNav.tsx now."""
    return _read(COMPONENTS / "TopNav.tsx")


@pytest.fixture(scope="module")
def inspector() -> str:
    """The live context panels: a fixed column once, a section of PC now."""
    return _read(COMPONENTS / "Inspector.tsx")


@pytest.fixture(scope="module")
def css() -> str:
    return _read(CSS)


@pytest.fixture(scope="module")
def main_module():
    import core.main as module
    return module


# =============================================================== navigation

def test_every_navigation_entry_has_a_view_the_shell_renders(navigation, shell):
    """A nav item that leads nowhere is a dead control."""
    declared = set(re.findall(r'\{\s*id:\s*"([a-z]+)"', navigation))
    assert declared, "the navigation declares no entries"
    for view in declared:
        assert f'view === "{view}"' in shell, f"the shell never renders the '{view}' view"


def test_the_view_union_and_the_shell_agree(navigation, shell):
    """ViewId is the single source of truth for what can be navigated to."""
    union = re.search(r"export type ViewId =(.*?);", navigation, re.S)
    assert union, "ViewId is no longer a declared union"
    ids = set(re.findall(r'"([a-z]+)"', union.group(1)))
    assert ids == set(VIEWS), f"ViewId drifted from the expected set: {ids ^ set(VIEWS)}"


def test_every_view_is_owned_by_exactly_one_section(navigation):
    """Grouping nine views into five sections must lose none and duplicate none.

    The top bar shows sections; a section with several views shows them as
    sub-tabs. A view in no section would be unreachable with the mouse, and a
    view in two would light up two tabs at once.
    """
    blocks = re.findall(r"views:\s*\[(.*?)\],\n  \},", navigation, re.S)
    assert blocks, "the section model disappeared"
    owned: list[str] = []
    for block in blocks:
        owned.extend(re.findall(r'\{\s*id:\s*"([a-z]+)"', block))
    assert sorted(owned) == sorted(VIEWS), (
        f"sections do not cover every view exactly once: {sorted(owned)}"
    )


def test_settings_is_reachable_without_a_keyboard(navigation):
    """Settings must have a clickable route, not only Ctrl+comma."""
    assert 'id: "settings"' in navigation, (
        "there is no clickable route into Settings"
    )


def test_keyboard_shortcuts_exist_but_are_not_the_only_route(shell):
    code = _strip_comments(shell)
    assert "key" in code and ("ctrlKey" in code or "metaKey" in code), "no keyboard shortcuts wired"
    # Every shortcut target must also have a visible control somewhere. Ctrl+B
    # toggles the conversation rail, and TopNav renders the button that does
    # the same thing with the mouse.
    assert "setRailOpen" in code, "Ctrl+B has no state to toggle"
    assert "onToggleRail" in code, "the rail can only be toggled from the keyboard"


def test_the_removed_chrome_is_not_reintroduced():
    """Three elements the user explicitly asked to stay gone."""
    for path in _tsx_files():
        code = _strip_comments(_read(path))
        assert "status-bar" not in code, f"{path.name} restores the bottom status bar"
        assert ">Atalhos<" not in code, f"{path.name} restores the visible Atalhos button"
        assert not re.search(r'>\s*Conectado\s*<', code), (
            f"{path.name} restores the 'Conectado' header card"
        )


# ================================================================ inspector

def test_inspector_never_calls_a_finished_task_current(inspector):
    """A CANCELLED task was being rendered as work in progress."""
    assert "isActiveTask" in inspector
    guard = re.search(r"function isActiveTask.*?\n}", inspector, re.S)
    assert guard, "the active-task guard disappeared"
    for terminal in ("COMPLETED", "CANCELLED", "FAILED"):
        assert terminal in guard.group(0), f"{terminal} is no longer excluded from 'current'"


def test_the_backend_agrees_that_a_finished_task_is_not_current(main_module):
    """The guard must hold on the server too, not only in the view."""
    task = main_module.task_engine.create_task("v1 contract: finished task")
    main_module.task_engine.cancel_task(task["id"])
    current = main_module.get_current_task()
    assert current is None or current["id"] != task["id"], (
        "a cancelled task is still reported as the current task"
    )


def test_terminal_statuses_are_excluded_from_the_active_set(main_module):
    """The two sets must not overlap, or the badge counts finished work."""
    overlap = main_module.ACTIVE_TASK_STATUSES & main_module.TERMINAL_TASK_STATUSES
    assert not overlap, f"these statuses count as both active and finished: {overlap}"


def test_inspector_renders_the_cards_the_design_calls_for(inspector):
    for title in ("Tarefa atual", "Modelo & provedor", "Voz & wake phrase",
                  "Saúde do sistema", "Atividade recente"):
        assert title in inspector, f"the inspector lost the '{title}' card"


def test_inspector_reads_provider_state_from_the_backend(inspector):
    """The provider badge must reflect a measurement, not a hope."""
    code = _strip_comments(inspector)
    assert "providers.groq.state" in code
    assert "providers.ollama.state" in code
    assert "route?.fallback" in code, "a silent fallback would not be visible to the user"


def test_inspector_is_a_summary_not_a_dead_end(inspector):
    """Each card links onward, or the detail is unreachable."""
    assert inspector.count("onNavigate(") >= 5


def test_the_context_panels_are_still_rendered_somewhere(inspector):
    """Removing the column must not have deleted six cards of real state."""
    assert "export default function ContextPanels" in inspector
    pages = _strip_comments(_read(COMPONENTS / "Pages.tsx"))
    assert "<ContextPanels" in pages, "nothing renders the context panels any more"


def test_the_fixed_inspector_column_is_gone(shell, css):
    """The permanent right-hand column was what made the shell feel technical."""
    code = _strip_comments(shell)
    assert "<Inspector" not in code, "the fixed inspector column is back in the shell"
    assert "inspector" not in css, (
        "the shell stylesheet still reserves space for the inspector column"
    )


# =========================================================== sidebar badge

def test_the_badge_counts_only_work_that_needs_the_user(main_module):
    """It read 68 because it counted all history."""
    counts = main_module.get_task_counts()
    for field in ("active", "attention", "badge", "total"):
        assert field in counts, f"task counts are missing '{field}'"
    assert counts["badge"] == counts["active"] + counts["attention"]
    assert counts["badge"] <= counts["total"]

    finished = sum(
        count for status, count in counts["byStatus"].items()
        if status in main_module.TERMINAL_TASK_STATUSES
    )
    if finished:
        assert counts["badge"] < counts["total"], (
            "the badge is still counting finished tasks"
        )


def test_a_cancelled_task_does_not_raise_the_badge(main_module):
    before = main_module.get_task_counts()["badge"]
    task = main_module.task_engine.create_task("v1 contract: badge probe")
    main_module.task_engine.cancel_task(task["id"])
    after = main_module.get_task_counts()["badge"]
    assert after <= before, "cancelling a task increased the attention badge"


def test_the_navigation_badge_is_fed_by_task_counts(navigation, shell):
    assert "counts" in navigation, "the navigation takes no counts prop"
    assert "get_task_counts" in shell, "the shell never fetches the badge counts"


# =============================================== responsive layout contract

def test_the_shell_grid_cannot_overflow_horizontally(css):
    """`1fr` refuses to shrink below its content; `minmax(0, 1fr)` does not.

    That difference is why the centre column pushed the side panel offscreen
    and the window had to be zoomed out to read it.

    EVERY `.app` rule that declares a column track is checked, not the first
    block whose selector happens to contain ".app". The first-match version of
    this test silently started reading the shell's drag-region rule when that
    was added above the grid, and would have gone on passing -- or failing --
    for reasons that had nothing to do with the track it exists to guard.
    """
    blocks = re.findall(r"[^{}]*\.app\b[^{}]*\{([^}]*)\}", css, re.S)
    tracks = [body for body in blocks if "grid-template-columns" in body]
    assert tracks, ".app never declares a column track"
    for body in tracks:
        assert "minmax(0, 1fr)" in body, (
            ".app uses a bare 1fr track, which cannot shrink and clips the layout: "
            f"{body.strip()!r}"
        )


def test_every_scroll_container_can_actually_shrink(css):
    """A flex/grid child defaults to min-size:auto and refuses to shrink.

    Each scroller needs a guard on the axis it can push on: `min-height: 0`
    so a long list does not stretch its flex parent past the viewport, and
    either `min-width: 0` or `overflow-x: hidden` so wide content scrolls
    inside the pane instead of widening the whole shell.
    """
    checked = 0
    for selector in (".conversation", ".rail__scroll", ".page-scroll"):
        block = re.search(re.escape(selector) + r"\s*\{[^}]*\}", css, re.S)
        if not block:
            continue
        checked += 1
        rules = block.group(0).replace(" ", "")
        assert "min-height:0" in rules, (
            f"{selector} has no min-height: 0 and will overflow its flex parent"
        )
        assert "min-width:0" in rules or "overflow-x:hidden" in rules, (
            f"{selector} can push the layout wider than the viewport"
        )
    assert checked >= 2, "the scroll containers were renamed; this test no longer guards anything"


def test_the_body_never_scrolls_sideways(css):
    assert re.search(r"(html|body)[^{]*\{[^}]*overflow-x:\s*hidden", css, re.S), (
        "nothing prevents the page itself from scrolling horizontally"
    )


def test_breakpoints_cover_every_target_width(css):
    """1280, 1366, 1440, 1920, 2560 at 100/125/150% Windows scaling.

    150% scaling on a 1920px panel reports 1280 CSS pixels, and on 1366 it
    reports ~911 -- so the small breakpoints are not hypothetical.
    """
    widths = {int(value) for value in re.findall(r"max-width:\s*(\d+)px", css)}
    assert any(width <= 780 for width in widths), "no breakpoint for ~911px (1366 at 150%)"
    assert any(1000 <= width <= 1120 for width in widths), "no breakpoint near 1080px"
    assert any(1240 <= width <= 1320 for width in widths), "no breakpoint near 1280px"
    assert any(1440 <= width <= 1560 for width in widths), "no breakpoint near 1500px"


def test_short_viewports_reclaim_vertical_space(css):
    """At 1366x768 with 125% scaling the composer fell below the fold."""
    assert "max-height:" in css, "no height breakpoint: the composer can fall off a short screen"


def test_the_composer_stays_in_view(css):
    """The input is pinned by its wrapper, never pushed past the viewport.

    `.conversation` takes all the slack with `flex: 1` while `.composer-wrap`
    is `flex: none`, so a long conversation scrolls instead of shoving the
    input below the fold.
    """
    wrap = re.search(r"\.composer-wrap\s*\{[^}]*\}", css, re.S)
    assert wrap, ".composer-wrap has no layout rule"
    rules = wrap.group(0).replace(" ", "")
    assert "flex:none" in rules or "flex-shrink:0" in rules or "position:sticky" in rules, (
        "the composer can be pushed out of the viewport by a long conversation"
    )

    scroller = re.search(r"\.conversation\s*\{[^}]*\}", css, re.S)
    assert scroller and "flex: 1" in scroller.group(0), (
        "the conversation does not absorb the free space, so it cannot scroll"
    )


def test_no_fixed_pixel_width_can_pin_the_layout_open(css):
    """A hard `width: 1400px` would reintroduce the horizontal scrollbar."""
    offenders = [
        match for match in re.findall(r"^\s*(?:min-)?width:\s*(\d{4,})px", css, re.M)
        if int(match) >= 1200
    ]
    assert not offenders, f"a fixed width of {offenders} px will overflow small screens"


# ================================================== chat output cleanliness

def test_internal_reasoning_tokens_never_reach_the_bubble():
    from_source = _read(COMPONENTS / "Conversation.tsx")
    assert "cleanAssistantText" in from_source
    cleaner = re.search(r"export function cleanAssistantText.*?\n}", from_source, re.S)
    assert cleaner, "the assistant-text cleaner disappeared"
    body = cleaner.group(0)
    for marker in ("think", "analysis", "assistantfinal"):
        assert marker in body, f"'{marker}' is no longer stripped from assistant output"


def test_the_cleaner_is_applied_where_the_message_is_rendered():
    code = _strip_comments(_read(COMPONENTS / "Conversation.tsx"))
    assert code.count("cleanAssistantText(") >= 2, (
        "the cleaner is defined but never applied to a rendered message"
    )


# ============================================== honest controls and states

def test_every_disabled_control_explains_itself():
    """A control that does nothing must say so, not fail silently."""
    for path in _tsx_files():
        code = _strip_comments(_read(path))
        for match in re.finditer(r"<button[^>]*\bdisabled\b[^>]*>", code, re.S):
            tag = match.group(0)
            has_reason = "title=" in tag or "aria-label=" in tag or "aria-disabled" in tag
            assert has_reason, f"{path.name} has a disabled button with no explanation: {tag[:90]}"


def test_not_yet_available_controls_say_brevemente():
    """The agreed wording for a deliberately inert control."""
    joined = "\n".join(_read(path) for path in _tsx_files())
    assert "revemente" in joined, "no control is marked as coming soon; check none were left silently dead"


def test_error_states_carry_something_actionable():
    ui = _read(COMPONENTS / "ui.tsx")
    assert "ErrorState" in ui, "there is no error state component"
    block = re.search(r"export function ErrorState\b.*?\n}\n", ui, re.S)
    assert block, "ErrorState is not a rendered component"
    body = block.group(0)
    assert "normalized.message" in body, "an error state that shows no message is not actionable"
    # The diagnosis sits behind a disclosure, so it informs without shouting.
    for field in ("component", "code", "timestamp"):
        assert f"normalized.{field}" in body, f"the error state hides the {field}"
    assert "onRetry" in body, "an error with no way forward is a dead end"


def test_empty_states_are_explicit_rather_than_blank():
    ui = _read(COMPONENTS / "ui.tsx")
    assert "EmptyState" in ui
    usage = sum(_read(path).count("<EmptyState") for path in _tsx_files())
    assert usage >= 5, "most views still render a bare blank area when they have no data"


def test_loading_is_distinguishable_from_empty(inspector):
    """A skeleton means 'not yet'; an empty state means 'nothing here'."""
    assert "Skeleton" in inspector and "EmptyState" in inspector, (
        "the inspector cannot tell 'still loading' apart from 'nothing to show'"
    )


def test_no_view_renders_placeholder_sample_data():
    """Lorem/demo/mock content would be fake readiness."""
    banned = ("lorem ipsum", "placeholder data", "sampleData", "mockData", "fakeData", "dummyData")
    for path in _tsx_files():
        code = _strip_comments(_read(path)).lower()
        for token in banned:
            assert token.lower() not in code, f"{path.name} renders {token}"


# ================================================ execution boundary intact

def test_the_frontend_never_executes_a_tool_directly():
    """Chat and UI must go through the policy boundary, never around it."""
    joined = "\n".join(_strip_comments(_read(path)) for path in _tsx_files())
    for forbidden in ("tool_executor", "execute_tool_direct", "run_tool_unchecked", "bypass_permission"):
        assert forbidden not in joined, f"the frontend calls {forbidden}, bypassing the executor"


def test_the_ui_still_routes_permissions_through_the_backend():
    joined = "\n".join(_read(path) for path in _tsx_files())
    assert "resolve_permission" in joined
    assert "set_emergency_stop" in joined
