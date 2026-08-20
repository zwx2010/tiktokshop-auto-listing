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


if __name__ == "__main__":
    unittest.main()
