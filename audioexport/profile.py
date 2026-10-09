"""Portable TOML profiles for batch exporting to multiple formats."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from .errors import InvalidExportError
from .formats import FORMATS, normalize_bitrate, normalize_format

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib


@dataclass(frozen=True, slots=True)
class OutputSpec:
    format: str
    filename: str | None = None
    bitrate: str | None = None
    use_cover: bool | None = None
    use_chapters: bool | None = None


@dataclass(frozen=True, slots=True)
class ExportProfile:
    outputs: tuple[OutputSpec, ...]
    metadata: Mapping[str, str]
    cover: Path | None = None
    timeline: Path | None = None


@dataclass(frozen=True, slots=True)
class ResolvedOutput:
    format: str
    filename: str
    bitrate: str | None
    cover: Path | None
    timeline: Path | None


COVER_FORMATS = frozenset({"mp3", "m4a", "m4b"})
CHAPTER_FORMATS = frozenset({"m4a", "m4b"})


def _validate_filename(filename: object, fmt: str) -> str:
    if (
        not isinstance(filename, str)
        or not filename
        or filename in {".", ".."}
        or "\x00" in filename
        or Path(filename).name != filename
        or PureWindowsPath(filename).name != filename
        or Path(filename).suffix.lower() != FORMATS[fmt].extension
    ):
        raise InvalidExportError(
            "output filename must be a simple name with matching extension",
            code="audioexport.profile_invalid",
        )
    return filename


def resolve_output(profile: ExportProfile, spec: OutputSpec, source_stem: str) -> ResolvedOutput:
    """Resolve one output's filename, bitrate, and selected profile resources.

    This is a pure operation over an already-loaded profile. It does not check
    resource contents or touch the filesystem.
    """
    if not isinstance(spec, OutputSpec):
        raise InvalidExportError("output spec is invalid", code="audioexport.profile_invalid")
    if not isinstance(spec.format, str):
        raise InvalidExportError(
            "output format must be a string", code="audioexport.profile_invalid"
        )
    fmt = normalize_format(spec.format)
    if (
        not isinstance(source_stem, str)
        or not source_stem
        or source_stem in {".", ".."}
        or "\x00" in source_stem
        or Path(source_stem).name != source_stem
        or PureWindowsPath(source_stem).name != source_stem
    ):
        raise InvalidExportError(
            "source stem must be a simple non-empty name", code="audioexport.profile_invalid"
        )
    if spec.filename is None:
        filename = f"{source_stem}{FORMATS[fmt].extension}"
    else:
        filename = _validate_filename(spec.filename, fmt)
    if spec.bitrate is not None and (
        isinstance(spec.bitrate, bool) or not isinstance(spec.bitrate, (str, int))
    ):
        raise InvalidExportError(
            "bitrate must be an integer or string", code="audioexport.profile_invalid"
        )
    bitrate = normalize_bitrate(spec.bitrate, fmt)
    for field, value in (("use_cover", spec.use_cover), ("use_chapters", spec.use_chapters)):
        if value is not None and not isinstance(value, bool):
            raise InvalidExportError(
                f"{field} must be a boolean", code="audioexport.profile_invalid"
            )

    if spec.use_cover is True and fmt not in COVER_FORMATS:
        raise InvalidExportError(
            f"cover art is unsupported for {fmt}", code="audioexport.cover_unsupported"
        )
    if spec.use_cover is True and profile.cover is None:
        raise InvalidExportError(
            "use_cover=true requires a profile cover", code="audioexport.cover_required"
        )
    if spec.use_chapters is True and fmt not in CHAPTER_FORMATS:
        raise InvalidExportError(
            f"chapters are unsupported for {fmt}", code="audioexport.chapters_unsupported"
        )
    if spec.use_chapters is True and profile.timeline is None:
        raise InvalidExportError(
            "use_chapters=true requires a profile timeline", code="audioexport.chapters_required"
        )

    use_cover = spec.use_cover is True or (
        spec.use_cover is None and profile.cover is not None and fmt in COVER_FORMATS
    )
    use_chapters = spec.use_chapters is True or (
        spec.use_chapters is None and profile.timeline is not None and fmt in CHAPTER_FORMATS
    )
    cover = profile.cover if use_cover else None
    timeline = profile.timeline if use_chapters else None
    if cover is not None and not isinstance(cover, Path):
        raise InvalidExportError(
            "profile cover must be a resolved path", code="audioexport.profile_invalid"
        )
    if timeline is not None and not isinstance(timeline, Path):
        raise InvalidExportError(
            "profile timeline must be a resolved path", code="audioexport.profile_invalid"
        )
    return ResolvedOutput(fmt, filename, bitrate, cover, timeline)


def _relative_resource(value: Any, root: Path, field: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise InvalidExportError(
            f"{field} must be a non-empty file path", code="audioexport.profile_invalid"
        )
    return (root / value).resolve()


def load_profile(path: str | Path) -> ExportProfile:
    path = Path(path).expanduser().resolve()
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise InvalidExportError(
            f"cannot load export profile: {exc}", code="audioexport.profile_invalid"
        ) from exc
    if raw.get("schema") != "audioexport.profile.v1":
        raise InvalidExportError(
            "expected schema='audioexport.profile.v1'", code="audioexport.profile_invalid"
        )
    allowed = {"schema", "metadata", "cover", "timeline", "outputs"}
    extra = set(raw) - allowed
    if extra:
        raise InvalidExportError(
            f"unknown profile fields: {sorted(extra)}", code="audioexport.profile_invalid"
        )
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in metadata.items()
    ):
        raise InvalidExportError(
            "[metadata] must contain string values", code="audioexport.profile_invalid"
        )
    outputs = raw.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise InvalidExportError("profile requires [[outputs]]", code="audioexport.profile_invalid")
    specifications = []
    seen_names: set[str] = set()
    for item in outputs:
        if not isinstance(item, dict) or set(item) - {
            "format",
            "filename",
            "bitrate",
            "use_cover",
            "use_chapters",
        }:
            raise InvalidExportError(
                "unknown [[outputs]] fields", code="audioexport.profile_invalid"
            )
        fmt = item.get("format")
        if not isinstance(fmt, str):
            raise InvalidExportError(
                "[[outputs]].format is required", code="audioexport.profile_invalid"
            )
        fmt = normalize_format(fmt)
        name = item.get("filename")
        if name is not None and (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or PureWindowsPath(name).name != name
            or Path(name).suffix.lower() != FORMATS[fmt].extension
        ):
            raise InvalidExportError(
                "output filename must be a simple name with matching extension",
                code="audioexport.profile_invalid",
            )
        if name is not None:
            normalized_name = name.casefold()
            if normalized_name in seen_names:
                raise InvalidExportError(
                    "duplicate profile output filenames", code="audioexport.profile_invalid"
                )
            seen_names.add(normalized_name)
        bitrate = item.get("bitrate")
        if bitrate is not None and not isinstance(bitrate, (str, int)):
            raise InvalidExportError(
                "bitrate must be an integer or string", code="audioexport.profile_invalid"
            )
        use_cover = item.get("use_cover")
        use_chapters = item.get("use_chapters")
        if use_cover is not None and not isinstance(use_cover, bool):
            raise InvalidExportError(
                "use_cover must be a boolean", code="audioexport.profile_invalid"
            )
        if use_chapters is not None and not isinstance(use_chapters, bool):
            raise InvalidExportError(
                "use_chapters must be a boolean", code="audioexport.profile_invalid"
            )
        # Validate eagerly. Keep None so default is chosen by encode().
        if bitrate is not None:
            normalize_bitrate(bitrate, fmt)
        specifications.append(
            OutputSpec(
                fmt,
                item.get("filename"),
                str(bitrate) if bitrate is not None else None,
                use_cover,
                use_chapters,
            )
        )
    cover = _relative_resource(raw.get("cover"), path.parent, "cover")
    timeline = _relative_resource(raw.get("timeline"), path.parent, "timeline")
    # Resource existence and content are checked only when a resolved output selects them.
    return ExportProfile(tuple(specifications), metadata, cover, timeline)
