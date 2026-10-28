import math
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from redis.exceptions import ConnectionError

from app.main import Settings, create_app


class MemoryRedis:
    """只替代 Redis I/O 邊界；HTTP、時間計算與 metrics 編碼走正式程式。"""

    def __init__(self, timestamps):
        self.data = {f"tool:{i}": {"updated_at": value} for i, value in enumerate(timestamps)}
        self.connected = True

    async def scan_iter(self, match):
        if not self.connected:
            raise ConnectionError("Redis unavailable")
        for key in list(self.data):
            yield key

    async def hgetall(self, key):
        return self.data.get(key, {})

    async def aclose(self):
        pass


def sample(response, name):
    assert response.status_code == 200
    return next(s.value for family in text_string_to_metric_families(response.text)
                for s in family.samples if s.name == name)


def timestamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def test_metrics_reports_oldest_tool_and_advances_without_api_reads():
    now = [100.0]
    cache = MemoryRedis([timestamp(98), timestamp(80)])
    with TestClient(create_app(Settings(), cache, clock=lambda: now[0])) as client:
        assert sample(client.get('/metrics'), 'tool_data_age_seconds') == 20
        now[0] = 150
        assert sample(client.get('/metrics'), 'tool_data_age_seconds') == 70
        # 沒有呼叫 /api/tools；停止寫入也必須能被監控看見。
        assert client.get('/health').status_code == 200


def test_future_timestamp_does_not_produce_negative_age():
    with TestClient(create_app(Settings(), MemoryRedis([timestamp(105)]), clock=lambda: 100)) as client:
        assert sample(client.get('/metrics'), 'tool_data_age_seconds') == 0


@pytest.mark.parametrize('timestamps', [[], ['broken'], ['2026-10-28T00:00:00'], [None]])
def test_unknown_age_is_nan_and_other_metrics_remain_available(timestamps):
    with TestClient(create_app(Settings(), MemoryRedis(timestamps), clock=lambda: 100)) as client:
        response = client.get('/metrics')
        assert math.isnan(sample(response, 'tool_data_age_seconds'))
        assert 'tool_api_info' in response.text


def test_redis_failure_does_not_reuse_previous_healthy_age_and_recovers():
    cache = MemoryRedis([timestamp(99)])
    with TestClient(create_app(Settings(), cache, clock=lambda: 100)) as client:
        assert sample(client.get('/metrics'), 'tool_data_age_seconds') == 1
        cache.connected = False
        response = client.get('/metrics')
        assert math.isnan(sample(response, 'tool_data_age_seconds'))
        assert 'http_requests_total' in response.text
        cache.connected = True
        cache.data['tool:0']['updated_at'] = timestamp(95)
        assert sample(client.get('/metrics'), 'tool_data_age_seconds') == 5


def test_empty_or_invalid_data_after_success_cannot_keep_last_age():
    cache = MemoryRedis([timestamp(99)])
    with TestClient(create_app(Settings(), cache, clock=lambda: 100)) as client:
        assert sample(client.get('/metrics'), 'tool_data_age_seconds') == 1
        cache.data['tool:0'] = {'updated_at': 'bad'}
        assert math.isnan(sample(client.get('/metrics'), 'tool_data_age_seconds'))
        cache.data.clear()
        assert math.isnan(sample(client.get('/metrics'), 'tool_data_age_seconds'))
