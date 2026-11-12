import json
import math
from pathlib import Path
import shutil
import subprocess


class MediaError(RuntimeError):
    pass


def run_media(arguments: list[str], timeout=900):
    if not shutil.which(arguments[0]):
        raise MediaError("FFmpeg와 ffprobe를 설치해 주세요. Docker 이미지에는 포함되어 있어요.")
    try:
        return subprocess.run(arguments, check=True, capture_output=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise MediaError("영상 변환에 실패했어요. 파일 형식과 저장 공간을 확인해 주세요.") from None


def probe_duration(video: Path, maximum: int) -> float:
    response = run_media(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)], timeout=30)
    try:
        duration = float(json.loads(response.stdout)["format"]["duration"])
    except (ValueError, KeyError, TypeError):
        raise MediaError("영상 길이를 읽을 수 없어요.") from None
    if not math.isfinite(duration) or not 0 < duration <= maximum:
        raise MediaError("영상 길이가 설정된 제한을 초과하거나 올바르지 않아요.")
    return duration


def prepare_video(source: Path, directory: Path, maximum: int) -> float:
    duration = probe_duration(source, maximum)
    destination = directory / "video.mp4"
    if source != destination:
        temporary = directory / "video.tmp.mp4"
        run_media(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
                   "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                   "-c:a", "aac", "-movflags", "+faststart", str(temporary)])
        temporary.replace(destination)
    if not (directory / "poster.jpg").is_file():
        temporary = directory / "poster.tmp.jpg"
        run_media(["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", str(min(duration / 3, 5)), "-i", str(destination),
                   "-frames:v", "1", "-vf", "scale=640:-2", str(temporary)], timeout=60)
        temporary.replace(directory / "poster.jpg")
    return duration
