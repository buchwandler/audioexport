"""Strict, portable TOML configuration for audiobook assembly."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from pathlib import Path, PureWindowsPath
from typing import Any

from .errors import InvalidExportError
from .formats import normalize_bitrate
from .inputs import SUPPORTED_AUDIO_EXTENSIONS
from .metadata import AudiobookMetadata, audiobook_tags

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib


@dataclass(frozen=True, slots=True)
class AudiobookAudioOptions:
    bitrate: str | None = None
    sample_rate: int | None = None
    channels: int | None = None


@dataclass(frozen=True, slots=True)
class AudiobookProfile:
    path: Path
    output: Path
    inputs: tuple[Path, ...]
    cover: Path | None
    chapters_file: Path | None
    audio: AudiobookAudioOptions
    metadata: AudiobookMetadata
    chapter_mode: str = "tracks"
    title_source: str = "tag"


def _profile_error(message: str, exc: BaseException | None = None) -> InvalidExportError:
    error = InvalidExportError(message, code="audioexport.audiobook_profile_invalid")
    if exc is not None:
        error.__cause__ = exc
    return error


def _path_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _profile_error(f"{field} must be a non-empty path")
    if "\x00" in value or any(character in value for character in "*?[]{}"):
        raise _profile_error(f"{field} must be an explicit path, not a glob or template")
    path_parts = (*Path(value).parts, *PureWindowsPath(value).parts)
    if ".." in path_parts:
        raise _profile_error(f"{field} must not traverse parent directories")
    return value


def _resolve_path(value: Any, root: Path, field: str, *, must_exist: bool) -> Path:
    text = _path_text(value, field)
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise _profile_error(f"{field} does not resolve to a usable path: {text}", exc) from exc


def _resolve_output(value: Any, root: Path) -> Path:
    text = _path_text(value, "output")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.absolute()


def _resource_file(value: Any, root: Path, field: str) -> Path:
    path = _resolve_path(value, root, field, must_exist=True)
    if not path.is_file():
        raise _profile_error(f"{field} must be an existing file: {path}")
    return path


def _validate_audio(raw: Any) -> AudiobookAudioOptions:
    if not isinstance(raw, dict) or set(raw) - {"bitrate", "sample_rate", "channels"}:
        raise _profile_error("[audio] contains unknown fields or is not a table")
    bitrate = raw.get("bitrate")
    if bitrate is not None:
        if isinstance(bitrate, bool) or not isinstance(bitrate, (int, str)):
            raise _profile_error("audio.bitrate must be an integer or string")
        try:
            bitrate = normalize_bitrate(bitrate, "m4b")
        except InvalidExportError as exc:
            raise _profile_error(f"invalid audio.bitrate: {exc}", exc) from exc
    sample_rate = raw.get("sample_rate")
    if sample_rate is not None and (
        isinstance(sample_rate, bool)
        or not isinstance(sample_rate, int)
        or not 8000 <= sample_rate <= 384000
    ):
        raise _profile_error("audio.sample_rate must be an integer from 8000 to 384000")
    channels = raw.get("channels")
    if channels is not None and (
        isinstance(channels, bool) or not isinstance(channels, int) or channels not in (1, 2)
    ):
        raise _profile_error("audio.channels must be 1 or 2")
    return AudiobookAudioOptions(bitrate, sample_rate, channels)


def _validate_metadata(raw: Any) -> AudiobookMetadata:
    if not isinstance(raw, dict):
        raise _profile_error("[metadata] must be a table")
    canonical = {item.name for item in fields(AudiobookMetadata)} - {"extra"}
    if set(raw) - canonical - {"extra"}:
        raise _profile_error(f"unknown metadata fields: {sorted(set(raw) - canonical - {'extra'})}")
    values: dict[str, str | None] = {}
    for key in canonical:
        value = raw.get(key)
        if value is not None and not isinstance(value, str):
            raise _profile_error(f"metadata.{key} must be a string")
        values[key] = value
    extra = raw.get("extra", {})
    if not isinstance(extra, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in extra.items()
    ):
        raise _profile_error("metadata.extra must contain string key/value pairs")
    try:
        result = AudiobookMetadata(**values, extra=extra)
        audiobook_tags(result)
    except (TypeError, InvalidExportError) as exc:
        raise _profile_error(f"invalid audiobook metadata: {exc}", exc) from exc
    return result


def load_audiobook_profile(path: Path | str) -> AudiobookProfile:
    """Load and fully validate an audioexport.audiobook.v1 TOML profile."""
    profile_path = Path(path).expanduser().resolve()
    try:
        with profile_path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise _profile_error(f"cannot load audiobook profile: {exc}", exc) from exc
    if not isinstance(raw, dict) or raw.get("schema") != "audioexport.audiobook.v1":
        raise _profile_error("expected schema='audioexport.audiobook.v1'")
    allowed = {
        "schema",
        "output",
        "inputs",
        "cover",
        "chapters_file",
        "audio",
        "metadata",
        "chapters",
    }
    extra_root = set(raw) - allowed
    if extra_root:
        raise _profile_error(f"unknown audiobook profile fields: {sorted(extra_root)}")

    root = profile_path.parent
    output = _resolve_output(raw.get("output"), root)
    if output.suffix.casefold() != ".m4b":
        raise _profile_error("output must use the .m4b extension")

    input_values = raw.get("inputs")
    if not isinstance(input_values, list) or not input_values:
        raise _profile_error("inputs must be a non-empty ordered array of files")
    inputs: list[Path] = []
    for index, value in enumerate(input_values, 1):
        source = _resource_file(value, root, f"inputs[{index}]")
        if source.suffix.casefold() not in SUPPORTED_AUDIO_EXTENSIONS:
            raise _profile_error(f"inputs[{index}] has an unsupported audio extension")
        inputs.append(source)
    identities = [os.path.normcase(str(item)) for item in inputs]
    if len(identities) != len(set(identities)):
        raise _profile_error("inputs contains duplicate resolved paths")

    cover = _resource_file(raw["cover"], root, "cover") if "cover" in raw else None
    chapters_file = (
        _resource_file(raw["chapters_file"], root, "chapters_file")
        if "chapters_file" in raw
        else None
    )
    audio = _validate_audio(raw.get("audio", {}))
    metadata = _validate_metadata(raw.get("metadata", {}))

    chapter_table = raw.get("chapters", {})
    if not isinstance(chapter_table, dict) or set(chapter_table) - {"mode", "title_source"}:
        raise _profile_error("[chapters] contains unknown fields or is not a table")
    chapter_mode = chapter_table.get("mode", "tracks")
    title_source = chapter_table.get("title_source", "tag")
    if not isinstance(chapter_mode, str) or chapter_mode not in {"tracks", "explicit", "none"}:
        raise _profile_error("chapters.mode must be tracks, explicit, or none")
    if not isinstance(title_source, str) or title_source not in {"tag", "filename"}:
        raise _profile_error("chapters.title_source must be tag or filename")
    if chapter_mode == "explicit" and chapters_file is None:
        raise _profile_error("chapters.mode='explicit' requires chapters_file")
    if chapter_mode != "explicit" and chapters_file is not None:
        raise _profile_error("chapters_file requires chapters.mode='explicit'")
    return AudiobookProfile(
        profile_path,
        output,
        tuple(inputs),
        cover,
        chapters_file,
        audio,
        metadata,
        chapter_mode,
        title_source,
    )


def resolve_profile_overrides(
    profile: AudiobookProfile,
    *,
    output: Path | str | None = None,
    metadata: Mapping[str, str] | None = None,
    cover: Path | str | None = None,
    chapters_file: Path | str | None = None,
    bitrate: str | int | None = None,
    sample_rate: int | None = None,
    channels: int | None = None,
    use_filenames_as_chapters: bool = False,
) -> AudiobookProfile:
    """Apply CLI-supplied values over profile values without changing input order."""
    canonical = {item.name for item in fields(AudiobookMetadata)} - {"extra"}
    values = {name: getattr(profile.metadata, name) for name in canonical}
    extras = dict(profile.metadata.extra)
    for key, value in (metadata or {}).items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise InvalidExportError(
                "CLI audiobook metadata must contain string key/value pairs",
                code="audioexport.audiobook_metadata_invalid",
            )
        if key in canonical:
            values[key] = value
        else:
            extras[key] = value
    merged_metadata = AudiobookMetadata(**values, extra=extras)
    audiobook_tags(merged_metadata)

    merged_audio = replace(
        profile.audio,
        bitrate=normalize_bitrate(bitrate, "m4b") if bitrate is not None else profile.audio.bitrate,
        sample_rate=sample_rate if sample_rate is not None else profile.audio.sample_rate,
        channels=channels if channels is not None else profile.audio.channels,
    )
    # Reuse the profile validators for override-only values.
    _validate_audio(
        {
            "bitrate": merged_audio.bitrate,
            "sample_rate": merged_audio.sample_rate,
            "channels": merged_audio.channels,
        }
    )
    return replace(
        profile,
        output=Path(output).expanduser().absolute() if output is not None else profile.output,
        cover=Path(cover).expanduser().resolve() if cover is not None else profile.cover,
        chapters_file=(
            Path(chapters_file).expanduser().resolve()
            if chapters_file is not None
            else profile.chapters_file
        ),
        audio=merged_audio,
        metadata=merged_metadata,
        chapter_mode="explicit" if chapters_file is not None else profile.chapter_mode,
        title_source="filename" if use_filenames_as_chapters else profile.title_source,
    )
