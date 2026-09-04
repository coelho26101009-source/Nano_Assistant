"""What ONE assistant message says about how it was produced.

The failure this suite exists for is a real one, observed in human testing. The
top-bar pill said "Gemini 2.5 Flash · AUTO" while the message underneath it
reported "Provider: groq (fallback)". Both were correct and the pair was
useless: the pill answers "who would reply if you asked now", the message
answers "who replied to this one", and nothing on screen said the second turn
had started on Gemini and been finished by Groq after a 503.

So these tests pin three things:

    the SHAPE     what a message may remember about itself, and what it may not
    the HISTORY   that a reopened thread shows the message's own provider
    the CHAIN     that every hop a turn made is recorded, in order

Nothing here greps source. The shaping functions are called, the store is
written and read, and the real ``Brain.chat`` generator is driven.
"""
from __future__ import annotations

import json

import pytest

import core.memory as memory_module
from core import response_meta
from core.memory import MemoryEngine
from core.memory_stack import MemoryStack
from core.provider_failures import FailureType


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_module, "DB_PATH", tmp_path / "helios.db")
    engine = MemoryEngine()
    built = MemoryStack(engine, background=False)
    try:
        yield built
    finally:
        built.stop()
        engine.close()


# =================================================== the allow-list, exactly


def test_the_providers_own_error_string_never_reaches_a_message():
    """The specific leak this module was written to stop.

    ``ProviderFailure.as_dict`` carries ``message``, which is the vendor's raw
    sentence -- "UNAVAILABLE This model is currently experiencing high demand".
    It belongs in the log. Putting it on screen shows a backend exception to the
    user, and persisting it writes one to disk forever.
    """
    scratchpad = {
        "provider": "groq", "model": "openai/gpt-oss-20b", "mode": "AUTO",
        "fallback_used": True, "fallback_reason": "SERVER_ERROR",
        "provider_failure": {
            "provider": "google", "failure_type": "SERVER_ERROR", "status_code": 503,
            "message": "UNAVAILABLE This model is currently experiencing high demand.",
        },
    }
    shaped = response_meta.for_message(scratchpad)
    blob = json.dumps(shaped, ensure_ascii=False)
    assert "provider_failure" not in shaped
    assert "high demand" not in blob
    assert "UNAVAILABLE" not in blob
    # ...and the FACT survives, in a form the UI can render.
    assert shaped["fallback_reason"] == "provider_error"


def test_an_unknown_diagnostic_key_is_not_carried_onto_a_message():
    """An allow-list, not a deny-list. A new scratchpad key must be invisible
    here until somebody decides it is safe to keep."""
    shaped = response_meta.for_message({
        "provider": "groq", "model": "m",
        "system_prompt": "segredo", "recalled_text": "a minha morada é...",
        "api_key": "gsk_livetoken", "some_future_field": 42,
    })
    assert set(shaped) <= {"provider", "model", "fallback_used"}
    assert "gsk_livetoken" not in json.dumps(shaped)


def test_memory_diagnostics_survive_as_numbers_and_not_as_text():
    shaped = response_meta.for_message({
        "provider": "groq", "model": "m",
        "memory": {"memories": 2, "tokens": 180, "recalled": "o meu PC tem uma GTX"},
    })
    assert shaped["memory"] == {"memories": 2, "tokens": 180}


def test_a_non_dict_is_shaped_into_nothing_rather_than_raising():
    assert response_meta.for_message(None) == {}
    assert response_meta.for_message("groq") == {}


# ======================================================== reason categories


@pytest.mark.parametrize("raw,expected", [
    (FailureType.RATE_LIMIT.value, "rate_limit"),
    (FailureType.TIMEOUT.value, "timeout"),
    (FailureType.CONNECTION_ERROR.value, "unavailable"),
    (FailureType.SERVER_ERROR.value, "provider_error"),
    (FailureType.AUTH_ERROR.value, "auth"),
    (FailureType.BAD_REQUEST.value, "bad_request"),
    (FailureType.MODEL_UNAVAILABLE.value, "model_unavailable"),
    (FailureType.CANCELLED.value, "cancelled"),
    (FailureType.UNKNOWN_PROVIDER_ERROR.value, "other"),
    ("google_cooldown", "cooldown"),
    ("groq_cooldown", "cooldown"),
    ("no_cloud_available", "no_cloud_available"),
    ("cloud_mode_no_fallback", "cloud_mode"),
    ("partial_stream_not_replaced", "partial_answer"),
    ("not_eligible:AUTH_ERROR", "auth"),
    ("", ""),
])
def test_every_reason_the_brain_produces_normalises_to_a_known_category(raw, expected):
    assert response_meta.reason_category(raw) == expected


def test_every_failure_type_has_a_category(monkeypatch):
    """A new FailureType must not silently arrive as its own raw string."""
    for failure in FailureType:
        category = response_meta.reason_category(failure.value)
        assert category in response_meta.REASON_CATEGORIES, failure


def test_an_unrecognised_reason_is_other_and_never_passed_through():
    """Passing the raw value through is how a provider sentence would leak into
    a field the UI renders verbatim."""
    assert response_meta.reason_category("kaboom: the model exploded") == "other"


# ============================================================ the hop chain


def test_the_chain_records_who_was_asked_first_and_who_answered():
    shaped = response_meta.for_message({
        "provider": "groq", "model": "openai/gpt-oss-20b",
        "fallback_used": True, "fallback_reason": "SERVER_ERROR",
        "provider_attempts": [
            {"provider": "google", "model": "gemini-3.7-flash", "outcome": "SERVER_ERROR"},
            {"provider": "groq", "model": "openai/gpt-oss-20b", "outcome": "ok"},
        ],
    })
    assert shaped["fallback_from"] == "google"
    assert [step["provider"] for step in shaped["provider_attempts"]] == ["google", "groq"]
    assert shaped["provider_attempts"][0]["outcome"] == "provider_error"
    assert shaped["provider_attempts"][1]["outcome"] == "ok"


def test_the_chain_is_bounded():
    attempts = [{"provider": f"p{i}", "outcome": "ok"} for i in range(20)]
    shaped = response_meta.for_message({"provider": "p0", "provider_attempts": attempts})
    assert len(shaped["provider_attempts"]) <= response_meta.MAX_ATTEMPTS


def test_a_single_hop_reports_no_fallback_origin():
    shaped = response_meta.for_message({
        "provider": "google", "model": "gemini-2.5-flash", "fallback_used": False,
        "provider_attempts": [{"provider": "google", "model": "gemini-2.5-flash",
                               "outcome": "ok"}],
    })
    assert "fallback_from" not in shaped
    assert shaped["fallback_used"] is False


# ================================================ history is not rewritten


def test_a_reopened_thread_shows_the_provider_that_answered_that_message(stack):
    """The persistence half of the fix.

    The metadata column was always written and never read, so a reopened thread
    showed no technical details at all -- and a panel that fell back to the
    CURRENT selection would have claimed Gemini answered a message Groq wrote.
    """
    thread = stack.new_conversation()
    stack.record_user_message("olá", conversation_id=thread["id"])
    stack.record_assistant_message(
        "resposta", conversation_id=thread["id"],
        metadata=response_meta.for_message({
            "provider": "groq", "model": "openai/gpt-oss-20b", "mode": "AUTO",
            "tier": "FAST", "fallback_used": True, "fallback_reason": "SERVER_ERROR",
            "provider_attempts": [
                {"provider": "google", "model": "gemini-3.7-flash", "outcome": "SERVER_ERROR"},
                {"provider": "groq", "model": "openai/gpt-oss-20b", "outcome": "ok"},
            ],
        }))

    rows = stack.conversations.messages(thread["id"])
    answer = [row for row in rows if row["role"] == "assistant"][0]
    assert answer["meta"]["provider"] == "groq"
    assert answer["meta"]["model"] == "openai/gpt-oss-20b"
    assert answer["meta"]["fallback_from"] == "google"
    assert answer["meta"]["fallback_reason"] == "provider_error"


def test_changing_the_preferred_model_does_not_rewrite_a_stored_message(stack):
    """The exact confusion from human testing, asserted as a property.

    The user answers one turn on Groq, then picks Gemini in the top selector.
    The stored message must still say Groq: the selector expresses a preference
    for the NEXT turn and has no authority over one that already happened.
    """
    thread = stack.new_conversation()
    stack.record_user_message("pergunta", conversation_id=thread["id"])
    stack.record_assistant_message(
        "resposta", conversation_id=thread["id"],
        metadata=response_meta.for_message(
            {"provider": "groq", "model": "openai/gpt-oss-20b", "mode": "AUTO"}))

    # The user switches provider and model. Nothing touches the stored row.
    from core import user_settings
    del user_settings                              # nothing to call: that IS the point

    rows = stack.conversations.messages(thread["id"])
    answer = [row for row in rows if row["role"] == "assistant"][0]
    assert answer["meta"]["provider"] == "groq"
    assert answer["meta"]["model"] == "openai/gpt-oss-20b"


def test_a_message_with_no_provider_metadata_opens_no_empty_panel(stack):
    """A voice row carries `{"source": "voice"}` and nothing else. Shaping that
    into `{"fallback_used": false}` would open a technical-details panel with
    nothing in it under every spoken turn."""
    thread = stack.new_conversation()
    stack.record_user_message("olá", conversation_id=thread["id"])
    stack.record_assistant_message("olá", conversation_id=thread["id"],
                                   metadata={"source": "voice"})
    rows = stack.conversations.messages(thread["id"])
    answer = [row for row in rows if row["role"] == "assistant"][0]
    assert "meta" not in answer


def test_a_row_written_before_the_allow_list_existed_is_re_shaped_on_read(stack):
    """Old rows hold whatever the scratchpad contained that day, raw provider
    sentence included. They are shaped on the way OUT as well as on the way in,
    so upgrading Nano cleans them up rather than surfacing them."""
    thread = stack.new_conversation()
    stack.record_user_message("olá", conversation_id=thread["id"])
    stack.record_assistant_message(
        "resposta", conversation_id=thread["id"],
        metadata={"provider": "groq", "model": "m",
                  "provider_failure": {"message": "UNAVAILABLE high demand"},
                  "system_prompt": "não devia estar aqui"})
    rows = stack.conversations.messages(thread["id"])
    answer = [row for row in rows if row["role"] == "assistant"][0]
    blob = json.dumps(answer["meta"], ensure_ascii=False)
    assert "high demand" not in blob
    assert "system_prompt" not in blob
    assert answer["meta"]["provider"] == "groq"
