import asyncio

import anyio
import httpx
import pytest

from tools import loadgen


class Clock:
    now = 0.0

    async def sleep(self, delay):
        self.now += delay
        await asyncio.sleep(0)


def test_lazy_backend_initialization_is_outside_timed_window(monkeypatch):
    clock = Clock()
    initialized = False
    starts = []

    async def initialize_backend(delay):
        nonlocal initialized
        if not initialized:
            # 首次網路後端準備耗 120ms；不應吃掉量測窗的第二個 50ms 時槽。
            clock.now += .12
            initialized = True
        await asyncio.sleep(0)

    monkeypatch.setattr(anyio, 'sleep', initialize_backend)

    async def respond(request):
        starts.append(clock.now)
        # 模擬 HTTP client 首次連線才延遲初始化 async backend 的行為。
        await anyio.sleep(0)
        return httpx.Response(200)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await loadgen.run_load(client, 'http://example/api/tools', seconds=.2, rps=20,
                                          clock=lambda: clock.now, sleep=clock.sleep)

    result = asyncio.run(exercise())
    assert result['sent'] == result['scheduled'] == 4
    assert result['dropped'] == 0
    assert result['elapsed_seconds'] == pytest.approx(.2)
    assert result['preparation_seconds'] == pytest.approx(.12)
    assert starts == pytest.approx([.12, .17, .22, .27])


def test_fixed_rate_and_http_status_statistics():
    clock = Clock()
    starts = []

    async def respond(request):
        starts.append(clock.now)
        return httpx.Response(200 if len(starts) % 2 else 500)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await loadgen.run_load(client, 'http://example/api/tools', seconds=1, rps=4,
                                          clock=lambda: clock.now, sleep=clock.sleep)

    result = asyncio.run(exercise())
    assert starts == pytest.approx([0, .25, .5, .75])
    assert result['scheduled'] == result['sent'] == 4
    assert result['dropped'] == 0
    assert result['status_codes'] == {'200': 2, '500': 2}
    assert result['network_errors'] == 0
    assert result['drop_reasons'] == {'schedule_late': 0, 'concurrency_limit': 0, 'window_ended': 0}
    assert result['first_drop'] is None


def test_slow_requests_are_bounded_and_overload_is_reported():
    clock = Clock()

    async def exercise():
        release = asyncio.Event()

        async def respond(request):
            await release.wait()
            return httpx.Response(200)

        async def paced_sleep(delay):
            await clock.sleep(delay)
            if clock.now >= 1:
                release.set()

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await loadgen.run_load(client, 'http://example/api/tools', seconds=1, rps=10,
                                          concurrency=2, clock=lambda: clock.now, sleep=paced_sleep)

    result = asyncio.run(exercise())
    assert result['sent'] == 2
    assert result['dropped'] == 8
    assert result['scheduled'] == result['sent'] + result['dropped']
    assert result['drop_reasons']['concurrency_limit'] == 8
    assert result['first_drop']['slot'] == 3
    assert result['first_drop']['in_flight'] == 2


@pytest.mark.parametrize('delay,reason,count', [(.3, 'schedule_late', 1), (1, 'window_ended', 3)])
def test_late_schedule_reports_reason_slot_and_lateness(delay, reason, count):
    clock = Clock()
    delayed = False

    async def sleep_with_jitter(seconds):
        nonlocal delayed
        await clock.sleep(seconds)
        if seconds > 0 and not delayed:
            clock.now += delay
            delayed = True

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
            return await loadgen.run_load(client, 'http://example/api/tools', seconds=1, rps=4,
                                          clock=lambda: clock.now, sleep=sleep_with_jitter)

    result = asyncio.run(exercise())
    assert result['dropped'] == count
    assert result['drop_reasons'][reason] == count
    assert sum(result['drop_reasons'].values()) == result['dropped']
    assert result['first_drop']['reason'] == reason
    assert result['first_drop']['slot'] == 2
    assert result['first_drop']['scheduled_offset_ms'] == 250
    assert result['first_drop']['lateness_ms'] == pytest.approx(delay * 1000)


def test_network_errors_are_counted_without_aborting_other_requests():
    clock = Clock()

    async def respond(request):
        raise httpx.ReadTimeout('timeout', request=request)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await loadgen.run_load(client, 'http://example/api/tools', seconds=1, rps=3,
                                          clock=lambda: clock.now, sleep=clock.sleep)

    result = asyncio.run(exercise())
    assert result['sent'] == result['network_errors'] == 3
    assert result['status_codes'] == {}


def test_unexpected_request_error_is_not_silently_discarded():
    clock = Clock()
    requests = []

    async def respond(request):
        requests.append(request)
        if len(requests) == 1:
            raise RuntimeError('unexpected transport error')
        return httpx.Response(200)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(RuntimeError, match='unexpected transport error'):
                await loadgen.run_load(client, 'http://example/api/tools', seconds=1, rps=3,
                                       clock=lambda: clock.now, sleep=clock.sleep)

    asyncio.run(exercise())


@pytest.mark.parametrize('kwargs', [
    {'seconds': 0}, {'seconds': float('inf')}, {'rps': -1}, {'rps': float('nan')},
    {'concurrency': 0}, {'seconds': 1e308, 'rps': 1e308},
])
def test_invalid_load_is_rejected_before_requests(kwargs):
    async def exercise():
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError):
                await loadgen.run_load(client, 'http://example/api/tools', **kwargs)

    asyncio.run(exercise())
