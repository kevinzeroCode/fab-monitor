"""對每一個 tool-api Pod 注入或清除故障。"""

import argparse
import json
import subprocess


PRESETS = {
    "latency": {"latency_ms": 500},
    "errors": {"error_rate": 0.2},
    "clear": {},
}

# slim image 沒有 curl，用容器內一定有的 Python 送請求。403 等錯誤會讓程序非零結束。
POST_SCRIPT = """
import sys, urllib.request
request = urllib.request.Request(
    "http://127.0.0.1:8080/api/chaos", data=sys.argv[1].encode(),
    headers={"Content-Type": "application/json"}, method="POST",
)
print(urllib.request.urlopen(request, timeout=5).read().decode())
"""


def apply_chaos(body: dict, context: str = "kind-fab", run=subprocess.run, emit=print) -> list[str]:
    kubectl = ["kubectl", "--context", context, "-n", "fab"]
    listed = run(
        kubectl + ["get", "pods", "-l", "app=tool-api", "--field-selector=status.phase=Running", "-o", "name"],
        check=True, capture_output=True, text=True,
    )
    pods = [line.removeprefix("pod/") for line in listed.stdout.split()]
    if not pods:
        raise ValueError(f"No running tool-api pod found in {context}/fab")

    failed = []
    # chaos 存在每個 Pod 自己的記憶體，打 NodePort 只會改到其中一個，所以要逐一進 Pod 送。
    # 失敗也繼續處理其餘 Pod：停在中途會留下部分 Pod 仍在故障，清除時尤其不能這樣。
    for pod in pods:
        try:
            sent = run(
                kubectl + ["exec", pod, "-c", "tool-api", "--", "python", "-c", POST_SCRIPT, json.dumps(body)],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as error:
            failed.append(pod)
            emit(f"{pod} FAILED: {(error.stderr or '').strip()}")
        else:
            # 沒有 GET /api/chaos；POST 回傳的 body 就是這個 Pod 當下生效的設定。
            emit(f"{pod} {sent.stdout.strip()}")
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=sorted(PRESETS), help="Fault to inject, or clear")
    parser.add_argument("--context", default="kind-fab", help="Explicit target Kubernetes context")
    args = parser.parse_args()
    try:
        failed = apply_chaos(PRESETS[args.mode], context=args.context)
    except (ValueError, OSError) as error:
        parser.exit(1, f"chaos: {error}\n")
    except subprocess.CalledProcessError as error:
        parser.exit(1, f"chaos: could not list pods (exit {error.returncode})\n")
    if failed:
        parser.exit(1, f"chaos: failed on {', '.join(failed)}\n")


if __name__ == "__main__":
    main()
