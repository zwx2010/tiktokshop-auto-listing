import unittest


class WorkflowIntegrationTests(unittest.TestCase):
    def test_publication_gate_blocks_missing_copy_or_failed_image_qa(self):
        from app.application.workflow import PublicationGate
        from app.domain.errors import InvalidStateTransition

        gate = PublicationGate()
        with self.assertRaises(InvalidStateTransition):
            gate.check(copy_status="copy_missing", image_qa="pass", approved=True)
        with self.assertRaises(InvalidStateTransition):
            gate.check(copy_status="ready", image_qa="fail", approved=True)
        self.assertTrue(gate.check(copy_status="ready", image_qa="pass", approved=True))

    def test_publication_gate_blocks_unconfirmed_account_integration(self):
        from app.application.workflow import PublicationGate
        from app.domain.errors import InvalidStateTransition

        with self.assertRaises(InvalidStateTransition):
            PublicationGate().check(
                copy_status="ready",
                image_qa="pass",
                approved=True,
                account_integration_status="not_configured",
            )

    def test_publication_gate_reports_all_failed_gates(self):
        from app.application.workflow import evaluate_publication_gates

        result = evaluate_publication_gates(
            copy_status="copy_missing",
            image_qa="fail",
            approved=False,
            account_integration_status="failed",
        )

        self.assertFalse(result["eligible"])
        self.assertEqual(
            result["failed_gates"], ["copy", "image_qa", "approval", "account_integration"]
        )

    def test_worker_runs_one_task_at_a_time_and_records_failure(self):
        from app.jobs.worker import TaskWorker

        events = []
        worker = TaskWorker(handlers={"upload": lambda task: events.append(task.task_type)})
        task = worker.add_task("upload")
        self.assertTrue(worker.run_once())
        self.assertEqual(events, ["upload"])
        self.assertEqual(task.status, "success")
        self.assertFalse(worker.run_once())

        failed = TaskWorker(handlers={"copy": lambda task: (_ for _ in ()).throw(RuntimeError("copy failed"))})
        bad_task = failed.add_task("copy")
        self.assertTrue(failed.run_once())
        self.assertEqual(bad_task.status, "failed")
        self.assertIn("copy failed", bad_task.logs[-1])

    def test_external_errors_are_classified_for_retry_or_human_review(self):
        from app.integrations.errors import ExternalError, classify_external_error

        self.assertEqual(classify_external_error(ExternalError("timeout", retryable=True)), "retryable")
        self.assertEqual(classify_external_error(ExternalError("policy", retryable=False)), "human_review")

    def test_listing_workflow_stops_before_cdp_when_copy_is_missing(self):
        from app.application.pipeline import ListingWorkflow

        calls = []

        class Copy:
            def generate(self, product, market_code):
                calls.append("copy")
                return {"status": "copy_missing"}

        class Qa:
            def check(self, image_urls, market_code):
                calls.append("qa")
                return {"overall": "pass"}

        class Feishu:
            def send_approval(self, payload):
                calls.append("approval")
                return "approval-1"

        class Cdp:
            def submit_listing(self, payload):
                calls.append("cdp")
                return {"status": "submitted"}

        result = ListingWorkflow(copy=Copy(), image_qa=Qa(), feishu=Feishu(), cdp=Cdp()).run(
            {"image_urls": ["https://img"], "market_code": "TH"}
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(calls, ["copy"])

    def test_listing_workflow_requires_explicit_account_integration_status(self):
        from app.application.pipeline import ListingWorkflow
        from app.domain.errors import InvalidStateTransition

        class Copy:
            def generate(self, product, market_code):
                return {"status": "ready", "title": "t", "description": "d"}

        class Qa:
            def check(self, image_urls, market_code):
                return {"overall": "pass"}

        class Feishu:
            def send_approval(self, payload):
                return "approval-1"

        class Cdp:
            def submit_listing(self, payload):
                raise AssertionError("CDP must not be called")

        workflow = ListingWorkflow(copy=Copy(), image_qa=Qa(), feishu=Feishu(), cdp=Cdp())
        with self.assertRaises(InvalidStateTransition):
            workflow.submit_after_approval({
                "copy": {"status": "ready"},
                "image_qa": {"overall": "pass"},
                "approved": True,
            })

    def test_legacy_copy_gateway_never_returns_fake_ready_copy(self):
        from app.coze_client import CopyResult
        from app.integrations.legacy import LegacyCopyGateway

        class MissingClient:
            def generate_copy(self, product, market_code):
                return CopyResult(source="")

        result = LegacyCopyGateway(MissingClient()).generate({"goods_id": "1"}, "TH")
        self.assertEqual(result["status"], "copy_missing")


if __name__ == "__main__":
    unittest.main()
