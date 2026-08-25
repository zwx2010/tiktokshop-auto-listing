import unittest


class FeishuWebhookVerificationTests(unittest.TestCase):
    def test_url_verification_echoes_challenge_without_event_signature(self):
        from fastapi.testclient import TestClient
        from app.main import app

        body = {"type": "url_verification", "challenge": "challenge-test", "token": "token"}
        with TestClient(app) as client:
            response = client.post("/api/feishu/webhook", json=body)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"challenge": "challenge-test"})


if __name__ == "__main__":
    unittest.main()
