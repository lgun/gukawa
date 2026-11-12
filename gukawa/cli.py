import argparse
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

from filelock import FileLock, Timeout

from .config import load_settings
from .db import Database, utc_now
from .pipeline import process_episode, reconcile, sha256
from .playback import prepare_playback
from .source import parse_instagram_url
from .subtitles import read_subtitles
from .subtitles import atomic_text
from .worker import sync


def import_local(args, db, settings):
    video = Path(args.video).resolve()
    if not video.is_file() or video.stat().st_size > settings.max_video_bytes:
        raise ValueError("영상 파일 경로와 크기를 확인해 주세요.")
    if video.suffix.lower() != ".mp4":
        raise ValueError("로컬 가져오기는 MP4 파일을 사용해 주세요.")
    subtitles = Path(args.subtitles).resolve() if args.subtitles else None
    if subtitles:
        read_subtitles(subtitles)
    episode_id = "local-" + sha256(video)[:20]
    if db.get(episode_id):
        print(f"이미 등록된 영상이에요: {episode_id}")
        return
    parent = settings.data_dir / "episodes"
    directory = parent / episode_id
    staging = parent / (".import-" + uuid4().hex)
    staging.mkdir()
    shutil.copyfile(video, staging / "input.mp4")
    if subtitles:
        # Normalize VTT/SRT input to UTF-8 SRT.
        import pysubs2
        pysubs2.load(str(subtitles), encoding="utf-8-sig").save(str(staging / "input.srt"), encoding="utf-8")
    metadata = {"id": episode_id, "title": args.title, "source_type": "local", "imported_at": utc_now()}
    (staging / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    staging.replace(directory)
    db.enqueue(episode_id, "", args.title, input_language=args.language)
    print(f"처리 대기 목록에 등록했어요: {episode_id}")


def verify(db, settings, checksum=False):
    checked, issues = 0, []
    for episode in db.all_stored():
        checked += 1
        directory = settings.data_dir / "episodes" / episode["id"]
        try:
            metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
            filename = metadata["video_file"]
            if Path(filename).name != filename:
                raise ValueError("Invalid stored video filename")
            video = directory / filename
            from .subtitles import read_cues
            cues = read_cues(directory / "source.json")
            if not video.is_file() or video.stat().st_size != metadata["video_bytes"]:
                raise ValueError("Video missing or size mismatch")
            if not (directory / "source.txt").read_text(encoding="utf-8").strip():
                raise ValueError("Source text is empty")
            if metadata["cue_count"] != len(cues):
                raise ValueError("Cue count mismatch")
            for extension in ("srt", "vtt"):
                read_subtitles(directory / f"{episode['input_language']}.{extension}")
            if checksum and sha256(video) != metadata["video_sha256"]:
                raise ValueError("Checksum mismatch")
        except (OSError, ValueError, KeyError, TypeError) as error:
            issues.append({"id": episode["id"], "error": str(error)})
    return {"checked": checked, "issues": issues, "counts": db.counts()}


def main():
    parser = argparse.ArgumentParser(description="구카와 수집·원문 저장 도구")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="최신 또는 전체 게시물 확인")
    scan.add_argument("--backfill", action="store_true", help="과거 게시물 전체 확인")
    scan.add_argument("--now", action="store_true", help="워커 요청 대신 이 프로세스에서 확인")
    add = commands.add_parser("add", help="게시물 주소 한 개 등록")
    add.add_argument("url")
    importer = commands.add_parser("import", help="로컬 영상으로 저장/OCR 검증")
    importer.add_argument("--video", required=True)
    importer.add_argument("--subtitles", help="있으면 SRT/VTT 사용, 없으면 영어 OCR 실행")
    importer.add_argument("--title", required=True)
    importer.add_argument("--language", choices=["en", "ko"], default="en")
    process = commands.add_parser("process", help="대기 중인 작업 처리; 번역은 기본 비활성")
    process.add_argument("--all", action="store_true")
    process.add_argument("--translate", action="store_true", help="명시적으로 OpenAI 번역도 실행")
    api_translation = commands.add_parser("translate-api", help="저장된 한 회차를 API로 번역해 직접 번역과 별도 보관")
    api_translation.add_argument("id")
    api_translation.add_argument("--model", required=True, help="비교할 정확한 API 모델 ID")
    api_translation.add_argument("--force", action="store_true", help="같은 입력·모델의 결과가 있어도 다시 API 호출")
    api_translation.add_argument("--raw-input", action="store_true", help="수정 전 OCR 입력으로 자동 처리 조건 시험")
    api_translation.add_argument("--no-context", action="store_true", help="직전 회차 번역을 문맥으로 보내지 않음")
    api_translation.add_argument("--no-japanese", action="store_true", help="일본어 OCR 원문을 보내지 않고 영어만 번역")
    api_translation.add_argument("--make-default", action="store_true", help="API 번역을 기본 자막(ko.json)으로 지정. 직접 번역은 direct로 유지")
    japanese = commands.add_parser("extract-ja", help="저장된 영상에서 영어 자막 구간별 일본어 자막을 OCR로 추출")
    japanese.add_argument("id", nargs="?")
    japanese.add_argument("--all", action="store_true")
    japanese.add_argument("--force", action="store_true", help="이미 추출한 회차도 다시 추출")
    retry = commands.add_parser("retry", help="실패/원문 저장 완료 작업 재등록")
    retry.add_argument("id", nargs="?")
    retry.add_argument("--failed", action="store_true")
    retry.add_argument("--reextract", action="store_true", help="OCR/자막 추출 결과와 번역 캐시를 초기화")
    commands.add_parser("status")
    check = commands.add_parser("verify", help="원본·원문·타임코드 저장 검사")
    check.add_argument("--checksum", action="store_true")
    commands.add_parser("repair", help="누락된 파일이 있는 회차 재등록")
    playback = commands.add_parser("prepare-playback", help="인스타그램 원본에서 본편 재생용 영상 만들기")
    playback.add_argument("id", nargs="?")
    playback.add_argument("--all", action="store_true")
    playback.add_argument("--force", action="store_true")
    cookies = commands.add_parser("cookies", help="사용자 지시로 브라우저의 인스타그램 쿠키만 내보내기")
    cookies.add_argument("--browser", choices=["firefox", "chrome", "edge"], default="firefox")
    args = parser.parse_args()
    settings = load_settings()
    db = Database(settings.database)
    if args.command == "translate-api":
        from .translation import PROMPT_VERSION, TranslationError, episode_context, translate
        from .translation_archive import comparison_data, save_translation, translation_input
        episode = db.get(args.id)
        if not episode or episode["status"] not in {"source_ready", "review", "ready"}:
            raise ValueError("원문이 저장된 회차 ID를 지정해 주세요.")
        settings = replace(settings, translation_model=args.model)
        with FileLock(str(settings.data_dir / "worker.lock"), timeout=0):
            directory = settings.data_dir / "episodes" / episode["id"]
            variant = "raw" if args.raw_input else "api"
            cues, _ = translation_input(directory, raw=args.raw_input)
            existing = comparison_data(directory)
            prior = existing["variants"].get(variant) if existing else None
            if prior and prior.get("model") == args.model and not args.force:
                print("같은 입력·모델의 API 번역이 있어 재사용해요. 새 요청은 보내지 않았어요.")
                return 0
            audit = []
            context = [] if args.no_context else episode_context(db, settings, episode)
            from .subtitles import read_japanese
            japanese = None if args.no_japanese else read_japanese(directory / "source.ja.json", cues)
            try:
                title, korean = translate(cues, episode["title"], settings, audit=audit, context=context, japanese=japanese)
            except TranslationError as error:
                raise ValueError(str(error)) from None
            save_translation(directory, variant, korean, title, model=args.model, audit=audit,
                             notes=["보정 전 OCR 원문을 입력한 자동 처리 조건 시험입니다." if args.raw_input else "직접 번역문을 전송하지 않고 같은 수정 영어 입력만 번역했습니다.",
                                    f"프롬프트 {PROMPT_VERSION}",
                                    f"일본어 원문: {'사용' if japanese else '없음'}",
                                    f"문맥: {', '.join(str(item['episode']) + '화' for item in context) or '없음'}"],
                             make_default=args.make_default and not args.raw_input)
            if args.make_default and not args.raw_input:
                db.update(episode["id"], title_ko=title)
                print(f"{episode['episode_number']}화 API 번역 {len(korean)}개를 기본 자막으로 지정했어요. 직접 번역은 direct로 유지합니다.")
            else:
                print(f"{episode['episode_number']}화 API 번역 {len(korean)}개를 별도 저장했어요. 직접 번역은 유지합니다.")
        return 0
    if args.command == "extract-ja":
        from .subtitles import extract_japanese, read_cues, write_japanese
        if bool(args.id) == bool(args.all):
            raise ValueError("회차 ID 하나 또는 --all을 지정해 주세요.")
        episodes = [db.get(args.id)] if args.id else sorted(db.all_stored(), key=lambda row: row["episode_number"] or 0)
        for episode in episodes:
            if not episode:
                raise ValueError("회차를 찾을 수 없어요.")
            directory = settings.data_dir / "episodes" / episode["id"]
            target = directory / "source.ja.json"
            videos = sorted(directory.glob("original.*"))
            if (target.is_file() and not args.force) or not videos or not (directory / "source.json").is_file():
                print(f"{episode['episode_number']}화: 건너뜀")
                continue
            cues = read_cues(directory / "source.json")
            japanese = extract_japanese(videos[0], settings, cues, directory / "ocr-observations.ja.jsonl")
            write_japanese(target, cues, japanese)
            print(f"{episode['episode_number']}화: 일본어 {sum(bool(text) for text in japanese)}/{len(cues)}줄", flush=True)
        return 0
    if args.command == "scan":
        if args.now:
            with FileLock(str(settings.data_dir / "worker.lock"), timeout=0):
                reconcile(db, settings)
                result = sync(db, settings, backfill=args.backfill)
                if result is None:
                    raise ValueError(db.get_state("sync_error"))
                print(f"새 영상 {result}개를 대기 목록에 등록했어요.")
        else:
            db.set_state("sync_requested", "backfill" if args.backfill else "latest")
            print("전체 게시물 확인을 요청했어요." if args.backfill else "최신 게시물 확인을 요청했어요.")
    elif args.command == "add":
        shortcode = parse_instagram_url(args.url)
        added = db.enqueue(shortcode, f"https://www.instagram.com/p/{shortcode}/", "게시물 확인 대기")
        print(f"{'등록했어요' if added else '이미 등록되어 있어요'}: {shortcode}")
    elif args.command == "import":
        import_local(args, db, settings)
    elif args.command == "process":
        if args.translate:
            if not settings.api_key:
                raise ValueError("번역하려면 OPENAI_API_KEY를 설정해 주세요.")
            settings = replace(settings, processing_mode="translate")
        with FileLock(str(settings.data_dir / "worker.lock"), timeout=0):
            db.recover()
            processed = 0
            failures = 0
            while episode := db.claim():
                process_episode(episode, db, settings)
                print(f"{episode['id']}: {db.get(episode['id'])['status']}")
                failures += db.get(episode["id"])["status"] not in {"source_ready", "ready", "review", "skipped"}
                processed += 1
                if not args.all:
                    break
            print(f"처리한 작업: {processed}개")
            if failures:
                return 1
    elif args.command == "retry":
        if not args.id and not args.failed:
            raise ValueError("회차 ID 또는 --failed를 지정해 주세요.")
        if args.reextract and not args.id:
            raise ValueError("--reextract는 회차 ID와 함께 사용해 주세요.")
        with FileLock(str(settings.data_dir / "worker.lock"), timeout=0):
            candidates = [db.get(args.id)] if args.id else db.list(public=False, limit=100000)
            count = 0
            for episode in candidates:
                if not episode or (args.failed and episode["status"] != "failed"):
                    continue
                allowed = ("failed", "source_ready", "review", "ready", "queued")
                if episode["status"] not in allowed:
                    continue
                if args.reextract:
                    directory = settings.data_dir / "episodes" / episode["id"]
                    for name in ("source.json", "source.txt", "en.srt", "en.vtt", "ko.json", "ko.srt", "ko.vtt", "title-ko.txt"):
                        (directory / name).unlink(missing_ok=True)
                count += db.transition(episode["id"], allowed, "queued", reset=True)
            print(f"다시 등록한 작업: {count}개")
    elif args.command == "status":
        print(json.dumps({"counts": db.counts(), **{key: db.get_state(key) for key in ("last_sync", "sync_error", "backfill_complete", "worker_heartbeat", "scan_mode", "scan_added")}}, ensure_ascii=False, indent=2))
    elif args.command == "verify":
        report = verify(db, settings, args.checksum)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["issues"] else (0 if report["checked"] else 2)
    elif args.command == "repair":
        with FileLock(str(settings.data_dir / "worker.lock"), timeout=0):
            print(f"다시 등록한 작업: {reconcile(db, settings)}개")
    elif args.command == "prepare-playback":
        if not args.id and not args.all:
            raise ValueError("회차 ID 또는 --all을 지정해 주세요.")
        completed, failures = 0, 0
        with FileLock(str(settings.data_dir / "worker.lock"), timeout=0):
            candidates = [db.get(args.id)] if args.id else db.all_stored()
            for episode in candidates:
                if not episode or episode["status"] not in {"source_ready", "ready", "review"}:
                    continue
                if episode["episode_number"] is not None and episode["episode_number"] < settings.min_episode:
                    continue
                directory = settings.data_dir / "episodes" / episode["id"]
                try:
                    metadata_path = directory / "metadata.json"
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    filename = metadata["video_file"]
                    if Path(filename).name != filename:
                        raise ValueError("원본 영상 파일 이름을 확인해 주세요.")
                    info = prepare_playback(directory / filename, directory, settings.max_video_seconds, force=args.force)
                    metadata["playback"] = info
                    metadata.pop("playback_error", None)
                    atomic_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2))
                    completed += 1
                    print(f"{episode['episode_number']}화: {info['width']}×{info['height']} 저장 완료", flush=True)
                except (OSError, ValueError, KeyError, RuntimeError) as error:
                    failures += 1
                    print(f"{episode['id']}: {error}", flush=True)
        print(f"완료 {completed}편 / 실패 {failures}편")
        return 1 if failures else 0
    elif args.command == "cookies":
        from http.cookiejar import MozillaCookieJar
        from yt_dlp.cookies import extract_cookies_from_browser
        destination = settings.instagram_cookies
        if not destination:
            raise ValueError("INSTAGRAM_COOKIES 경로를 설정해 주세요.")
        jar = MozillaCookieJar(str(destination))
        for cookie in extract_cookies_from_browser(args.browser):
            if cookie.domain.lstrip(".") in {"instagram.com", "www.instagram.com", "i.instagram.com"}:
                jar.set_cookie(cookie)
        if not any(cookie.name == "sessionid" for cookie in jar):
            raise ValueError("로그인 세션이 없어요. 해당 브라우저에서 인스타그램에 로그인해 주세요.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        jar.save(ignore_discard=True, ignore_expires=True)
        destination.chmod(0o600)
        print("인스타그램 쿠키를 로컬 파일에 저장했어요. 내용을 채팅이나 저장소에 올리지 마세요.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Timeout:
        sys.exit("워커가 실행 중이에요. scan 명령은 --now 없이 실행하거나 워커를 잠시 중지해 주세요.")
    except (ValueError, OSError) as error:
        sys.exit(str(error))
