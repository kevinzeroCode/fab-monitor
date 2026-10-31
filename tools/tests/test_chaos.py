import json
import subprocess
import unittest
from unittest.mock import Mock

from tools.chaos import PRESETS, apply_chaos


def result(stdout):
    return Mock(stdout=stdout)


class ChaosTests(unittest.TestCase):
    def test_presets_match_web_buttons_and_clear_is_empty(self):
        self.assertEqual(PRESETS, {
            "latency": {"latency_ms": 500}, "errors": {"error_rate": 0.2}, "clear": {},
        })

    def test_every_running_pod_receives_the_body(self):
        run = Mock(side_effect=[
            result("pod/tool-api-a\npod/tool-api-b\n"),
            result('{"latency_ms":0,"error_rate":0.2}\n'),
            result('{"latency_ms":0,"error_rate":0.2}\n'),
        ])
        output = []
        failed = apply_chaos({"error_rate": 0.2}, context="kind-fab", run=run, emit=output.append)
        self.assertEqual(failed, [])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][:5], ["kubectl", "--context", "kind-fab", "-n", "fab"])
        self.assertIn("app=tool-api", commands[0])
        self.assertIn("--field-selector=status.phase=Running", commands[0])
        # 每個 Pod 各送一次：chaos 存在 Pod 自己的記憶體，打 NodePort 只會到其中一個。
        self.assertEqual([command[6] for command in commands[1:]], ["tool-api-a", "tool-api-b"])
        for command in commands[1:]:
            self.assertEqual(json.loads(command[-1]), {"error_rate": 0.2})
        # 沒有 GET /api/chaos，輸出 POST 回傳的 body，那就是該 Pod 當下生效的設定。
        self.assertEqual(output, [
            'tool-api-a {"latency_ms":0,"error_rate":0.2}',
            'tool-api-b {"latency_ms":0,"error_rate":0.2}',
        ])

    def test_one_pod_failing_still_tries_the_rest(self):
        # 清除時尤其重要：停在第一個失敗會讓其他 Pod 繼續故障。
        run = Mock(side_effect=[
            result("pod/tool-api-a\npod/tool-api-b\n"),
            subprocess.CalledProcessError(1, "kubectl", stderr="HTTP Error 403"),
            result('{"latency_ms":0,"error_rate":0.0}\n'),
        ])
        output = []
        failed = apply_chaos({}, run=run, emit=output.append)
        self.assertEqual(failed, ["tool-api-a"])
        self.assertEqual(run.call_count, 3)
        self.assertIn("tool-api-a FAILED", output[0])
        self.assertIn("HTTP Error 403", output[0])

    def test_no_running_pod_is_an_error(self):
        run = Mock(return_value=result(""))
        with self.assertRaises(ValueError):
            apply_chaos({}, run=run)
        self.assertEqual(run.call_count, 1)

    def test_pod_list_failure_is_not_hidden(self):
        run = Mock(side_effect=subprocess.CalledProcessError(1, "kubectl"))
        with self.assertRaises(subprocess.CalledProcessError):
            apply_chaos({}, run=run)


if __name__ == "__main__":
    unittest.main()
