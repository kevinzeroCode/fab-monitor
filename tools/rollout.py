"""建置、載入，再以單次 Pod template 修改更新 API 版本。"""

import argparse
import json
from pathlib import Path
import re
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_patch(version: str) -> dict:
    # 在傳給遞迴 make 前驗證 tag，並確保 image 與顯示版本由同一個值產生。
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", version):
        raise ValueError("Version must be a valid image tag, for example v2")
    return {"spec": {"template": {"spec": {"containers": [{
        "name": "tool-api",
        "image": f"fab/tool-api:{version}",
        "env": [{"name": "APP_VERSION", "value": version}],
    }]}}}}


def rollout(version: str, context: str = "kind-fab", run=subprocess.run) -> None:
    patch = make_patch(version)
    kubectl = ["kubectl", "--context", context, "-n", "fab"]
    commands = [
        ["make", "build", f"VERSION={version}"],
        # 明確匯入 image，不受 S 環境變數或父 make 的壓測參數影響。
        ["make", "load-images", f"VERSION={version}"],
        # strategic merge 以容器與 env 的 name 合併，保留既有 Downward API、
        # probes 與其他設定；同一次 patch 避免 image/env 分開觸發兩次更新。
        kubectl + ["patch", "deployment/tool-api", "--type=strategic", "--patch", json.dumps(patch)],
        kubectl + ["rollout", "status", "deployment/tool-api", "--timeout=180s"],
    ]
    for command in commands:
        run(command, check=True, cwd=PROJECT_ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="API image tag and APP_VERSION, for example v2")
    parser.add_argument("--context", default="kind-fab", help="Explicit target Kubernetes context")
    args = parser.parse_args()
    try:
        rollout(args.version, context=args.context)
    except (ValueError, OSError) as error:
        parser.exit(1, f"rollout: {error}\n")
    except subprocess.CalledProcessError as error:
        # 前一步失敗即停止，不在載入失敗後仍修改正在提供服務的 Deployment。
        parser.exit(1, f"rollout: command failed (exit {error.returncode}); stopped\n")


if __name__ == "__main__":
    main()
