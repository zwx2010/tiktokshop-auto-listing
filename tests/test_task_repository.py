import unittest


class TaskRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database import Base
        from app import models  # noqa: F401

        cls.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        from app.models import ApprovalAudit, ApprovalRun, Task, TaskLog

        session = self.Session()
        session.query(TaskLog).delete()
        session.query(Task).delete()
        session.query(ApprovalRun).delete()
        session.query(ApprovalAudit).delete()
        session.commit()
        session.close()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_claim_and_finish_are_persistent_and_single_owner(self):
        from app.infrastructure.task_repository import SqlAlchemyTaskRepository

        session = self.Session()
        repo = SqlAlchemyTaskRepository(session)
        task = repo.create("upload")
        claimed = repo.claim_pending(owner="worker-a")
        self.assertEqual(claimed.id, task.id)
        self.assertEqual(claimed.status, "running")
        self.assertEqual(repo.claim_pending(owner="worker-b"), None)
        repo.finish(claimed, owner="worker-a", success=True)
        session.expire_all()
        self.assertEqual(repo.get(task.id).status, "success")

    def test_persistent_worker_executes_handler_and_closes_session(self):
        from app.jobs.worker import PersistentTaskWorker
        from app.infrastructure.task_repository import SqlAlchemyTaskRepository

        seed = self.Session()
        task = SqlAlchemyTaskRepository(seed).create("upload")
        seed.close()
        seen = []
        worker = PersistentTaskWorker(
            session_factory=self.Session,
            handlers={"upload": lambda item: seen.append(item.id)},
            owner="worker-a",
        )
        self.assertTrue(worker.run_once())
        self.assertEqual(seen, [task.id])
        check = self.Session()
        self.assertEqual(SqlAlchemyTaskRepository(check).get(task.id).status, "success")
        check.close()

    def test_worker_entrypoint_builds_the_persistent_worker(self):
        from app.jobs.worker import PersistentTaskWorker
        from scripts.worker import build_worker

        worker = build_worker(
            session_factory=self.Session,
            handlers={"upload": lambda task: None},
            owner="entrypoint-test",
        )
        self.assertIsInstance(worker, PersistentTaskWorker)
        self.assertEqual(worker.owner, "entrypoint-test")

    def test_expired_lease_is_reclaimed_and_logged(self):
        from datetime import datetime, timedelta, timezone
        from app.infrastructure.task_repository import SqlAlchemyTaskRepository
        from app.models import TaskLog

        session = self.Session()
        repo = SqlAlchemyTaskRepository(session, lease_seconds=60)
        task = repo.create("upload")
        task.status = "running"
        task.owner = "dead-worker"
        task.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        task.attempts = 1
        session.commit()
        claimed = SqlAlchemyTaskRepository(session, lease_seconds=60).claim_pending(
            owner="recovery-worker"
        )
        self.assertEqual(claimed.id, task.id)
        self.assertEqual(claimed.owner, "recovery-worker")
        self.assertEqual(claimed.attempts, 2)
        logs = session.query(TaskLog).filter(TaskLog.task_id == task.id).all()
        self.assertTrue(any("claimed:recovery-worker" in log.message for log in logs))
        session.close()

    def test_create_is_idempotent_by_key(self):
        from app.infrastructure.task_repository import SqlAlchemyTaskRepository

        session = self.Session()
        repo = SqlAlchemyTaskRepository(session)
        first = repo.create("upload", idempotency_key="w2-same")
        second = repo.create("upload", idempotency_key="w2-same")
        self.assertEqual(first.id, second.id)
        self.assertEqual(session.query(self._task_model()).count(), 1)
        session.close()

    @staticmethod
    def _task_model():
        from app.models import Task

        return Task

    def test_approval_repository_transition_is_persistent_and_idempotent(self):
        from app.infrastructure.approval_repository import ApprovalRunRepository

        session = self.Session()
        repo = ApprovalRunRepository(session)
        repo.create("w2-approval", {"mode": "review_only"}, {"title": "test"})
        session.close()

        fresh = self.Session()
        persistent = ApprovalRunRepository(fresh)
        self.assertEqual(persistent.get("w2-approval").status, "pending")
        approved = persistent.transition("w2-approval", "approve_all")
        self.assertEqual(approved["status"], "approved")
        self.assertIsNone(persistent.transition("w2-approval", "reject"))
        self.assertEqual(persistent.get("w2-approval").decision, "approve_all")
        from app.models import ApprovalAudit
        audits = fresh.query(ApprovalAudit).filter_by(run_id="w2-approval").all()
        self.assertEqual([a.event for a in audits], ["transition", "duplicate_callback"])
        fresh.close()

    def test_mysql_two_workers_only_one_claims_same_task(self):
        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError
        from app.database import SessionLocal
        if SessionLocal is None:
            self.skipTest("configured MySQL required for concurrency proof")

        probe = SessionLocal()
        try:
            probe.execute(text("SELECT 1"))
        except SQLAlchemyError:
            self.skipTest("reachable MySQL required for concurrency proof")
        finally:
            probe.close()

        from app.infrastructure.task_repository import SqlAlchemyTaskRepository
        from app.models import Task
        import threading
        seed = SessionLocal()
        task = SqlAlchemyTaskRepository(seed).create("upload", idempotency_key="w2-mysql-claim")
        seed.close()
        results = []
        barrier = threading.Barrier(2)
        def claim(owner):
            session = SessionLocal()
            try:
                barrier.wait()
                row = SqlAlchemyTaskRepository(session).claim_pending(owner=owner)
                results.append(row.owner if row else None)
            finally:
                session.close()
        threads = [threading.Thread(target=claim, args=(f"mysql-{i}",)) for i in (1, 2)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(sum(x is not None for x in results), 1)
        cleanup = SessionLocal()
        cleanup.query(Task).filter(Task.idempotency_key == "w2-mysql-claim").delete()
        cleanup.commit(); cleanup.close()


if __name__ == "__main__":
    unittest.main()
