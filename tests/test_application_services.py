import unittest


class ApplicationServiceTests(unittest.TestCase):
    def test_listing_creation_is_idempotent_by_product_account_market(self):
        from app.application.services import ListingService

        service = ListingService()
        first = service.create(product_id=1, account_id=2, market_code="TH", seller_sku="SKU-1")
        second = service.create(product_id=1, account_id=2, market_code="TH", seller_sku="SKU-2")

        self.assertIs(first, second)
        self.assertEqual(second.seller_sku, "SKU-1")

    def test_listing_cannot_be_ready_with_warnings(self):
        from app.application.services import ListingService
        from app.domain.errors import InvalidStateTransition

        service = ListingService()
        listing = service.create(product_id=1, account_id=2, market_code="TH", seller_sku="SKU-1")
        listing.warning_count = 1
        with self.assertRaises(InvalidStateTransition):
            service.mark_ready(listing)

    def test_task_claim_is_single_owner_and_success_requires_running(self):
        from app.application.services import TaskService
        from app.domain.errors import InvalidStateTransition, TaskAlreadyClaimed

        service = TaskService()
        task = service.create(task_type="upload")
        service.claim(task, owner="worker-a")
        with self.assertRaises(TaskAlreadyClaimed):
            service.claim(task, owner="worker-b")
        service.finish(task, owner="worker-a")
        self.assertEqual(task.status, "success")
        with self.assertRaises(InvalidStateTransition):
            service.finish(task, owner="worker-a")


if __name__ == "__main__":
    unittest.main()
