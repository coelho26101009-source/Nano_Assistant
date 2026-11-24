"""The filter that keeps a proactive NANO from becoming an annoying one.

Everything NANO says without being asked (the morning briefing, a heads-up
before an event, a PC alert, a suggestion, a finished screen watch) passes
through :class:`NoticeGate` before it reaches the window, the island, a
Windows notification, the phone or the speakers. The gate decides, for each
notice, one of three things:

* **deliver** it now (and whether to say it out loud);
* **hold** it, because the person is away, playing in full screen, in their
  quiet hours or asked for silence, and give it later as one short summary
  ("Enquanto estavas fora: …") when they are back;
* **drop** it, because they turned that kind off, it repeats something said a
  moment ago, or it is not important enough for the level they chose.

THE RULES, IN ORDER
-------------------
1. **Level** (Definições → Geral → "Quanto o NANO fala sozinho"): ``off`` lets
   only critical notices through (battery about to die, a watch the person
   asked for); ``essential`` adds the important ones (an event about to start,
   battery low, the network dropped); ``balanced`` (the default) adds the
   useful ones (briefing, downloads, disk, memory) and a few suggestions a day;
   ``all`` lets everything through with looser limits.
2. **Muted kinds.** "Não me avises disto" on a notice turns that kind off.
3. **Repeats.** The same kind and text within 30 minutes is said once.
4. **Silence** ("não me incomodes durante uma hora", the island's button):
   only critical notices pass; the rest is held.
5. **Quiet hours** (23:00-07:30 by default): critical passes; important ones
   are shown without a sound or voice; the rest is held until morning.
6. **Full screen** (a game, a film, a presentation, as Windows itself reports
   it): like quiet hours, until it ends.
7. **Away** (no keyboard or mouse for 10 minutes): useful and low notices are
   held, so they are not lost in an empty room, and summed up on return.
8. **Rate limit**: at most a few non-critical notices an hour, and at most a
   few suggestions a day. Over the limit, useful notices are held and
   suggestions dropped.

WHAT IS STORED
--------------
``proactive_gate.json`` in the data directory: the silence end time, the kinds
the person muted, and the last 40 decisions (kind, title, decision, reason,
time), so Definições can show "why didn't NANO tell me". Never the text of a
notice. "Apagar todos os dados" removes the file.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from core.i18n import tr

logger = logging.getLogger("nano.notice_gate")

# ── priorities ──────────────────────────────────────────────────────────────
CRITICAL, HIGH, NORMAL, LOW = "critical", "high", "normal", "low"
_RANK = {CRITICAL: 3, HIGH: 2, NORMAL: 1, LOW: 0}

#: The priority of each kind when the notice does not say. A kind not listed
#: here is NORMAL.
KIND_PRIORITY = {
    "watch": CRITICAL,           # the person asked for it ("avisa-me quando…")
    "event": HIGH, "network": HIGH, "battery": HIGH,
    "briefing": NORMAL, "prep": NORMAL, "download": NORMAL, "disk": NORMAL,
    "memory": NORMAL, "digest": NORMAL, "routine": NORMAL, "weather": NORMAL,
    "suggestion": LOW, "bedtime": LOW, "break": LOW, "habit": LOW, "uptime": LOW,
    "battery_full": LOW, "overdue": LOW, "welcome": LOW,
}
#: Kinds the person can turn off from the notice itself. A watch they asked
#: for, a digest and a routine they made are theirs already.
MUTABLE_KINDS = frozenset(set(KIND_PRIORITY) - {"watch", "digest", "routine"})

LEVELS = ("off", "essential", "balanced", "all")
DEFAULT_LEVEL = "balanced"
#: The lowest priority each level lets through.
LEVEL_FLOOR = {"off": CRITICAL, "essential": HIGH, "balanced": LOW, "all": LOW}
#: Non-critical notices per rolling hour, per level.
HOURLY_CAP = {"off": 0, "essential": 3, "balanced": 4, "all": 8}
#: Suggestions (LOW) per day, per level.
DAILY_LOW_CAP = {"off": 0, "essential": 0, "balanced": 3, "all": 6}

SPEAK_CHOICES = ("off", "important", "all")
DEFAULT_SPEAK = "important"
DEFAULT_QUIET_START = "23:00"
DEFAULT_QUIET_END = "07:30"
#: Minutes without keyboard or mouse after which the person is "away".
AWAY_SECONDS = 10 * 60
REPEAT_WINDOW_SECONDS = 30 * 60
HELD_MAX = 12
HELD_TTL_SECONDS = 12 * 3600
HISTORY_MAX = 40
TICK_SECONDS = 20.0
SNOOZE_CHOICES = (15, 30, 60, 120, 240)
#: How long an offer ("queres que ative o modo estudo?") can be answered with
#: a plain "sim" by voice or in the chat.
OFFER_SECONDS = 180

_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def valid_time(value: Any) -> bool:
    return isinstance(value, str) and bool(_TIME.match(value))


#: Settings this module owns, with their defaults. user_settings asks
#: :func:`valid_setting` about them.
SETTING_DEFAULTS: dict[str, Any] = {
    "proactive_level": DEFAULT_LEVEL,
    "proactive_speak": DEFAULT_SPEAK,
    "proactive_quiet_enabled": True,
    "proactive_quiet_start": DEFAULT_QUIET_START,
    "proactive_quiet_end": DEFAULT_QUIET_END,
    "proactive_respect_fullscreen": True,
    "proactive_hold_when_away": True,
}


def valid_setting(key: str, value: Any) -> bool | None:
    """True/False for a key this module owns, None for any other key."""
    if key == "proactive_level":
        return isinstance(value, str) and value in LEVELS
    if key == "proactive_speak":
        return isinstance(value, str) and value in SPEAK_CHOICES
    if key in {"proactive_quiet_start", "proactive_quiet_end"}:
        return valid_time(value)
    if key in {"proactive_quiet_enabled", "proactive_respect_fullscreen", "proactive_hold_when_away"}:
        return isinstance(value, bool)
    return None


def _settings() -> dict[str, Any]:
    try:
        from core import user_settings
        get = user_settings.get
    except Exception:  # pragma: no cover
        def get(_key, default=None):
            return default
    out = {}
    for key, default in SETTING_DEFAULTS.items():
        value = get(key, default)
        out[key] = value if valid_setting(key, value) else default
    return out


def priority_of(notice: dict) -> str:
    wanted = str(notice.get("priority") or "")
    if wanted in _RANK:
        return wanted
    return KIND_PRIORITY.get(str(notice.get("kind") or ""), NORMAL)


def _minutes(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    """Whether ``now`` falls between start and end; the span may cross midnight."""
    if not (valid_time(start) and valid_time(end)) or start == end:
        return False
    current, begin, finish = now.hour * 60 + now.minute, _minutes(start), _minutes(end)
    if begin < finish:
        return begin <= current < finish
    return current >= begin or current < finish


def quiet_ends_at(now: datetime, end: str) -> datetime:
    hour, minute = (int(part) for part in end.split(":"))
    moment = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return moment if moment > now else moment + timedelta(days=1)


def _plain(text: Any) -> str:
    decomposed = unicodedata.normalize("NFD", str(text or "").casefold())
    bare = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", bare).split())


# ── spoken / typed commands: "não me incomodes durante uma hora" ───────────
_NUMBER_WORDS = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5,
    "dez": 10, "quinze": 15, "vinte": 20, "trinta": 30, "quarenta": 40, "meia": 0.5,
    "one": 1, "an": 1, "a": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "half": 0.5,
}
_SILENCE = re.compile(
    r"^(?:(?:ei|hey|ok|ola) )?(?:nano )?(?:por favor )?"
    r"(?:nao me incomodes|nao incomodar|modo nao incomodar|ativa o modo nao incomodar|"
    r"fica calado|fica em silencio|silencio|cala te|para de me avisar|nao me avises|"
    r"do not disturb|dont disturb me|don t disturb me|be quiet|stay quiet|quiet mode|silence|"
    r"stop notifying me|no notifications)"
    r"(?: (?:por favor|please|nano))?"
    r"(?: (?:durante|por|for|during)(?: (?:a|o|os|as|the))? (?P<amount>\d+|[a-z]+)(?: (?:e meia|and a half))?"
    r" (?P<unit>minutos?|mins?|horas?|h|minutes?|hours?))?"
    r"(?: (?P<tomorrow>ate amanha|until tomorrow|ate de manha|until the morning|hoje|today|for today))?"
    r"(?: (?:por favor|please|nano))?$")
_RESUME = re.compile(
    r"^(?:(?:ei|hey|ok|ola) )?(?:nano )?(?:ja )?(?:podes )?"
    r"(?:voltar a falar|volta a falar|podes falar|fala outra vez|desliga o nao incomodar|"
    r"desativa o nao incomodar|sai do nao incomodar|acabou o silencio|ja podes avisar|"
    r"you can talk again|you can speak again|resume notifications|turn off do not disturb|"
    r"end do not disturb|stop being quiet)(?: (?:por favor|please|nano))?$")
_YES = frozenset({
    "sim", "sim por favor", "sim faz", "sim ativa", "sim podes", "pode ser", "podes", "claro",
    "ok", "okay", "esta bem", "ta bem", "forca", "bora", "vamos", "faz isso", "faz", "ativa",
    "sim obrigado", "yes", "yes please", "yeah", "yep", "sure", "do it", "go ahead", "ok do it",
})
_NO = frozenset({
    "nao", "nao obrigado", "agora nao", "deixa", "deixa estar", "esquece", "nao preciso",
    "no", "no thanks", "not now", "nope", "never mind", "skip",
})


def parse_silence(text: str) -> dict | None:
    """``{"minutes": n}``, ``{"until": "morning"}`` or ``{"resume": True}``
    for a clear silence command; None for anything else."""
    plain = _plain(text)
    if not plain or len(plain) > 90:
        return None
    if _RESUME.match(plain):
        return {"resume": True}
    match = _SILENCE.match(plain)
    if not match:
        return None
    if match.group("tomorrow"):
        return {"until": "morning"}
    amount, unit = match.group("amount"), match.group("unit")
    if not amount:
        return {"minutes": 60}
    value = float(amount) if amount.isdigit() else _NUMBER_WORDS.get(amount)
    if value is None:
        return None
    if "e meia" in plain or "and a half" in plain:
        value += 0.5
    minutes = value * 60 if unit and unit.startswith(("h", "hora", "hour")) else value
    return {"minutes": int(max(1, min(minutes, 24 * 60)))}


def answer_kind(text: str) -> str | None:
    """"yes" / "no" for a bare answer to an offer, None otherwise."""
    plain = _plain(text)
    for prefix in ("ei nano ", "hey nano ", "nano "):
        if plain.startswith(prefix):
            plain = plain[len(prefix):]
    plain = plain.removesuffix(" nano").strip()
    if plain in _YES:
        return "yes"
    if plain in _NO:
        return "no"
    return None


# ── the gate ───────────────────────────────────────────────────────────────
class NoticeGate:
    """One instance per app. ``submit`` from any thread; ``tick`` every ~30 s."""

    def __init__(self, *, deliver: Callable[[dict, bool], None], state_path: Path | None,
                 settings: Callable[[], dict] = _settings,
                 clock: Callable[[], float] = time.time,
                 now: Callable[[], datetime] = datetime.now,
                 idle: Callable[[], float | None] | None = None,
                 busy: Callable[[], str | None] | None = None) -> None:
        if idle is None or busy is None:
            from core import presence
            idle = idle or presence.idle_seconds
            busy = busy or presence.busy_state
        self._deliver = deliver
        self._settings = settings
        self._clock = clock
        self._now = now
        self._idle = idle
        self._busy = busy
        self.state_path = state_path
        self._lock = threading.RLock()
        self._held: list[dict] = []
        self._sent: list[tuple[float, str, str]] = []      # (when, priority, repeat-key)
        self._offers: dict[str, dict] = {}
        self._last_offer: str | None = None
        self._was_away = False
        self._state = self._load()

    # ── persistence ────────────────────────────────────────────────────────
    def _load(self) -> dict:
        empty = {"snooze_until": 0.0, "muted": [], "history": [], "once": []}
        try:
            if self.state_path and self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {
                        "snooze_until": float(data.get("snooze_until") or 0.0),
                        "muted": [str(k) for k in data.get("muted") or [] if str(k) in MUTABLE_KINDS][:40],
                        "history": [h for h in data.get("history") or [] if isinstance(h, dict)][-HISTORY_MAX:],
                        "once": [str(k) for k in data.get("once") or []][-100:],
                    }
        except Exception:
            logger.debug("Notice gate state unreadable; starting fresh", exc_info=True)
        return empty

    def _save(self) -> None:
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self._state), encoding="utf-8")
        except Exception:
            logger.debug("Could not save the notice gate state", exc_info=True)

    def forget(self) -> None:
        with self._lock:
            self._state = {"snooze_until": 0.0, "muted": [], "history": [], "once": []}
            self._held.clear()
            self._sent.clear()
            self._offers.clear()
            self._last_offer = None
            try:
                if self.state_path and self.state_path.exists():
                    self.state_path.unlink()
            except Exception:
                logger.debug("Could not delete the notice gate state", exc_info=True)

    # ── what the person controls ───────────────────────────────────────────
    def snooze(self, minutes: float | None = None, *, until_morning: bool = False) -> dict:
        settings = self._settings()
        with self._lock:
            if until_morning:
                end = quiet_ends_at(self._now(), settings.get("proactive_quiet_end") or DEFAULT_QUIET_END)
                until = self._clock() + max(60.0, (end - self._now()).total_seconds())
            else:
                until = self._clock() + max(1.0, float(minutes or 60)) * 60
            self._state["snooze_until"] = until
            self._save()
        return self.snooze_status()

    def resume(self) -> dict:
        with self._lock:
            self._state["snooze_until"] = 0.0
            self._save()
        self.tick()
        return self.snooze_status()

    def snooze_status(self) -> dict:
        until = float(self._state.get("snooze_until") or 0.0)
        left = until - self._clock()
        if left <= 0:
            return {"active": False, "until": None, "minutesLeft": 0}
        moment = self._now() + timedelta(seconds=left)
        return {"active": True, "until": moment.strftime("%H:%M"), "minutesLeft": int(left // 60) + 1}

    def mute(self, kind: str) -> bool:
        if kind not in MUTABLE_KINDS:
            return False
        with self._lock:
            if kind not in self._state["muted"]:
                self._state["muted"].append(kind)
                self._save()
        return True

    def unmute(self, kind: str) -> bool:
        with self._lock:
            if kind not in self._state["muted"]:
                return False
            self._state["muted"].remove(kind)
            self._save()
        return True

    def muted(self) -> list[str]:
        return list(self._state["muted"])

    def history(self) -> list[dict]:
        return list(reversed(self._state["history"]))

    # ── presence ───────────────────────────────────────────────────────────
    def _away(self) -> bool:
        try:
            idle = self._idle()
        except Exception:
            idle = None
        return idle is not None and idle >= AWAY_SECONDS

    def _busy_reason(self) -> str:
        try:
            return self._busy() or ""
        except Exception:
            return ""

    def present(self) -> bool:
        """At the PC and not in a full-screen game/film/presentation."""
        settings = self._settings()
        if settings.get("proactive_respect_fullscreen", True) and self._busy_reason():
            return False
        return not self._away()

    def idle_seconds(self) -> float | None:
        try:
            return self._idle()
        except Exception:
            return None

    def busy_reason(self) -> str:
        return self._busy_reason()

    # ── the decision ───────────────────────────────────────────────────────
    def _context(self, settings: dict) -> dict:
        now = self._now()
        return {
            "snoozed": float(self._state.get("snooze_until") or 0.0) > self._clock(),
            "quiet": bool(settings.get("proactive_quiet_enabled", True)) and in_quiet_hours(
                now, settings.get("proactive_quiet_start") or DEFAULT_QUIET_START,
                settings.get("proactive_quiet_end") or DEFAULT_QUIET_END),
            "busy": bool(settings.get("proactive_respect_fullscreen", True)) and bool(self._busy_reason()),
            "away": bool(settings.get("proactive_hold_when_away", True)) and self._away(),
        }

    def decide(self, notice: dict) -> tuple[str, str, bool]:
        """(decision, reason, speak) for one notice, without side effects."""
        settings = self._settings()
        level = settings.get("proactive_level") or DEFAULT_LEVEL
        priority = priority_of(notice)
        rank = _RANK[priority]
        kind = str(notice.get("kind") or "")
        if rank < _RANK[LEVEL_FLOOR.get(level, LOW)]:
            return "drop", "level", False
        if priority != CRITICAL and kind in self._state["muted"]:
            return "drop", "muted", False
        now = self._clock()
        self._sent = [entry for entry in self._sent if now - entry[0] < 24 * 3600]
        repeat = self._repeat_key(notice)
        if any(key == repeat and now - when < REPEAT_WINDOW_SECONDS for when, _p, key in self._sent):
            return "drop", "repeat", False
        if notice.get("once") and str(notice["once"]) in self._state["once"]:
            return "drop", "repeat", False
        ctx = self._context(settings)
        if priority == CRITICAL:
            # Always shown; said out loud only when it would not wake anyone
            # or talk over a game.
            return "deliver", "", self._may_speak(settings, priority, kind,
                                                 quiet=ctx["quiet"] or ctx["busy"])
        if ctx["snoozed"]:
            return "hold", "snoozed", False
        if ctx["quiet"] or ctx["busy"]:
            if notice.get("night") and ctx["quiet"] and not ctx["busy"] and not ctx["away"]:
                # Made for the night ("já é tarde"), and the person is up:
                # shown, never said out loud.
                return "deliver", "quiet", False
            if priority == HIGH:
                return "deliver", "quiet" if ctx["quiet"] else "busy", False
            if priority == LOW and not notice.get("keep"):
                return "drop", "quiet" if ctx["quiet"] else "busy", False
            return "hold", "quiet" if ctx["quiet"] else "busy", False
        if ctx["away"] and priority != HIGH:
            if priority == LOW and not notice.get("keep"):
                return "drop", "away", False
            return "hold", "away", False
        hour = [entry for entry in self._sent if now - entry[0] < 3600 and entry[1] != CRITICAL]
        if priority != HIGH and len(hour) >= HOURLY_CAP.get(level, 4):
            return ("drop" if priority == LOW else "hold"), "rate", False
        if priority == LOW:
            today = [entry for entry in self._sent if entry[1] == LOW]
            if len(today) >= DAILY_LOW_CAP.get(level, 3):
                return "drop", "daily", False
        return "deliver", "", self._may_speak(settings, priority, kind, quiet=False)

    @staticmethod
    def _may_speak(settings: dict, priority: str, kind: str, *, quiet: bool) -> bool:
        choice = settings.get("proactive_speak") or DEFAULT_SPEAK
        if quiet or choice == "off":
            return False
        if choice == "all":
            return True
        return priority in (CRITICAL, HIGH) or kind in ("briefing", "digest")

    @staticmethod
    def _repeat_key(notice: dict) -> str:
        return f"{notice.get('kind')}|{_plain(notice.get('title'))}|{_plain(notice.get('text'))[:200]}"

    def submit(self, notice: dict) -> str:
        """Deliver, hold or drop one notice. Returns the decision."""
        if not isinstance(notice, dict) or not str(notice.get("text") or "").strip():
            return "drop"
        notice = dict(notice)
        notice.setdefault("id", f"n-{uuid.uuid4().hex[:12]}")
        notice["priority"] = priority_of(notice)
        with self._lock:
            decision, reason, speak = self.decide(notice)
            self._record(notice, decision, reason)
            if decision == "hold":
                self._hold(notice)
                return "hold"
            if decision == "drop":
                return "drop"
            self._mark_sent(notice)
        self._send(notice, speak=speak, silent=bool(reason))
        return "deliver"

    def _mark_sent(self, notice: dict) -> None:
        self._sent.append((self._clock(), notice["priority"], self._repeat_key(notice)))
        if notice.get("once"):
            self._state["once"] = (self._state["once"] + [str(notice["once"])])[-100:]
            self._save()
        if notice.get("offer"):
            offer = dict(notice["offer"])
            offer["at"] = self._clock()
            offer["kind"] = notice.get("kind")
            self._offers[notice["id"]] = offer
            self._last_offer = notice["id"]
            # Keep the map small: offers older than a day are useless.
            for key in [k for k, v in self._offers.items() if self._clock() - v["at"] > 24 * 3600]:
                self._offers.pop(key, None)

    def _send(self, notice: dict, *, speak: bool, silent: bool) -> None:
        payload = dict(notice)
        payload["silent"] = silent or notice["priority"] == LOW
        payload["mutable"] = notice.get("kind") in MUTABLE_KINDS
        try:
            self._deliver(payload, speak)
        except Exception:
            logger.debug("Delivering a notice failed", exc_info=True)

    def _record(self, notice: dict, decision: str, reason: str) -> None:
        stamp = self._now().strftime("%Y-%m-%d %H:%M")
        self._state["history"] = (self._state["history"] + [{
            "at": stamp, "kind": str(notice.get("kind") or "")[:16],
            "title": str(notice.get("title") or "")[:64], "decision": decision, "reason": reason,
        }])[-HISTORY_MAX:]
        self._save()

    def _hold(self, notice: dict) -> None:
        expires = notice.get("expires_at")
        notice["expires_at"] = float(expires) if isinstance(expires, (int, float)) else \
            self._clock() + HELD_TTL_SECONDS
        # A newer notice of the same kind and title replaces the older one
        # ("Sem rede" then "A rede voltou" leaves only the latest).
        self._held = [held for held in self._held
                      if not (held.get("kind") == notice.get("kind") and held.get("kind") in ("network",))]
        self._held.append(notice)
        self._held = self._held[-HELD_MAX:]

    def held(self) -> list[dict]:
        return [dict(item) for item in self._held]

    # ── giving back what was held ──────────────────────────────────────────
    def tick(self) -> list[dict]:
        """Expire old held notices and, once the person is back and nothing
        stops it, give them: one alone as itself, several as one summary."""
        settings = self._settings()
        with self._lock:
            now = self._clock()
            self._held = [item for item in self._held if item.get("expires_at", now + 1) > now]
            ctx = self._context(settings)
            away_now = ctx["away"]
            back = self._was_away and not away_now
            self._was_away = away_now
            if not self._held or ctx["snoozed"] or ctx["quiet"] or ctx["busy"] or away_now:
                return []
            reason = "away" if back else ""
            items, self._held = self._held, []
        out: list[dict] = []
        if len(items) == 1:
            item = items[0]
            with self._lock:
                self._mark_sent(item)
                self._record(item, "deliver", "later")
            self._send(item, speak=self._may_speak(settings, item["priority"], str(item.get("kind")),
                                                   quiet=False), silent=False)
            return [item]
        digest = build_digest(items, back_from_away=bool(reason))
        with self._lock:
            self._mark_sent(digest)
            self._record(digest, "deliver", "later")
        self._send(digest, speak=self._may_speak(settings, NORMAL, "digest", quiet=False), silent=False)
        out.append(digest)
        return out

    # ── answers to an offer ────────────────────────────────────────────────
    def offer(self, notice_id: str) -> dict | None:
        item = self._offers.get(str(notice_id or ""))
        return dict(item) if item else None

    def latest_offer(self) -> tuple[str, dict] | None:
        """The last offer still answerable with a bare "sim"."""
        with self._lock:
            if not self._last_offer:
                return None
            item = self._offers.get(self._last_offer)
            if not item or self._clock() - item["at"] > OFFER_SECONDS:
                return None
            return self._last_offer, dict(item)

    def close_offer(self, notice_id: str) -> None:
        with self._lock:
            self._offers.pop(str(notice_id or ""), None)
            if self._last_offer == notice_id:
                self._last_offer = None

    # ── the thread: gives back held notices when the person is back ───────
    def start(self) -> None:
        if getattr(self, "_thread", None) and self._thread.is_alive():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="nano-notice-gate", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        stop = getattr(self, "_stop", None)
        if stop is not None:
            stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(TICK_SECONDS):
            try:
                self.tick()
            except Exception:
                logger.exception("Notice gate loop failed")

    def status(self) -> dict:
        settings = self._settings()
        with self._lock:
            ctx = self._context(settings)
            return {"snooze": self.snooze_status(), "muted": self.muted(),
                    "held": len(self._held), "history": self.history()[:20],
                    "now": {"quiet": ctx["quiet"], "busy": self._busy_reason() if ctx["busy"] else "",
                            "away": ctx["away"]}}


def build_digest(items: list[dict], *, back_from_away: bool) -> dict:
    """Several held notices as one short message."""
    lines = [tr("Bem-vindo de volta. Enquanto estavas fora:") if back_from_away
             else tr("Enquanto estavas ocupado:")]
    for item in items[:8]:
        first = str(item.get("text") or "").strip().split("\n")[0]
        title = str(item.get("title") or "").strip()
        lines.append(f"• {title}: {first}" if title and not first.startswith(title) else f"• {first}")
    if len(items) > 8:
        lines.append(tr("• e mais {count}", count=len(items) - 8))
    titles = ", ".join(str(item.get("title") or "") for item in items[:3] if item.get("title"))
    speech = (tr("Enquanto estavas fora: {titles}.", titles=titles) if back_from_away
              else tr("Enquanto estavas ocupado: {titles}.", titles=titles)) if titles else ""
    return {"id": f"n-{uuid.uuid4().hex[:12]}", "kind": "digest", "priority": NORMAL,
            "title": tr("Resumo dos avisos"), "text": "\n".join(lines), "speech": speech}


__all__ = [
    "CRITICAL", "HIGH", "LEVELS", "LOW", "MUTABLE_KINDS", "NORMAL", "NoticeGate",
    "SETTING_DEFAULTS", "SNOOZE_CHOICES", "answer_kind", "build_digest", "in_quiet_hours",
    "parse_silence", "priority_of", "valid_setting",
]
