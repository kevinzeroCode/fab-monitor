import asyncio
import os
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest,
)
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError


@dataclass(frozen=True)
class Settings:
    redis_url: str = "redis://localhost:6379/0"
    version: str = "v1"
    pod_name: str = "local-api"
    node_name: str = "local"
    chaos_enabled: bool = False

    @classmethod
    def from_env(cls):
        return cls(
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            version=os.getenv("APP_VERSION", "v1"),
            pod_name=os.getenv("POD_NAME", "local-api"),
            node_name=os.getenv("NODE_NAME", "local"),
            chaos_enabled=os.getenv("CHAOS_ENABLED", "false").lower() == "true",
        )


class Chaos(BaseModel):
    latency_ms: int = Field(default=0, ge=0)
    error_rate: float = Field(default=0, ge=0, le=1)


def decode_tool(data: dict) -> dict:
    return {**data, "temperature_c": float(data["temperature_c"]),
            "wafer_count": int(data["wafer_count"])}


def create_app(settings: Settings | None = None, redis_client=None) -> FastAPI:
    settings = settings or Settings.from_env()
    cache = redis_client if redis_client is not None else Redis.from_url(
        settings.redis_url, decode_responses=True,
        socket_connect_timeout=1, socket_timeout=1,
    )

    @asynccontextmanager
    async def lifespan(app):
        yield
        await cache.aclose()

    async def apply_chaos(request: Request):
        # 保留控制端點與探針，才能在 100% 故障時清除注入並觀察健康狀態。
        if request.url.path.startswith("/api/") and request.url.path != "/api/chaos":
            chaos = app.state.chaos
            await asyncio.sleep(chaos.latency_ms / 1000)
            if random.random() < chaos.error_rate:
                raise HTTPException(status_code=500, detail="Injected chaos error")

    app = FastAPI(title="fab-monitor", lifespan=lifespan, dependencies=[Depends(apply_chaos)])
    app.state.chaos = Chaos()
    registry = CollectorRegistry()
    requests = Counter("http_requests_total", "HTTP responses", ["route", "method", "code"], registry=registry)
    duration = Histogram(
        "http_request_duration_seconds", "HTTP response latency", ["route"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.3, 0.5, 1, 2.5, 5), registry=registry,
    )
    Gauge("tool_api_info", "Application identity", ["version", "pod"], registry=registry).labels(
        version=settings.version, pod=settings.pod_name,
    ).set(1)

    @app.middleware("http")
    async def measure(request: Request, call_next):
        start = time.perf_counter()
        code = 500
        try:
            response = await call_next(request)
            code = response.status_code
            return response
        finally:
            # 路由完成比對後才取樣板，避免機台 ID 與任意 404 路徑造成 label 爆量。
            route = request.scope.get("route")
            label = getattr(route, "path", "unmatched")
            requests.labels(route=label, method=request.method, code=str(code)).inc()
            duration.labels(route=label).observe(time.perf_counter() - start)

    @app.exception_handler(RedisError)
    async def redis_unavailable(request, exc):
        return JSONResponse({"detail": "Redis unavailable"}, status_code=503)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        await cache.ping()
        return {"status": "ready"}

    @app.get("/api/tools")
    async def tools(status: Literal["RUN", "IDLE", "DOWN"] | None = None):
        result = []
        async for key in cache.scan_iter(match="tool:*"):
            data = await cache.hgetall(key)
            if data and (status is None or data["status"] == status):
                result.append(decode_tool(data))
        return sorted(result, key=lambda tool: tool["id"])

    @app.get("/api/tools/{id}")
    async def tool(id: str):
        data = await cache.hgetall(f"tool:{id}")
        if not data:
            raise HTTPException(status_code=404, detail="Tool not found")
        return decode_tool(data)

    @app.get("/api/whoami")
    async def whoami():
        return {"pod_name": settings.pod_name, "node_name": settings.node_name, "version": settings.version}

    @app.post("/api/chaos")
    async def chaos(body: Chaos):
        if not settings.chaos_enabled:
            raise HTTPException(status_code=403, detail="Chaos is disabled")
        app.state.chaos = body
        return body.model_dump()

    @app.get("/metrics")
    async def metrics():
        return Response(generate_latest(registry), headers={"Content-Type": CONTENT_TYPE_LATEST})

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return Path(__file__).with_name("index.html").read_text(encoding="utf-8")

    return app


app = create_app()
