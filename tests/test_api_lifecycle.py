import unittest


class ApiLifecycleTests(unittest.TestCase):
    def test_live_health_does_not_require_database(self):
        from fastapi.testclient import TestClient
        from app.api.app import create_app

        with TestClient(create_app(database_ping=lambda: True)) as client:
            response = client.get("/api/v1/health/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_ready_health_reports_database_failure(self):
        from fastapi.testclient import TestClient
        from app.api.app import create_app

        def unavailable():
            raise RuntimeError("db unavailable")

        with TestClient(create_app(database_ping=unavailable)) as client:
            response = client.get("/api/v1/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "DATABASE_UNAVAILABLE")

    def test_api_only_lifecycle_does_not_start_worker(self):
        from fastapi.testclient import TestClient
        from app.api.app import create_app

        with TestClient(create_app(database_ping=lambda: True)) as client:
            self.assertFalse(client.app.state.worker_started)

    def test_lifecycle_runs_explicit_runtime_initializer(self):
        from fastapi.testclient import TestClient
        from app.api.app import create_app

        calls = []
        with TestClient(create_app(
            database_ping=lambda: True,
            runtime_initializer=lambda: calls.append("started"),
        )):
            self.assertEqual(calls, ["started"])

    def test_main_exposes_versioned_api_without_implicit_worker(self):
        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app) as client:
            response = client.get("/api/v1/health/live")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(client.app.state.worker_started)

    def test_task_api_persists_a_task_for_an_independent_worker(self):
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from app import models  # noqa: F401
        from app.api.app import create_app
        from app.database import Base
        from app.infrastructure.task_repository import SqlAlchemyTaskRepository
        from app.jobs.worker import PersistentTaskWorker

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        def repository_factory():
            return SqlAlchemyTaskRepository(session_factory())

        with TestClient(create_app(
            database_ping=lambda: True,
            task_repository_factory=repository_factory,
        )) as client:
            response = client.post("/api/v1/tasks", json={"task_type": "upload"})
        self.assertEqual(response.status_code, 201)
        task_id = response.json()["id"]
        seen = []
        worker = PersistentTaskWorker(
            session_factory=session_factory,
            handlers={"upload": lambda task: seen.append(task.id)},
            owner="worker-a",
        )
        self.assertTrue(worker.run_once())
        self.assertEqual(seen, [task_id])
        check = repository_factory()
        self.assertEqual(check.get(task_id).status, "success")
        check.session.close()
        engine.dispose()

    def test_task_api_reuses_idempotency_key(self):
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from app import models  # noqa: F401
        from app.api.app import create_app
        from app.database import Base
        from app.infrastructure.task_repository import SqlAlchemyTaskRepository

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        def repository_factory():
            return SqlAlchemyTaskRepository(session_factory())

        with TestClient(create_app(
            database_ping=lambda: True,
            task_repository_factory=repository_factory,
        )) as client:
            first = client.post(
                "/api/v1/tasks", headers={"Idempotency-Key": "api-w2-same"},
                json={"task_type": "upload"},
            )
            second = client.post(
                "/api/v1/tasks", headers={"Idempotency-Key": "api-w2-same"},
                json={"task_type": "upload"},
            )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json()["id"], second.json()["id"])
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
