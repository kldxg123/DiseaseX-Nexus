from src.processors.mobility_proxy_builder import gravity_flow, infectious_inflow_index


def test_gravity_flow_positive():
    assert gravity_flow(1000, 2000, 10) > gravity_flow(1000, 2000, 100)


def test_infectious_inflow():
    dest = {"lat": 0, "lon": 0, "population": 1000}
    origins = [{"lat": 0, "lon": 1, "population": 1000, "active_cases": 10}]
    assert infectious_inflow_index(dest, origins) > 0


def test_fallback_without_real_traffic():
    dest = {"lat": 0, "lon": 0, "population": 1000}
    origins = [{"lat": 1, "lon": 1, "population": 1000, "active_cases": 5, "road_accessibility_index": 0.5}]
    assert infectious_inflow_index(dest, origins) > 0
