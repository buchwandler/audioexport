"""Read-only validation of every output in an already-loaded profile."""

from __future__ import annotations

from pathlib import Path

from .chapters import load_chapters, normalize_chapters
from .errors import EncodingError, InvalidExportError
from .fftools import available_encoders, executable, probe
from .formats import FORMATS
from .profile import ExportProfile, ResolvedOutput, resolve_output
from .validation import cover_kind, duration_ms, validate_metadata


def preflight_profile(
    profile: ExportProfile,
    source: Path | str,
    *,
    ffmpeg: Path | str | None = None,
    ffprobe: Path | str | None = None,
) -> tuple[ResolvedOutput, ...]:
    """Resolve and validate a complete profile without creating output artifacts."""
    if not profile.outputs:
        raise InvalidExportError(
            "profile requires at least one output", code="audioexport.profile_invalid"
        )

    source_path = Path(source).expanduser().absolute()
    if not source_path.is_file():
        raise InvalidExportError(
            f"source audio missing: {source_path}", code="audioexport.source_missing"
        )

    resolved_outputs: list[ResolvedOutput] = []
    seen_filenames: set[str] = set()
    for spec in profile.outputs:
        resolved = resolve_output(profile, spec, source_path.stem)
        key = resolved.filename.casefold()
        if key in seen_filenames:
            raise InvalidExportError(
                "duplicate profile output filenames", code="audioexport.profile_invalid"
            )
        seen_filenames.add(key)
        resolved_outputs.append(resolved)
    validate_metadata(profile.metadata)

    ffmpeg_exe = executable("ffmpeg", ffmpeg)
    ffprobe_exe = executable("ffprobe", ffprobe)
    source_info = probe(source_path, ffprobe_exe)
    source_duration_ms = duration_ms(source_info)

    encoder_names = available_encoders(ffmpeg_exe)
    requested: dict[tuple[str, ...], list[str]] = {}
    for resolved in resolved_outputs:
        candidates = (FORMATS[resolved.format].codec, *FORMATS[resolved.format].fallback_codecs)
        requested.setdefault(candidates, []).append(resolved.format)
    for candidates, formats in requested.items():
        if not any(candidate in encoder_names for candidate in candidates):
            names = ", ".join(repr(candidate) for candidate in candidates)
            raise EncodingError(
                "required FFmpeg encoder unavailable for "
                f"{', '.join(dict.fromkeys(formats))}: {names}",
                code="audioexport.encoder_missing",
            )

    checked_covers: set[Path] = set()
    checked_timelines: set[Path] = set()
    for resolved in resolved_outputs:
        if resolved.cover is not None and resolved.cover not in checked_covers:
            cover_kind(resolved.cover)
            checked_covers.add(resolved.cover)
        if resolved.timeline is not None and resolved.timeline not in checked_timelines:
            chapters = load_chapters(resolved.timeline)
            normalize_chapters(chapters, source_duration_ms)
            checked_timelines.add(resolved.timeline)

    return tuple(resolved_outputs)
