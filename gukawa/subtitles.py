from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import html
import json
import math
from pathlib import Path
import re

import pysubs2


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def validate_cues(cues: list[Cue]) -> list[Cue]:
    if not cues or len(cues) > 5000:
        raise ValueError("자막이 비어 있거나 너무 많아요.")
    previous_end = 0.0
    for cue in cues:
        if not math.isfinite(cue.start) or not math.isfinite(cue.end):
            raise ValueError("자막 시간이 올바르지 않아요.")
        if cue.start < previous_end - 0.002 or cue.end <= cue.start:
            raise ValueError("자막 시간이 겹치거나 순서가 올바르지 않아요.")
        if not cue.text.strip() or len(cue.text) > 1000:
            raise ValueError("자막 문장이 비어 있거나 너무 길어요.")
        previous_end = cue.end
    return cues


def read_subtitles(path: Path) -> list[Cue]:
    subtitles = pysubs2.load(str(path), encoding="utf-8-sig")
    return validate_cues([Cue(event.start / 1000, event.end / 1000, event.plaintext.strip())
                          for event in subtitles if event.plaintext.strip() and not event.is_comment])


def read_cues(path: Path) -> list[Cue]:
    return validate_cues([Cue(**item) for item in json.loads(path.read_text(encoding="utf-8"))])


def write_cues(path: Path, cues: list[Cue]):
    validate_cues(cues)
    atomic_text(path, json.dumps([asdict(cue) for cue in cues], ensure_ascii=False, indent=2))


def atomic_text(path: Path, content: str):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def timestamp(seconds: float, separator=".") -> str:
    total = round(seconds * 1000)
    hours, rest = divmod(total, 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds_int, milliseconds = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{seconds_int:02}{separator}{milliseconds:03}"


def write_tracks(directory: Path, cues: list[Cue], language: str, *, stem: str | None = None):
    validate_cues(cues)
    stem = stem or language
    if not re.fullmatch(r"[a-z][a-z0-9.-]{0,30}", stem):
        raise ValueError("Invalid subtitle filename")
    vtt = ["WEBVTT", ""]
    if language == "ko":
        vtt += ["STYLE", "::cue { color: white; background-color: rgb(17, 17, 17); }", ""]
    srt = []
    for index, cue in enumerate(cues, 1):
        # The web player positions its own overlay; exported VTT uses a bottom cue.
        position = " line:85% position:50% align:center" if language == "ko" else ""
        safe_text = html.escape(cue.text.replace("\x00", "").replace("\r", ""), quote=False)
        safe_text = re.sub(r"\n\s*\n", "\n", safe_text)
        vtt += [str(index), f"{timestamp(cue.start)} --> {timestamp(cue.end)}{position}", safe_text, ""]
        srt += [str(index), f"{timestamp(cue.start, ',')} --> {timestamp(cue.end, ',')}", safe_text, ""]
    atomic_text(directory / f"{stem}.vtt", "\n".join(vtt) + "\n")
    atomic_text(directory / f"{stem}.srt", "\n".join(srt) + "\n")


def normalized(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def order_ocr_lines(lines):
    """Group words by their line height, then read each row from left to right."""
    rows = []
    for line in sorted(lines, key=lambda item: item[0]):
        if rows and abs(line[0] - rows[-1][0]) <= min(line[4], rows[-1][1]) * 0.65:
            rows[-1][2].append(line)
            rows[-1][0] = sum(item[0] for item in rows[-1][2]) / len(rows[-1][2])
        else:
            rows.append([line[0], line[4], [line]])
    return [line for row in rows for line in sorted(row[2], key=lambda item: item[1])]


def merge_observations(observations, sample_period: float, duration: float) -> list[Cue]:
    """Merge consecutive OCR frames, never across an empty frame. Require 2 sightings."""
    result: list[Cue] = []
    current = None

    def flush():
        if current and current["count"] >= 2:
            end = min(current["last"] + sample_period, duration)
            if end > current["start"]:
                result.append(Cue(current["start"], end, current["text"]))

    for moment, text, confidence in observations:
        text = " ".join(text.split())
        key = normalized(text)
        if not key:
            flush()
            current = None
            continue
        same = current and (key == current["key"] or (
            min(len(key), len(current["key"])) >= 8
            and SequenceMatcher(None, key, current["key"]).ratio() >= 0.92
        ))
        if same:
            current["last"] = moment
            current["count"] += 1
            if confidence > current["confidence"]:
                current.update(text=text, key=key, confidence=confidence)
        else:
            flush()
            current = dict(start=moment, last=moment, text=text, key=key, confidence=confidence, count=1)
    flush()
    return validate_cues(result)


def extract_english(video: Path, settings, duration: float, observations_path: Path | None = None) -> list[Cue]:
    import cv2
    from rapidocr import ModelType, OCRVersion, RapidOCR

    cv2.setNumThreads(2)
    engine = RapidOCR(params={
        "Global.model_root_dir": str(settings.data_dir / "models"),
        "Global.log_level": "error",
        "Global.use_cls": False,
        "Det.ocr_version": OCRVersion.PPOCRV4,
        "Det.model_type": ModelType.MOBILE,
        "Det.limit_type": "max",
        "Det.limit_side_len": 960,
        "Det.mean": [0.485, 0.456, 0.406],
        "Det.std": [0.229, 0.224, 0.225],
        "Rec.ocr_version": OCRVersion.PPOCRV4,
        "Rec.model_type": ModelType.MOBILE,
        "Rec.rec_batch_num": 1,
        "EngineConfig.onnxruntime.intra_op_num_threads": 2,
        "EngineConfig.onnxruntime.inter_op_num_threads": 2,
    })
    # Bound FFmpeg decoder threads too; host CPU counts can be much larger than
    # the container's available memory, especially with MPEG-4 test/import files.
    capture = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 2])
    if not capture.isOpened():
        raise ValueError("영상을 열 수 없어요.")
    fps = capture.get(cv2.CAP_PROP_FPS)
    if not math.isfinite(fps) or fps <= 0:
        capture.release()
        raise ValueError("영상 프레임 속도를 읽을 수 없어요.")

    raw_log = observations_path.open("w", encoding="utf-8") if observations_path else None

    def observations():
        frame_index = 0
        next_sample = 0.0
        while True:
            success, frame = capture.read()
            if not success:
                break
            moment = frame_index / fps
            frame_index += 1
            if moment >= duration:
                break
            if moment + 1e-6 < next_sample:
                continue
            next_sample = moment + 1 / settings.sample_fps
            height = frame.shape[0]
            cropped = frame[int(height * settings.crop_top):int(height * settings.crop_bottom)]
            # Upscale small, outlined video text before line detection/recognition.
            if cropped.shape[1] < 1280:
                cropped = cv2.resize(cropped, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            output = engine(cropped)
            lines = []
            if output.txts:
                for box, text, score in zip(output.boxes, output.txts, output.scores):
                    letters = sum(character.isalpha() for character in text)
                    latin = len(re.findall(r"[A-Za-z]", text))
                    if score >= settings.min_confidence and latin >= 2 and latin / max(letters, 1) >= 0.8:
                        lines.append((float(box[:, 1].mean()), float(box[:, 0].mean()), text, float(score), float(box[:, 1].max() - box[:, 1].min())))
            lines = order_ocr_lines(lines)
            text = " ".join(line[2] for line in lines)
            confidence = sum(line[3] for line in lines) / len(lines) if lines else 0
            if raw_log:
                raw_log.write(json.dumps({"time": moment, "text": text, "confidence": confidence}, ensure_ascii=False) + "\n")
                raw_log.flush()
            yield moment, text, confidence

    try:
        return merge_observations(observations(), 1 / settings.sample_fps, duration)
    finally:
        capture.release()
        if raw_log:
            raw_log.close()


KANA = re.compile(r"[぀-ヿ]")
CJK = re.compile(r"[㐀-鿿]")
LATIN = re.compile(r"[A-Za-z]")


def japanese_from_lines(lines, min_confidence: float = 0.6) -> str:
    """Pick the Japanese row out of stacked English / Chinese / Japanese OCR rows.

    `lines` are (y_center, x_center, text, score). Rows with kana are Japanese. When no row has kana,
    the lowest of two or more CJK-only rows is taken, because Chiikawa posts stack Chinese above Japanese.
    """
    usable = [line for line in lines if line[3] >= min_confidence and not LATIN.search(line[2])]
    japanese = [line for line in usable if KANA.search(line[2])]
    if not japanese:
        cjk = sorted((line for line in usable if CJK.search(line[2])), key=lambda line: line[0])
        japanese = cjk[-1:] if len(cjk) >= 2 else []
    japanese.sort(key=lambda line: (round(line[0] / 20), line[1]))
    return "".join(line[2].strip() for line in japanese)


def choose_japanese(candidates) -> str:
    """Most frequently read text within a cue; ties go to the highest confidence."""
    votes: dict[str, list[float]] = {}
    for text, confidence in candidates:
        key = re.sub(r"\s+", "", text)
        if key:
            votes.setdefault(key, []).append(confidence)
    if not votes:
        return ""
    return max(votes, key=lambda key: (len(votes[key]), max(votes[key])))


def extract_japanese(video: Path, settings, cues: list[Cue], observations_path: Path | None = None,
                     sample_fps: float = 2.0) -> list[str]:
    """Read the burned-in Japanese line for each existing cue window. Returns one string per cue ('' if none)."""
    import cv2
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

    cv2.setNumThreads(2)
    engine = RapidOCR(params={
        "Global.model_root_dir": str(settings.data_dir / "models"),
        "Global.log_level": "error",
        "Global.use_cls": False,
        "Det.ocr_version": OCRVersion.PPOCRV6,
        "Det.model_type": ModelType.SMALL,
        "Rec.ocr_version": OCRVersion.PPOCRV6,
        "Rec.model_type": ModelType.SMALL,
        "Rec.lang_type": LangRec.JAPAN,
        "EngineConfig.onnxruntime.intra_op_num_threads": 2,
        "EngineConfig.onnxruntime.inter_op_num_threads": 2,
    })
    capture = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 2])
    if not capture.isOpened():
        raise ValueError("영상을 열 수 없어요.")
    fps = capture.get(cv2.CAP_PROP_FPS)
    if not math.isfinite(fps) or fps <= 0:
        capture.release()
        raise ValueError("영상 프레임 속도를 읽을 수 없어요.")
    candidates: list[list[tuple[str, float]]] = [[] for _ in cues]
    raw_log = observations_path.open("w", encoding="utf-8") if observations_path else None
    try:
        frame_index = 0
        next_sample = 0.0
        last_end = max(cue.end for cue in cues)
        while True:
            success, frame = capture.read()
            if not success:
                break
            moment = frame_index / fps
            frame_index += 1
            if moment >= last_end:
                break
            if moment + 1e-6 < next_sample:
                continue
            next_sample = moment + 1 / sample_fps
            inside = [i for i, cue in enumerate(cues) if cue.start <= moment < cue.end]
            if not inside:
                continue
            height = frame.shape[0]
            cropped = frame[int(height * settings.crop_top):int(height * settings.crop_bottom)]
            if cropped.shape[1] < 1280:
                cropped = cv2.resize(cropped, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            output = engine(cropped)
            lines = []
            if output.txts:
                for box, text, score in zip(output.boxes, output.txts, output.scores):
                    lines.append((float(box[:, 1].mean()), float(box[:, 0].mean()), text, float(score)))
            text = japanese_from_lines(lines)
            confidence = min((line[3] for line in lines if line[2] in text), default=0.0) if text else 0.0
            if raw_log:
                raw_log.write(json.dumps({"time": moment, "text": text, "confidence": confidence,
                                          "rows": [line[2] for line in lines]}, ensure_ascii=False) + "\n")
            if text:
                for i in inside:
                    candidates[i].append((text, confidence))
    finally:
        capture.release()
        if raw_log:
            raw_log.close()
    return [choose_japanese(items) for items in candidates]


def write_japanese(path: Path, cues: list[Cue], japanese: list[str]):
    if len(cues) != len(japanese):
        raise ValueError("Japanese line count mismatch")
    atomic_text(path, json.dumps([{"start": cue.start, "end": cue.end, "ja": text} for cue, text in zip(cues, japanese)],
                                 ensure_ascii=False, indent=2))


def read_japanese(path: Path, cues: list[Cue]) -> list[str] | None:
    """Japanese text aligned to `cues` by timing, or None when no Japanese extraction exists."""
    if not path.is_file():
        return None
    by_time = {(item["start"], item["end"]): item["ja"] for item in json.loads(path.read_text(encoding="utf-8"))}
    return [by_time.get((cue.start, cue.end), "") for cue in cues]
