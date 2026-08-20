import unittest

from app.integrations.outcomes import IntegrationOutcome, credential_status
from app.integrations.status import integration_statuses


class _CredentialQuery:
    def all(self):
        return []


class _Db:
    def query(self, model):
        return _CredentialQuery()


class IntegrationStatusTests(unittest.TestCase):
    def test_missing_credentials_are_not_configured_and_not_success(self):
        result = credential_status("ai", "copy_generation", {"COZE_API_TOKEN": ""})

        self.assertEqual(result.status, "not_configured")
        self.assertFalse(result.is_success)
        self.assertEqual(result.details, {"missing": ["COZE_API_TOKEN"]})

    def test_configured_credentials_are_not_claimed_as_connected(self):
        result = credential_status("ai", "copy_generation", {"COZE_API_TOKEN": "secret"})

        self.assertEqual(result.status, "configured")
        self.assertFalse(result.is_success)
        self.assertNotIn("secret", result.as_dict()["details"])

    def test_local_status_snapshot_is_explicit_about_missing_services(self):
        results = {item.provider: item for item in integration_statuses(_Db(), env={})}

        self.assertEqual(results["tiktok"].status, "not_configured")
        self.assertEqual(results["ai"].status, "not_configured")
        self.assertEqual(results["cdp"].status, "not_configured")
        self.assertTrue(all(not item.is_success for item in results.values()))


if __name__ == "__main__":
    unittest.main()
