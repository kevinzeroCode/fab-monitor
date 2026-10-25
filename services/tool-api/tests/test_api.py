import time

import fakeredis
import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from app.main import Settings, create_app


@pytest.fixture
def server():
    return fakeredis.FakeServer()


@pytest.fixture
def client(server):
    cache = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    settings = Settings(pod_name="compose-api", node_name="local", version="v1", chaos_enabled=True)
    with TestClient(create_app(settings, cache)) as client:
        yield client


@pytest.fixture
def seeded(server):
    cache = fakeredis.FakeRedis(server=server, decode_responses=True)
    for number, status in enumerate(["RUN", "DOWN"], 1):
        cache.hset(f"tool:ETCH-{number:02}", mapping={
            "id": f"ETCH-{number:02}", "type": "ETCH", "status": status,
            "temperature_c": "42.5", "wafer_count": "10",
            "updated_at": "2026-10-24T00:00:00+00:00",
        })
    yield cache
    cache.close()


def test_health_is_independent_of_redis(client, server):
    server.connected = False
    assert client.get("/health").json() == {"status": "ok"}


def test_ready_tracks_redis_and_recovers(client, server):
    assert client.get("/ready").status_code == 200
    server.connected = False
    assert client.get("/ready").status_code == 503
    assert client.get("/api/tools").status_code == 503
    assert client.get("/api/tools/ETCH-01").status_code == 503
    assert client.get("/").status_code == 200
    server.connected = True
    assert client.get("/ready").status_code == 200


def test_tools_list_filter_and_types(client, seeded):
    tools = client.get("/api/tools").json()
    assert len(tools) == 2
    assert tools[0]["temperature_c"] == 42.5
    assert tools[0]["wafer_count"] == 10
    assert [tool["status"] for tool in client.get("/api/tools?status=DOWN").json()] == ["DOWN"]
    assert client.get("/api/tools?status=broken").status_code == 422


def test_empty_tools(client):
    assert client.get("/api/tools").json() == []


def test_tool_detail_and_missing(client, seeded):
    assert client.get("/api/tools/ETCH-01").json()["id"] == "ETCH-01"
    assert client.get("/api/tools/missing").status_code == 404


def test_whoami(client):
    assert client.get("/api/whoami").json() == {
        "pod_name": "compose-api", "node_name": "local", "version": "v1",
    }


def test_settings_read_environment(monkeypatch):
    values = {"REDIS_URL": "redis://example:6379/1", "APP_VERSION": "test-v1",
              "POD_NAME": "api-test", "NODE_NAME": "test-node", "CHAOS_ENABLED": "true"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    assert settings.redis_url == values["REDIS_URL"]
    assert settings.version == "test-v1"
    assert settings.pod_name == "api-test"
    assert settings.node_name == "test-node"
    assert settings.chaos_enabled is True


def test_chaos_disabled_by_default(server):
    cache = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    with TestClient(create_app(Settings(), cache)) as client:
        assert client.post("/api/chaos", json={}).status_code == 403


def test_chaos_errors_clear_and_keep_control_available(client):
    assert client.post("/api/chaos", json={"error_rate": 1}).status_code == 200
    assert client.get("/api/whoami").status_code == 500
    assert client.get("/api/tools").status_code == 500
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/metrics").status_code == 200
    assert client.get("/").status_code == 200
    assert client.post("/api/chaos", json={}).json() == {"latency_ms": 0, "error_rate": 0}
    assert client.get("/api/whoami").status_code == 200


def test_chaos_latency_and_clear(client):
    client.post("/api/chaos", json={"latency_ms": 80})
    start = time.perf_counter()
    assert client.get("/api/whoami").status_code == 200
    assert time.perf_counter() - start >= 0.075
    client.post("/api/chaos", json={})
    assert client.app.state.chaos.latency_ms == 0


@pytest.mark.parametrize("body", [
    {"error_rate": -0.1}, {"error_rate": 1.1}, {"latency_ms": -1},
])
def test_chaos_rejects_invalid_values(client, body):
    assert client.post("/api/chaos", json=body).status_code == 422


def test_metrics_use_route_templates_and_required_buckets(client, seeded):
    client.get("/api/tools/ETCH-01")
    client.get("/api/tools/missing")
    client.get("/some-unknown-url")
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "text/plain" in metrics.headers["content-type"]
    assert 'route="/api/tools/{id}"' in metrics.text
    assert 'code="404"' in metrics.text
    assert 'route="unmatched"' in metrics.text
    assert 'route="/api/tools/ETCH-01"' not in metrics.text
    for bucket in ["0.1", "0.3", "0.5", "1.0"]:
        assert f'le="{bucket}"' in metrics.text
    assert 'tool_api_info{pod="compose-api",version="v1"} 1.0' in metrics.text


def test_dashboard_is_self_contained(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert all(path in response.text for path in ["/api/tools", "/api/whoami", "/api/chaos"])
    assert '<script src=' not in response.text
