import json
from pathlib import Path
import tempfile
import unittest

import httpx
from fastapi.testclient import TestClient

from gukawa import news
from gukawa.config import Settings, parse_feeds
from gukawa.db import Database
from gukawa.web import create_app

MARKET = news.BUILTIN_SOURCES[0]
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def product(pid, title, *, available=True, price="1650", tags=(), published="2026-11-13T11:00:23+09:00"):
    return {"id": pid, "title": title, "handle": str(pid), "body_html": "<p>サイズ：H200<br>素材</p>",
            "published_at": published, "tags": list(tags), "images": [{"src": f"https://cdn.shopify.com/s/files/{pid}.jpg?v=1"}],
            "variants": [{"price": price, "available": available}]}


ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>tag:1</id><published>2026-10-26T17:52:58+09:00</published>
    <link rel="alternate" type="text/html" href="https://chiikawamarket.jp/blogs/info/20261026"/>
    <title>棚卸に伴う出荷停止のお知らせ</title><content type="html">&lt;p&gt;出荷を停止いたします&lt;/p&gt;</content></entry>
</feed>"""

RSS_FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
  <item><title>新グッズのお知らせ</title><link>https://example.com/post/1</link><guid>post-1</guid>
    <pubDate>Mon, 16 Nov 2026 03:00:00 GMT</pubDate><description>&lt;img src="https://example.com/a.png"&gt;本文</description></item>
</channel></rss>"""


class ParserTests(unittest.TestCase):
    def test_shopify_product_fields_and_purchase_limit(self):
        items = news.shopify_items(MARKET, {"products": [product(1, "ちいかわ ぬいぐるみ", tags=["20261113", "2個/1会計"]),
                                                          product(2, "うさぎ 缶バッジ", available=False, price="550")]})
        first, second = items
        self.assertEqual(first["id"], "chiikawamarket:p:1")
        self.assertEqual(first["url"], "https://chiikawamarket.jp/products/1")
        self.assertEqual((first["price"], first["purchase_limit"], first["available"]), (1650, 2, 1))
        self.assertEqual(first["published_at"], "2026-11-13T02:00:23+00:00")
        self.assertEqual(first["summary"], "サイズ：H200\n素材")
        self.assertEqual((second["available"], second["purchase_limit"]), (0, None))

    def test_restock_needs_a_previous_sold_out_sighting(self):
        products = news.shopify_items(MARKET, {"products": [product(1, "A"), product(2, "B")]})
        self.assertEqual(news.restocks(MARKET, products, {}), [])
        found = news.restocks(MARKET, products, {"1": False, "2": True})
        self.assertEqual([item["kind"] for item in found], ["restock"])
        self.assertTrue(found[0]["id"].startswith("chiikawamarket:r:1:"))

    def test_atom_and_rss(self):
        notice, = news.feed_items(news.BUILTIN_SOURCES[3], ATOM_FEED)
        self.assertEqual(notice["title"], "棚卸に伴う出荷停止のお知らせ")
        self.assertEqual(notice["url"], "https://chiikawamarket.jp/blogs/info/20261026")
        self.assertEqual(notice["summary"], "出荷を停止いたします")
        self.assertEqual(notice["published_at"], "2026-10-26T08:52:58+00:00")
        post, = news.feed_items(news.Source("feed-x", "X", "feed", "https://example.com/rss"), RSS_FEED, kind="post")
        self.assertEqual((post["kind"], post["image_url"], post["summary"]), ("post", "https://example.com/a.png", "本文"))
        self.assertEqual(post["published_at"], "2026-11-16T03:00:00+00:00")

    def test_feed_setting_parser(self):
        self.assertEqual(parse_feeds("치이카와 X|https://rss.example/x, 기타|https://b"),
                         (("치이카와 X", "https://rss.example/x"), ("기타", "https://b")))
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            Settings(Path(temporary), news_feeds=(("bad", "ftp://x"),))


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(Path(self.temp.name))
        self.db = Database(self.settings.database)
        self.available = False
        self.calls = []

    def handler(self, request):
        self.calls.append(str(request.url))
        host, path = request.url.host, request.url.path
        if path == "/products.json":
            if host == "nagano-market.jp":
                return httpx.Response(429, headers={"retry-after": "500"})
            return httpx.Response(200, json={"products": [product(7, "ハチワレ マスコット", available=self.available, tags=["1個/1会計"])]})
        if path.endswith(".atom"):
            return httpx.Response(200, text=ATOM_FEED)
        if host == "cdn.shopify.com":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if host == "open.er-api.com":
            return httpx.Response(200, json={"rates": {"KRW": 1400, "JPY": 150}, "time_last_update_utc": "now"})
        return httpx.Response(404)

    def collect(self):
        with httpx.Client(transport=httpx.MockTransport(self.handler)) as client:
            return news.collect(self.db, self.settings, pause=0, client=client)

    def test_collect_records_items_images_restocks_and_errors(self):
        added = self.collect()
        self.assertEqual(added["chiikawamarket"], 1)
        self.assertEqual(added["chiikawamarket-notice"], 1)
        errors = json.loads(self.db.get_state("news_errors"))
        self.assertIn("429", errors["naganomarket"])  # Retry-After too long: skipped until the next run.
        item, = self.db.news(source="chiikawamarket")
        self.assertEqual((item["available"], item["purchase_limit"]), (0, 1))
        self.assertRegex(item["image"], news.MEDIA_NAME)
        self.assertTrue((self.settings.data_dir / "news-media" / item["image"]).is_file())
        self.assertTrue(any("width=480" in call for call in self.calls))
        self.assertEqual(json.loads(self.db.get_state("fx_rates"))["usd_krw"], 1400)

        self.assertEqual(self.collect()["chiikawamarket"], 0)  # Nothing new, no duplicates.
        self.available = True
        self.assertEqual(self.collect()["chiikawamarket"], 1)
        restock, = self.db.news(source="chiikawamarket", kind="restock")
        self.assertEqual(restock["title"], "ハチワレ マスコット")
        self.assertEqual(self.db.news(source="chiikawamarket", kind="product")[0]["available"], 1)

    def test_retry_after_is_honoured_once(self):
        waits, responses = [], iter([httpx.Response(429, headers={"retry-after": "3"}), httpx.Response(200, text="ok")])
        with httpx.Client(transport=httpx.MockTransport(lambda request: next(responses))) as client:
            self.assertEqual(news.get(client, "https://chiikawamarket.jp/products.json", wait=waits.append).text, "ok")
        self.assertEqual(waits, [4])


class PageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(Path(self.temp.name), admin_password="admin-secret",
                                 viewer_username="watcher", viewer_password="pw")
        self.db = Database(self.settings.database)
        for item in news.shopify_items(MARKET, {"products": [product(1, "ちいかわ ぬいぐるみ", tags=["2個/1会計"])]}):
            self.db.add_news(item)
        for source in news.BUILTIN_SOURCES[3:5]:
            for item in news.feed_items(source, ATOM_FEED):
                self.db.add_news(item)
        (self.settings.data_dir / "news-media" / ("a" * 24 + ".png")).write_bytes(PNG)

    def client(self, auth=("watcher", "pw")):
        client = TestClient(create_app(self.settings), client=("192.0.2.1", 1234), follow_redirects=False)
        client.auth = auth
        return client

    def test_pages_render_for_viewer(self):
        with self.client() as client:
            for path in ("/", "/news", "/news?kind=restock", "/news?source=chiikawamarket&page=2", "/videos",
                         "/shop", "/shop/chiikawamarket", "/shop/mogumogu", "/shop/order"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, path)
            page = client.get("/news").text
            self.assertIn('<h3 class="news-title" lang="ja">', page)
            self.assertIn("ちいかわ ぬいぐるみ", page)
            self.assertIn("1회 결제 2개까지", page)
            self.assertIn('data-add-cart', page)
            # The sister shops post the same notice; it shows once with both names.
            self.assertEqual(page.count("棚卸に伴う出荷停止のお知らせ"), 1)
            self.assertRegex(page, r"(치이카와 마켓 공지 · 나가노 마켓 공지|나가노 마켓 공지 · 치이카와 마켓 공지)")
            self.assertIn('aria-current="page">소식', page)
            self.assertNotIn('href="/admin"', page)
            guide = client.get("/shop/chiikawamarket").text
            self.assertIn("建物名、部屋番号など", guide)
            self.assertIn("¥990", guide)
            self.assertEqual(client.get("/shop/unknown").status_code, 404)
            self.assertEqual(client.get("/news?kind=bogus").status_code, 200)

    def test_news_media_is_served_only_by_strict_name_and_with_login(self):
        with self.client() as client:
            response = client.get("/news-media/" + "a" * 24 + ".png")
            self.assertEqual(response.status_code, 200)
            self.assertIn("max-age", response.headers["cache-control"])
            self.assertEqual(client.get("/news-media/..%2Fgukawa.sqlite3").status_code, 404)
            self.assertEqual(client.get("/news-media/" + "b" * 24 + ".png").status_code, 404)
        with self.client(auth=None) as anonymous:
            self.assertEqual(anonymous.get("/news-media/" + "a" * 24 + ".png").status_code, 401)
            self.assertEqual(anonymous.get("/shop").status_code, 303)

    def test_admin_can_request_news_check(self):
        with self.client() as viewer:
            self.assertEqual(viewer.post("/api/admin/news", headers={"X-Gukawa-Action": "admin"}).status_code, 401)
        with self.client(auth=("admin", "admin-secret")) as admin:
            self.assertIn("소식 수집", admin.get("/admin").text)
            self.assertEqual(admin.post("/api/admin/news").status_code, 403)
            self.assertEqual(admin.post("/api/admin/news", headers={"X-Gukawa-Action": "admin"}).status_code, 202)
        self.assertEqual(self.db.pop_state("news_requested"), "1")
        self.assertEqual(self.db.pop_state("news_requested"), "")


if __name__ == "__main__":
    unittest.main()
