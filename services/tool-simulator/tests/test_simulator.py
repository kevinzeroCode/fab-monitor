from datetime import datetime

import fakeredis
import pytest
from prometheus_client import CollectorRegistry, generate_latest

from simulator.main import Simulator, Settings, transition


@pytest.mark.parametrize("before,roll,after", [
    ("RUN", 0.029, "DOWN"), ("RUN", 0.03, "RUN"),
    ("DOWN", 0.19, "IDLE"), ("DOWN", 0.20, "DOWN"),
    ("IDLE", 0.29, "RUN"), ("IDLE", 0.30, "IDLE"),
])
def test_transition_probabilities(before, roll, after):
    assert transition(before, roll) == after


def test_default_tools_and_tick_metrics():
    cache = fakeredis.FakeRedis(decode_responses=True)
    registry = CollectorRegistry()
    simulator = Simulator(cache, registry=registry, random_value=lambda: 0.99)
    simulator.tick()
    tools = [cache.hgetall(key) for key in sorted(cache.scan_iter("tool:*"))]
    assert len(tools) == 12
    assert [tool["id"] for tool in simulator.tools[:6]] == [
        "ETCH-01", "CVD-02", "PHOTO-03", "CMP-04", "IMP-05", "ETCH-06",
    ]
    for tool in tools:
        assert set(tool) == {"id", "type", "status", "temperature_c", "wafer_count", "updated_at"}
        assert float(tool["temperature_c"]) > 0
        assert datetime.fromisoformat(tool["updated_at"]).tzinfo is not None
    before = int(cache.hget("tool:ETCH-01", "wafer_count"))
    simulator.tick()
    assert int(cache.hget("tool:ETCH-01", "wafer_count")) > before
    metrics = generate_latest(registry).decode()
    assert "simulator_ticks_total 2.0" in metrics
    assert 'simulator_tools{status="RUN"} 12.0' in metrics
    assert 'simulator_tools{status="IDLE"} 0.0' in metrics
    assert 'simulator_tools{status="DOWN"} 0.0' in metrics


def test_down_and_idle_tools_do_not_produce_wafers():
    cache = fakeredis.FakeRedis(decode_responses=True)
    simulator = Simulator(cache, count=1, registry=CollectorRegistry(), random_value=lambda: 0.0)
    simulator.tick()
    assert cache.hget("tool:ETCH-01", "status") == "DOWN"
    assert cache.hget("tool:ETCH-01", "wafer_count") == "0"
    simulator.tick()
    assert cache.hget("tool:ETCH-01", "status") == "IDLE"
    assert cache.hget("tool:ETCH-01", "wafer_count") == "0"
    simulator.tick()
    assert cache.hget("tool:ETCH-01", "status") == "RUN"
    assert int(cache.hget("tool:ETCH-01", "wafer_count")) > 0


def test_tick_recovers_after_redis_returns():
    server = fakeredis.FakeServer()
    cache = fakeredis.FakeRedis(server=server, decode_responses=True)
    registry = CollectorRegistry()
    simulator = Simulator(cache, count=1, registry=registry)
    server.connected = False
    assert simulator.tick() is False
    assert "simulator_ticks_total 0.0" in generate_latest(registry).decode()
    server.connected = True
    assert simulator.tick() is True
    assert cache.exists("tool:ETCH-01")


def test_simulator_settings(monkeypatch):
    monkeypatch.setenv("TOOL_COUNT", "5")
    monkeypatch.setenv("TICK_SECONDS", "0.5")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    settings = Settings.from_env()
    assert settings.tool_count == 5
    assert settings.tick_seconds == 0.5
    assert settings.redis_url == "redis://redis:6379/0"


@pytest.mark.parametrize("name,value", [("TOOL_COUNT", "0"), ("TICK_SECONDS", "0")])
def test_invalid_simulator_settings(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        Settings.from_env()
