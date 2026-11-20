"""The chat view, the conversation rail and the knowledge graph, as rendered.

Every claim here is about what a person SEES at a moment in a sequence, so
every one of them is checked by driving the shipped production bundle in
Electron's own Chromium and measuring the result. Nothing greps a source file:

    "there is exactly ONE thinking indicator"  is a count of live DOM nodes
                                               DURING a stream
    "the selected row has no left stripe"      is a computed ::before, which a
                                               stylesheet can reintroduce from
                                               anywhere
    "selecting a node highlights its edges"    is a class applied by a click
                                               handler that has to actually run
    "bulk delete issues one call"              is what the bridge recorded

The harnesses live in electron/test/. They install a scripted `window.eel`
speaking the real payload shapes -- a test double of the TRANSPORT, not of the
components -- and return JSON. This module runs them once each and turns their
steps into named assertions, so a failure names the behaviour rather than
"the drive exited non-zero".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.electron_harness import run_harness

ROOT = Path(__file__).resolve().parent.parent

# The spawn itself -- the platform-correct binary name, UTF-8 decoding, the
# evidence-based timeout and the process-tree cleanup -- lives in
# tests/electron_harness.py, because three modules need exactly the same thing
# and three copies of it had already drifted apart.
_run = run_harness


@pytest.fixture(scope="module")
def chat_report() -> dict:
    return _run("chat-drive.js")


@pytest.fixture(scope="module")
def graph_report() -> dict:
    return _run("memory-render.js")


def _step(report: dict, needle: str) -> dict:
    matches = [step for step in report["steps"] if needle.lower() in step["label"].lower()]
    assert matches, f"no driven step matching {needle!r}; ran: " + \
        "; ".join(step["label"] for step in report["steps"])
    return matches[0]


def _passed(report: dict, needle: str) -> None:
    step = _step(report, needle)
    assert step["pass"], f"{step['label']} — {step['detail']}"


# ============================================================ every step green


#: The number of steps chat-drive.js emits on a healthy run. A FLOOR, not an
#: equality: adding coverage should never need this file edited, but LOSING it
#: must be impossible to do quietly.
#:
#: This constant exists because the harness used to guard whole sections behind
#: `if (someElement) { ... }`. When a fixed sleep under-waited and the element
#: was not there yet, up to 29 assertions simply did not run -- and the report
#: still said every step it had emitted passed. A measured drop from 83 steps
#: to 78 was observed while the harness reported itself green. Every one of
#: those preconditions is now its own named step, so a miss is a failure; this
#: floor is the second lock, catching a whole section going missing at once.
MINIMUM_CHAT_STEPS = 97


def test_the_real_chat_view_passes_every_driven_step(chat_report):
    failures = [step for step in chat_report["steps"] if not step["pass"]]
    assert not failures, "\n".join(
        f"{step['label']} — {step['detail']}" for step in failures)


def test_no_driven_step_went_missing(chat_report):
    """A green report that measured less than it used to is not a green report."""
    ran = len(chat_report["steps"])
    assert ran >= MINIMUM_CHAT_STEPS, (
        f"chat-drive.js emitted {ran} steps but should emit at least "
        f"{MINIMUM_CHAT_STEPS}. Steps do not vanish because the product "
        f"changed; they vanish because a precondition was not met and the "
        f"assertions behind it never ran.\nran: "
        + "; ".join(step["label"] for step in chat_report["steps"]))


def test_every_function_the_chat_view_calls_actually_exists(chat_report):
    """`call()` resolves null for an unknown name, so a UI calling a function
    the backend does not expose renders an empty state instead of an error."""
    import re

    source = (ROOT / "core" / "main.py").read_text(encoding="utf-8")
    exposed = set(re.findall(r"^def ([a-z_0-9]+)\(", source, re.M))
    for name in chat_report["calledFunctions"]:
        assert name in exposed, f"the UI calls {name}(), which core/main.py does not expose"


# ==================================================== the thinking indicator


def test_a_pending_turn_shows_exactly_one_thinking_indicator(chat_report):
    """The defect human testing found: two at once, "O Nano está a pensar…"
    inside the bubble and "A pensar…" underneath it."""
    _passed(chat_report, "exactly ONE thinking indicator")


def test_an_idle_conversation_shows_none(chat_report):
    _passed(chat_report, "idle conversation shows no thinking indicator")


def test_tool_activity_still_narrates_itself_in_that_one_indicator(chat_report):
    """The second indicator carried the tool status line. Deleting it without
    moving the status would have removed information, not duplication."""
    _passed(chat_report, "tool activity still narrates itself")


def test_the_indicator_becomes_the_answer_in_the_same_bubble(chat_report):
    _passed(chat_report, "first token replaces the indicator in the SAME bubble")
    _passed(chat_report, "nothing is inserted or removed between the question")


def test_a_finished_turn_shows_no_indicator(chat_report):
    _passed(chat_report, "finished turn shows no thinking indicator")


# ================================================= the technical-details panel


def test_the_disclosure_is_a_real_accessible_control(chat_report):
    _passed(chat_report, "real button with aria-expanded")
    _passed(chat_report, "points at the region it controls")
    _passed(chat_report, "is focusable")
    _passed(chat_report, "focused disclosure is visually distinguished")


def test_the_disclosure_is_discoverable_without_being_loud(chat_report):
    """Human testing called the old one visually weak: a text line with a "›"
    for a caret, no hover surface and no focus ring."""
    _passed(chat_report, "visible at rest")
    _passed(chat_report, "real chevron, not a text glyph")
    _passed(chat_report, "stays compact")


def test_the_disclosure_opens_and_closes(chat_report):
    _passed(chat_report, "hidden while the disclosure is closed")
    _passed(chat_report, "clicking opens the panel")
    _passed(chat_report, "clicking again closes the panel")


# ================================== per-message provider, and the live pill


def test_the_panel_reports_the_provider_that_answered_that_message(chat_report):
    _passed(chat_report, "names the provider that ANSWERED this message")


def test_the_top_selector_does_not_rewrite_a_messages_history(chat_report):
    """The exact confusion from human testing: the pill said "Gemini 2.5 Flash ·
    AUTO" while the message had been answered by Groq. Both statements are true
    and they answer different questions, so BOTH must keep saying their own."""
    _passed(chat_report, "does not claim the currently selected provider")
    _passed(chat_report, "top selector still shows the configured preference")


def test_the_fallback_is_explained_rather_than_merely_labelled(chat_report):
    _passed(chat_report, "explains the fallback in words")
    _passed(chat_report, "shows the hop chain")


def test_no_raw_provider_exception_reaches_the_panel(chat_report):
    _passed(chat_report, "no raw provider exception text")


# ================================================== the conversation rail


def test_the_row_actions_are_discoverable(chat_report):
    """They used to sit at opacity 0 until the row was hovered, so on a first
    visit there was no evidence a row could be renamed or deleted at all."""
    _passed(chat_report, "visible without hovering")
    _passed(chat_report, "usable hit target")
    _passed(chat_report, "reachable by keyboard")
    _passed(chat_report, "names what it does")


def test_the_active_conversation_has_no_left_stripe_and_is_still_obvious(chat_report):
    _passed(chat_report, "no vertical bar on its left edge")
    _passed(chat_report, "still unmistakable without the bar")


# ======================================================= multi-select + delete


def test_selection_mode_is_a_mode_and_not_a_permanent_column(chat_report):
    _passed(chat_report, "checkboxes do not clutter the rail")
    _passed(chat_report, "way into selection mode")
    _passed(chat_report, "checkbox on every row")


def test_conversations_can_be_selected_deselected_and_selected_all(chat_report):
    _passed(chat_report, "selecting two rows is counted")
    _passed(chat_report, "deselected individually")
    _passed(chat_report, "Select all selects every visible conversation")


def test_escape_leaves_selection_mode(chat_report):
    _passed(chat_report, "Escape leaves selection mode")


def test_bulk_delete_confirms_once_and_says_how_many(chat_report):
    _passed(chat_report, "bulk delete asks for confirmation")
    _passed(chat_report, "counts what will be removed")
    _passed(chat_report, "long-term memories survive")


def test_nothing_is_deleted_until_the_user_confirms(chat_report):
    _passed(chat_report, "nothing has been deleted before the user confirms")
    _passed(chat_report, "cancelling the confirmation deletes nothing")


def test_confirming_issues_one_bulk_call_rather_than_a_loop(chat_report):
    """Forty round trips from the renderer can be interrupted half way, and
    nothing would then know how far the batch got.

    The assertion names the FIRST bulk delete rather than pinning the whole
    call history. It used to compare the entire list to one literal, so adding
    any further bulk delete to the drive -- the selection-versus-filter case
    does exactly that -- failed this test without anything being wrong with the
    behaviour it exists to protect. The shape check below is new and stricter:
    EVERY bulk call must be one call carrying one list, which is the property
    that actually rules out a loop of single deletes wearing a batch's name.
    """
    _passed(chat_report, "ONE bulk call, not one call per conversation")
    _passed(chat_report, "no per-conversation delete was issued alongside")
    calls = chat_report["bulkDeleteArgs"]
    assert calls, "no bulk delete was issued at all"
    assert calls[0] == [["c1", "c2"]], calls
    assert all(len(args) == 1 and isinstance(args[0], list) for args in calls), calls


# =============================== a turn that outlives the conversation it began in


def test_leaving_a_thread_mid_answer_clears_the_pending_indicator(chat_report):
    """"A pensar…" means THIS conversation is waiting for something.

    The stream events carry a turn id and nothing about which conversation the
    turn belongs to, so the flag survived the switch and painted a spinner
    under a conversation with nothing pending.
    """
    _passed(chat_report, "switching conversations clears the pending indicator")


def test_an_answer_never_lands_in_a_conversation_that_did_not_ask(chat_report):
    """The worse half of the same defect.

    on_stream_chunk created the assistant bubble when it could not find it, so
    an answer to a question asked in one thread was appended to whichever
    thread the user had switched to — a message appearing in a conversation
    that never contained it.
    """
    _passed(chat_report, "a chunk from the thread we left is not written into this one")
    _passed(chat_report, "the finished answer does not land in the conversation on screen")
    _passed(chat_report, "no thinking indicator survives the finished turn either")


# ==================================================== turns that do not succeed


def test_a_failed_turn_releases_the_indicator_and_says_what_happened(chat_report):
    """A spinner left running after a failure tells the user to keep waiting
    for something that is never coming."""
    _passed(chat_report, "a pending turn shows an indicator before it fails")
    _passed(chat_report, "an error clears the thinking indicator")
    _passed(chat_report, "an error says so in the bubble rather than leaving it blank")
    _passed(chat_report, "a failed turn is not left marked as still streaming")


def test_a_rate_limited_turn_ends_and_names_its_real_wait(chat_report):
    """Driven as core/main.py really emits it: on_rate_limited and then always
    on_stream_end. The wait is read from `wait_seconds`, which is the key
    providers.py actually sends."""
    _passed(chat_report, "a rate-limited turn stops showing a thinking indicator")
    _passed(chat_report, "the rate limit is announced with its real wait")


# ================================= the selection and the search filter agree


def test_a_selection_hidden_by_the_search_filter_is_still_deleted(chat_report):
    """The counter, the confirmation and the ids sent to the backend were
    computed from different lists. Filtering a selected row off screen left the
    dialog offering to delete it while the call carried an empty list — which
    is dropped before it is sent, so the user confirmed a deletion that never
    happened."""
    _passed(chat_report, "one row can be selected on its own")
    _passed(chat_report, "filtering does not silently shrink the selection")
    _passed(chat_report, "the confirmation still counts the selected conversation")
    _passed(chat_report, "a selection hidden by the filter is still what gets deleted")
    _passed(chat_report, "the conversation the user selected is the one that went")


# ============================================================== the graph


def _grafo(report: dict) -> list[dict]:
    views = [view for view in report["views"] if view["view"] == "grafo"]
    assert views, "the graph view was never measured"
    return views


def test_the_graph_draws_its_nodes_and_its_edges(graph_report):
    """An SVG whose layout is computed in JavaScript cannot be verified by
    reading CSS, and a graph that renders zero lines looks exactly like a graph
    with no connections."""
    for view in _grafo(graph_report):
        assert view["graphSvg"], view["viewport"]
        assert view["graphNodes"] >= 6, f"{view['viewport']}: {view['graphNodes']} nodes"
        assert view["graphEdges"] >= 5, f"{view['viewport']}: {view['graphEdges']} edges"
        assert view["graphPositionsFinite"], f"{view['viewport']}: a node has no coordinate"
        assert view["graphLabels"], f"{view['viewport']}: no legible labels"


def test_selecting_a_node_highlights_it_and_fades_the_rest(graph_report):
    for view in _grafo(graph_report):
        step = view["interaction"]
        assert step["measured"], view["viewport"]
        assert step["selectedCount"] == 1, step
        assert step["litEdges"] >= 1, step
        assert step["dimmedNodes"] >= 1, step
        assert step["detailPanel"], step


def test_unrelated_elements_fade_rather_than_disappear(graph_report):
    """The shape of the rest of the graph is context for the part being read."""
    for view in _grafo(graph_report):
        opacity = view["interaction"]["dimOpacity"]
        assert opacity is not None and 0.05 < opacity < 0.6, view["interaction"]


def test_connected_nodes_settle_nearer_than_unconnected_ones(graph_report):
    """"Graph-aware layout" as a measurement rather than a claim: a ring layout
    scores 1.0 here, because every pair is equally far apart."""
    for view in _grafo(graph_report):
        step = view["interaction"]
        assert step["meanEdge"] < step["meanPair"], step


def test_a_node_can_be_dragged_and_only_that_node_moves(graph_report):
    for view in _grafo(graph_report):
        step = view["interaction"]
        assert step["draggedBy"] > 20, step
        assert step["othersMoved"] is False, step


def test_the_graph_layout_is_deterministic_across_renders(graph_report):
    """A layout that reshuffles on each visit makes a knowledge graph
    impossible to learn. Each viewport is a separate page load of the same
    fixture, so identical geometry across them is the real check."""
    signatures = {(view["interaction"]["meanEdge"], view["interaction"]["meanPair"])
                  for view in _grafo(graph_report)}
    assert len(signatures) == 1, signatures


def test_no_memory_view_overflows_at_any_supported_size(graph_report):
    for view in graph_report["views"]:
        assert not view["horizontalOverflow"], f"{view['view']} @ {view['viewport']}"
        assert not view["offenders"], f"{view['view']} @ {view['viewport']}: {view['offenders']}"


# ================================================ geometry at every size


def test_every_selection_action_stays_reachable_at_every_size(chat_report):
    """The toolbar WRAPS rather than clipping, which is a claim about how many
    buttons you can still press.

    "Nothing overflows" does not cover it: a button pushed onto a second line
    is fine, and a button collapsed to zero width is not, and both leave the
    rail's right edge exactly where it was. The drive already counted the
    actions with a real box at each viewport -- the number was recorded and
    never looked at, so the requirement it encodes was measured and then
    dropped on the floor.

    Three actions in selection mode: select-all/clear, delete, cancel.
    """
    measured = [view for view in chat_report["views"]
                if view.get("state") == "selectionMode" and view.get("rendered")]
    assert len(measured) >= 4, f"selection mode was measured at {len(measured)} sizes"
    for view in measured:
        assert view["actionsVisible"] >= 3, (
            f'{view["viewport"]}: only {view["actionsVisible"]} selection actions '
            f"had a visible box")
        assert view["withinRail"], f'{view["viewport"]}: the toolbar left the rail'


@pytest.mark.parametrize("state", ["detailsOpen", "selectionMode", "confirmation"])
def test_each_new_chat_state_renders_without_clipping_at_every_size(chat_report, state):
    """The behavioural sequence runs once, at one size, because behaviour does
    not change with the window. Clipping does, so the three states this round
    introduced are re-entered and measured at every supported size down to the
    shell's own 940x620 minimum."""
    steps = [step for step in chat_report["steps"]
             if step["label"].startswith(f"{state} renders without clipping")]
    assert len(steps) >= 4, f"{state} was measured at only {len(steps)} sizes"
    failures = [step for step in steps if not step["pass"]]
    assert not failures, "\n".join(
        f"{step['label']} — {step['detail']}" for step in failures)
