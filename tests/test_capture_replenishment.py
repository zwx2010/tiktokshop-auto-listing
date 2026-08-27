import unittest


class CaptureReplenishmentTests(unittest.TestCase):
    def test_merge_capture_attempts_accumulates_new_and_filtered_items(self):
        from app.agent.approval import _merge_capture_attempts

        result = _merge_capture_attempts([
            {"ok": True, "stage_result": {
                "captured": 3, "imported": 0, "already": 2,
                "cost_filtered": 1, "run_dir": "run-1", "errors": []}},
            {"ok": True, "stage_result": {
                "captured": 3, "imported": 2, "already": 1,
                "cost_filtered": 0, "run_dir": "run-2", "errors": []}},
        ], "bridge_to_db")

        self.assertEqual(result["stage_result"]["captured"], 6)
        self.assertEqual(result["stage_result"]["imported"], 2)
        self.assertEqual(result["stage_result"]["already"], 3)
        self.assertEqual(result["stage_result"]["cost_filtered"], 1)
        self.assertEqual(result["stage_result"]["attempts"], 2)


if __name__ == "__main__":
    unittest.main()
