from datetime import timezone
from http.cookiejar import MozillaCookieJar
from pathlib import Path
import re
import tempfile
import unicodedata
import json
from urllib.parse import urlparse

import instaloader
import yt_dlp

DOWNLOAD_FORMAT = "bv*+ba/b"


class SourceError(RuntimeError):
    pass


class EpisodeExcluded(SourceError):
    pass


def eligible_episode(number: int | None, minimum: int) -> bool:
    return type(number) is int and number >= minimum


def ensure_episode_allowed(number: int | None, minimum: int):
    if not eligible_episode(number, minimum):
        reason = "회차 번호를 확인할 수 없어요." if number is None else f"{number}화는 수집 범위 밖이에요."
        raise EpisodeExcluded(f"{reason} {minimum}화부터 수집해요.")


def parse_instagram_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        match = re.fullmatch(r"/(?:p|reel|tv)/([A-Za-z0-9_-]{1,32})/?", parsed.path)
        if parsed.scheme != "https" or parsed.hostname not in {"instagram.com", "www.instagram.com"} or parsed.port not in {None, 443} or parsed.username or parsed.password or not match:
            raise ValueError
        return match.group(1)
    except ValueError:
        raise ValueError("https://www.instagram.com/reel/게시물ID/ 형식의 주소를 입력해 주세요.") from None


def episode_number(caption: str) -> int | None:
    match = re.search(r"(?:episode|ep\.?|第)\s*([0-9〇零一二三四五六七八九十百千]+)", unicodedata.normalize("NFKC", caption), re.I)
    if not match:
        return None
    token = match.group(1)
    if token.isascii() and token.isdigit():
        return int(token)
    digits = dict(zip("〇零一二三四五六七八九", [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9]))
    if not any(character in token for character in "十百千"):
        return int("".join(str(digits[character]) if character in digits else character for character in token))
    result, current = 0, 0
    for character in token:
        if character in "十百千":
            result += (current or 1) * {"十": 10, "百": 100, "千": 1000}[character]
            current = 0
        else:
            current = current * 10 + (digits[character] if character in digits else int(character))
    return result + current


def post_metadata(post) -> dict:
    caption = (post.caption or "").strip()
    return {
        "episode_id": post.shortcode,
        "source_url": f"https://www.instagram.com/p/{post.shortcode}/",
        "title": caption.splitlines()[0][:160] if caption else "새 에피소드",
        "caption": caption,
        "episode_number": episode_number(caption),
        "published_at": post.date_utc.replace(tzinfo=timezone.utc).isoformat(),
    }


class InstagramSource:
    def __init__(self, settings):
        self.settings = settings

    def loader(self):
        loader = instaloader.Instaloader(quiet=True, max_connection_attempts=1, request_timeout=30)
        path = self.settings.instagram_cookies
        if path and path.is_file():
            try:
                jar = MozillaCookieJar(str(path))
                jar.load(ignore_discard=True)
                cookies = {cookie.name: cookie.value for cookie in jar
                           if cookie.domain.lstrip(".") in {"instagram.com", "www.instagram.com", "i.instagram.com"} and not cookie.is_expired()}
                loader.context.update_cookies(cookies)
                if cookies.get("sessionid"):
                    username = loader.test_login()
                    if not username:
                        raise SourceError("인스타그램 로그인 세션이 만료됐어요. 쿠키 파일을 갱신해 주세요.")
                    loader.context.username = username
            except SourceError:
                loader.close()
                raise
            except Exception:
                loader.close()
                raise SourceError("인스타그램 쿠키 파일을 읽거나 세션을 확인하지 못했어요.") from None
        return loader

    def discover(self, *, backfill=False, known_ids=None):
        try:
            with self.loader() as loader:
                profile = instaloader.Profile.from_username(loader.context, self.settings.instagram_username)
                known_ids = known_ids or set()
                seen = set()
                # Reels need a separate feed: some are absent from the profile grid.
                for get_feed in (profile.get_posts, profile.get_reels):
                    for index, post in enumerate(get_feed()):
                        if post.shortcode not in seen and post.is_video and post.owner_username.lower() == self.settings.instagram_username.lower():
                            seen.add(post.shortcode)
                            metadata = post_metadata(post)
                            if eligible_episode(metadata["episode_number"], self.settings.min_episode):
                                yield metadata
                        # Read past pinned posts and continue through any unseen history.
                        if not backfill and index + 1 >= self.settings.discovery_limit:
                            if not known_ids or post.shortcode in known_ids:
                                break
        except SourceError:
            raise
        except Exception:
            raise SourceError("새 게시물을 확인하지 못했어요. 로그인 세션과 인스타그램 요청 제한을 확인해 주세요.") from None

    def inspect(self, shortcode: str):
        try:
            with self.loader() as loader:
                post = instaloader.Post.from_shortcode(loader.context, shortcode)
                if post.owner_username.lower() != self.settings.instagram_username.lower():
                    raise SourceError("설정된 인스타그램 계정의 게시물만 가져올 수 있어요.")
                if not post.is_video:
                    raise SourceError("동영상 게시물이 아니에요.")
                metadata = post_metadata(post)
                ensure_episode_allowed(metadata["episode_number"], self.settings.min_episode)
                return metadata
        except SourceError:
            raise
        except Exception:
            raise SourceError("게시물을 확인하지 못했어요. 로그인 세션과 게시물 주소를 확인해 주세요.") from None

    def download(self, url: str, directory: Path) -> Path:
        class SilentLogger:
            def debug(self, *_): pass
            def warning(self, *_): pass
            def error(self, *_): pass

        def size_guard(progress):
            if progress.get("downloaded_bytes", 0) > self.settings.max_video_bytes:
                raise SourceError("영상이 설정된 파일 크기 제한을 초과했어요.")

        options = {
            "format": DOWNLOAD_FORMAT, "merge_output_format": "mp4",
            "outtmpl": str(directory / "download.%(ext)s"),
            "noplaylist": True, "quiet": True, "logger": SilentLogger(),
            "socket_timeout": 30, "retries": 2, "fragment_retries": 2,
            "max_filesize": self.settings.max_video_bytes, "progress_hooks": [size_guard],
            "writesubtitles": True, "subtitleslangs": ["en", "en-US", "en-GB"],
            "subtitlesformat": "vtt/srt/best",
        }
        # yt-dlp saves its cookie jar on exit; copy the read-only mounted original.
        with tempfile.TemporaryDirectory(prefix="gukawa-cookies-") as temporary:
            original = self.settings.instagram_cookies
            if original and original.is_file():
                cookie_copy = Path(temporary) / "cookies.txt"
                cookie_copy.write_bytes(original.read_bytes())
                cookie_copy.chmod(0o600)
                options["cookiefile"] = str(cookie_copy)
            try:
                with yt_dlp.YoutubeDL(options) as downloader:
                    info = downloader.extract_info(url, download=False)
                    if not info or info.get("_type") == "playlist":
                        raise SourceError("한 편의 영상이 있는 게시물을 사용해 주세요.")
                    if (info.get("duration") or 0) > self.settings.max_video_seconds:
                        raise SourceError("영상이 설정된 길이 제한을 초과했어요.")
                    downloader.process_ie_result(info, download=True)
                    path = Path(downloader.prepare_filename(info))
                    if not path.is_file() or path.stat().st_size > self.settings.max_video_bytes:
                        raise SourceError("영상을 저장하지 못했거나 크기 제한을 초과했어요.")
                    keys = ("format_id", "ext", "width", "height", "fps", "tbr", "vbr", "abr", "vcodec", "acodec")
                    selection = [{key: item.get(key) for key in keys if item.get(key) is not None}
                                 for item in info.get("requested_formats", [info])]
                    (directory / "download-formats.json").write_text(
                        json.dumps({"selector": DOWNLOAD_FORMAT, "selected": selection, "reencoded": False}, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    return path
            except SourceError:
                raise
            except Exception:
                raise SourceError("영상 다운로드가 실패했어요. 게시물 접근 권한과 로그인 세션을 확인해 주세요.") from None
