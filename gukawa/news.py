"""Collects Chiikawa news: new goods and restocks from the official shops, shop notices,
and optional extra feeds (RSS/Atom, Instagram accounts).

The official info site (chiikawa-info.jp) refuses automated clients, so it is only linked, never fetched.

Every source is read politely (one request per source per run, a pause between requests)
because the Shopify stores answer bursts with HTTP 429.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import json
import logging
import re
import time
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import httpx

from .db import utc_now

log = logging.getLogger(__name__)

USER_AGENT = "gukawa-news/1.0 (personal news reader)"
PAUSE_SECONDS = 2.5
MAX_IMAGE_BYTES = 3 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
MEDIA_NAME = re.compile(r"[0-9a-f]{24}\.(?:jpg|png|webp|gif)")


@dataclass(frozen=True)
class Source:
    key: str
    name: str          # Korean label shown on the site
    kind: str          # shopify | atom | feed | instagram
    url: str
    shop: str = ""     # purchase guide that applies to items from this source


BUILTIN_SOURCES = (
    Source("chiikawamarket", "치이카와 마켓", "shopify", "https://chiikawamarket.jp", "chiikawamarket"),
    Source("naganomarket", "나가노 마켓", "shopify", "https://nagano-market.jp", "naganomarket"),
    Source("mogumogu", "모구모구 본포", "shopify", "https://chiikawamogumogu.shop", "mogumogu"),
    Source("chiikawamarket-notice", "치이카와 마켓 공지", "atom", "https://chiikawamarket.jp/blogs/info.atom", "chiikawamarket"),
    Source("naganomarket-notice", "나가노 마켓 공지", "atom", "https://nagano-market.jp/blogs/info.atom", "naganomarket"),
)


def sources(settings) -> tuple[Source, ...]:
    extra = [Source(f"feed-{slug(name)}", name, "feed", url) for name, url in settings.news_feeds]
    extra += [Source(f"ig-{account.lower()}", f"@{account}", "instagram", f"https://www.instagram.com/{account}/")
              for account in settings.news_instagram]
    return BUILTIN_SOURCES + tuple(extra)


def source_map(settings) -> dict[str, Source]:
    return {source.key: source for source in sources(settings)}


def slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text[:24] or hashlib.sha1(value.encode()).hexdigest()[:8]


def clean(text: str | None, limit: int = 300) -> str:
    text = re.sub(r"<!--.*?-->", " ", text or "", flags=re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t　]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text[:limit]


def iso(value: str | None) -> str:
    if not value:
        return utc_now()
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            moment = parsedate_to_datetime(value.strip())
        except (TypeError, ValueError):
            return utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


# --- parsers (pure; tested without network) ---------------------------------

def purchase_limit(tags) -> int | None:
    """Shops tag per-order limits as '2個/1会計' (two per checkout)."""
    for tag in tags or ():
        match = re.fullmatch(r"\s*(\d+)\s*個\s*/\s*1\s*会計\s*", str(tag))
        if match:
            return int(match.group(1))
    return None


def shopify_items(source: Source, data: dict) -> list[dict]:
    items = []
    for product in data.get("products", []):
        variants = product.get("variants") or []
        prices = [int(float(v["price"])) for v in variants if v.get("price") not in (None, "")]
        images = product.get("images") or []
        items.append({
            "id": f"{source.key}:p:{product['id']}", "source": source.key, "kind": "product",
            "title": clean(product.get("title"), 200) or "(제목 없음)",
            "summary": clean(product.get("body_html"), 240),
            "url": f"{source.url}/products/{product['handle']}",
            "image_url": images[0]["src"] if images else "",
            "price": min(prices) if prices else None,
            "purchase_limit": purchase_limit(product.get("tags")),
            "available": int(any(v.get("available") for v in variants)),
            "published_at": iso(product.get("published_at") or product.get("created_at")),
            "product_id": str(product["id"]),
        })
    return items


def restocks(source: Source, products: list[dict], previous: dict) -> list[dict]:
    """A product seen sold out on an earlier run that is now available becomes a restock item."""
    if not previous:
        return []  # First run: no history to compare with.
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    found = []
    for item in products:
        if item["available"] and previous.get(item["product_id"]) is False:
            found.append({**item, "id": f"{source.key}:r:{item['product_id']}:{today}", "kind": "restock",
                          "published_at": utc_now()})
    return found


ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"


def feed_items(source: Source, text: str, kind: str = "notice") -> list[dict]:
    """Parses Atom or RSS 2.0."""
    root = ET.fromstring(text.encode() if isinstance(text, str) else text)
    items = []
    entries = root.findall(f"{ATOM}entry") or root.findall("./channel/item")
    for entry in entries[:30]:
        if entry.tag == f"{ATOM}entry":
            link = next((l.get("href") for l in entry.findall(f"{ATOM}link") if l.get("rel", "alternate") == "alternate"), "")
            title = entry.findtext(f"{ATOM}title")
            summary = entry.findtext(f"{ATOM}summary") or entry.findtext(f"{ATOM}content")
            published = entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated")
            key = entry.findtext(f"{ATOM}id") or link
        else:
            link = entry.findtext("link") or ""
            title = entry.findtext("title")
            summary = entry.findtext("description")
            published = entry.findtext("pubDate")
            key = entry.findtext("guid") or link
        image = ""
        for element in entry.iter():
            if element.tag in (f"{MEDIA}content", f"{MEDIA}thumbnail", "enclosure") and (element.get("type", "image/").startswith("image/")):
                image = element.get("url", "")
                break
        if not image:
            match = re.search(r"<img[^>]+src=\"([^\"]+)\"", html.unescape(summary or ""))
            image = match.group(1) if match else ""
        if not link:
            continue
        items.append({
            "id": f"{source.key}:{hashlib.sha1(key.encode()).hexdigest()[:16]}", "source": source.key, "kind": kind,
            "title": clean(title, 200) or "(제목 없음)", "summary": clean(summary, 240),
            "url": link, "image_url": image, "published_at": iso(published),
        })
    return items


# --- network ------------------------------------------------------------------

class NewsError(RuntimeError):
    pass


def get(client: httpx.Client, url: str, *, wait=time.sleep) -> httpx.Response:
    for attempt in range(2):
        try:
            response = client.get(url)
        except httpx.HTTPError:
            raise NewsError("사이트에 연결하지 못했어요.") from None
        retry_after = response.headers.get("retry-after", "")
        # Shopify asks for a pause with Retry-After; wait once as asked, then give up until the next run.
        if response.status_code == 429 and attempt == 0 and retry_after.isdigit() and int(retry_after) <= 90:
            wait(int(retry_after) + 1)
            continue
        break
    if response.status_code == 429:
        raise NewsError("요청이 많아 사이트가 잠시 막았어요(429). 다음 확인 때 다시 시도해요.")
    if response.status_code != 200:
        raise NewsError(f"사이트 응답 코드 {response.status_code}")
    return response


def instagram_items(source: Source, settings) -> list[dict]:
    import instaloader
    from .source import InstagramSource

    account = source.url.rstrip("/").rsplit("/", 1)[-1]
    items = []
    try:
        with InstagramSource(settings).loader() as loader:
            profile = instaloader.Profile.from_username(loader.context, account)
            for index, post in enumerate(profile.get_posts()):
                if index >= 8:
                    break
                caption = (post.caption or "").strip()
                items.append({
                    "id": f"{source.key}:{post.shortcode}", "source": source.key, "kind": "post",
                    "title": clean(caption.splitlines()[0] if caption else "새 게시물", 200),
                    "summary": clean(caption, 240), "url": f"https://www.instagram.com/p/{post.shortcode}/",
                    "image_url": post.url, "published_at": post.date_utc.replace(tzinfo=timezone.utc).isoformat(),
                })
    except Exception as error:
        raise NewsError(f"인스타그램 게시물을 읽지 못했어요 ({type(error).__name__}).") from None
    return items


def fetch(source: Source, client: httpx.Client, db, settings) -> list[dict]:
    if source.kind == "shopify":
        products = shopify_items(source, get(client, f"{source.url}/products.json?limit=60").json())
        found = restocks(source, products, db.product_states(source.key))
        db.save_product_states(source.key, {item["product_id"]: bool(item["available"]) for item in products})
        return products + found
    if source.kind == "atom":
        return feed_items(source, get(client, source.url).text, kind="notice")
    if source.kind == "feed":
        return feed_items(source, get(client, source.url).text, kind="post")
    if source.kind == "instagram":
        return instagram_items(source, settings)
    return []


def save_image(client: httpx.Client, url: str, directory) -> str:
    if not url or urlparse(url).scheme not in {"http", "https"}:
        return ""
    if "cdn.shopify.com" in url:
        url += ("&" if "?" in url else "?") + "width=480"
    name = hashlib.sha1(url.encode()).hexdigest()[:24]
    for extension in IMAGE_TYPES.values():
        if (directory / (name + extension)).is_file():
            return name + extension
    try:
        with client.stream("GET", url) as response:
            kind = response.headers.get("content-type", "").split(";")[0].strip()
            if response.status_code != 200 or kind not in IMAGE_TYPES:
                return ""
            data = b""
            for chunk in response.iter_bytes():
                data += chunk
                if len(data) > MAX_IMAGE_BYTES:
                    return ""
    except httpx.HTTPError:
        return ""
    filename = name + IMAGE_TYPES[kind]
    temporary = directory / (filename + ".part")
    temporary.write_bytes(data)
    temporary.replace(directory / filename)
    return filename


# --- exchange rates ----------------------------------------------------------------

def refresh_rates(client: httpx.Client, db, max_age: int = 12 * 3600):
    """USD-based rates for the order calculator (customs judges the duty-free limit in USD)."""
    try:
        current = json.loads(db.get_state("fx_rates") or "{}")
    except ValueError:
        current = {}
    if current.get("fetched", 0) > time.time() - max_age:
        return current
    try:
        data = get(client, "https://open.er-api.com/v6/latest/USD").json()
        rates = {"usd_krw": float(data["rates"]["KRW"]), "usd_jpy": float(data["rates"]["JPY"]),
                 "updated": data.get("time_last_update_utc", ""), "fetched": time.time()}
    except (NewsError, ValueError, KeyError, TypeError):
        return current
    db.set_state("fx_rates", json.dumps(rates))
    return rates


# --- run ------------------------------------------------------------------------

def collect(db, settings, *, pause: float = PAUSE_SECONDS, client: httpx.Client | None = None) -> dict:
    """One pass over every source. Returns {source key: new item count}; errors go to state."""
    db.set_state("news_last_attempt", utc_now())
    added, errors = {}, {}
    media = settings.data_dir / "news-media"
    owned = client is None
    client = client or httpx.Client(timeout=25, follow_redirects=True,
                                    headers={"User-Agent": USER_AGENT})
    try:
        for index, source in enumerate(sources(settings)):
            if index and pause:
                time.sleep(pause)
            try:
                items = fetch(source, client, db, settings)
            except NewsError as error:
                errors[source.key] = str(error)
                continue
            except (ValueError, ET.ParseError, KeyError, TypeError) as error:
                errors[source.key] = f"내용을 읽지 못했어요 ({type(error).__name__})."
                continue
            known = db.news_ids(source.key)
            fresh = [item for item in items if item["id"] not in known]
            count = 0
            for item in items:
                if db.add_news(item):
                    count += 1
            for item in fresh[:40]:
                image = save_image(client, item.get("image_url", ""), media)
                if image:
                    db.set_news_image(item["id"], image)
            added[source.key] = count
        refresh_rates(client, db)
    finally:
        if owned:
            client.close()
    db.set_state("news_errors", json.dumps(errors, ensure_ascii=False))
    db.set_state("news_last_sync", utc_now())
    log.info("News collected: %s; errors: %s", added, list(errors))
    return added
