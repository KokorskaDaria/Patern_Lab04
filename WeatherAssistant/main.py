from pathlib import Path
import sqlite3

from weather import (
    ClothingStrategy, ForecastCacheDecorator, ForecastProvider, GeocodingClient,
    RainStrategy, STRATEGIES, WalkStrategy, WeatherAdapter, WeatherError,
    WeatherFacade, WeatherHistory,
)


def show_history(history, city=None):
    rows = history.recent(city)
    if not rows:
        print("Історія порожня.")
    for name, retrieved, forecast_time, temp, rain in rows:
        print(f"{name:18} отримано {retrieved} | прогноз {forecast_time} | {temp:.1f} °C | опади {rain}%")


def main():
    provider = ForecastProvider()
    cached_provider = ForecastCacheDecorator(provider, ttl_seconds=600)
    history = WeatherHistory(Path(__file__).with_name("weather_history.db"))
    facade = WeatherFacade(GeocodingClient(), cached_provider, WeatherAdapter(), history)
    strategy = STRATEGIES["1"]

    while True:
        print("\n=== WEATHER ASSISTANT ===")
        print("1. Прогноз міста\n2. Вибрати стратегію\n3. Остання історія\n4. Історія міста\n5. Лічильник HTTP-запитів\n0. Вихід")
        option = input("Ваш вибір: ").strip()
        try:
            if option == "0":
                print("До побачення!")
                return
            if option == "1":
                name = input("Назва міста: ").strip()
                if len(name) < 2:
                    print("Введіть назву міста (не менше 2 символів).")
                    continue
                forecasts = facade.get_weather(name)
                print("Кеш прогнозу:", "HIT" if cached_provider.last_cache_hit else "MISS")
                for w in forecasts:
                    print(f"{w.city.name}, {w.city.country} | {w.forecast_time} | {w.temperature_c:.1f} °C | опади {w.precipitation_probability}%")
                    print("  Рекомендація:", strategy.recommend(w))
            elif option == "2":
                print("1. Прогулянка\n2. Одяг\n3. Опади")
                selection = input("Стратегія: ").strip()
                if selection in STRATEGIES:
                    strategy = STRATEGIES[selection]
                    print("Стратегію змінено.")
                else:
                    print("Невідома стратегія.")
            elif option == "3":
                show_history(history)
            elif option == "4":
                show_history(history, input("Місто: ").strip())
            elif option == "5":
                print("Фактичних запитів до Forecast API:", provider.http_requests)
            else:
                print("Невідомий пункт меню.")
        except (WeatherError, sqlite3.Error) as exc:
            print("Помилка:", exc)


if __name__ == "__main__":
    main()
