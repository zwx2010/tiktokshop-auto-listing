import unittest
from pathlib import Path

from app.metrics import active_product_count
from app.models import Product


class _Query:
    def __init__(self):
        self.filters = []

    def filter(self, *expressions):
        self.filters.extend(expressions)
        return self

    def scalar(self):
        return 7


class _Db:
    def __init__(self):
        self.model = None
        self.query_result = _Query()

    def query(self, model):
        self.model = model
        return self.query_result


class MetricsConsistencyTests(unittest.TestCase):
    def test_active_product_count_uses_active_filter(self):
        db = _Db()

        self.assertEqual(active_product_count(db), 7)
        self.assertEqual(len(db.query_result.filters), 1)
        self.assertEqual(db.query_result.filters[0].left.key, "active")
        self.assertEqual(str(db.query_result.filters[0].right).lower(), "true")

    def test_dashboard_identifies_mysql_runtime(self):
        template = Path("app/templates/index.html").read_text(encoding="utf-8")

        self.assertIn("MySQL", template)
        self.assertNotIn("Python + SQLite", template)


if __name__ == "__main__":
    unittest.main()
