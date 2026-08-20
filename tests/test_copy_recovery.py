import unittest
from types import SimpleNamespace

from app.application.copy_recovery import record_copy_result


class CopyRecoveryTests(unittest.TestCase):
    def test_valid_copy_records_source_and_ready_state(self):
        listing = SimpleNamespace()

        record_copy_result(
            listing,
            title="RoseSeek Bracelet",
            description="A real provider description",
            source="coze",
            valid=True,
        )

        self.assertEqual(listing.listing_status, "ready")
        self.assertEqual(listing.copy_source, "coze")
        self.assertEqual(listing.copy_reason, "")
        self.assertIsNotNone(listing.copy_checked_at)

    def test_missing_copy_clears_text_and_records_reason(self):
        listing = SimpleNamespace(title="old", description="old")

        record_copy_result(
            listing,
            title="",
            description="",
            source="",
            valid=False,
            reason="provider unavailable",
        )

        self.assertEqual(listing.listing_status, "copy_missing")
        self.assertEqual(listing.title, "")
        self.assertEqual(listing.description, "")
        self.assertEqual(listing.copy_source, "")
        self.assertEqual(listing.copy_reason, "provider unavailable")
        self.assertIsNotNone(listing.copy_checked_at)


if __name__ == "__main__":
    unittest.main()
