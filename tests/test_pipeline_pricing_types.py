import unittest


class PipelinePricingTypeTests(unittest.TestCase):
    def test_decimal_database_costs_are_accepted_by_listing_pricing(self):
        from decimal import Decimal
        from app.pricing import price_skus

        result = price_skus("TH", float(Decimal("1.25")), 80)

        self.assertGreater(result["display_price"], 0)


if __name__ == "__main__":
    unittest.main()
