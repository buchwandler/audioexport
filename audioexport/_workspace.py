"""Shared security checks for native workspace adapters."""

from __future__ import annotations

import hashlib
import math
import stat
import unicodedata
import wave
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import Any

from .errors import InvalidExportError, VerificationError

DURATION_TOLERANCE_SECONDS = 0.01


def safe_variant(variant: str, *, error_code: str) -> str:
    """Validate a variant as one portable, safe directory component."""
    if (
        not isinstance(variant, str)
        or not variant
        or variant in {".", ".."}
        or "/" in variant
        or "\\" in variant
        or ":" in variant
        or PureWindowsPath(variant).is_absolute()
        or any(unicodedata.category(character) == "Cc" for character in variant)
    ):
        raise InvalidExportError(
            "variant must be a safe single directory component", code=error_code
        )
    return variant


def resolve_stage_root(
    workspace: Path,
    root: str | Path | None,
    *,
    stage_name: str,
    missing_code: str,
    invalid_code: str,
) -> Path:
    """Resolve an existing stage root, interpreting relative overrides under workspace."""
    selected = Path(root).expanduser() if root is not None else workspace / stage_name
    if not selected.is_absolute():
        selected = workspace / selected
    try:
        resolved = selected.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InvalidExportError(
            f"{stage_name} directory does not exist: {selected}", code=missing_code
        ) from exc
    if selected.is_symlink() or not resolved.is_dir():
        raise InvalidExportError(
            f"{stage_name} root must be a non-symlink directory: {selected}",
            code=invalid_code,
        )
    return resolved


def path_is_within(root: Path, path: Path) -> bool:
    """Return whether an absolute path is contained by an absolute directory."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def safe_report_file(
    variant_dir: Path,
    reported: Any,
    *,
    error_code: str,
) -> Path:
    """Resolve a regular report-declared file without traversal or symlink hops."""
    if not isinstance(reported, str) or not reported or "\x00" in reported or "\\" in reported:
        raise VerificationError(
            "compose report output must be a relative POSIX path", code=error_code
        )
    relative = Path(reported)
    windows = PureWindowsPath(reported)
    if relative.is_absolute() or windows.is_absolute() or windows.drive:
        raise VerificationError(
            "compose report output must be relative to its variant directory",
            code=error_code,
        )
    if any(part in {"", ".", ".."} for part in reported.split("/")):
        raise VerificationError(
            "compose report output must not contain traversal components",
            code=error_code,
        )
    candidate = variant_dir / relative
    current = variant_dir
    try:
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise VerificationError(
                    "compose report output must not traverse symlinks", code=error_code
                )
        resolved = candidate.resolve(strict=True)
    except VerificationError:
        raise
    except (OSError, RuntimeError) as exc:
        raise VerificationError(
            f"compose report output does not resolve to a regular file: {reported}",
            code=error_code,
        ) from exc
    if not path_is_within(variant_dir, resolved):
        raise VerificationError(
            "compose report output escapes its variant directory", code=error_code
        )
    try:
        mode = resolved.lstat().st_mode
    except OSError as exc:
        raise VerificationError(
            f"compose report output is unavailable: {reported}", code=error_code
        ) from exc
    if not stat.S_ISREG(mode):
        raise VerificationError(
            f"compose report output must be a regular non-symlink file: {reported}",
            code=error_code,
        )
    return resolved


def sha256_file(path: Path, *, error_code: str) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise VerificationError(f"cannot hash composed WAV {path}", code=error_code) from exc
    return digest.hexdigest()


def _positive_int(value: Any, field: str, *, error_code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidExportError(f"{field} must be a positive integer", code=error_code)
    return value


def verify_reported_wav(
    path: Path,
    report: Mapping[str, Any],
    *,
    report_code: str,
    geometry_code: str,
    item_label: str = "compose item",
) -> tuple[int, int, int, float]:
    """Verify a WAV header against report geometry using the established tolerance."""
    expected_rate = _positive_int(
        report.get("sample_rate"), f"{item_label} sample_rate", error_code=report_code
    )
    expected_channels = _positive_int(
        report.get("channels"), f"{item_label} channels", error_code=report_code
    )
    expected_frames = _positive_int(
        report.get("frames"), f"{item_label} frames", error_code=report_code
    )
    duration = report.get("duration_seconds")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(float(duration))
        or duration <= 0
    ):
        raise InvalidExportError(
            f"{item_label} duration_seconds must be a positive finite number",
            code=report_code,
        )
    complete_audio = True
    try:
        with wave.open(str(path), "rb") as source:
            actual_rate = source.getframerate()
            actual_channels = source.getnchannels()
            actual_frames = source.getnframes()
            sample_width = source.getsampwidth()
            frame_size = actual_channels * sample_width
            if frame_size <= 0:
                complete_audio = False
            else:
                remaining_frames = actual_frames
                while remaining_frames > 0:
                    chunk = source.readframes(min(remaining_frames, 65536))
                    if not chunk or len(chunk) % frame_size:
                        complete_audio = False
                        break
                    frames_read = len(chunk) // frame_size
                    if frames_read <= 0:
                        complete_audio = False
                        break
                    remaining_frames -= frames_read
                if remaining_frames:
                    complete_audio = False
    except (OSError, EOFError, wave.Error) as exc:
        raise VerificationError(
            f"composed artifact is not a readable WAV file: {path}", code=geometry_code
        ) from exc
    actual_duration = actual_frames / actual_rate if actual_rate > 0 else 0.0
    if (
        not complete_audio
        or actual_rate != expected_rate
        or actual_channels != expected_channels
        or actual_frames != expected_frames
        or abs(actual_duration - float(duration)) > DURATION_TOLERANCE_SECONDS
    ):
        raise VerificationError(
            f"composed WAV geometry does not match the report: {path}", code=geometry_code
        )
    return actual_rate, actual_channels, actual_frames, actual_duration
