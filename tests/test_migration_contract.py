import unittest


class MigrationContractTests(unittest.TestCase):
    def test_migration_plan_is_idempotent_for_existing_rows(self):
        from app.migration.plan import classify_row

        self.assertEqual(classify_row(existing=True, conflict=False), "skip_existing")
        self.assertEqual(classify_row(existing=False, conflict=False), "insert")
        self.assertEqual(classify_row(existing=False, conflict=True), "conflict")

    def test_report_contains_table_counts_and_relation_failures(self):
        from app.migration.report import MigrationReport

        report = MigrationReport()
        report.record_table("products", read=3, inserted=2, skipped=1)
        report.record_relation_failure("product_skus", "product_id=99")
        payload = report.to_dict()
        self.assertEqual(payload["tables"]["products"]["inserted"], 2)
        self.assertEqual(payload["relation_failures"], [{"table": "product_skus", "detail": "product_id=99"}])

    def test_tables_follow_foreign_key_dependencies(self):
        from sqlalchemy import MetaData, Table, Column, Integer, ForeignKey
        from app.migration.plan import ordered_tables

        metadata = MetaData()
        parent = Table("products", metadata, Column("id", Integer, primary_key=True))
        Table("listings", metadata, Column("id", Integer, primary_key=True),
              Column("product_id", Integer, ForeignKey("products.id")))
        self.assertEqual(ordered_tables(metadata, {"listings", "products"}), ["products", "listings"])

    def test_only_confirmed_style_translation_null_is_coerced(self):
        from app.migration.plan import coerce_explicit_null

        self.assertEqual(coerce_explicit_null("product_skus", "style_en", None), "")
        self.assertIsNone(coerce_explicit_null("products", "title_cn", None))


if __name__ == "__main__":
    unittest.main()
