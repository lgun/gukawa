import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from gukawa import forwarders as fw
from gukawa.config import Settings
from gukawa.db import Database
from gukawa.web import create_app

ARAKU_AIR = fw.FORWARDER_MAP["araku"].methods[0].rates


class RateTests(unittest.TestCase):
    def test_every_table_matches_the_weight_points(self):
        for forwarder in fw.FORWARDERS:
            for method in forwarder.methods:
                self.assertEqual(len(method.rates), len(fw.WEIGHTS), (forwarder.key, method.key))
                self.assertEqual(list(method.rates), sorted(method.rates), (forwarder.key, method.key))
                self.assertTrue(all(fw.region_of(p) for p in method.prefectures), forwarder.key)

    def test_weight_is_rounded_up_to_half_kilograms(self):
        self.assertEqual(fw.price_at(ARAKU_AIR, 0.2), (650, False))
        self.assertEqual(fw.price_at(ARAKU_AIR, 1.36), (842, False))  # billed as 1.5kg
        self.assertEqual(fw.price_at(ARAKU_AIR, 10), (2788, False))
        self.assertEqual(fw.price_at(ARAKU_AIR, 10.1), (None, False))

    def test_weights_between_table_points_are_estimates(self):
        price, estimated = fw.price_at(ARAKU_AIR, 2.5)  # between 2kg (953) and 3kg (1158)
        self.assertTrue(estimated)
        self.assertAlmostEqual(price, (953 + 1158) / 2)

    def test_domestic_fee_follows_the_warehouse_region(self):
        self.assertEqual(fw.domestic_fee("chiikawamarket", 5000, ("大阪府",)), (1045, "近畿"))
        self.assertEqual(fw.domestic_fee("chiikawamarket", 5000, ("東京都",)), (990, "関東"))
        fee, region = fw.domestic_fee("chiikawamarket", 5000, ("東京都", "福岡県"))
        self.assertEqual(fee, 1320)  # Unknown which warehouse: the higher fee.
        self.assertIn("창고에 따라", region)
        self.assertEqual(fw.domestic_fee("chiikawamarket", 11000, ("大阪府",)), (0, "무료"))
        self.assertEqual(fw.domestic_fee("mogumogu", 5000, ("大阪府",))[0], None)

    def test_compare_sorts_by_total_and_filters_by_kind(self):
        rows = fw.compare(1.0, subtotal=5000, won_per_yen=9.0)
        totals = [row["total_won"] for row in rows]
        self.assertEqual(totals, sorted(totals))
        araku_air = next(row for row in rows if row["forwarder"].key == "araku" and row["method"].key == "air")
        self.assertEqual(araku_air["international_won"], round(750 * 8.80))  # The forwarder's own rate.
        self.assertEqual(araku_air["total_won"], round(750 * 8.80 + 1045 * 9.0))
        eldex_sea = next(row for row in rows if row["forwarder"].key == "eldex" and row["method"].key == "sea")
        self.assertEqual(eldex_sea["international_won"], 9700)
        self.assertTrue(all(row["method"].kind == "sea" for row in fw.compare(1.0, kind="sea", won_per_yen=9.0)))
        # Without a market rate, forwarders with no published rate cannot be totalled and go last.
        rows = fw.compare(1.0, won_per_yen=None)
        self.assertIsNone(rows[-1]["total_won"])


class PageTests(unittest.TestCase):
    def test_comparison_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(Path(temporary), local_preview=True)
            Database(settings.database).set_state("fx_rates", json.dumps({"usd_krw": 1350, "usd_jpy": 150}))
            with TestClient(create_app(settings), client=("127.0.0.1", 1)) as client:
                page = client.get("/shop/forwarders?weight=2.3&kind=sea&shop=naganomarket&subtotal=3000")
                self.assertEqual(page.status_code, 200)
                self.assertIn("가장 저렴", page.text)
                self.assertIn("data-pick-forwarder", page.text)
                self.assertNotIn("항공 (도쿄)", page.text.split("업체별 조건")[0])  # sea only
                self.assertEqual(client.get("/shop/forwarders?weight=50").status_code, 422)
                self.assertEqual(client.get("/shop/forwarders?kind=x&shop=nope").status_code, 200)
                self.assertIn("배대지 고르기", client.get("/shop").text)
                self.assertIn('"forwarders"', client.get("/shop/order").text)


if __name__ == "__main__":
    unittest.main()
