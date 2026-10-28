import logging
import math
import os
import random
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from prometheus_client import REGISTRY, Counter, Gauge, start_http_server
from redis import Redis
from redis.exceptions import RedisError

LOGGER = logging.getLogger(__name__)
TYPES = ("ETCH", "CVD", "PHOTO", "CMP", "IMP")


@dataclass(frozen=True)
class Settings:
    redis_url: str = "redis://localhost:6379/0"
    tool_count: int = 12
    tick_seconds: float = 2

    @classmethod
    def from_env(cls):
        settings = cls(
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            tool_count=int(os.getenv("TOOL_COUNT", "12")),
            tick_seconds=float(os.getenv("TICK_SECONDS", "2")),
        )
        if settings.tool_count < 1 or settings.tick_seconds <= 0 or not math.isfinite(settings.tick_seconds):
            raise ValueError("TOOL_COUNT and TICK_SECONDS must be positive and finite")
        return settings


def transition(status: str, roll: float) -> str:
    target, probability = {"RUN": ("DOWN", 0.03), "DOWN": ("IDLE", 0.2), "IDLE": ("RUN", 0.3)}[status]
    return target if roll < probability else status


class Simulator:
    def __init__(self, cache, count=12, registry=REGISTRY, random_value=random.random, clock=time.time):
        self.cache = cache
        self.random_value = random_value
        self.clock = clock
        self.tools = [
            {"id": f"{TYPES[i % 5]}-{i + 1:02}", "type": TYPES[i % 5], "status": "RUN",
             "temperature_c": 45.0, "wafer_count": 0, "updated_at": ""}
            for i in range(count)
        ]
        self.ticks = Counter("simulator_ticks_total", "Ticks successfully written to Redis", registry=registry)
        # 0 表示尚未成功寫入；只有 transaction 成功後才記錄，不能把嘗試誤算成成功。
        self.last_tick = Gauge("simulator_last_tick_timestamp_seconds", "Last successful Redis write (Unix seconds)", registry=registry)
        self.counts = Gauge("simulator_tools", "Tools by status", ["status"], registry=registry)
        for status in ("RUN", "IDLE", "DOWN"):
            self.counts.labels(status=status).set(0)

    def tick(self) -> bool:
        updated = []
        now = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()
        for previous in self.tools:
            tool = previous.copy()
            tool["status"] = transition(tool["status"], self.random_value())
            if tool["status"] == "RUN":
                tool["wafer_count"] += 1
            base = {"RUN": 65, "IDLE": 35, "DOWN": 25}[tool["status"]]
            tool["temperature_c"] = round(base + self.random_value() * 5, 1)
            tool["updated_at"] = now
            updated.append(tool)
        try:
            # 將同批寫入放進 transaction，減少往返並讓寫入一起完成。
            with self.cache.pipeline() as pipe:
                for tool in updated:
                    pipe.hset(f"tool:{tool['id']}", mapping=tool)
                pipe.execute()
        except RedisError:
            LOGGER.warning("Redis unavailable; retrying next tick")
            return False
        self.tools = updated
        self.ticks.inc()
        self.last_tick.set(self.clock())
        for status in ("RUN", "IDLE", "DOWN"):
            self.counts.labels(status=status).set(sum(tool["status"] == status for tool in self.tools))
        return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    cache = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
    simulator = Simulator(cache, settings.tool_count)
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    metrics_server, metrics_thread = start_http_server(8081)
    LOGGER.info("Simulating %s tools every %ss", settings.tool_count, settings.tick_seconds)
    try:
        while not stopped.is_set():
            simulator.tick()
            stopped.wait(settings.tick_seconds)
    finally:
        metrics_server.shutdown()
        metrics_server.server_close()
        metrics_thread.join()
        cache.close()


if __name__ == "__main__":
    main()
