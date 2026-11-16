from dataclasses import dataclass
import os
from pathlib import Path
import re

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    admin_password: str = ""
    viewer_username: str = ""
    viewer_password: str = ""
    local_preview: bool = False
    instagram_username: str = "chiikawa.anime"
    min_episode: int = 360
    instagram_cookies: Path | None = None
    auto_sync: bool = True
    sync_interval: int = 3600
    discovery_limit: int = 12
    max_attempts: int = 3
    processing_mode: str = "source"
    initial_backfill: bool = True
    create_playback: bool = True
    api_key: str = ""
    translation_model: str = "gpt-6-luna"
    auto_publish: bool = True
    crop_top: float = 0.55
    crop_bottom: float = 1.0
    sample_fps: float = 4.0
    min_confidence: float = 0.75
    max_video_seconds: int = 600
    max_video_bytes: int = 524288000
    news_sync: bool = True
    news_interval: int = 1800
    # Extra RSS/Atom feeds as (name, url), e.g. an RSSHub address for an X account.
    news_feeds: tuple = ()
    # Instagram accounts whose posts appear in the news (uses the same login cookies).
    news_instagram: tuple = ()

    def __post_init__(self):
        if self.min_episode < 1:
            raise ValueError("MIN_EPISODE must be positive")
        if self.processing_mode not in {"source", "translate"}:
            raise ValueError("PROCESSING_MODE must be source or translate")
        if not 0 <= self.crop_top < self.crop_bottom <= 1:
            raise ValueError("OCR_CROP_TOP/BOTTOM must satisfy 0 <= top < bottom <= 1")
        if not 0.5 <= self.sample_fps <= 12:
            raise ValueError("OCR_SAMPLE_FPS must be between 0.5 and 12")
        if self.sync_interval < 300 or not 1 <= self.discovery_limit <= 100:
            raise ValueError("Sync interval must be >= 300 seconds; discovery limit must be 1..100")
        if not 0 <= self.min_confidence <= 1 or not 1 <= self.max_attempts <= 10:
            raise ValueError("Invalid OCR confidence or retry limit")
        if self.max_video_seconds <= 0 or self.max_video_bytes <= 0:
            raise ValueError("Video limits must be positive")
        if self.news_interval < 600:
            raise ValueError("NEWS_INTERVAL_SECONDS must be >= 600")
        for name, url in self.news_feeds:
            if not name or not url.startswith(("https://", "http://")):
                raise ValueError("NEWS_FEEDS entries look like 이름|https://주소")
        if not all(re.fullmatch(r"[A-Za-z0-9_.]{1,30}", account) for account in self.news_instagram):
            raise ValueError("Invalid NEWS_INSTAGRAM_ACCOUNTS")
        if not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", self.instagram_username):
            raise ValueError("Invalid Instagram username")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "episodes").mkdir(exist_ok=True)
        (self.data_dir / "news-media").mkdir(exist_ok=True)

    @property
    def database(self) -> Path:
        return self.data_dir / "gukawa.sqlite3"


def load_settings() -> Settings:
    load_dotenv()
    def flag(name: str, default: str) -> bool:
        return os.getenv(name, default).lower() in {"true", "1", "yes"}
    cookies = os.getenv("INSTAGRAM_COOKIES", "./secrets/instagram-cookies.txt")
    return Settings(
        data_dir=Path(os.getenv("DATA_DIR", "./data")).resolve(),
        admin_password=os.getenv("ADMIN_PASSWORD", ""),
        viewer_username=os.getenv("VIEWER_USERNAME", "").strip(),
        viewer_password=os.getenv("VIEWER_PASSWORD", ""),
        local_preview=flag("LOCAL_PREVIEW", "false"),
        instagram_username=os.getenv("INSTAGRAM_USERNAME", "chiikawa.anime"),
        min_episode=int(os.getenv("MIN_EPISODE", "360")),
        instagram_cookies=Path(cookies).resolve() if cookies else None,
        auto_sync=flag("AUTO_SYNC", "true"),
        sync_interval=int(os.getenv("SYNC_INTERVAL_SECONDS", "3600")),
        discovery_limit=int(os.getenv("DISCOVERY_LIMIT", "12")),
        max_attempts=int(os.getenv("MAX_ATTEMPTS", "3")),
        processing_mode=os.getenv("PROCESSING_MODE", "source"),
        initial_backfill=flag("INITIAL_BACKFILL", "true"),
        create_playback=flag("CREATE_PLAYBACK", "true"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        translation_model=os.getenv("TRANSLATION_MODEL", "gpt-6-luna"),
        auto_publish=flag("AUTO_PUBLISH", "true"),
        crop_top=float(os.getenv("OCR_CROP_TOP", "0.55")),
        crop_bottom=float(os.getenv("OCR_CROP_BOTTOM", "1")),
        sample_fps=float(os.getenv("OCR_SAMPLE_FPS", "4")),
        min_confidence=float(os.getenv("OCR_MIN_CONFIDENCE", "0.75")),
        max_video_seconds=int(os.getenv("MAX_VIDEO_SECONDS", "600")),
        max_video_bytes=int(os.getenv("MAX_VIDEO_BYTES", "524288000")),
        news_sync=flag("NEWS_SYNC", "true"),
        news_interval=int(os.getenv("NEWS_INTERVAL_SECONDS", "1800")),
        news_feeds=parse_feeds(os.getenv("NEWS_FEEDS", "")),
        news_instagram=tuple(item.strip().lstrip("@") for item in os.getenv("NEWS_INSTAGRAM_ACCOUNTS", "").split(",") if item.strip()),
    )


def parse_feeds(value: str) -> tuple:
    """'이름|https://주소, 이름2|https://주소2' -> (('이름', 'https://주소'), ...)"""
    feeds = []
    for entry in value.split(","):
        if entry.strip():
            name, _, url = entry.partition("|")
            feeds.append((name.strip(), url.strip()))
    return tuple(feeds)
