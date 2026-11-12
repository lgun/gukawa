"""Versioned translations and a shared, reproducible input for quality review."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from .db import utc_now
from .subtitles import Cue, atomic_text, read_cues, validate_cues, write_cues, write_tracks


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def translation_input(directory: Path, *, raw=False) -> tuple[list[Cue], dict]:
    original = directory / "source.json"
    reviewed = directory / "translation-input.json"
    if reviewed.is_file() and not raw:
        info = json.loads((directory / "translation-input.meta.json").read_text(encoding="utf-8"))
        if info["original_sha256"] != digest(original) or info["input_sha256"] != digest(reviewed):
            raise ValueError("번역 입력이 검토 후 변경됐어요. 수정 내역을 다시 확인해 주세요.")
        return read_cues(reviewed), info
    return read_cues(original), {"input_sha256": digest(original), "original_sha256": digest(original),
                                 "input_file": "source.json", "corrections": [], "excluded": []}


def save_translation(directory: Path, variant: str, cues: list[Cue], title: str,
                     *, model: str | None = None, notes: list[str] | None = None,
                     audit: list | None = None, make_default=False) -> dict:
    if variant not in {"direct", "api", "raw"}:
        raise ValueError("Unknown translation variant")
    source, info = translation_input(directory, raw=variant == "raw")
    validate_cues(cues)
    if [(c.start, c.end) for c in cues] != [(c.start, c.end) for c in source]:
        raise ValueError("Translation timing or cue count mismatch")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("Invalid translated title")
    stem = f"ko.{variant}"
    write_cues(directory / f"{stem}.json", cues)
    write_tracks(directory, cues, "ko", stem=stem)
    manifest = {"variant": variant, "method": "codex_direct" if variant == "direct" else "openai_api",
                "model": model, "title_ko": title, "created_at": utc_now(), "cue_count": len(cues),
                "input_file": info["input_file"], "input_sha256": info["input_sha256"],
                "original_sha256": info["original_sha256"], "notes": notes or [],
                "translation_sha256": digest(directory / f"{stem}.json"),
                "vtt_sha256": digest(directory / f"{stem}.vtt")}
    if audit is not None:
        audit_file = f"translation.{variant}.audit.json"
        save_json(directory / audit_file, audit)
        manifest["audit_file"] = audit_file
        manifest["response_models"] = sorted({entry["response"]["model"] for entry in audit
                                              if isinstance(entry.get("response", {}).get("model"), str)})
    # The manifest is the completion marker for a particular input and translation.
    save_json(directory / f"translation.{variant}.json", manifest)
    if make_default:
        write_cues(directory / "ko.json", cues)
        write_tracks(directory, cues, "ko")
        atomic_text(directory / "title-ko.txt", title)
        metadata_path = directory / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        metadata["translation"] = manifest
        save_json(metadata_path, metadata)
    return manifest


def comparison_data(directory: Path) -> dict | None:
    if not any((directory / f"translation.{variant}.json").is_file() for variant in ("direct", "api", "raw")):
        return None
    source, info = translation_input(directory)
    variants = {}
    texts = {}
    original, original_info = translation_input(directory, raw=True)
    for variant in ("direct", "api", "raw"):
        manifest_path = directory / f"translation.{variant}.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        subtitle = directory / f"ko.{variant}.json"
        vtt = directory / f"ko.{variant}.vtt"
        expected_input = original_info if variant == "raw" else info
        if (manifest.get("input_sha256") != expected_input["input_sha256"]
                or manifest.get("translation_sha256") != digest(subtitle)
                or manifest.get("vtt_sha256") != digest(vtt)):
            raise ValueError("번역 결과나 비교 입력이 변경됐어요. 번역 기록을 확인해 주세요.")
        cues = read_cues(subtitle)
        expected_cues = original if variant == "raw" else source
        if [(c.start, c.end) for c in cues] != [(c.start, c.end) for c in expected_cues]:
            raise ValueError("Comparison timing mismatch")
        variants[variant] = manifest
        texts[variant] = {(cue.start, cue.end): cue.text for cue in cues}
    originals = {(cue.start, cue.end): cue.text for cue in original}
    rows = [{"index": i, **asdict(cue), "direct": texts.get("direct", {}).get((cue.start, cue.end)),
             "api": texts.get("api", {}).get((cue.start, cue.end)),
             "raw": texts.get("raw", {}).get((cue.start, cue.end)),
             "raw_source": originals.get((cue.start, cue.end)),
             "time": f"{int(cue.start)//60}:{int(cue.start)%60:02d}"} for i, cue in enumerate(source)]
    return {"input": info, "variants": variants, "rows": rows,
            "complete": "direct" in variants and "api" in variants}
