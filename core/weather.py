"""Today's weather for the morning briefing, from Open-Meteo (free, no key).

WHAT LEAVES THE PC
------------------
Only when the person typed a city in Definições → Geral → "Cidade para o
tempo": the city NAME once, to Open-Meteo's geocoding service, to find its
coordinates; then those coordinates (rounded to two decimals, about 1 km) to
Open-Meteo's forecast service, at most once every 30 minutes. Nothing about
the person, no key, no account. With the field empty this module makes no
request at all and the briefing simply has no weather line.

Open-Meteo (open-meteo.com) is free for non-commercial use and needs no
sign-up, which is what NANO's "free, nothing to configure" promise needs.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from core.i18n import tr

logger = logging.getLogger("nano.weather")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_SECONDS = 30 * 60
TIMEOUT_SECONDS = 6.0
RAIN_LIKELY = 50
MAX_CITY = 60


def valid_city(value: Any) -> bool:
    return isinstance(value, str) and len(value.strip()) <= MAX_CITY and \
        not any(ord(ch) < 32 for ch in value)


def describe_code(code: Any) -> str:
    """A WMO weather code in a few words."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return ""
    if code == 0:
        return tr("céu limpo")
    if code in (1, 2):
        return tr("pouco nublado")
    if code == 3:
        return tr("nublado")
    if code in (45, 48):
        return tr("nevoeiro")
    if 51 <= code <= 57:
        return tr("chuviscos")
    if 61 <= code <= 67:
        return tr("chuva")
    if 71 <= code <= 77 or code in (85, 86):
        return tr("neve")
    if 80 <= code <= 82:
        return tr("aguaceiros")
    if code >= 95:
        return tr("trovoada")
    return ""


def _part_of_day(hour: int) -> str:
    if hour < 12:
        return tr("de manhã")
    if hour < 19:
        return tr("à tarde")
    return tr("à noite")


def _http_get_json(url: str, params: dict) -> dict:
    from core import safe_http

    with safe_http.public_client(timeout=TIMEOUT_SECONDS, headers={"User-Agent": "NANO-assistant"},
                                 follow_redirects=False) as client:
        response = client.get(f"{url}?{urlencode(params)}")
        response.raise_for_status()
        data = response.json()
    return data if isinstance(data, dict) else {}


def summarise(forecast: dict, now: datetime, city: str) -> dict | None:
    """``{"line", "speech", "rain"}`` for today, or None if the data is unusable."""
    current = forecast.get("current") or {}
    daily = forecast.get("daily") or {}
    try:
        high = round(float((daily.get("temperature_2m_max") or [None])[0]))
        low = round(float((daily.get("temperature_2m_min") or [None])[0]))
    except (TypeError, ValueError):
        return None
    temp = current.get("temperature_2m")
    words = describe_code(current.get("weather_code", (daily.get("weather_code") or [None])[0]))
    # The first hour still ahead today when rain is likely.
    rain_hour, rain_chance = None, 0
    hourly = forecast.get("hourly") or {}
    for stamp, chance in zip(hourly.get("time") or [], hourly.get("precipitation_probability") or []):
        try:
            moment = datetime.fromisoformat(str(stamp))
            chance = int(chance or 0)
        except (TypeError, ValueError):
            continue
        if moment.date() != now.date() or moment.hour < now.hour:
            continue
        if chance >= RAIN_LIKELY:
            rain_hour, rain_chance = moment.hour, chance
            break
    place = city.strip()
    if isinstance(temp, (int, float)):
        line = tr("{city}: {temp}° agora, {words}, entre {low}° e {high}°.",
                  city=place, temp=round(float(temp)), words=words or "—", low=low, high=high)
    else:
        line = tr("{city}: entre {low}° e {high}°, {words}.", city=place, low=low, high=high,
                  words=words or "—")
    speech = tr("Em {city} estão {temp} graus, máxima de {high}.", city=place,
                temp=round(float(temp)) if isinstance(temp, (int, float)) else low, high=high)
    if rain_hour is not None:
        when = _part_of_day(rain_hour)
        line += " " + tr("Chuva provável {when} ({chance}%): leva guarda-chuva.", when=when, chance=rain_chance)
        speech += " " + tr("Deve chover {when}, leva guarda-chuva.", when=when)
    # Open-Meteo's data is CC BY 4.0: every place that shows it credits the
    # source (https://open-meteo.com/en/licence). The spoken version needn't.
    line += " " + tr("Dados: Open-Meteo.com.")
    return {"line": line, "speech": speech, "rain": rain_hour is not None}


class Weather:
    """Geocode once per city, forecast at most every 30 minutes."""

    def __init__(self, *, cache_path: Path | None = None,
                 fetch: Callable[[str, dict], dict] = _http_get_json,
                 clock: Callable[[], float] = time.time) -> None:
        self._fetch = fetch
        self._clock = clock
        self.cache_path = cache_path
        self._lock = threading.Lock()
        self._cache = self._load()

    def _load(self) -> dict:
        try:
            if self.cache_path and self.cache_path.exists():
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            logger.debug("Weather cache unreadable", exc_info=True)
        return {}

    def _save(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache), encoding="utf-8")
        except Exception:
            logger.debug("Could not save the weather cache", exc_info=True)

    def forget(self) -> None:
        with self._lock:
            self._cache = {}
            try:
                if self.cache_path and self.cache_path.exists():
                    self.cache_path.unlink()
            except Exception:
                logger.debug("Could not delete the weather cache", exc_info=True)

    def locate(self, city: str) -> dict | None:
        """``{"name", "lat", "lon", "country"}`` for a city name, cached."""
        key = city.strip().casefold()
        if not key:
            return None
        place = (self._cache.get("places") or {}).get(key)
        if place:
            return place
        data = self._fetch(GEOCODE_URL, {"name": city.strip()[:MAX_CITY], "count": 1, "format": "json"})
        results = data.get("results") or []
        if not results or not isinstance(results[0], dict):
            return None
        first = results[0]
        try:
            place = {"name": str(first.get("name") or city)[:MAX_CITY],
                     "lat": round(float(first["latitude"]), 2), "lon": round(float(first["longitude"]), 2),
                     "country": str(first.get("country") or "")[:60]}
        except (KeyError, TypeError, ValueError):
            return None
        with self._lock:
            places = self._cache.setdefault("places", {})
            places[key] = place
            if len(places) > 8:
                places.pop(next(iter(places)))
            self._save()
        return place

    def today(self, city: str, now: datetime | None = None) -> dict | None:
        """Today's summary for ``city``, or None (no city, offline, unknown)."""
        if not city or not valid_city(city):
            return None
        now = now or datetime.now()
        try:
            place = self.locate(city)
            if place is None:
                return None
            cached = self._cache.get("forecast") or {}
            if (cached.get("key") == f"{place['lat']},{place['lon']}"
                    and self._clock() - float(cached.get("at") or 0) < CACHE_SECONDS):
                forecast = cached.get("data") or {}
            else:
                forecast = self._fetch(FORECAST_URL, {
                    "latitude": place["lat"], "longitude": place["lon"],
                    "current": "temperature_2m,weather_code",
                    "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                    "hourly": "precipitation_probability",
                    "timezone": "auto", "forecast_days": 1,
                })
                with self._lock:
                    self._cache["forecast"] = {"key": f"{place['lat']},{place['lon']}",
                                               "at": self._clock(), "data": forecast}
                    self._save()
            return summarise(forecast, now, place["name"])
        except Exception:
            logger.info("Weather unavailable for the briefing", exc_info=True)
            return None


__all__ = ["Weather", "describe_code", "summarise", "valid_city"]
