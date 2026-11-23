from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import requests
from contextlib import closing


class WeatherError(Exception):
    """A readable application-level error."""


@dataclass(frozen=True)
class City:
    name: str
    country: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Weather:
    city: City
    forecast_time: str
    temperature_c: float
    precipitation_probability: int


class GeocodingClient:              # геокодування
    URL = "https://geocoding-api.open-meteo.com/v1/search"

    def find_city(self, name: str) -> City:         # пошук міста
        data = request_json(self.URL, {"name": name, "count": 1, "language": "uk", "format": "json"})
        results = data.get("results")
        if not isinstance(results, list) or not results:
            raise WeatherError(f"Місто '{name}' не знайдено.")
        item = results[0]
        try:
            return City(str(item["name"]), str(item.get("country", "")), float(item["latitude"]), float(item["longitude"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise WeatherError("API повернув некоректні координати міста.") from exc


def request_json(url: str, params: dict) -> dict:       # надсилає HTTP до API, в-дь у форматі json
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise WeatherError("API повернув JSON неправильного типу.")
        return data
    except requests.exceptions.Timeout as exc:
        raise WeatherError("Час очікування відповіді API вичерпано.") from exc
    except requests.exceptions.RequestException as exc:
        raise WeatherError(f"Помилка з'єднання або HTTP: {exc}") from exc
    except ValueError as exc:
        raise WeatherError("API повернув некоректний JSON.") from exc


class WeatherAdapter:           # патерн адаптер
    """Adapter: external hourly JSON -> stable internal Weather models."""

    def normalize(self, city: City, payload: dict) -> list[Weather]:
        try:
            hourly = payload["hourly"]
            times = hourly["time"]
            temperatures = hourly["temperature_2m"]
            probabilities = hourly["precipitation_probability"]
            if not all(isinstance(a, list) for a in (times, temperatures, probabilities)):
                raise ValueError("Масиви прогнозу мають бути списками")
            if not times or len(times) != len(temperatures) or len(times) != len(probabilities):
                raise ValueError("Масиви прогнозу порожні або мають різну довжину")
            result = []
            for t, temp, prob in zip(times, temperatures, probabilities):       # об'єднує ел 3х списків за індексами
                dt = datetime.fromisoformat(t)
                if isinstance(temp, bool) or isinstance(prob, bool):
                    raise ValueError("Булеве значення замість числа")
                temp = float(temp)
                if not -100 <= temp <= 70:
                    raise ValueError("Температура поза допустимими межами")
                if not isinstance(prob, (int, float)) or not 0 <= prob <= 100:
                    raise ValueError("Імовірність опадів поза межами 0–100")
                result.append(Weather(city, dt.isoformat(timespec="minutes"), temp, int(prob)))
            return result
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise WeatherError(f"Некоректна структура прогнозу: {exc}") from exc


class ForecastSource(Protocol):
    def get_forecast(self, latitude: float, longitude: float) -> dict: ...


class ForecastProvider:         # постачальник
    """Real external API provider, without caching logic."""
    URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self):
        self.http_requests = 0      # лічильник запитів

    def get_forecast(self, latitude: float, longitude: float) -> dict:  # погодиний прогноз за коорд
        self.http_requests += 1
        return request_json(self.URL, {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "temperature_2m,precipitation_probability",
            "forecast_days": 2,
            "timezone": "auto",
        })


class ForecastCacheDecorator:           # патерн Декоратор
    """Decorator: same interface as ForecastSource, plus in-memory TTL."""

    def __init__(self, provider: ForecastSource, ttl_seconds: float = 600, clock=None):  # кешування
        self.provider = provider
        self.ttl_seconds = ttl_seconds
        self.clock = clock or time.monotonic
        self.cache: dict[tuple[float, float], tuple[float, dict]] = {}
        self.last_cache_hit = False

    def get_forecast(self, latitude: float, longitude: float) -> dict:
        key = (latitude, longitude)
        now = self.clock()
        entry = self.cache.get(key)
        if entry is not None and now < entry[0]:
            self.last_cache_hit = True
            return entry[1]
        self.last_cache_hit = False
        result = self.provider.get_forecast(latitude, longitude)
        self.cache[key] = (self.clock() + self.ttl_seconds, result)
        return result


class RecommendationStrategy(Protocol):
    def recommend(self, weather: Weather) -> str: ...


class WalkStrategy:
    def recommend(self, weather: Weather) -> str:
        t, p = weather.temperature_c, weather.precipitation_probability
        if p >= 70:
            return "Прогулянку краще перенести: висока ймовірність опадів."
        if t < 0 or t > 32:
            return "Для тривалої прогулянки температура несприятлива."
        if 10 <= t <= 26 and p < 40:
            return "Хороші умови для прогулянки."
        return "Прогулянка можлива, але врахуйте температуру та ймовірність опадів."


class ClothingStrategy:
    def recommend(self, weather: Weather) -> str:
        t = weather.temperature_c
        if t < 0:
            advice = "Тепла зимова куртка, шапка й рукавички."
        elif t < 12:
            advice = "Куртка або теплий верхній одяг."
        elif t < 22:
            advice = "Легка куртка чи кофта."
        else:
            advice = "Легкий одяг за погодою."
        if weather.precipitation_probability >= 50:
            advice += " Варто взяти водостійкий верхній одяг."
        return advice


class RainStrategy:
    def recommend(self, weather: Weather) -> str:
        p = weather.precipitation_probability
        if p >= 70:
            return "Висока ймовірність опадів: візьміть парасольку або дощовик."
        if p >= 40:
            return "Опади можливі: парасолька буде доречною."
        return "Імовірність опадів невисока; парасолька, ймовірно, не потрібна."


STRATEGIES: dict[str, RecommendationStrategy] = {
    "1": WalkStrategy(), "2": ClothingStrategy(), "3": RainStrategy()
}


class WeatherHistory:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("""CREATE TABLE IF NOT EXISTS weather_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                retrieved_at TEXT NOT NULL,
                forecast_time TEXT NOT NULL,
                temperature_c REAL NOT NULL,
                precipitation_probability INTEGER NOT NULL,
                source TEXT NOT NULL
            )""")


    def _connect(self):
        return sqlite3.connect(self.db_path)

    def save(self, forecasts: list[Weather]):
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        rows = [(w.city.name, w.city.latitude, w.city.longitude, timestamp,
                 w.forecast_time, w.temperature_c, w.precipitation_probability, "Open-Meteo")
                for w in forecasts]
        with closing(self._connect()) as connection:
            with connection:
                connection.executemany("""INSERT INTO weather_history
                (city, latitude, longitude, retrieved_at, forecast_time,
                 temperature_c, precipitation_probability, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", rows)


    def recent(self, city: str | None = None, limit: int = 10):
        with closing(self._connect()) as connection:
            if city:
                return connection.execute("""SELECT city, retrieved_at, forecast_time,
                    temperature_c, precipitation_probability FROM weather_history
                    WHERE city = ? COLLATE NOCASE ORDER BY id DESC LIMIT ?""", (city, limit)).fetchall()
            return connection.execute("""SELECT city, retrieved_at, forecast_time,
                temperature_c, precipitation_probability FROM weather_history
                ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()


class WeatherFacade:
    """Facade: a single entry point for the complete weather workflow."""

    def __init__(self, geocoder: GeocodingClient, provider: ForecastSource,
                 adapter: WeatherAdapter, history: WeatherHistory):
        self.geocoder = geocoder
        self.provider = provider
        self.adapter = adapter
        self.history = history

    def get_weather(self, city_name: str) -> list[Weather]:
        city = self.geocoder.find_city(city_name)
        raw = self.provider.get_forecast(city.latitude, city.longitude)
        forecasts = self.adapter.normalize(city, raw)
        # Show the three nearest forecast intervals, including current hour.
        now = datetime.now().astimezone()
        # Open-Meteo's timezone=auto returns local times for the city's timezone.
        # Use the UTC offset supplied in its response for correct cross-timezone filtering.
        offset = raw.get("utc_offset_seconds")
        if not isinstance(offset, (int, float)):
            raise WeatherError("API не повернув часовий зсув для прогнозу.")
        from datetime import timedelta, timezone
        city_now = now.astimezone(timezone(timedelta(seconds=int(offset)))).replace(tzinfo=None)
        current_hour = city_now.replace(minute=0, second=0, microsecond=0)
        upcoming = [w for w in forecasts if datetime.fromisoformat(w.forecast_time) >= current_hour]
        selected = upcoming[:3]
        if not selected:
            raise WeatherError("Немає доступних найближчих інтервалів прогнозу.")
        self.history.save(selected)  # Save selected intervals on every successful use, even cache hits.
        return selected
