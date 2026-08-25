import unittest


class W3ProjectionSecurityTests(unittest.TestCase):
    def test_redact_removes_credentials_from_log_text(self):
        from app.security.logging import redact
        out = redact("Authorization: Bearer abc password=secret cookie=session123")
        self.assertNotIn("abc", out)
        self.assertNotIn("secret", out)
        self.assertNotIn("session123", out)
        self.assertIn("[REDACTED]", out)

    def test_task_projection_reads_database_and_retries_write(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app import models  # noqa: F401
        from app.database import Base
        from app.infrastructure.task_repository import SqlAlchemyTaskRepository
        from app.feishu.projection import project_task

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        task = SqlAlchemyTaskRepository(session).create("upload")
        calls = []
        def writer(record_id, fields):
            calls.append(fields)
            if len(calls) == 1:
                raise RuntimeError("temporary Feishu error")
        result = project_task(task.id, "rec-1", session=session, writer=writer)
        self.assertTrue(result["ok"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(calls[-1]["状态"], "pending")
        session.close()


if __name__ == "__main__":
    unittest.main()
