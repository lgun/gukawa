import base64
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import time
from typing import Literal
from urllib.parse import parse_qs, quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .config import Settings, load_settings
from .db import Database

ROOT = Path(__file__).parent
security = HTTPBasic(auto_error=False)
SESSION_COOKIE = "gukawa_session"
SESSION_DAYS = 30
STATE_KEYS = ("last_sync", "last_sync_attempt", "sync_error", "backfill_complete", "worker_heartbeat", "scan_mode", "scan_added")


def caption_region(metadata: dict, showing_main: bool) -> tuple[float, float, bool]:
    extraction = metadata.get("extraction", {})
    burned = extraction.get("method") == "ocr"
    if not burned:
        return 0.74, 1.0, False
    # OCR's search area can include most of the picture. The caption band is
    # a separate layout setting, so a broad OCR search doesn't hide the scene.
    region = metadata.get("subtitle_region", {})
    top = float(region.get("top", 0.57))
    bottom = float(region.get("bottom", 0.66))
    crop = metadata.get("playback", {}).get("crop")
    if showing_main:
        if not crop:
            return 0.71, 1.0, True
        # Both supported source layouts are portrait 9:16 and cropped at full width.
        source_height = crop["width"] * 16 / 9
        top = (top * source_height - crop["y"]) / crop["height"]
        bottom = (bottom * source_height - crop["y"]) / crop["height"]
    bottom = min(1.0, max(0.05, bottom))
    return max(0.0, min(top, bottom - 0.03)), bottom, True


class LoginRequired(Exception):
    def __init__(self, admin_only: bool = False):
        self.admin_only = admin_only


def safe_next(value: str | None) -> str:
    """Only same-site paths are allowed as the post-login destination."""
    value = value or "/"
    if not value.startswith("/") or value.startswith("//") or "\\" in value or any(ord(c) < 32 for c in value):
        return "/"
    return value


class Sessions:
    """Signed cookie sessions. The key lives in DATA_DIR; changing a password logs that account out."""

    def __init__(self, settings: Settings):
        self.settings = settings
        path = settings.data_dir / "session.key"
        if not path.is_file():
            path.write_bytes(secrets.token_bytes(32))
            try:
                path.chmod(0o600)
            except OSError:
                pass
        self.key = path.read_bytes()

    def _fingerprint(self, role: str) -> str:
        password = self.settings.admin_password if role == "admin" else self.settings.viewer_password
        return hashlib.sha256(self.key + password.encode()).hexdigest()[:16] if password else ""

    def _sign(self, payload: str) -> str:
        return hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()

    def issue(self, role: str) -> str:
        payload = f"{role}|{int(time.time()) + SESSION_DAYS * 86400}|{self._fingerprint(role)}"
        return base64.urlsafe_b64encode(payload.encode()).decode() + "." + self._sign(payload)

    def role(self, token: str | None) -> str | None:
        try:
            encoded, signature = (token or "").rsplit(".", 1)
            payload = base64.urlsafe_b64decode(encoded.encode()).decode()
            if not hmac.compare_digest(signature, self._sign(payload)):
                return None
            role, expires, fingerprint = payload.split("|")
            expired = int(expires) < time.time()
        except (ValueError, UnicodeDecodeError):
            return None
        if role not in {"admin", "viewer"} or expired:
            return None
        expected = self._fingerprint(role)
        return role if expected and hmac.compare_digest(fingerprint, expected) else None


class Throttle:
    """Slows password guessing: 10 failures per address in 10 minutes."""

    def __init__(self, limit: int = 10, window: int = 600):
        self.limit, self.window, self.failures = limit, window, {}

    def blocked(self, address: str) -> bool:
        now = time.time()
        recent = [t for t in self.failures.get(address, []) if now - t < self.window]
        self.failures[address] = recent
        return len(recent) >= self.limit

    def fail(self, address: str):
        if len(self.failures) > 10000:
            self.failures.clear()
        self.failures.setdefault(address, []).append(time.time())


class ScanRequest(BaseModel):
    mode: Literal["latest", "backfill"] = "latest"


def create_app(settings: Settings | None = None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings or load_settings()
        app.state.db = Database(app.state.settings.database)
        app.state.sessions = Sessions(app.state.settings)
        app.state.throttle = Throttle()
        yield

    app = FastAPI(title="구카와 수집 관리", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(ROOT / "templates"))
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    def check(settings: Settings, username: str, password: str) -> str | None:
        """Return the role for a username/password pair, or None."""
        if settings.admin_password and secrets.compare_digest(username.encode(), b"admin") \
                and secrets.compare_digest(password.encode(), settings.admin_password.encode()):
            return "admin"
        # Optional watch-only account: it can open the viewer and its files, never the admin page or actions.
        if settings.viewer_username and settings.viewer_password \
                and secrets.compare_digest(username.encode(), settings.viewer_username.encode()) \
                and secrets.compare_digest(password.encode(), settings.viewer_password.encode()):
            return "viewer"
        return None

    def current_role(request: Request, credentials: HTTPBasicCredentials | None) -> str | None:
        role = request.app.state.sessions.role(request.cookies.get(SESSION_COOKIE))
        if not role and credentials:
            # Basic auth still works for scripts and curl; browsers get the login page instead of a popup.
            role = check(request.app.state.settings, credentials.username, credentials.password)
        return role

    def admin(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)):
        if not request.app.state.settings.admin_password:
            raise HTTPException(503, "ADMIN_PASSWORD를 설정하면 수집 관리 화면을 사용할 수 있어요.")
        if current_role(request, credentials) != "admin":
            raise LoginRequired(admin_only=True)
        request.state.role = "admin"

    def mutation(request: Request, _=Depends(admin)):
        if request.headers.get("X-Gukawa-Action") != "admin":
            raise HTTPException(403, "관리 화면에서 요청해 주세요.")

    def viewer(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)):
        settings = request.app.state.settings
        request.state.role = "admin"
        if settings.local_preview and request.client and request.client.host in {"127.0.0.1", "::1"}:
            return
        role = current_role(request, credentials)
        if not role:
            raise LoginRequired()
        request.state.role = role

    @app.exception_handler(LoginRequired)
    async def login_required(request: Request, exc: LoginRequired):
        # Pages redirect to the login form; API and file requests get a plain 401 (no browser popup).
        page = request.method == "GET" and not request.url.path.startswith(("/api/", "/files/"))
        if page:
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
        return JSONResponse({"detail": "관리자 로그인이 필요해요." if exc.admin_only else "로그인이 필요해요."}, status_code=401)

    def login_page(request: Request, next_url: str, error: str = "", username: str = "", status_code: int = 200):
        return templates.TemplateResponse(request=request, name="login.html", status_code=status_code,
                                          context={"next": next_url, "error": error, "username": username})

    @app.get("/login")
    def login_form(request: Request, next: str = Query("/", max_length=500)):
        target = safe_next(next)
        role = request.app.state.sessions.role(request.cookies.get(SESSION_COOKIE))
        if role == "admin" or (role and not target.startswith("/admin")):
            return RedirectResponse(target, status_code=303)
        return login_page(request, target, "관리자 계정으로 로그인해 주세요." if role else "")

    @app.post("/login")
    async def login(request: Request):
        body = await request.body()
        if len(body) > 4096:
            raise HTTPException(413)
        form = {key: values[0] for key, values in parse_qs(body.decode("utf-8", "replace")).items()}
        username, password = form.get("username", "").strip(), form.get("password", "")
        target = safe_next(form.get("next"))
        address = request.client.host if request.client else "unknown"
        throttle = request.app.state.throttle
        if throttle.blocked(address):
            return login_page(request, target, "로그인 시도가 너무 많아요. 10분 뒤에 다시 해 주세요.", username, 429)
        role = check(request.app.state.settings, username, password)
        if not role:
            throttle.fail(address)
            return login_page(request, target, "이름 또는 비밀번호가 맞지 않아요.", username, 401)
        if role != "admin" and target.startswith("/admin"):
            target = "/"
        response = RedirectResponse(target, status_code=303)
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(SESSION_COOKIE, request.app.state.sessions.issue(role), max_age=SESSION_DAYS * 86400,
                            httponly=True, samesite="lax", secure=secure, path="/")
        return response

    @app.post("/logout")
    def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def health(request: Request):
        request.app.state.db.counts()
        return {"status": "ok"}

    def inventory(request):
        db = request.app.state.db
        return {"counts": db.counts(), **{key: db.get_state(key) for key in STATE_KEYS}}

    def playlist(request):
        data_dir = request.app.state.settings.data_dir / "episodes"
        episodes = request.app.state.db.watchable()
        for item in episodes:
            item["poster"] = (data_dir / item["id"] / "playback.jpg").is_file()
            item["label"] = item["title_ko"] or item["title"]
        return episodes

    @app.get("/", dependencies=[Depends(viewer)])
    def index(request: Request, q: str = Query("", max_length=100)):
        episodes = playlist(request)
        query = q.strip()
        shown = [item for item in episodes if not query or query in str(item["episode_number"]) or query in item["label"]]
        return templates.TemplateResponse(request=request, name="index.html", context={
            "episodes": shown, "total": len(episodes), "q": query,
            "latest": episodes[-1] if episodes else None, "first": episodes[0] if episodes else None,
        })

    @app.get("/admin", dependencies=[Depends(admin)])
    def admin_page(request: Request, q: str = Query("", max_length=100), offset: int = Query(0, ge=0)):
        episodes = request.app.state.db.list(public=False, search=q, limit=50, offset=offset)
        return templates.TemplateResponse(request=request, name="admin.html", context={
            "episodes": episodes, "inventory": inventory(request), "q": q, "offset": offset,
            "account": request.app.state.settings.instagram_username,
            "min_episode": request.app.state.settings.min_episode,
            "local_preview": request.app.state.settings.local_preview,
        })

    @app.get("/api/admin/status", dependencies=[Depends(admin)])
    def status(request: Request):
        return inventory(request)

    @app.get("/api/admin/episodes", dependencies=[Depends(admin)])
    def episodes(request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return request.app.state.db.list(public=False, limit=limit, offset=offset)

    @app.post("/api/admin/scan", status_code=202, dependencies=[Depends(mutation)])
    def scan(body: ScanRequest, request: Request):
        request.app.state.db.set_state("sync_requested", body.mode)
        return {"requested": body.mode}

    @app.post("/api/admin/episodes/{episode_id}/retry", dependencies=[Depends(mutation)])
    def retry(episode_id: str, request: Request):
        success = request.app.state.db.transition(episode_id, ("failed",), "queued", reset=True)
        if not success:
            raise HTTPException(409, "실패한 작업만 다시 등록할 수 있어요.")
        return {"queued": episode_id}

    @app.get("/episodes/{episode_id}", dependencies=[Depends(viewer)])
    def detail(episode_id: str, request: Request, view: Literal["main", "original"] = "main"):
        episode = request.app.state.db.get(episode_id)
        if not episode:
            raise HTTPException(404)
        directory = request.app.state.settings.data_dir / "episodes" / episode_id
        metadata = {}
        if (directory / "metadata.json").is_file():
            metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        playback_available = (directory / "playback.mp4").is_file() and (directory / "playback.json").is_file()
        showing_main = playback_available and view == "main"
        _, caption_bottom, _ = caption_region(metadata, showing_main)
        korean = directory / "ko.vtt"
        episodes = playlist(request)
        position = next((i for i, item in enumerate(episodes) if item["id"] == episode_id), None)
        previous_episode = episodes[position - 1] if position else None
        next_episode = episodes[position + 1] if position is not None and position + 1 < len(episodes) else None
        return templates.TemplateResponse(request=request, name="detail.html", context={
            "playlist": episodes, "previous_episode": previous_episode, "next_episode": next_episode,
            "episode": episode, "showing_main": showing_main,
            "video_file": "playback.mp4" if showing_main else metadata.get("video_file"),
            "has_korean": korean.is_file() and korean.stat().st_size > 6,
            "caption_bottom": caption_bottom,
        })

    @app.get("/files/{episode_id}/{filename}", dependencies=[Depends(viewer)])
    def file(episode_id: str, filename: str, request: Request):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", episode_id) or not request.app.state.db.get(episode_id):
            raise HTTPException(404)
        allowed = {"source.txt", "source.json", "metadata.json", "ocr-observations.jsonl", "en.srt", "en.vtt", "ko.srt", "ko.vtt", "original.mp4", "original.webm", "original.mkv", "playback.mp4", "playback.jpg", "playback.json"}
        allowed.update({"translation-input.json", "translation-input.meta.json", "translation.direct.json", "translation.api.json", "translation.raw.json"})
        allowed.update(f"ko.{variant}.{extension}" for variant in ("direct", "api", "raw") for extension in ("json", "srt", "vtt"))
        if filename not in allowed:
            raise HTTPException(404)
        directory = request.app.state.settings.data_dir / "episodes" / episode_id
        path = (directory / filename).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            raise HTTPException(404)
        types = {".vtt": "text/vtt", ".srt": "text/plain", ".txt": "text/plain", ".json": "application/json", ".jsonl": "application/x-ndjson"}
        return FileResponse(path, media_type=types.get(path.suffix))

    return app


app = create_app()
