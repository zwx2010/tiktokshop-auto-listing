import os
import unittest


class DatabaseContractTests(unittest.TestCase):
    def test_runtime_database_requires_mysql(self):
        from app.config import validate_runtime_database_url

        self.assertTrue(validate_runtime_database_url("mysql+pymysql://u:p@db/tiktok"))
        with self.assertRaises(ValueError):
            validate_runtime_database_url("sqlite:///data/platform.db")

    def test_mysql_url_is_selected_from_environment(self):
        from app.config import database_url_from_environment

        env = {"DATABASE_URL": "mysql+pymysql://u:p@db/tiktok"}
        self.assertEqual(database_url_from_environment(env), env["DATABASE_URL"])

    def test_target_schema_contains_all_business_tables(self):
        from app.database import Base
        from app import models  # noqa: F401

        expected = {
            "accounts", "account_api_credentials", "keywords", "products", "product_skus",
            "listings", "upload_results", "orders", "order_items", "messages",
            "message_attachments", "image_qa_records", "tasks", "task_logs",
            "sync_watermarks", "skipped_products", "dedup_registry", "settings", "audit_logs",
        }
        self.assertTrue(expected.issubset(set(Base.metadata.tables)))

    def test_money_columns_use_fixed_precision(self):
        from sqlalchemy import Numeric
        from app.database import Base
        from app import models  # noqa: F401

        for table, columns in {
            "products": ["cost_cny_used"],
            "product_skus": ["cost_cny"],
            "listings": ["price", "target_sale_price", "discount_rate"],
            "orders": ["total_amount"],
            "order_items": ["unit_price"],
        }.items():
            for column in columns:
                column_type = Base.metadata.tables[table].c[column].type
                self.assertIs(type(column_type), Numeric)


if __name__ == "__main__":
    unittest.main()
