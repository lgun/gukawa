from contextlib import asynccontextmanager
import json
from pathlib import Path
import re
import secrets
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .config import Settings, load_settings
from .db import Database

ROOT = Path(__file__).parent
security = HTTPBasic(auto_error=False)
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


class ScanRequest(BaseModel):
    mode: Literal["latest", "backfill"] = "latest"


def create_app(settings: Settings | None = None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings or load_settings()
        app.state.db = Database(app.state.settings.database)
        yield

    app = FastAPI(title="구카와 수집 관리", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(ROOT / "templates"))
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    def admin(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)):
        password = request.app.state.settings.admin_password
        if not password:
            raise HTTPException(503, "ADMIN_PASSWORD를 설정하면 수집 관리 화면을 사용할 수 있어요.")
        valid_name = credentials and secrets.compare_digest(credentials.username.encode(), b"admin")
        valid_password = credentials and secrets.compare_digest(credentials.password.encode(), password.encode())
        if not valid_name or not valid_password:
            raise HTTPException(401, "관리자 로그인이 필요해요.", headers={"WWW-Authenticate": 'Basic realm="gukawa", charset="UTF-8"'})

    def mutation(request: Request, _=Depends(admin)):
        if request.headers.get("X-Gukawa-Action") != "admin":
            raise HTTPException(403, "관리 화면에서 요청해 주세요.")

    def viewer(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)):
        settings = request.app.state.settings
        request.state.role = "admin"
        if settings.local_preview and request.client and request.client.host in {"127.0.0.1", "::1"}:
            return
        # Optional watch-only account: it can open the viewer and its files, never the admin page or actions.
        if settings.viewer_username and settings.viewer_password and credentials:
            if (secrets.compare_digest(credentials.username.encode(), settings.viewer_username.encode())
                    and secrets.compare_digest(credentials.password.encode(), settings.viewer_password.encode())):
                request.state.role = "viewer"
                return
        if not settings.admin_password and settings.viewer_password:
            raise HTTPException(401, "로그인이 필요해요.", headers={"WWW-Authenticate": 'Basic realm="gukawa", charset="UTF-8"'})
        admin(request, credentials)

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
