"""固定發送速率的本機 API 壓測工具。"""

import argparse
import asyncio
import json
import math
from collections import Counter
from urllib.parse import urlsplit

import anyio
import httpx


async def run_load(client, url, seconds=300, rps=20, concurrency=100, *, clock=None, sleep=asyncio.sleep):
    if (not math.isfinite(seconds) or seconds <= 0 or not math.isfinite(rps) or rps <= 0
            or not math.isfinite(seconds * rps) or concurrency < 1):
        raise ValueError('seconds、rps 必須是正有限數，concurrency 至少為 1')
    clock = clock or asyncio.get_running_loop().time
    preparation_start = clock()
    scheduled = math.ceil(seconds * rps)
    codes = Counter()
    pending = set()
    sent = dropped = network_errors = 0
    latency_sum = latency_max = 0.0
    drop_reasons = {'schedule_late': 0, 'concurrency_limit': 0, 'window_ended': 0}
    first_drop = None

    def record_drop(reason, slot, now, count=1):
        nonlocal dropped, first_drop
        dropped += count
        drop_reasons[reason] += count
        # 只留第一筆，長時間壓測也不會累積無上限的診斷紀錄。
        if first_drop is None:
            first_drop = {
                'reason': reason, 'slot': slot + 1,
                'scheduled_offset_ms': round(slot / rps * 1000, 3),
                'lateness_ms': round(max(0, now - start - slot / rps) * 1000, 3),
                'in_flight': len(pending),
            }

    async def request():
        nonlocal network_errors, latency_sum, latency_max
        began = clock()
        try:
            response = await client.get(url)
            codes[str(response.status_code)] += 1
        except httpx.RequestError:
            network_errors += 1
        finally:
            elapsed = max(0, clock() - began)
            latency_sum += elapsed
            latency_max = max(latency_max, elapsed)

    # HTTPX／HTTPCore 首次連線會延遲載入 AnyIO backend；透過公開 API
    # 先完成後端準備，再開始固定 RPS 計時，避免初始化吃掉第二個時槽。
    await anyio.sleep(0)
    start = clock()
    try:
        for i in range(scheduled):
            due = start + i / rps
            await sleep(max(0, due - clock()))
            for task in tuple(pending):
                if task.done():
                    pending.remove(task)
                    # 先取結果再釋放，不能默默丟掉非 HTTP 連線類的程式錯誤。
                    task.result()
            # 不因慢回應降低預定發送頻率；超載時明確記錄略過，避免無限排隊。
            now = clock()
            if now >= start + seconds:
                record_drop('window_ended', i, now, scheduled - i)
                break
            if now - due >= 1 / rps:
                record_drop('schedule_late', i, now)
                continue
            if len(pending) >= concurrency:
                record_drop('concurrency_limit', i, now)
                continue
            pending.add(asyncio.create_task(request()))
            sent += 1
            await sleep(0)
        await sleep(max(0, start + seconds - clock()))
        if pending:
            await asyncio.gather(*pending)
    finally:
        # Ctrl+C 或非預期錯誤時不留下背景請求。
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    return {
        'requested_rps': rps, 'duration_seconds': seconds,
        'preparation_seconds': round(start - preparation_start, 3),
        'elapsed_seconds': round(clock() - start, 3),
        'scheduled': scheduled, 'sent': sent, 'dropped': dropped,
        'drop_reasons': drop_reasons, 'first_drop': first_drop,
        'achieved_send_rps': round(sent / seconds, 3),
        'status_codes': dict(sorted(codes.items())), 'network_errors': network_errors,
        'mean_latency_ms': round(latency_sum * 1000 / sent, 3) if sent else None,
        'max_latency_ms': round(latency_max * 1000, 3) if sent else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:30080/api/tools')
    parser.add_argument('--seconds', type=float, default=300)
    parser.add_argument('--rps', type=float, default=20)
    parser.add_argument('--concurrency', type=int, default=100)
    parser.add_argument('--timeout', type=float, default=2)
    args = parser.parse_args()
    if urlsplit(args.url).scheme not in {'http', 'https'} or not urlsplit(args.url).hostname:
        parser.error('--url 必須是 HTTP(S) 網址')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout 必須是正有限數')

    async def execute():
        limits = httpx.Limits(max_connections=max(1, args.concurrency), max_keepalive_connections=20)
        async with httpx.AsyncClient(timeout=args.timeout, limits=limits, trust_env=False) as client:
            return await run_load(client, args.url, args.seconds, args.rps, args.concurrency)

    try:
        result = asyncio.run(execute())
    except ValueError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # HTTP／連線失敗和發送不足都要回報，不能只因程序跑完就判定壓測成功。
    return int(result['network_errors'] > 0 or result['dropped'] > 0
               or any(int(code) >= 400 for code in result['status_codes']))


if __name__ == '__main__':
    raise SystemExit(main())
