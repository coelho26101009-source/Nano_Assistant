"""NANO keeps an eye on the screen for a while, and says when something changes.

"Avisa-me quando o download acabar", "diz-me se aparecer um erro": the person
asks once, approves once, and NANO checks every few seconds until the
condition is met or the time runs out, then says so like the morning briefing
does (a message in the chat and a Windows notification).

WHAT IT LOOKS AT
----------------
* **Window titles** (``window_appears`` / ``window_closes``): the same list
  ``pc_window_list`` already reads without asking. No picture is taken.
* **Screen text** (``appears`` / ``disappears``): the primary monitor read with
  the LOCAL Windows OCR, only while "Ler texto do ecrã" is on in Privacidade.
  Nothing leaves the PC, no image or text is kept, and a check is skipped when
  a password manager or authenticator window is visible.

WHAT IT NEVER DOES
------------------
It never acts: when the condition is met it only tells the person. Starting a
watch is an approval-gated tool (``pc.screen.watch``), bounded to
``MAX_MINUTES`` and ``MAX_WATCHES``. What it saw never goes back to the model,
so a page on screen cannot use a watch to give NANO orders.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from core.i18n import tr

logger = logging.getLogger("nano.screen_watch")

MAX_WATCHES = 3
MAX_MINUTES = 120
DEFAULT_MINUTES = 30
MAX_WORDS = 6
MAX_WORD_CHARS = 60
#: Screen text is OCR'd at most this often (it is the expensive check).
TEXT_INTERVAL_SECONDS = 10.0
WINDOW_INTERVAL_SECONDS = 4.0
TICK_SECONDS = 2.0
#: "disappears" / "window_closes" must hold on this many checks in a row, so
#: one OCR miss or a window redrawing does not count as gone.
CONFIRMATIONS = 2


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").casefold()).strip()


def _rose(base: dict[str, int], low: dict[str, int], word: str, count: int) -> bool:
    """True when ``word`` is on screen MORE often than when the watch began.

    "Appears" used to mean "is on screen", so a watch fired on its first check
    whenever the word was already there -- and it usually was, because NANO's
    own chat shows the request: "avisa-me se aparecer erro" contains "erro".
    The first reading is now the baseline and only a rise counts. A lower
    reading lowers the baseline only after CONFIRMATIONS checks in a row, so
    one OCR miss cannot make the same text "appear" again on the next check.
    """
    if word not in base:
        base[word] = count
        return False
    if count > base[word]:
        return True
    if count < base[word]:
        low[word] = low.get(word, 0) + 1
        if low[word] >= CONFIRMATIONS:
            base[word], low[word] = count, 0
    else:
        low[word] = 0
    return False


def _words(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for item in items:
        word = _norm(item)[:MAX_WORD_CHARS]
        if word and word not in out:
            out.append(word)
    return out[:MAX_WORDS]


@dataclass
class Watch:
    goal: str
    appears: list[str] = field(default_factory=list)
    disappears: list[str] = field(default_factory=list)
    window_appears: list[str] = field(default_factory=list)
    window_closes: list[str] = field(default_factory=list)
    expires_at: float = 0.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    started_at: float = 0.0
    next_text_at: float = 0.0
    next_window_at: float = 0.0
    seen_text: bool = False
    seen_window: bool = False
    gone_text: int = 0
    gone_window: int = 0
    #: How often each "appears" word was on screen when the watch began, and
    #: how many checks in a row it has been seen less often. See `_rose`.
    text_base: dict[str, int] = field(default_factory=dict)
    text_low: dict[str, int] = field(default_factory=dict)
    window_base: dict[str, int] = field(default_factory=dict)
    window_low: dict[str, int] = field(default_factory=dict)

    @property
    def needs_text(self) -> bool:
        return bool(self.appears or self.disappears)

    @property
    def needs_windows(self) -> bool:
        return bool(self.window_appears or self.window_closes)

    def describe(self, now: float) -> dict:
        return {"id": self.id, "goal": self.goal, "appears": self.appears,
                "disappears": self.disappears, "windowAppears": self.window_appears,
                "windowCloses": self.window_closes,
                "minutesLeft": max(0, round((self.expires_at - now) / 60))}


class ScreenWatcher:
    """The active watches and the thread that checks them. Tests drive ``tick``."""

    def __init__(self, *, read_text: Callable[[], str | None] | None = None,
                 list_titles: Callable[[], list[str]] | None = None,
                 emit: Callable[[dict], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self._read_text = read_text
        self._list_titles = list_titles
        self._emit = emit
        self._clock = clock
        self._lock = threading.Lock()
        self._watches: dict[str, Watch] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── wiring ───────────────────────────────────────────────────────────
    def configure(self, *, read_text=None, list_titles=None, emit=None) -> None:
        if read_text is not None:
            self._read_text = read_text
        if list_titles is not None:
            self._list_titles = list_titles
        if emit is not None:
            self._emit = emit

    # ── the API the tools use ────────────────────────────────────────────
    def start(self, goal: str, *, appears: Any = None, disappears: Any = None,
              window_appears: Any = None, window_closes: Any = None,
              minutes: Any = None, text_allowed: bool = True) -> dict:
        goal = " ".join(str(goal or "").split())[:160]
        watch = Watch(goal=goal or tr("Vigiar o ecrã"), appears=_words(appears),
                      disappears=_words(disappears), window_appears=_words(window_appears),
                      window_closes=_words(window_closes))
        if not (watch.needs_text or watch.needs_windows):
            return {"ok": False, "error": "no_condition",
                    "message": tr("Diz o que devo procurar: um texto que aparece ou desaparece, "
                                  "ou uma janela que abre ou fecha.")}
        if watch.needs_text and not text_allowed:
            return {"ok": False, "error": "screen_text_disabled",
                    "message": tr("Para vigiar texto no ecrã, liga primeiro Definições → Privacidade → "
                                  "Ler texto do ecrã. Vigiar janelas funciona sem isso.")}
        try:
            span = float(minutes) if minutes not in (None, "") else DEFAULT_MINUTES
        except (TypeError, ValueError):
            span = DEFAULT_MINUTES
        span = max(1.0, min(float(MAX_MINUTES), span))
        now = self._clock()
        watch.started_at = now
        watch.expires_at = now + span * 60
        if watch.window_appears:
            # Listing windows is cheap, so the baseline is taken now: a window
            # that opens a second after the request still counts as opening.
            # Screen text is OCR (slow), so its baseline is the first check.
            found = self._safe(self._list_titles)
            if isinstance(found, list):
                titles = [_norm(t) for t in found]
                for word in watch.window_appears:
                    watch.window_base[word] = sum(word in title for title in titles)
        with self._lock:
            if len(self._watches) >= MAX_WATCHES:
                return {"ok": False, "error": "too_many_watches",
                        "message": tr("Já estou a vigiar {count} coisas. Pede-me para parar uma primeiro.",
                                      count=len(self._watches))}
            self._watches[watch.id] = watch
        self._ensure_thread()
        logger.info("Vigia %s iniciada por %d min", watch.id, int(span))
        return {"ok": True, "watch": watch.describe(now),
                "message": tr("Combinado: vigio o ecrã durante {minutes} min e aviso-te. "
                              "Diz “para de vigiar” para eu parar.", minutes=int(span))}

    def stop(self, watch_id: str | None = None) -> dict:
        with self._lock:
            if watch_id:
                removed = [self._watches.pop(watch_id)] if watch_id in self._watches else []
            else:
                removed = list(self._watches.values())
                self._watches.clear()
        return {"ok": True, "stopped": [w.id for w in removed],
                "message": (tr("Parei de vigiar o ecrã.") if removed
                            else tr("Não estava a vigiar nada."))}

    def status(self) -> list[dict]:
        now = self._clock()
        with self._lock:
            return [w.describe(now) for w in self._watches.values()]

    # ── the decision ─────────────────────────────────────────────────────
    def tick(self) -> list[dict]:
        now = self._clock()
        with self._lock:
            watches = list(self._watches.values())
        if not watches:
            return []
        text = titles = None
        if any(w.needs_text and now >= w.next_text_at for w in watches):
            text = self._safe(self._read_text)
        if any(w.needs_windows and now >= w.next_window_at for w in watches):
            found = self._safe(self._list_titles)
            titles = [_norm(t) for t in found] if isinstance(found, list) else None

        notices: list[dict] = []
        for watch in watches:
            notice = self._check(watch, now, text, titles)
            if notice is not None:
                with self._lock:
                    self._watches.pop(watch.id, None)
                notices.append(notice)
        for notice in notices:
            try:
                if self._emit:
                    self._emit(notice)
            except Exception:
                logger.debug("Falha a mostrar um aviso de vigia", exc_info=True)
        return notices

    def _check(self, watch: Watch, now: float, text: str | None, titles: list[str] | None) -> dict | None:
        if watch.needs_text and now >= watch.next_text_at and text is not None:
            watch.next_text_at = now + TEXT_INTERVAL_SECONDS
            screen = _norm(text)
            for word in watch.appears:
                if _rose(watch.text_base, watch.text_low, word, screen.count(word)):
                    return self._notice(watch, tr("apareceu “{word}” no ecrã.", word=word))
            if watch.disappears:
                present = any(word in screen for word in watch.disappears)
                if present:
                    watch.seen_text, watch.gone_text = True, 0
                elif watch.seen_text:
                    watch.gone_text += 1
                    if watch.gone_text >= CONFIRMATIONS:
                        return self._notice(watch, tr("“{word}” já não está no ecrã.",
                                                      word=watch.disappears[0]))
        if watch.needs_windows and now >= watch.next_window_at and titles is not None:
            watch.next_window_at = now + WINDOW_INTERVAL_SECONDS
            for word in watch.window_appears:
                count = sum(word in title for title in titles)
                if _rose(watch.window_base, watch.window_low, word, count):
                    return self._notice(watch, tr("abriu uma janela com “{word}”.", word=word))
            if watch.window_closes:
                present = any(word in title for title in titles for word in watch.window_closes)
                if present:
                    watch.seen_window, watch.gone_window = True, 0
                elif watch.seen_window:
                    watch.gone_window += 1
                    if watch.gone_window >= CONFIRMATIONS:
                        return self._notice(watch, tr("a janela “{word}” fechou.",
                                                      word=watch.window_closes[0]))
        if now >= watch.expires_at:
            return {"kind": "watch", "title": tr("Deixei de vigiar"),
                    "text": tr("{goal}: o tempo acabou e não vi a mudança.", goal=watch.goal)}
        return None

    @staticmethod
    def _notice(watch: Watch, what: str) -> dict:
        return {"kind": "watch", "title": tr("O ecrã mudou"), "text": f"{watch.goal}: {what}"}

    @staticmethod
    def _safe(function):
        if function is None:
            return None
        try:
            return function()
        except Exception:
            logger.debug("Leitura para a vigia falhou", exc_info=True)
            return None

    # ── the thread ───────────────────────────────────────────────────────
    def _ensure_thread(self) -> None:
        # Under the same lock the loop holds when it decides to exit. Without
        # it a watch added just as the last one ended found the thread "alive",
        # started nothing, and was never checked: the loop was already leaving.
        # Each thread gets its own stop event, so a thread still winding down
        # after shutdown() cannot be revived by the next start().
        with self._lock:
            if self._thread is not None and self._thread.is_alive() and not self._stop.is_set():
                return
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._loop, args=(self._stop,),
                                            name="nano-screen-watch", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        self.stop()

    def _loop(self, stop: threading.Event | None = None) -> None:
        stop = stop or self._stop
        while not stop.wait(TICK_SECONDS):
            with self._lock:
                if not self._watches:
                    if self._thread is threading.current_thread():
                        self._thread = None
                    return
            try:
                self.tick()
            except Exception:
                logger.exception("Falha no ciclo da vigia do ecrã")


#: The one watcher NANO uses; plugins/pc_control.py and core/main.py wire it.
WATCHER = ScreenWatcher()

__all__ = ["MAX_MINUTES", "MAX_WATCHES", "ScreenWatcher", "WATCHER", "Watch"]
