"""Deterministic local source discovery for audiobook assembly."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .errors import InvalidExportError

SUPPORTED_AUDIO_EXTENSIONS = frozenset(
    {
        ".aac",
        ".aif",
        ".aiff",
        ".alac",
        ".amr",
        ".ape",
        ".caf",
        ".flac",
        ".m4a",
        ".m4b",
        ".mka",
        ".mp3",
        ".mpc",
        ".oga",
        ".ogg",
        ".opus",
        ".wav",
        ".wma",
        ".webm",
    }
)

_KNOWN_SIDECARS = frozenset(
    {"cover.jpg", "cover.jpeg", "cover.png", "description.txt", "chapters.txt", "ffmetadata.txt"}
)


def natural_sort_key(path: Path | str) -> tuple[tuple[int, int | str], ...]:
    """Return a case-stable natural key (so 2 sorts before 10)."""
    name = Path(path).name
    parts = re.split(r"(\d+)", name.casefold())
    key = tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts)
    return (*key, (2, name.casefold()), (3, name))


def discover_audio_inputs(inputs: Sequence[Path | str] | Path | str) -> tuple[Path, ...]:
    """Resolve ordered files and expand any directories non-recursively.

    Directory contents are naturally ordered and unsupported files (including
    conventional sidecars) are ignored. Explicit file arguments must use a
    supported audio extension.
    """
    if isinstance(inputs, (str, Path)):
        requested: tuple[Path | str, ...] = (inputs,)
    else:
        requested = tuple(inputs)
    if not requested:
        raise InvalidExportError(
            "at least one audiobook input is required", code="audioexport.audiobook_no_inputs"
        )

    resolved: list[Path] = []
    seen: set[str] = set()
    for raw in requested:
        if not isinstance(raw, (str, Path)):
            raise InvalidExportError(
                "audiobook inputs must be file paths", code="audioexport.audiobook_input_invalid"
            )
        candidate = Path(raw).expanduser()
        try:
            source = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise InvalidExportError(
                f"audiobook input does not exist: {candidate}",
                code="audioexport.audiobook_input_invalid",
            ) from exc

        if source.is_dir():
            try:
                children = sorted(
                    (
                        item
                        for item in source.iterdir()
                        if item.is_file()
                        and item.name.casefold() not in _KNOWN_SIDECARS
                        and item.suffix.casefold() in SUPPORTED_AUDIO_EXTENSIONS
                    ),
                    key=natural_sort_key,
                )
            except OSError as exc:
                raise InvalidExportError(
                    f"could not read audiobook directory: {source}",
                    code="audioexport.audiobook_input_invalid",
                ) from exc
            if not children:
                raise InvalidExportError(
                    f"audiobook directory contains no supported audio files: {source}",
                    code="audioexport.audiobook_input_invalid",
                )
            candidates = [item.resolve() for item in children]
        elif source.is_file():
            if source.suffix.casefold() not in SUPPORTED_AUDIO_EXTENSIONS:
                raise InvalidExportError(
                    f"unsupported audiobook input extension: {source.suffix or '(none)'}",
                    code="audioexport.audiobook_input_invalid",
                )
            candidates = [source]
        else:
            raise InvalidExportError(
                f"audiobook input is not a regular file or directory: {candidate}",
                code="audioexport.audiobook_input_invalid",
            )

        for item in candidates:
            key = os.path.normcase(str(item))
            if key in seen:
                raise InvalidExportError(
                    f"duplicate audiobook input: {item}",
                    code="audioexport.audiobook_duplicate_input",
                )
            seen.add(key)
            resolved.append(item)
    return tuple(resolved)


def source_title(probe_payload: Mapping[str, Any]) -> str | None:
    """Return the first source audio title tag, falling back to format tags."""
    streams = probe_payload.get("streams", ())
    if isinstance(streams, Sequence) and not isinstance(streams, (str, bytes)):
        for stream in streams:
            if isinstance(stream, Mapping) and stream.get("codec_type") == "audio":
                tags = stream.get("tags", {})
                if isinstance(tags, Mapping):
                    title = next(
                        (value for key, value in tags.items() if str(key).casefold() == "title"),
                        None,
                    )
                    if isinstance(title, str) and title.strip():
                        return title.strip()
                break
    container = probe_payload.get("format", {})
    tags = container.get("tags", {}) if isinstance(container, Mapping) else {}
    if isinstance(tags, Mapping):
        title = next((value for key, value in tags.items() if str(key).casefold() == "title"), None)
        if isinstance(title, str) and title.strip():
            return title.strip()
    return None
