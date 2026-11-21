"""What is playing on this PC, and the system volume, for the desktop island.

The island (electron/lib/island.js) shows the song that is playing -- Spotify,
a browser tab, any app that appears in Windows' own media controls -- with its
cover, title, artist and progress, and lets the person skip or pause from
there. It also shows a short level bar when the volume changes, whoever
changed it (the keyboard, Windows, or NANO itself).

WHERE IT COMES FROM
-------------------
* The song: Windows' "System Media Transport Controls" (the same source as the
  media flyout over the volume keys), through ``winrt.windows.media.control``.
  It is read only on Windows and only when that package is installed; without
  it the island simply shows no music. Nothing is asked of Spotify or of any
  web service, and nothing leaves this PC: the title and the cover go to the
  island window and nowhere else (not the phone, not the conversation).
* The volume: the default playback device's master volume, through the same
  ctypes endpoint the volume tools use (core.pc_control.winapi.AudioEndpoint).

WHAT THE ISLAND MAY DO
----------------------
Previous, play/pause and next, on the session that is shown -- exactly the
three things a keyboard's media keys do, which is why the policy engine
already treats ``pc.media.control`` as autonomous and reversible. Anything
else is refused here (MEDIA_ACTIONS), whatever the caller sends.

CHEAP BY DESIGN
---------------
The song is read once a second and the volume four times a second, both on
background threads, and an event is sent only when something changed: a new
song, play/pause, the available controls, a seek (the reported position moved
more than SEEK_TOLERANCE away from where it should be), or a new level. The
window moves the progress bar on by itself between events, from the position
and the moment Windows reported it.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as _dt
import logging
import sys
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("nano.now_playing")

MEDIA_ACTIONS = frozenset({"play_pause", "next", "previous"})
MEDIA_TICK_SECONDS = 1.0
VOLUME_TICK_SECONDS = 0.25
#: A position this far from where it should be is a seek, not clock noise.
SEEK_TOLERANCE = 2.5
#: Covers bigger than this are not sent (a normal cover is 20-60 KB).
MAX_ART_BYTES = 280_000
#: One reading (or one skip/pause) gives up after this long. Windows' media
#: controls can leave a call unanswered for ever -- seen with Spotify playing,
#: where reading the cover never completed -- and the island must not freeze.
WINDOWS_CALL_TIMEOUT = 4.0
ART_TYPES = {"image/png": "png", "image/jpeg": "jpeg", "image/jpg": "jpeg",
             "image/webp": "webp", "image/bmp": "bmp"}

#: SMTC playback status values (GlobalSystemMediaTransportControlsSessionPlaybackStatus).
_STATUS = {4: "playing", 5: "paused"}


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _seconds(value: Any) -> float:
    """A WinRT TimeSpan (timedelta, or 100 ns ticks) as seconds."""
    if isinstance(value, _dt.timedelta):
        return max(0.0, value.total_seconds())
    try:
        return max(0.0, float(getattr(value, "duration", value)) / 10_000_000)
    except (TypeError, ValueError):
        return 0.0


def _epoch_ms(value: Any, fallback: float) -> float:
    """A WinRT DateTime as epoch milliseconds; `fallback` when it is unset."""
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        stamp = value.timestamp() * 1000
        # An unset DateTime is year 1601; a stamp from the future is a clock
        # mismatch. Either way "now" is the honest anchor.
        return stamp if 0 < stamp <= fallback + 5000 else fallback
    return fallback


def app_name(app_id: str) -> str:
    """'Spotify.exe' -> 'Spotify'; 'Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic' -> 'ZuneMusic'."""
    name = str(app_id or "").split("!")[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    name = name.split(".")[-1] if "." in name else name
    return _text(name, 40)


def art_data_url(data: bytes | None, content_type: str | None) -> str:
    """A cover as a data: URL the island can show, or '' when it is unusable."""
    if not data or len(data) > MAX_ART_BYTES:
        return ""
    kind = ART_TYPES.get(str(content_type or "").lower().split(";")[0].strip())
    if kind is None:
        # Guess from the bytes: some apps report no content type.
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            kind = "png"
        elif data[:3] == b"\xff\xd8\xff":
            kind = "jpeg"
        else:
            return ""
    return f"data:image/{kind};base64,{base64.b64encode(data).decode('ascii')}"


# ── Reading Windows ─────────────────────────────────────────────────────────

def winrt_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winrt.windows.media.control  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


async def _read_art(reference) -> tuple[bytes | None, str | None]:
    if reference is None:
        return None, None
    from winrt.windows.storage.streams import Buffer, InputStreamOptions  # type: ignore

    stream = await reference.open_read_async()
    size = int(getattr(stream, "size", 0) or 0)
    if size <= 0 or size > MAX_ART_BYTES:
        return None, None
    # ReadAsync may hand back a different buffer than the one it was given.
    filled = await stream.read_async(Buffer(size), size, InputStreamOptions.READ_AHEAD)
    data = bytes(memoryview(filled))[: int(getattr(filled, "length", size))]
    return data, getattr(stream, "content_type", None)


async def _current_session():
    from winrt.windows.media.control import (  # type: ignore
        GlobalSystemMediaTransportControlsSessionManager as Manager)

    manager = await Manager.request_async()
    return manager.get_current_session()


async def _read_media_async(want_art_for: str | None) -> dict | None:
    session = await _current_session()
    if session is None:
        return None
    props = await session.try_get_media_properties_async()
    info = session.get_playback_info()
    timeline = session.get_timeline_properties()
    controls = getattr(info, "controls", None)
    app_id = str(getattr(session, "source_app_user_model_id", "") or "")
    title = _text(getattr(props, "title", ""), 120)
    artist = _text(getattr(props, "artist", "") or getattr(props, "album_artist", ""), 100)
    key = f"{app_id}|{title}|{artist}"
    now_ms = time.time() * 1000
    start = _seconds(getattr(timeline, "start_time", 0))
    end = _seconds(getattr(timeline, "end_time", 0))
    reading = {
        "key": key[:200],
        "app": app_name(app_id),
        "title": title,
        "artist": artist,
        "status": _STATUS.get(int(getattr(info, "playback_status", 0) or 0), "stopped"),
        "position": max(0.0, _seconds(getattr(timeline, "position", 0)) - start),
        "duration": max(0.0, end - start),
        "position_at": _epoch_ms(getattr(timeline, "last_updated_time", None), now_ms),
        "can_previous": bool(getattr(controls, "is_previous_enabled", True)),
        "can_next": bool(getattr(controls, "is_next_enabled", True)),
        "can_toggle": bool(getattr(controls, "is_play_pause_toggle_enabled", True)),
    }
    if want_art_for != key:
        try:
            data, content_type = await _read_art(getattr(props, "thumbnail", None))
            reading["art"] = art_data_url(data, content_type)
        except Exception:
            logger.debug("could not read the cover", exc_info=True)
            reading["art"] = ""
    return reading


def read_media(want_art_for: str | None = None) -> dict | None:
    """The current media session, or None (nothing playing, not Windows, no winrt).

    The cover is read only when the song is not `want_art_for`, so a song's
    cover is fetched once, not every second.
    """
    if not winrt_available():
        return None
    return _run_bounded(_read_media_async(want_art_for))


def _run_bounded(coroutine):
    """Run one Windows call, but never for longer than WINDOWS_CALL_TIMEOUT."""
    async def bounded():
        return await asyncio.wait_for(coroutine, WINDOWS_CALL_TIMEOUT)

    try:
        return asyncio.run(bounded())
    except (asyncio.TimeoutError, TimeoutError):
        logger.debug("Windows' media controls did not answer in %.0f s", WINDOWS_CALL_TIMEOUT)
        return None


async def _control_async(action: str) -> bool:
    session = await _current_session()
    if session is None:
        return False
    method = {"play_pause": "try_toggle_play_pause_async", "next": "try_skip_next_async",
              "previous": "try_skip_previous_async"}[action]
    return bool(await getattr(session, method)())


def read_volume_with(endpoint) -> dict:
    return {"level": int(round(endpoint.get_level() * 100)), "muted": bool(endpoint.get_mute())}


# ── Deciding what is news ───────────────────────────────────────────────────

class NowPlaying:
    """Turns readings into events. The readers are injectable for the tests."""

    def __init__(self, *, emit: Callable[[str, dict], None],
                 media_reader: Callable[[str | None], dict | None] = read_media,
                 clock: Callable[[], float] = time.time) -> None:
        self._emit = emit
        self._read_media = media_reader
        self._clock = clock
        self._last: dict | None = None
        self._art: tuple[str, str] = ("", "")       # (key, data url)
        self._volume: dict | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []

    # media
    def media_tick(self) -> dict | None:
        """Read the session once; emit and return the payload if it is news."""
        try:
            reading = self._read_media(self._art[0] or None)
        except Exception:
            logger.debug("media read failed", exc_info=True)
            reading = None
        if reading is not None and "art" in reading:
            self._art = (reading["key"], reading.pop("art") or "")
        if reading is None or reading.get("status") not in {"playing", "paused"} or not reading.get("title"):
            if self._last is not None:
                self._last = None
                payload = {"status": "none"}
                self._emit("media", payload)
                return payload
            return None
        changed = self._what_changed(reading)
        if not changed:
            return None
        payload = dict(reading, changed=changed,
                       art=self._art[1] if self._art[0] == reading["key"] else "")
        self._last = reading
        self._emit("media", payload)
        return payload

    def _what_changed(self, reading: dict) -> str | None:
        last = self._last
        if last is None or last["key"] != reading["key"]:
            return "track"
        for field in ("status", "can_previous", "can_next", "can_toggle", "duration"):
            if last.get(field) != reading.get(field):
                return field
        if reading["status"] == "playing" and reading["duration"] > 0:
            # Where the song is now, by the new reading and by the last one
            # sent. Apart by more than a few seconds: the person sought.
            now_ms = self._clock() * 1000
            reported_now = reading["position"] + max(0.0, now_ms - reading["position_at"]) / 1000
            expected_now = last["position"] + max(0.0, now_ms - last["position_at"]) / 1000
            if abs(reported_now - expected_now) > SEEK_TOLERANCE:
                return "seek"
        return None

    # volume
    def volume_tick(self, reading: dict | None) -> dict | None:
        if reading is None:
            return None
        if self._volume is None:
            self._volume = reading           # the level NANO started at is not news
            return None
        if reading == self._volume:
            return None
        self._volume = reading
        self._emit("volume", dict(reading))
        return reading

    # control
    def control(self, action: str) -> dict:
        action = str(action or "")
        if action not in MEDIA_ACTIONS:
            return {"ok": False, "error": "invalid_action"}
        done = False
        if winrt_available():
            try:
                done = bool(_run_bounded(_control_async(action)))
            except Exception:
                logger.debug("SMTC control failed", exc_info=True)
        if not done:
            # The same thing a keyboard's media key does.
            try:
                from core.pc_control import keyboard
                done = bool(keyboard.press_media(action).get("sent"))
            except Exception:
                logger.debug("media key failed", exc_info=True)
        self._wake.set()        # show the result quickly
        return {"ok": done}

    # threads
    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for target, name in ((self._media_loop, "nano-now-playing"), (self._volume_loop, "nano-volume-watch")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _media_loop(self) -> None:
        if not winrt_available():
            logger.info("Music on the island: off (needs Windows and winrt-Windows.Media.Control).")
            return
        while not self._stop.is_set():
            self.media_tick()
            self._wake.wait(MEDIA_TICK_SECONDS)
            if self._wake.is_set():
                self._wake.clear()
                self._stop.wait(0.35)   # let the app apply a skip before reading again

    def _volume_loop(self) -> None:
        if sys.platform != "win32":
            return
        from core.pc_control import winapi

        while not self._stop.is_set():
            try:
                # Reopened now and then, so a new default device (headphones
                # plugged in) is followed.
                with winapi.AudioEndpoint() as endpoint:
                    opened = time.monotonic()
                    while not self._stop.is_set() and time.monotonic() - opened < 30:
                        self.volume_tick(read_volume_with(endpoint))
                        self._stop.wait(VOLUME_TICK_SECONDS)
            except Exception:
                logger.debug("volume watch failed", exc_info=True)
                self._stop.wait(5.0)


__all__ = ["MEDIA_ACTIONS", "NowPlaying", "app_name", "art_data_url", "read_media", "winrt_available"]
