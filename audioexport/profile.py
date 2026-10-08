"""Portable TOML profiles for batch exporting to multiple formats."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import InvalidExportError
from .formats import FORMATS, normalize_bitrate, normalize_format

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True, slots=True)
class OutputSpec:
    format: str
    filename: str | None = None
    bitrate: str | None = None


@dataclass(frozen=True, slots=True)
class ExportProfile:
    outputs: tuple[OutputSpec, ...]
    metadata: Mapping[str, str]
    cover: Path | None = None
    timeline: Path | None = None


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
        if not isinstance(item, dict) or set(item) - {"format", "filename", "bitrate"}:
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
            or Path(name).suffix.lower() != FORMATS[fmt].extension
        ):
            raise InvalidExportError(
                "output filename must be a simple name with matching extension",
                code="audioexport.profile_invalid",
            )
        name = name or f"{{stem}}{FORMATS[fmt].extension}"
        if name in seen_names:
            raise InvalidExportError(
                "duplicate profile output filenames", code="audioexport.profile_invalid"
            )
        seen_names.add(name)
        bitrate = item.get("bitrate")
        if bitrate is not None and not isinstance(bitrate, (str, int)):
            raise InvalidExportError(
                "bitrate must be an integer or string", code="audioexport.profile_invalid"
            )
        # Validate eagerly. Keep None so default is chosen by encode().
        if bitrate is not None:
            normalize_bitrate(bitrate, fmt)
        specifications.append(
            OutputSpec(fmt, item.get("filename"), str(bitrate) if bitrate is not None else None)
        )
    return ExportProfile(
        tuple(specifications),
        metadata,
        _relative_resource(raw.get("cover"), path.parent, "cover"),
        _relative_resource(raw.get("timeline"), path.parent, "timeline"),
    )
