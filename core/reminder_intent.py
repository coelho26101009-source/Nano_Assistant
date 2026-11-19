"""Clear reminder requests, recognised without asking the model.

WHY THIS EXISTS. "lembra-me daqui a 1 minuto de beber água" was offered the
``set_reminder`` tool, and the cloud model still answered "Já registei um
lembrete" WITHOUT calling it. Nothing was stored, so no notification ever came.
A reminder is too simple and too important to depend on the model choosing to
call a tool: when the sentence is unambiguous ("lembra-me", a time and,
optionally, what to remember), Nano creates it directly through the normal
tool path (guardrails, policy, permission, executor) and says what it did.

Anything that is not clearly a one-off reminder with a time (a recurring one,
"lembra-me de X" with no time, a long message, an attachment) returns ``None``
and goes to the model exactly as before.

This module only PARSES. It never touches the database or the scheduler.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

#: Longer than this is a conversation, not a reminder request.
MAX_LENGTH = 220


@dataclass(frozen=True)
class ReminderRequest:
    text: str        # what to remember ("" when the person said only when)
    when: str        # a phrase plugins.reminders.parse_when understands
    language: str    # "pt" or "en": the language the reply must be in


_NUMBER_WORDS = {
    # Portuguese
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "três": 3, "quatro": 4,
    "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
    "quinze": 15, "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50,
    # English
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
}
_NUMBER = r"(\d{1,4}|" + "|".join(sorted(map(re.escape, _NUMBER_WORDS), key=len, reverse=True)) + r")"

_UNITS = {
    "segundo": "segundos", "segundos": "segundos", "seg": "segundos", "segs": "segundos",
    "minuto": "minutos", "minutos": "minutos", "min": "minutos", "mins": "minutos",
    "hora": "horas", "horas": "horas", "h": "horas",
    "dia": "dias", "dias": "dias",
    "second": "segundos", "seconds": "segundos", "sec": "segundos", "secs": "segundos",
    "minute": "minutos", "minutes": "minutos",
    "hour": "horas", "hours": "horas", "hr": "horas", "hrs": "horas",
    "day": "dias", "days": "dias",
}
_UNIT = r"(" + "|".join(sorted(map(re.escape, _UNITS), key=len, reverse=True)) + r")"

# ─── Portuguese ──────────────────────────────────────────────────────────────

_PT_TRIGGER = re.compile(
    r"^(?:(?:ó|o|olá|ola|ei|hey)\s+)?(?:nano\s*[,!:]?\s*)?(?:por\s+favor\s*,?\s*)?"
    r"(?:(?:podes|consegues|pode)\s+)?"
    r"(?:lembra-me|lembra\s+me|lembrame|lembrar-me|lembra-nos|recorda-me|"
    r"avisa-me|avisa\s+me|avisame|avisar-me|alerta-me|"
    r"cria(?:-me)?\s+um\s+lembrete|criar\s+um\s+lembrete|p[õo]e\s+um\s+lembrete|"
    r"faz(?:-me)?\s+um\s+lembrete|define\s+um\s+lembrete|marca\s+um\s+lembrete)\b"
    r"(?:\s+por\s+favor)?\s*[,:]?\s*",
    re.IGNORECASE,
)
_PT_RELATIVE = re.compile(
    rf"\b(?:daqui\s+a|dentro\s+de|em)\s+(?:{_NUMBER}\s*{_UNIT}|(meia\s+hora))\b",
    re.IGNORECASE,
)
_PT_TIME = r"(?:\d{1,2}(?::\d{2}|h\d{2}|h)?(?:\s*horas)?(?:\s+e\s+meia)?(?:\s+da\s+(?:manhã|manha|tarde|noite))?|meio-dia|meio\s+dia|meia-noite)"
_PT_DAY = r"(?:depois\s+de\s+amanhã|depois\s+de\s+amanha|amanhã|amanha|hoje|(?:na\s+|no\s+|esta\s+|este\s+|próxima\s+|proxima\s+)?(?:segunda|terça|terca|quarta|quinta|sexta|sábado|sabado|domingo)(?:-feira)?)"
_PT_ABSOLUTE = re.compile(
    rf"\b(?:(?P<day>{_PT_DAY})\s*,?\s+)?(?:às|as|à|pelas|ao)\s+(?P<time>{_PT_TIME})(?!\w)"
    rf"|\b(?P<dayonly>{_PT_DAY})(?:\s+(?:de\s+manhã|de\s+manha|à\s+tarde|a\s+tarde|à\s+noite|a\s+noite))?\b",
    re.IGNORECASE,
)
_PT_CONNECTOR = re.compile(r"^(?:de\s+que\s+|de\s+|que\s+|para\s+|pra\s+|a\s+)", re.IGNORECASE)

# ─── English ─────────────────────────────────────────────────────────────────

_EN_TRIGGER = re.compile(
    r"^(?:(?:hey|hi|ok)\s+)?(?:nano\s*[,!:]?\s*)?(?:please\s*,?\s*)?"
    r"(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
    r"(?:remind\s+me|set\s+(?:me\s+)?a\s+reminder|create\s+a\s+reminder|add\s+a\s+reminder|alert\s+me)\b"
    r"(?:\s+please)?\s*[,:]?\s*",
    re.IGNORECASE,
)
_EN_RELATIVE = re.compile(
    rf"\b(?:in|within)\s+(?:{_NUMBER}\s*{_UNIT}|(half\s+an\s+hour))\b",
    re.IGNORECASE,
)
_EN_TIME = r"(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2}|noon|midnight|\d{1,2}\s*o'?clock)"
_EN_DAY = r"(?:the\s+day\s+after\s+tomorrow|tomorrow|today|tonight|(?:on\s+|next\s+|this\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))"
_EN_ABSOLUTE = re.compile(
    rf"\b(?:(?P<day>{_EN_DAY})\s*,?\s+)?(?:at\s+)(?P<time>{_EN_TIME})(?!\w)"
    rf"(?:\s+(?P<dayafter>{_EN_DAY}))?"
    rf"|\b(?P<dayonly>{_EN_DAY})\b",
    re.IGNORECASE,
)
_EN_CONNECTOR = re.compile(r"^(?:to\s+|about\s+|that\s+|of\s+)", re.IGNORECASE)

#: "lembra-me o que fiz", "remind me what I said": recall a memory, not a reminder.
_RECALL = re.compile(
    r"^(?:o\s+que|do\s+que|quem|onde|quando|como|qual|quais|what|who|where|when|how|which)\b",
    re.IGNORECASE,
)

#: A repetition is the model's job (it fills ``repeat``); never guessed here.
_RECURRING = re.compile(
    r"\b(?:todos\s+os|todas\s+as|todo\s+(?:o\s+)?dia|diariamente|semanalmente|mensalmente|"
    r"dias\s+[úu]teis|every|daily|weekly|monthly|weekdays)\b",
    re.IGNORECASE,
)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", " ".join(str(text or "").split()))


def _number(token: str) -> int:
    return int(token) if token.isdigit() else _NUMBER_WORDS[token.lower()]


def _clean_text(text: str, connector: re.Pattern) -> str:
    text = text.strip(" \t,;:.!?-–—")
    previous = None
    while previous != text:
        previous = text
        text = connector.sub("", text).strip(" \t,;:.!?-–—")
    return text


def _cut(message: str, match: re.Match) -> str:
    return (message[:match.start()] + " " + message[match.end():]).strip()


def parse(message: str) -> ReminderRequest | None:
    """The reminder this message clearly asks for, or None."""
    text = _fold(message)
    if not text or len(text) > MAX_LENGTH or "\n" in str(message or "").strip():
        return None
    if _RECURRING.search(text):
        return None
    for language, trigger in (("pt", _PT_TRIGGER), ("en", _EN_TRIGGER)):
        head = trigger.match(text)
        if head:
            rest = text[head.end():]
            request = _parse_pt(rest) if language == "pt" else _parse_en(rest)
            if request is None or not _understood(request.when):
                return None
            if _RECALL.match(request.text):
                return None
            return request
    return None


def _understood(when: str) -> bool:
    """Only a moment the reminders plugin itself can read counts as clear."""
    from plugins.reminders import parse_when  # noqa: PLC0415 - avoid import cycles

    try:
        due = parse_when(when)
        return due is not None and due > datetime.now()
    except Exception:
        return False


def _parse_pt(rest: str) -> ReminderRequest | None:
    relative = _PT_RELATIVE.search(rest)
    if relative:
        if relative.group(3):
            when = "30 minutos"
        else:
            when = f"{_number(relative.group(1))} {_UNITS[relative.group(2).lower()]}"
        what = _cut(rest, relative)
    else:
        absolute = _PT_ABSOLUTE.search(rest)
        if not absolute:
            return None
        if absolute.group("time"):
            day = absolute.group("day") or ""
            when = f"{day} às {absolute.group('time')}".strip()
        else:
            when = absolute.group(0)
        what = _cut(rest, absolute)
    return ReminderRequest(_clean_text(what, _PT_CONNECTOR), _fold(when), "pt")


def _parse_en(rest: str) -> ReminderRequest | None:
    relative = _EN_RELATIVE.search(rest)
    if relative:
        if relative.group(3):
            when = "30 minutos"
        else:
            when = f"{_number(relative.group(1))} {_UNITS[relative.group(2).lower()]}"
        what = _cut(rest, relative)
    else:
        absolute = _EN_ABSOLUTE.search(rest)
        if not absolute:
            return None
        if absolute.group("time"):
            day = absolute.group("day") or absolute.group("dayafter") or ""
            clock = re.sub(r"\s*o'?clock$", "", absolute.group("time"), flags=re.IGNORECASE)
            if re.fullmatch(r"\d{1,2}", clock):
                clock = f"{clock}:00"
            when = f"{day} at {clock}".strip()
        else:
            when = absolute.group(0)
        what = _cut(rest, absolute)
    return ReminderRequest(_clean_text(what, _EN_CONNECTOR), _fold(when), "en")


# ─── The sentence Nano says back ─────────────────────────────────────────────

def confirmation(request: ReminderRequest, result: dict, now: datetime | None = None) -> str:
    """What Nano answers once the tool ran, in the language the person used."""
    pt = request.language == "pt"
    if not result.get("success"):
        reason = str(result.get("message") or result.get("error") or "").strip()
        if result.get("cancelled"):
            return ("Ok, não criei o lembrete." if pt else "Okay, I didn't create the reminder.")
        if pt:
            return f"Não consegui criar o lembrete. {reason}".strip()
        return f"I couldn't create the reminder. {reason}".strip()

    now = now or datetime.now()
    try:
        due = datetime.strptime(str(result.get("due_at")), "%Y-%m-%d %H:%M")
    except ValueError:
        due = None
    what = str(result.get("text") or request.text or "").strip()
    if due is None:
        moment = ""
    elif due.date() == now.date():
        moment = f"às {due:%H:%M}" if pt else f"at {due:%H:%M}"
    elif (due.date() - now.date()).days == 1:
        moment = f"amanhã às {due:%H:%M}" if pt else f"tomorrow at {due:%H:%M}"
    else:
        moment = f"a {due:%d/%m} às {due:%H:%M}" if pt else f"on {due:%d/%m} at {due:%H:%M}"
    if pt:
        if what and what.lower() != "lembrete":
            return f"Combinado! Lembrete criado {moment}: {what}. Vou avisar-te com uma notificação.".replace("  ", " ")
        return f"Combinado! Lembrete criado {moment}. Vou avisar-te com uma notificação.".replace("  ", " ")
    if what and what.lower() != "reminder":
        return f"Done! Reminder set {moment}: {what}. I'll notify you then.".replace("  ", " ")
    return f"Done! Reminder set {moment}. I'll notify you then.".replace("  ", " ")


def default_text(request: ReminderRequest) -> str:
    return request.text or ("Lembrete" if request.language == "pt" else "Reminder")


# ─── The other half: a model that claims it did it ───────────────────────────

_CLAIM = re.compile(
    r"\b(?:registei|criei|defini|agendei|marquei|programei|configurei|guardei|anotei)\b[^.!?\n]{0,40}\blembrete"
    r"|\blembrete\b[^.!?\n]{0,30}\b(?:criado|registado|definido|agendado|marcado|configurado)\b"
    r"|\bvou\s+lembrar-te\b|\bvou\s+avisar-te\b|\bte\s+lembrarei\b"
    r"|\b(?:i(?:'ve| have)?|i\s+will|i'll)\s+(?:set|created|scheduled|added|remind)\b[^.!?\n]{0,40}"
    r"|\breminder\b[^.!?\n]{0,30}\b(?:set|created|scheduled|added)\b",
    re.IGNORECASE,
)


def claims_a_reminder(answer: str) -> bool:
    """True when an answer says a reminder exists. Used only on turns where
    no reminder tool ran, to stop the model from claiming one that does not."""
    text = str(answer or "")
    if not _CLAIM.search(text):
        return False
    return bool(re.search(r"lembr|remind|avis|alert", text, re.IGNORECASE))


def unbacked_claim_note(answer: str) -> str:
    """The correction shown under an answer that claimed a reminder it never made."""
    if re.search(r"lembr|avis", str(answer or ""), re.IGNORECASE):
        return ("⚠️ Atenção: afinal não foi criado nenhum lembrete. Pede de novo, por exemplo: "
                "\"lembra-me daqui a 10 minutos de beber água\".")
    return ("⚠️ Heads up: no reminder was actually created. Ask again, for example: "
            "\"remind me in 10 minutes to drink water\".")
