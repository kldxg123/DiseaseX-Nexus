from src.collectors.openmeteo_collector import aggregate_hourly_to_daily
from src.processors.weather_aggregator import calculate_absolute_humidity, rolling_weather_features


def test_absolute_humidity_positive():
    assert calculate_absolute_humidity(25, 80) > 0


def test_rolling_weather_features():
    records = [
        {"date": f"2014-01-{idx+1:02d}", "country": "A", "adm1": "X", "temperature_2m_mean": 20 + idx, "relative_humidity_2m_mean": 70, "precipitation_sum": 1}
        for idx in range(30)
    ]
    features = rolling_weather_features(records)
    assert "temp_mean_7d" in features[-1]
    assert "absolute_humidity_14d" in features[-1]
    assert features[-1]["precipitation_sum_30d"] == 30


def test_hourly_aggregation_handles_missing_values():
    payload = {"hourly": {"time": ["2014-01-01T00:00", "2014-01-01T01:00"], "relative_humidity_2m": [70, None], "dew_point_2m": [20, 21]}}
    rows = aggregate_hourly_to_daily(payload)
    assert rows[0]["relative_humidity_2m_mean"] == 70
    assert rows[0]["dew_point_2m_mean"] == 20.5
