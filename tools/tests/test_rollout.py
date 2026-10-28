import json
import subprocess
import unittest
from unittest.mock import Mock

from tools.rollout import make_patch, rollout


class RolloutTests(unittest.TestCase):
    def test_image_and_display_version_change_in_one_template_patch(self):
        patch = make_patch("v2")
        containers = patch["spec"]["template"]["spec"]["containers"]
        self.assertEqual(containers, [{
            "name": "tool-api", "image": "fab/tool-api:v2",
            "env": [{"name": "APP_VERSION", "value": "v2"}],
        }])
        self.assertNotIn("replicas", patch["spec"])

    def test_valid_image_tags(self):
        for version in ["v1", "v2", "v2.1", "test_123", "a" * 128]:
            with self.subTest(version=version):
                self.assertEqual(make_patch(version)["spec"]["template"]["spec"]["containers"][0]["image"], f"fab/tool-api:{version}")

    def test_invalid_tags_never_start_build_or_kubectl(self):
        for version in ["", "bad version", "../v2", "v2:other", "-v2", "v2;echo", "a" * 129]:
            with self.subTest(version=version):
                run = Mock()
                with self.assertRaises(ValueError):
                    rollout(version, run=run)
                run.assert_not_called()

    def test_build_load_single_patch_and_wait_in_order(self):
        run = Mock()
        rollout("v2", context="kind-fab", run=run)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0], ["make", "build", "VERSION=v2"])
        # S 可能由父 make／環境傳入；內部不可使用會切換成壓測的 load。
        self.assertEqual(commands[1], ["make", "load-images", "VERSION=v2"])
        self.assertEqual(commands[2][:8], ["kubectl", "--context", "kind-fab", "-n", "fab", "patch", "deployment/tool-api", "--type=strategic"])
        self.assertEqual(json.loads(commands[2][-1]), make_patch("v2"))
        self.assertEqual(commands[3], ["kubectl", "--context", "kind-fab", "-n", "fab", "rollout", "status", "deployment/tool-api", "--timeout=180s"])
        for call in run.call_args_list:
            self.assertTrue(call.kwargs["check"])
            self.assertTrue(call.kwargs["cwd"].is_absolute())

    def test_build_failure_does_not_load_or_change_deployment(self):
        run = Mock(side_effect=subprocess.CalledProcessError(1, "make"))
        with self.assertRaises(subprocess.CalledProcessError):
            rollout("v2", run=run)
        self.assertEqual(run.call_count, 1)

    def test_load_failure_does_not_change_deployment(self):
        run = Mock(side_effect=[None, subprocess.CalledProcessError(1, "make")])
        with self.assertRaises(subprocess.CalledProcessError):
            rollout("v2", run=run)
        self.assertEqual(run.call_count, 2)

    def test_patch_failure_does_not_report_success_or_wait(self):
        run = Mock(side_effect=[None, None, subprocess.CalledProcessError(1, "kubectl")])
        with self.assertRaises(subprocess.CalledProcessError):
            rollout("v2", run=run)
        self.assertEqual(run.call_count, 3)

    def test_wait_failure_is_not_hidden(self):
        run = Mock(side_effect=[None, None, None, subprocess.CalledProcessError(1, "kubectl")])
        with self.assertRaises(subprocess.CalledProcessError):
            rollout("v2", run=run)


if __name__ == "__main__":
    unittest.main()
