"""Clear reminder requests are created by Nano itself, not left to the model.

Measured on 2026-11-18: "lembra-me daqui a 1 minuto de beber água" got the
answer "Já registei um lembrete" from Groq, with no set_reminder call. Nothing
was stored and no notification ever came.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from core import plugin_loader
from core import reminder_intent as ri
from core.brain import Brain
from core.memory import MemoryEngine
from core.permission_manager import PermissionManager
from plugins import reminders


@pytest.mark.parametrize("message, text, minutes", [
    ("lembra-me daqui a 1 minuto de beber água", "beber água", 1),
    ("Lembra-me de beber água daqui a 1 minuto", "beber água", 1),
    ("lembra-me daqui a um minuto de beber água", "beber água", 1),
    ("avisa-me em 10 minutos para tirar a pizza do forno", "tirar a pizza do forno", 10),
    ("Avisa-me em 10 minutos", "", 10),
    ("lembra-me dentro de 2 horas que tenho de ligar à avó", "tenho de ligar à avó", 120),
    ("Nano, lembra-me daqui a meia hora de sair", "sair", 30),
    ("cria um lembrete daqui a 5 minutos para estudar", "estudar", 5),
    ("remind me in 5 minutes to call mom", "call mom", 5),
    ("Remind me to stretch in 20 minutes", "stretch", 20),
    ("can you remind me in an hour to check the oven", "check the oven", 60),
    ("set a reminder in 2 minutes", "", 2),
])
def test_relative_requests_are_understood(message, text, minutes):
    request = ri.parse(message)
    assert request is not None, message
    assert request.text == text
    due = reminders.parse_when(request.when)
    assert due is not None
    assert abs((due - datetime.now()) - timedelta(minutes=minutes)) < timedelta(seconds=5)


@pytest.mark.parametrize("message, text, hour, minute", [
    ("lembra-me amanhã às 9 de ir ao médico", "ir ao médico", 9, 0),
    ("lembra-me às 18:30 de ligar ao João", "ligar ao João", 18, 30),
    ("lembra-me às 9 da noite de tomar o comprimido", "tomar o comprimido", 21, 0),
    ("remind me tomorrow at 9am to pay the rent", "pay the rent", 9, 0),
    ("remind me to pay the rent at 5pm", "pay the rent", 17, 0),
])
def test_absolute_requests_are_understood(message, text, hour, minute):
    request = ri.parse(message)
    assert request is not None, message
    assert request.text == text
    due = reminders.parse_when(request.when)
    assert (due.hour, due.minute) == (hour, minute)


@pytest.mark.parametrize("message", [
    "lembra-me de tirar a roupa da máquina",          # no time: the model asks when
    "lembra-te que o meu gato se chama Bolinhas",     # a fact, not a reminder
    "lembra-me todos os dias às 8 de tomar o comprimido",  # recurring: model fills repeat
    "remind me every monday at 9 to water the plants",
    "o que é um lembrete?",
    "lembra-me o que fiz hoje",
    "quanto é 2 + 2",
    "lembra-me daqui a 1 minuto de beber água\ne já agora explica-me a fotossíntese",
    "",
])
def test_anything_unclear_goes_to_the_model(message):
    assert ri.parse(message) is None


def test_the_reply_is_in_the_language_used():
    now = datetime(2026, 11, 19, 10, 0)
    pt = ri.parse("lembra-me daqui a 1 minuto de beber água")
    en = ri.parse("remind me in 1 minute to drink water")
    ok_pt = {"success": True, "text": "beber água", "due_at": "2026-11-19 10:01"}
    ok_en = {"success": True, "text": "drink water", "due_at": "2026-11-20 10:01"}
    assert ri.confirmation(pt, ok_pt, now) == (
        "Combinado! Lembrete criado às 10:01: beber água. Vou avisar-te com uma notificação.")
    assert ri.confirmation(en, ok_en, now) == (
        "Done! Reminder set tomorrow at 10:01: drink water. I'll notify you then.")
    assert ri.confirmation(pt, {"success": False, "cancelled": True}) == "Ok, não criei o lembrete."


@pytest.mark.parametrize("answer, claims", [
    ("Já registei um lembrete para daqui a 1 minuto.", True),
    ("Entendido! Vou lembrar-te de beber água.", True),
    ("Lembrete criado para as 10:01.", True),
    ("I've set a reminder for 5 minutes from now.", True),
    ("A fotossíntese é o processo...", False),
    ("Queres que crie um lembrete?", False),
])
def test_a_false_claim_is_recognised(answer, claims):
    assert ri.claims_a_reminder(answer) is claims


# ─── The whole turn, through the real executor ───────────────────────────────

class _FakeMemory:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)


@pytest.fixture
def reminder_db(monkeypatch):
    if "set_reminder" not in plugin_loader._all_handlers:
        plugin_loader.load_all_plugins()
    fake = _FakeMemory()
    # The loader imports plugins as its own module objects: patch that one too.
    for module in {reminders, plugin_loader.get_plugin("reminders")}:
        monkeypatch.setattr(module, "get_memory", lambda: fake)
        monkeypatch.setattr(module, "start_scheduler", lambda: True)
    return fake


def _brain(confirm: bool):
    guard = MagicMock()
    guard.requires_confirmation = MagicMock(return_value=True)
    guard.ask_confirmation = AsyncMock(return_value=confirm)
    manager = PermissionManager(confirmation_callback=lambda *_: confirm)
    brain = Brain("", guard, MemoryEngine(), {"ollama_enabled": False}, permission_manager=manager)
    # The model must never be asked: any route attempt fails the test.
    brain.route_for_async = AsyncMock(side_effect=AssertionError("the model was consulted"))
    return brain, guard


async def _answer(brain, message):
    tokens = [t async for t in brain.chat(message)]
    return "".join(t for t in tokens if not t.startswith("_thinking_:"))


@pytest.mark.asyncio
async def test_the_reported_sentence_creates_a_real_reminder(reminder_db):
    brain, guard = _brain(confirm=True)
    answer = await _answer(brain, "lembra-me daqui a 1 minuto de beber água")

    rows = reminder_db.conn.execute("SELECT text, due_at, fired FROM reminders").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "beber água" and rows[0][2] == 0
    due = datetime.fromisoformat(rows[0][1])
    assert timedelta(seconds=30) < due - datetime.now() < timedelta(seconds=90)
    assert answer.startswith("Combinado! Lembrete criado às")
    assert "beber água" in answer
    guard.ask_confirmation.assert_awaited()          # the safety chain still runs
    # The history shows a real tool call and its result, for later turns.
    roles = [m["role"] for m in brain.conversation[-4:]]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert brain.conversation[-3]["tool_calls"][0]["function"]["name"] == "set_reminder"
    assert json.loads(brain.conversation[-3]["tool_calls"][0]["function"]["arguments"]) == {
        "text": "beber água", "when": "1 minutos"}
    assert brain.last_metadata["direct_action"] == "set_reminder"


@pytest.mark.asyncio
async def test_declining_the_confirmation_creates_nothing_and_says_so(reminder_db):
    brain, _guard = _brain(confirm=False)
    answer = await _answer(brain, "remind me in 5 minutes to call mom")
    assert reminder_db.conn.execute(
        "SELECT name FROM sqlite_master WHERE name='reminders'").fetchone() is None or \
        reminder_db.conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0
    assert answer == "Okay, I didn't create the reminder."


@pytest.mark.asyncio
async def test_a_model_that_claims_a_reminder_without_the_tool_is_corrected():
    brain = Brain("", MagicMock(), MemoryEngine(), {"ollama_enabled": False})
    note = brain._unbacked_reminder_note("Já registei um lembrete para daqui a pouco.")
    assert note and "não foi criado" in note
    brain._turn_tool_results = {"x": {"success": True}}
    brain.conversation.append({"role": "user", "content": "lembra-me"})
    brain.conversation.append({"role": "assistant", "content": None, "tool_calls": [
        {"id": "1", "type": "function", "function": {"name": "set_reminder", "arguments": "{}"}}]})
    assert brain._unbacked_reminder_note("Já registei um lembrete.") is None
