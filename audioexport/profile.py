"""Portable TOML profiles for batch exporting to multiple formats."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from .errors import InvalidExportError
from .formats import FORMATS, normalize_bitrate, normalize_format

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef, import-not-found]


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


_COVER_FORMATS = frozenset({"mp3", "m4a", "m4b"})
_CHAPTER_FORMATS = frozenset({"m4a", "m4b"})


def resolve_output(profile: ExportProfile, spec: OutputSpec, source_stem: str) -> ResolvedOutput:
    """Resolve profile-wide resources using per-format defaults and explicit selectors."""
    if spec.use_cover is True and profile.cover is None:
        raise InvalidExportError(
            "use_cover=true requires a profile cover", code="audioexport.profile_invalid"
        )
    if spec.use_chapters is True and profile.timeline is None:
        raise InvalidExportError(
            "use_chapters=true requires a profile timeline", code="audioexport.profile_invalid"
        )
    if spec.use_cover is True and spec.format not in _COVER_FORMATS:
        raise InvalidExportError(
            f"cover art is unsupported for {spec.format}", code="audioexport.profile_invalid"
        )
    if spec.use_chapters is True and spec.format not in _CHAPTER_FORMATS:
        raise InvalidExportError(
            f"chapters are unsupported for {spec.format}", code="audioexport.profile_invalid"
        )
    cover = (
        profile.cover
        if spec.use_cover is True
        or (spec.use_cover is None and profile.cover is not None and spec.format in _COVER_FORMATS)
        else None
    )
    timeline = (
        profile.timeline
        if spec.use_chapters is True
        or (
            spec.use_chapters is None
            and profile.timeline is not None
            and spec.format in _CHAPTER_FORMATS
        )
        else None
    )
    return ResolvedOutput(
        format=spec.format,
        filename=spec.filename or f"{source_stem}{FORMATS[spec.format].extension}",
        bitrate=spec.bitrate,
        cover=cover,
        timeline=timeline,
    )


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
        if use_cover is True and raw.get("cover") is None:
            raise InvalidExportError(
                "use_cover=true requires a profile cover", code="audioexport.profile_invalid"
            )
        if use_chapters is True and raw.get("timeline") is None:
            raise InvalidExportError(
                "use_chapters=true requires a profile timeline", code="audioexport.profile_invalid"
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
    for field, resource in (("cover", cover), ("timeline", timeline)):
        if resource is not None and not resource.is_file():
            raise InvalidExportError(
                f"{field} must be an existing file", code="audioexport.profile_invalid"
            )
    return ExportProfile(tuple(specifications), metadata, cover, timeline)
