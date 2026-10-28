from prometheus_client import CollectorRegistry
from redis.exceptions import ConnectionError

from simulator.main import Simulator


class MemoryRedis:
    connected = True

    def pipeline(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def hset(self, *args, **kwargs):
        pass

    def execute(self):
        if not self.connected:
            raise ConnectionError('Redis unavailable')


def test_timestamp_only_advances_after_successful_write():
    now = [100.0]
    registry = CollectorRegistry()
    cache = MemoryRedis()
    simulator = Simulator(cache, count=1, registry=registry, clock=lambda: now[0])
    assert registry.get_sample_value('simulator_last_tick_timestamp_seconds') == 0
    assert simulator.tick() is True
    assert registry.get_sample_value('simulator_last_tick_timestamp_seconds') == 100
    now[0] = 150
    cache.connected = False
    assert simulator.tick() is False
    assert registry.get_sample_value('simulator_last_tick_timestamp_seconds') == 100
    cache.connected = True
    now[0] = 180
    assert simulator.tick() is True
    assert registry.get_sample_value('simulator_last_tick_timestamp_seconds') == 180
