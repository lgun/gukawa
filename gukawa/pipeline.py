import hashlib
import json
import logging
from pathlib import Path
import time

from .db import utc_now
from .media import MediaError, probe_duration
from .playback import prepare_playback
from .source import EpisodeExcluded, InstagramSource, SourceError, ensure_episode_allowed
from .subtitles import (atomic_text, extract_english, extract_japanese, read_cues, read_japanese, read_subtitles,
                        write_cues, write_japanese, write_tracks)
from .translation import PROMPT_VERSION, TranslationError, episode_context, translate

log = logging.getLogger(__name__)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_episode(episode: dict, db, settings):
    episode_id = episode["id"]
    directory = settings.data_dir / "episodes" / episode_id
    directory.mkdir(exist_ok=True)
    language = episode["input_language"]
    try:
        # Download/extraction intentionally work without any translation API key.
        metadata_path = directory / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
        originals = sorted(directory.glob("original.*"))
        source = None
        if episode["source_url"]:
            number = episode.get("episode_number")
            if number is None:
                number = metadata.get("episode_number")
            if number is not None:
                ensure_episode_allowed(number, settings.min_episode)
            if number is None or (not originals and not (directory / "input.mp4").is_file()):
                source = InstagramSource(settings)
                post = source.inspect(episode_id)
                ensure_episode_allowed(post.get("episode_number"), settings.min_episode)
                metadata.update(post)
                db.update(episode_id, **{key: value for key, value in post.items() if key not in {"episode_id", "source_url"}})
                atomic_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2))
        if originals:
            video = originals[0]
        else:
            local = directory / "input.mp4"
            if local.is_file():
                source_video = local
            else:
                if source is None:
                    source = InstagramSource(settings)
                    post = source.inspect(episode_id)
                    ensure_episode_allowed(post.get("episode_number"), settings.min_episode)
                    metadata.update(post)
                    db.update(episode_id, **{key: value for key, value in post.items() if key not in {"episode_id", "source_url"}})
                    atomic_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2))
                source_video = source.download(episode["source_url"], directory)
                formats_path = directory / "download-formats.json"
                if formats_path.is_file():
                    metadata["download_formats"] = json.loads(formats_path.read_text(encoding="utf-8"))
            video = directory / f"original{source_video.suffix}"
            source_video.replace(video)
            metadata.update(downloaded_at=utc_now(), video_file=video.name, video_sha256=sha256(video), video_bytes=video.stat().st_size)
        if video.stat().st_size > settings.max_video_bytes:
            raise MediaError("영상이 설정된 파일 크기 제한을 초과했어요.")
        if not metadata.get("video_sha256") or metadata.get("video_file") != video.name:
            # Recover a crash between the atomic video rename and metadata checkpoint.
            metadata.update(video_file=video.name, video_sha256=sha256(video), video_bytes=video.stat().st_size)
        elif metadata.get("video_bytes") != video.stat().st_size:
            raise MediaError("저장된 원본 영상의 파일 크기가 달라요. 원본 파일을 확인해 주세요.")
        duration = probe_duration(video, settings.max_video_seconds)
        metadata.update(id=episode_id, source_url=episode["source_url"], title=db.get(episode_id)["title"], duration=duration)
        if settings.create_playback:
            try:
                metadata["playback"] = prepare_playback(video, directory, settings.max_video_seconds)
                metadata.pop("playback_error", None)
            except MediaError as error:
                # A new/unverified layout must not destroy the archive or fail OCR.
                metadata["playback_error"] = str(error)
                log.warning("Episode %s: viewing copy unavailable", episode_id)
        atomic_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2))
        db.update(episode_id, status="extracting", duration=duration)
        input_cues = directory / "source.json"
        extraction = metadata.get("extraction", {})
        if input_cues.is_file():
            cues = read_cues(input_cues)
        else:
            supplied = directory / "input.srt"
            tracks = sorted(directory.glob("download.en*.vtt")) + sorted(directory.glob("download.en*.srt"))
            if supplied.is_file():
                cues = read_subtitles(supplied)
                extraction = {"method": "provided_subtitles"}
            elif tracks:
                cues = read_subtitles(tracks[0])
                extraction = {"method": "embedded_subtitles"}
            else:
                cues = extract_english(video, settings, duration, directory / "ocr-observations.jsonl")
                extraction = {"method": "ocr", "sample_fps": settings.sample_fps,
                              "model": "PP-OCRv4 mobile (ch/en)",
                              "crop_top": settings.crop_top, "crop_bottom": settings.crop_bottom,
                              "min_confidence": settings.min_confidence, "manually_verified": False}
            write_cues(input_cues, cues)
        if cues[-1].end > duration + 0.1:
            raise ValueError("Subtitle extends beyond video")
        atomic_text(directory / "source.txt", "\n".join(cue.text for cue in cues) + "\n")
        write_tracks(directory, cues, language)
        japanese_path = directory / "source.ja.json"
        if language != "ko" and not japanese_path.is_file():
            # The burned-in Japanese line is the real source text; English stays as a fallback if this fails.
            try:
                write_japanese(japanese_path, cues, extract_japanese(video, settings, cues, directory / "ocr-observations.ja.jsonl"))
                extraction = {**extraction, "japanese": {"method": "ocr", "model": "PP-OCRv6 small (japan)"}}
            except Exception:
                log.exception("Episode %s: Japanese subtitle extraction failed; translating from English", episode_id)
        metadata.update(extraction=extraction, source_language=language, cue_count=len(cues), extracted_at=utc_now())
        atomic_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2))
        if settings.processing_mode == "source":
            db.update(episode_id, status="source_ready", error=None)
            log.info("Episode %s: original video and %s source cues saved", episode_id, len(cues))
            return
        title = db.get(episode_id)["title"]
        if language == "ko":
            title_ko, korean = title, cues
        else:
            db.update(episode_id, status="translating")
            from .translation_archive import save_translation, translation_input
            cues, _ = translation_input(directory)
            checkpoint = directory / "ko.json"
            title_file = directory / "title-ko.txt"
            if checkpoint.is_file() and title_file.is_file():
                korean = read_cues(checkpoint)
                title_ko = title_file.read_text(encoding="utf-8")
                if [(cue.start, cue.end) for cue in korean] != [(cue.start, cue.end) for cue in cues]:
                    raise ValueError("Translation timing mismatch")
            else:
                audit = []
                context = episode_context(db, settings, db.get(episode_id))
                japanese = read_japanese(directory / "source.ja.json", cues)
                title_ko, korean = translate(cues, title, settings, audit=audit, context=context, japanese=japanese)
                save_translation(directory, "api", korean, title_ko, model=settings.translation_model,
                                 audit=audit, make_default=True,
                                 notes=[f"프롬프트 {PROMPT_VERSION}",
                                        f"문맥: {', '.join(str(item['episode']) + '화' for item in context) or '없음'}"])
        write_tracks(directory, korean, "ko")
        db.update(episode_id, title_ko=title_ko, status="ready" if settings.auto_publish else "review", error=None)
    except EpisodeExcluded as error:
        db.update(episode_id, status="skipped", error=str(error), available_at=0)
        log.info("Episode %s excluded from collection range", episode_id)
    except Exception as error:
        if isinstance(error, (SourceError, MediaError, TranslationError)):
            message = str(error)
        elif isinstance(error, ValueError):
            message = "원문 자막을 추출하거나 검증하지 못했어요. 자막 영역·타임코드·metadata.json을 확인해 주세요."
        else:
            message = f"처리에 실패했어요 ({type(error).__name__}). 설치된 도구와 저장 공간을 확인해 주세요."
        attempts = episode["attempts"]
        retry = attempts < settings.max_attempts
        db.update(episode_id, status="queued" if retry else "failed", error=message, available_at=time.time() + min(3600, 60 * 5 ** (attempts - 1)))
        log.warning("Episode %s failed (%s), retry=%s", episode_id, type(error).__name__, retry)


def reconcile(db, settings) -> int:
    repaired = 0
    for episode in db.all_stored():
        directory = settings.data_dir / "episodes" / episode["id"]
        required = [directory / "source.json", directory / "source.txt", directory / "metadata.json"]
        originals = list(directory.glob("original.*"))
        tracks = [directory / f"{episode['input_language']}.srt", directory / f"{episode['input_language']}.vtt"]
        if episode["status"] in {"ready", "review"}:
            tracks += [directory / "ko.vtt", directory / "ko.srt"]
        if not originals or not originals[0].stat().st_size or any(not path.is_file() or not path.stat().st_size for path in required + tracks):
            repaired += db.transition(episode["id"], (episode["status"],), "queued", reset=True)
    return repaired
