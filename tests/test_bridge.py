import json
import unittest
from unittest.mock import patch


class ClaudeBridgeTests(unittest.TestCase):
    @patch("app.agent.bridge.subprocess.run")
    @patch("app.agent.bridge.claude_bin", return_value="claude")
    def test_successful_envelope_ignores_stderr_warning(self, _bin, run):
        from app.agent.bridge import run_claude

        run.return_value.returncode = 0
        run.return_value.stderr = '[claude-code:unrecognized_model] {"model":"deepseek-v4-flash"}'
        run.return_value.stdout = json.dumps({
            "result": '{"pong": true}', "is_error": False, "session_id": "s1",
        })

        result = run_claude("ping")

        self.assertTrue(result["ok"])
        self.assertEqual(result["stage_result"], {"pong": True})
        self.assertEqual(result["error"], "")


if __name__ == "__main__":
    unittest.main()
