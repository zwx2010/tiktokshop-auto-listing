import unittest


class W4ProbeTests(unittest.TestCase):
    def setUp(self):
        from app.integrations import probes
        probes._RECEIPTS.clear()

    def test_probe_confirmed_expires_and_config_change_invalidates(self):
        from app.integrations.probes import confirmed, probe
        probe("feishu", lambda: "read-only ok", config={"app": "a"}, now=100)
        self.assertTrue(confirmed("feishu", config={"app": "a"}, now=100 + 10))
        self.assertFalse(confirmed("feishu", config={"app": "a"}, now=100 + 86401))
        self.assertFalse(confirmed("feishu", config={"app": "b"}, now=100 + 10))

    def test_probe_failure_is_explicit(self):
        from app.integrations.probes import probe
        result = probe("tiktok", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
        self.assertEqual(result.status, "failed")
        self.assertIn("offline", result.detail)

    def test_release_gate_requires_all_probes_approval_and_limit(self):
        from app.application.publication import validate_test_release
        good = validate_test_release(candidate_count=50, account="store-ph",
            market="ph", probes={k: True for k in ("feishu", "ai", "cdp", "tiktok")},
            approved=True, rollback_plan="disable listings by upload ids")
        self.assertTrue(good["eligible"])
        bad = validate_test_release(candidate_count=51, account="store-ph", market="ph",
            probes={k: True for k in ("feishu", "ai", "cdp", "tiktok")}, approved=True,
            rollback_plan="rollback")
        self.assertFalse(bad["eligible"])
        self.assertIn("candidate_count", bad["failed_gates"])
