import unittest


class PipelinePricingTypeTests(unittest.TestCase):
    def test_string_form_1688_images_are_normalized(self):
        from app.cleaning import clean_images

        raw = "https://cbu01.alicdn.com/a.jpg https://cbu01.alicdn.com/b.jpg"
        self.assertEqual(clean_images(raw), [
            "https://cbu01.alicdn.com/a.jpg",
            "https://cbu01.alicdn.com/b.jpg",
        ])

    def test_json_string_images_are_normalized(self):
        from app.cleaning import clean_images

        raw = '["https://cbu01.alicdn.com/a.jpg", "https://cbu01.alicdn.com/b.jpg"]'
        self.assertEqual(clean_images(raw), [
            "https://cbu01.alicdn.com/a.jpg",
            "https://cbu01.alicdn.com/b.jpg",
        ])

    def test_export_keeps_real_urls_unverified_when_vision_helper_is_missing(self):
        import importlib

        exporter = importlib.import_module("tools.export_platform_to_staging")
        original = exporter.QWEN_TOOL
        try:
            exporter.QWEN_TOOL = "missing-qwen-vision-helper"
            self.assertEqual(
                exporter.filter_small_images(
                    ["https://example.com/a.jpg", "https://example.com/b.jpg"],
                    300,
                ),
                ["https://example.com/a.jpg", "https://example.com/b.jpg"],
            )
        finally:
            exporter.QWEN_TOOL = original

    def test_decimal_database_costs_are_accepted_by_listing_pricing(self):
        from decimal import Decimal
        from app.pricing import price_skus

        result = price_skus("TH", Decimal("1.25"), 80)

        self.assertGreater(result["display_price"], 0)


if __name__ == "__main__":
    unittest.main()
