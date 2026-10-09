"""Subprocess access to FFmpeg and FFprobe. No shell invocation."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Any

from .errors import EncodingError, ToolNotFoundError, VerificationError
from .formats import FORMATS, normalize_format


def executable(name: str, provided: str | Path | None = None) -> str:
    if provided is None:
        found = shutil.which(name)
    else:
        found = shutil.which(str(provided))
    if not found:
        raise ToolNotFoundError(
            f"{name} executable not found: {provided or name}",
            code=f"audioexport.{name}_missing",
        )
    return found


def run(
    args: list[str], *, error_type: type[EncodingError | VerificationError] = EncodingError
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise error_type(f"failed to run {args[0]}: {exc}", code="audioexport.tool_failed") from exc
    if result.returncode:
        tail = "\n".join(result.stderr.splitlines()[-6:]).strip()
        raise error_type(
            f"{Path(args[0]).name} exited {result.returncode}: {tail}",
            code="audioexport.tool_failed",
        )
    return result


def version(exe: str) -> str:
    return run([exe, "-version"]).stdout.splitlines()[0].strip()


def _probe_number(value: Any, field: str, *, allow_zero: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise VerificationError(
            f"ffprobe returned invalid {field}", code="audioexport.probe_invalid"
        )
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise VerificationError(
            f"ffprobe returned invalid {field}", code="audioexport.probe_invalid"
        ) from exc
    if not math.isfinite(number) or number < 0 or (not allow_zero and number == 0):
        raise VerificationError(
            f"ffprobe returned invalid {field}", code="audioexport.probe_invalid"
        )
    return number


def _probe_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise VerificationError(
            f"ffprobe returned invalid {field}", code="audioexport.probe_invalid"
        )
    try:
        number = int(value)
    except ValueError as exc:
        raise VerificationError(
            f"ffprobe returned invalid {field}", code="audioexport.probe_invalid"
        ) from exc
    if number <= 0 or str(number) != str(value).strip():
        raise VerificationError(
            f"ffprobe returned invalid {field}", code="audioexport.probe_invalid"
        )
    return number


def _validate_tags(value: Any, field: str) -> None:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(tag, str) for key, tag in value.items()
    ):
        raise VerificationError(
            f"ffprobe returned invalid {field}", code="audioexport.probe_invalid"
        )


def _validate_probe_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise VerificationError(
            "ffprobe returned invalid JSON structure", code="audioexport.probe_invalid"
        )
    streams = payload.get("streams")
    container = payload.get("format")
    chapters = payload.get("chapters", [])
    if (
        not isinstance(streams, list)
        or not isinstance(container, dict)
        or not isinstance(chapters, list)
    ):
        raise VerificationError(
            "ffprobe returned invalid JSON structure", code="audioexport.probe_invalid"
        )
    if "tags" in container:
        _validate_tags(container["tags"], "format tags")
    if container.get("duration") is not None:
        _probe_number(container["duration"], "format duration", allow_zero=False)
    has_audio = False
    for index, stream in enumerate(streams):
        if not isinstance(stream, dict):
            raise VerificationError(
                "ffprobe returned invalid stream", code="audioexport.probe_invalid"
            )
        if "tags" in stream:
            _validate_tags(stream["tags"], f"stream {index} tags")
        if stream.get("codec_type") == "audio":
            has_audio = True
            for field in ("channels", "sample_rate"):
                if field in stream:
                    _probe_positive_int(stream[field], f"audio {field}")
            if stream.get("duration") is not None:
                _probe_number(stream["duration"], "audio duration", allow_zero=False)
    for index, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            raise VerificationError(
                "ffprobe returned invalid chapter", code="audioexport.probe_invalid"
            )
        if "tags" in chapter:
            _validate_tags(chapter["tags"], f"chapter {index} tags")
        for field in ("start_time", "end_time"):
            if field in chapter:
                _probe_number(chapter[field], f"chapter {field}", allow_zero=True)
    if not has_audio:
        raise VerificationError("input has no audio stream", code="audioexport.no_audio")
    return payload


def probe(path: Path | str, ffprobe: str | Path | None = None) -> dict[str, Any]:
    exe = executable("ffprobe", ffprobe)
    text = run(
        [
            exe,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            "-of",
            "json",
            str(path),
        ],
        error_type=VerificationError,
    ).stdout
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise VerificationError(
            "ffprobe returned invalid JSON", code="audioexport.probe_invalid"
        ) from exc
    return _validate_probe_payload(payload)


def _parse_encoder_names(text: str) -> frozenset[str]:
    return frozenset(fields[1] for line in text.splitlines() if len(fields := line.split()) >= 2)


def available_encoders(ffmpeg_exe: str | Path) -> frozenset[str]:
    """Return encoder names reported by one FFmpeg capability query."""
    result = run([str(ffmpeg_exe), "-hide_banner", "-encoders"])
    return _parse_encoder_names(result.stdout + "\n" + result.stderr)


def encoder_for_format(fmt: str, ffmpeg: str | Path | None = None) -> str:
    """Return the preferred available encoder for a format."""
    normalized = normalize_format(fmt)
    encoder_names = available_encoders(executable("ffmpeg", ffmpeg))
    spec = FORMATS[normalized]
    selected = next(
        (
            candidate
            for candidate in (spec.codec, *spec.fallback_codecs)
            if candidate in encoder_names
        ),
        None,
    )
    if selected is None:
        candidates = ", ".join(repr(candidate) for candidate in (spec.codec, *spec.fallback_codecs))
        raise EncodingError(
            f"required FFmpeg encoders unavailable: {normalized}: encoders {candidates} are not available in this FFmpeg build",
            code="audioexport.encoder_unavailable",
        )
    return selected


def doctor(
    *,
    ffmpeg: str | Path | None = None,
    ffprobe: str | Path | None = None,
    requested_formats: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Report tool availability and encoder capability without exporting media."""
    requested = (
        {normalize_format(item) for item in requested_formats}
        if requested_formats is not None
        else None
    )
    tools: dict[str, Any] = {}
    for name, override in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        try:
            exe = executable(name, override)
            tools[name] = {"available": True, "path": exe, "version": version(exe)}
        except (ToolNotFoundError, EncodingError) as exc:
            tools[name] = {"available": False, "error": str(exc)}
    ff = tools["ffmpeg"]
    supported: dict[str, Any] = {}
    encoder_names: frozenset[str] = frozenset()
    encoder_error: str | None = None
    if ff["available"]:
        try:
            result = run([ff["path"], "-hide_banner", "-encoders"])
            encoder_names = _parse_encoder_names(result.stdout + "\n" + result.stderr)
        except EncodingError as exc:
            encoder_error = str(exc)
    else:
        encoder_error = "FFmpeg is unavailable"
    for name, spec in FORMATS.items():
        available: bool | None = None
        reason: str | None = None
        if encoder_error is None:
            selected = next(
                (
                    candidate
                    for candidate in (spec.codec, *spec.fallback_codecs)
                    if candidate in encoder_names
                ),
                None,
            )
            available = selected is not None
            if not available:
                candidates = ", ".join(
                    repr(candidate) for candidate in (spec.codec, *spec.fallback_codecs)
                )
                reason = f"encoders {candidates} are not available in this FFmpeg build"
        else:
            selected = None
            reason = encoder_error
        supported[name] = {
            "encoder": selected or spec.codec,
            "available": available,
        }
        if reason is not None:
            supported[name]["reason"] = reason
    all_formats_ready = all(item["available"] is True for item in supported.values())
    requested_formats_ready = (
        all(supported[name]["available"] is True for name in requested)
        if requested is not None
        else None
    )
    try:
        package_version = distribution_version("audioexport")
    except PackageNotFoundError:
        package_version = "0+uninstalled"
    ffprobe_available = tools["ffprobe"]["available"] is True
    ffmpeg_available = tools["ffmpeg"]["available"] is True
    aac_available = supported["m4b"]["available"] is True
    features: dict[str, dict[str, Any]] = {
        "m4b_encode": {
            "available": ffmpeg_available and ffprobe_available and aac_available,
            "requires": ["ffmpeg", "ffprobe", "aac"],
        },
        "m4b_audiobook": {
            "available": ffmpeg_available and ffprobe_available and aac_available,
            "requires": ["ffmpeg", "ffprobe", "aac"],
        },
        "m4b_stream_copy": {
            "available": ffmpeg_available and ffprobe_available,
            "requires": ["ffmpeg", "ffprobe"],
        },
    }
    for feature in features.values():
        if not feature["available"]:
            feature["reason"] = "one or more required tools or encoders are unavailable"
    return {
        "package": {"name": "audioexport", "version": package_version},
        "tools": tools,
        "formats": supported,
        "features": features,
        "ready": all(x["available"] for x in tools.values()),
        "all_formats_ready": all_formats_ready,
        "requested_formats_ready": requested_formats_ready,
    }
