import unittest


class UploadAuthorizationTests(unittest.TestCase):
    def test_authorization_is_persisted_and_not_inferred_from_batch_name(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app import models  # noqa: F401
        from app.database import Base
        from app.infrastructure.upload_authorization_repository import UploadAuthorizationRepository

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        repo = UploadAuthorizationRepository(session)

        self.assertFalse(repo.is_authorized("sel_forged", ["rec_forged"]))
        repo.authorize("sel_robot_01", record_ids=["rec_robot_1"], command="上架PH站点3件耳环")
        self.assertTrue(repo.is_authorized("sel_robot_01", ["rec_robot_1"]))
        self.assertFalse(repo.is_authorized("sel_robot_01", ["rec_robot_1", "rec_forged"]))
        session.close()


if __name__ == "__main__":
    unittest.main()
