"""Lightweight event bus for Nano internal communication."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable


class EventBus:
    """Small publish/subscribe event bus used by task orchestration and notifications."""

    def __init__(self):
        self._listeners: dict[str, list[Callable[[dict], None]]] = defaultdict(list)
        self._history: list[dict] = []

    def subscribe(self, event_name: str, callback: Callable[[dict], None]) -> Callable[[], None]:
        self._listeners[event_name].append(callback)

        def unsubscribe() -> None:
            listeners = self._listeners.get(event_name, [])
            if callback in listeners:
                listeners.remove(callback)

        return unsubscribe

    def publish(self, event_name: str, payload: dict | None = None) -> None:
        event_payload = dict(payload or {})
        self._history.append({"event": event_name, "payload": event_payload, "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()})
        self._history = self._history[-200:]
        for callback in list(self._listeners.get(event_name, [])):
            try:
                callback(event_payload)
            except Exception:
                pass

    def get_listener_count(self, event_name: str) -> int:
        return len(self._listeners.get(event_name, []))

    def get_recent_events(self, limit: int = 20) -> list[dict]:
        return list(reversed(self._history[-max(1, int(limit)):]))
