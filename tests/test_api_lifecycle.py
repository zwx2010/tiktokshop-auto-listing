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

    def test_main_exposes_versioned_api_without_implicit_worker(self):
        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app) as client:
            response = client.get("/api/v1/health/live")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(client.app.state.worker_started)

    def test_task_api_returns_versioned_task_shape(self):
        from fastapi.testclient import TestClient
        from app.api.app import create_app

        with TestClient(create_app(database_ping=lambda: True)) as client:
            response = client.post("/api/v1/tasks", json={"task_type": "upload"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["task_type"], "upload")
        self.assertEqual(response.json()["status"], "pending")


if __name__ == "__main__":
    unittest.main()
