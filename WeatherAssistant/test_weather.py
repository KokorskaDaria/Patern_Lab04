import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weather import (
    City, ClothingStrategy, ForecastCacheDecorator, RainStrategy, WalkStrategy,
    Weather, WeatherAdapter, WeatherError, WeatherFacade, WeatherHistory,
)


CITY = City("Львів", "Україна", 49.84, 24.03)
PAYLOAD = {"hourly": {"time": ["2026-11-19T12:00", "2026-11-19T13:00", "2026-11-19T14:00"],
                       "temperature_2m": [12.0, 15.0, 17.0],
                       "precipitation_probability": [20, 50, 90]}, "utc_offset_seconds": 7200}


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def get_forecast(self, latitude, longitude):
        self.calls += 1
        return PAYLOAD


class WeatherTests(unittest.TestCase):
    def test_adapter(self):
        data = WeatherAdapter().normalize(CITY, PAYLOAD)
        self.assertEqual(len(data), 3)
        self.assertEqual(data[1].precipitation_probability, 50)

    def test_adapter_rejects_mismatched_arrays(self):
        bad = {"hourly": {"time": ["2026-11-19T12:00"],
                          "temperature_2m": [12, 15], "precipitation_probability": [20]}}
        with self.assertRaises(WeatherError):
            WeatherAdapter().normalize(CITY, bad)

    def test_cache_and_expiry(self):
        clock = [0]
        provider = FakeProvider()
        cache = ForecastCacheDecorator(provider, 600, lambda: clock[0])
        cache.get_forecast(49.84, 24.03)
        cache.get_forecast(49.84, 24.03)
        self.assertEqual(provider.calls, 1)
        self.assertTrue(cache.last_cache_hit)
        cache.get_forecast(50.0, 25.0)
        self.assertEqual(provider.calls, 2)
        clock[0] = 601
        cache.get_forecast(49.84, 24.03)
        self.assertEqual(provider.calls, 3)

    def test_strategies(self):
        weather = Weather(CITY, "2026-11-19T12:00", 15.0, 80)
        self.assertIn("перенести", WalkStrategy().recommend(weather))
        self.assertIn("водостійкий", ClothingStrategy().recommend(weather))
        self.assertIn("парасольку", RainStrategy().recommend(weather))

    def test_persistent_history(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "history.db"
            w = Weather(CITY, "2026-11-19T12:00", 15.0, 80)
            WeatherHistory(db).save([w])
            self.assertEqual(len(WeatherHistory(db).recent("Львів")), 1)
            self.assertEqual(len(WeatherHistory(db).recent("Київ")), 0)


if __name__ == "__main__":
    unittest.main()
