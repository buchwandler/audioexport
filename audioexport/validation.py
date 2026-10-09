"""Shared validation helpers for encoding and profile preflight."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import InvalidExportError


def duration_ms(info: Mapping[str, Any]) -> int:
    """Extract a positive audio duration from an FFprobe payload."""
    try:
        streams = info["streams"]
        stream = next(row for row in streams if row.get("codec_type") == "audio")
        raw = stream.get("duration") or info["format"].get("duration")
        seconds = float(raw)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("non-positive duration")
        return round(seconds * 1000)
    except (ValueError, TypeError, KeyError, StopIteration) as exc:
        raise InvalidExportError(
            "source has no valid positive duration", code="audioexport.duration_invalid"
        ) from exc


def validate_metadata(metadata: Mapping[str, str] | None) -> dict[str, str]:
    if metadata is not None and not isinstance(metadata, Mapping):
        raise InvalidExportError(
            "metadata must be string key/value pairs with simple keys",
            code="audioexport.metadata_invalid",
        )
    result: dict[str, str] = {}
    for key, value in (metadata or {}).items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key)
            or not isinstance(value, str)
        ):
            raise InvalidExportError(
                "metadata must be string key/value pairs with simple keys",
                code="audioexport.metadata_invalid",
            )
        result[key.lower()] = value
    return dict(sorted(result.items()))


def cover_kind(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            signature = stream.read(8)
    except OSError as exc:
        raise InvalidExportError(
            f"cover not readable: {path}", code="audioexport.cover_invalid"
        ) from exc
    if signature.startswith(b"\xff\xd8\xff"):
        return "mjpeg"
    if signature == b"\x89PNG\r\n\x1a\n":
        return "png"
    raise InvalidExportError("cover must be JPEG or PNG", code="audioexport.cover_invalid")
