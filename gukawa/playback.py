"""Create a landscape viewing copy from the verified Instagram layout."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .media import MediaError, probe_duration, run_media
from .subtitles import atomic_text

CROPS = {
    (720, 1280): {"x": 0, "y": 452, "width": 720, "height": 388},
    (1080, 1920): {"x": 0, "y": 678, "width": 1080, "height": 582},
}


def video_stream(path: Path) -> dict:
    result = run_media(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,codec_name,sample_aspect_ratio", "-of", "json", str(path)], timeout=30)
    try:
        return json.loads(result.stdout)["streams"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        raise MediaError("영상 화면 정보를 읽지 못했어요.") from None


def prepare_playback(source: Path, directory: Path, maximum: int, *, force=False) -> dict:
    source = source.resolve()
    directory = directory.resolve()
    destination = directory / "playback.mp4"
    if source == destination:
        raise MediaError("인스타그램 원본 영상으로 재생용 파일을 만들어 주세요.")
    stream = video_stream(source)
    dimensions = (stream["width"], stream["height"])
    if dimensions not in CROPS:
        raise MediaError("확인되지 않은 화면 구성이에요. 원본을 보관하고 자르기 영역을 먼저 확인해 주세요.")
    crop = CROPS[dimensions].copy()
    preset = f"instagram-center-{dimensions[0]}x{dimensions[1]}-v1"
    duration = probe_duration(source, maximum)
    with source.open("rb") as handle:
        source_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    sidecar = directory / "playback.json"
    poster = directory / "playback.jpg"
    if not force and destination.is_file() and poster.is_file() and sidecar.is_file():
        try:
            cached = json.loads(sidecar.read_text(encoding="utf-8"))
            if cached.get("preset") == preset and cached.get("crop") == crop and cached.get("source_sha256") == source_hash and cached.get("video_bytes") == destination.stat().st_size:
                return cached
        except (ValueError, OSError):
            pass
    temporary = directory / "playback.tmp.mp4"
    try:
        # Keep the original timeline and copy the audio packets unchanged.
        run_media(["ffmpeg", "-nostdin", "-y", "-v", "error", "-filter_threads", "2", "-i", str(source),
                   "-map", "0:v:0", "-map", "0:a:0?", "-vf", f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']},setsar=1",
                   "-c:v", "libx264", "-threads", "2", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p",
                   "-c:a", "copy", "-movflags", "+faststart", str(temporary)])
        output = video_stream(temporary)
        output_duration = probe_duration(temporary, maximum)
        if (output["width"], output["height"]) != (crop["width"], crop["height"]) or abs(output_duration - duration) > 0.15:
            raise MediaError("재생용 영상의 크기나 재생 시간이 원본과 맞지 않아요.")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    poster_temporary = directory / "playback.tmp.jpg"
    try:
        run_media(["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", str(min(duration / 3, 15)),
                   "-i", str(destination), "-frames:v", "1", str(poster_temporary)], timeout=60)
        poster_temporary.replace(poster)
    finally:
        poster_temporary.unlink(missing_ok=True)
    info = {
        "preset": preset, "source_sha256": source_hash, "source_file": source.name,
        "video_file": "playback.mp4", "poster_file": "playback.jpg", "crop": crop,
        "width": crop["width"], "height": crop["height"], "duration": output_duration,
        "video_bytes": destination.stat().st_size, "audio": "copied", "upscaled": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_text(sidecar, json.dumps(info, ensure_ascii=False, indent=2))
    return info
