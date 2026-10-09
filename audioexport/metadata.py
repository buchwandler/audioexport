"""Typed audiobook metadata and FFmpeg-compatible tag policy."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any

from .errors import InvalidExportError

_CANONICAL_TO_FFMPEG = {
    "title": "title",
    "author": "artist",
    "album": "album",
    "album_artist": "album_artist",
    "writer": "composer",
    "genre": "genre",
    "description": "description",
    "long_description": "synopsis",
    "comment": "comment",
    "copyright": "copyright",
    "encoded_by": "encoded_by",
    "language": "language",
    "publisher": "publisher",
    "grouping": "grouping",
}


@dataclass(frozen=True, slots=True)
class AudiobookMetadata:
    title: str | None = None
    author: str | None = None
    album: str | None = None
    album_artist: str | None = None
    writer: str | None = None
    genre: str | None = None
    description: str | None = None
    long_description: str | None = None
    comment: str | None = None
    copyright: str | None = None
    encoded_by: str | None = None
    language: str | None = None
    publisher: str | None = None
    grouping: str | None = None
    extra: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name == "extra":
                if not isinstance(value, Mapping):
                    raise InvalidExportError(
                        "extra audiobook metadata must be a mapping",
                        code="audioexport.audiobook_metadata_invalid",
                    )
            elif value is not None and not isinstance(value, str):
                raise InvalidExportError(
                    f"audiobook metadata {item.name} must be a string",
                    code="audioexport.audiobook_metadata_invalid",
                )


def _put_tag(tags: dict[str, str], key: Any, value: Any) -> None:
    if (
        not isinstance(key, str)
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key)
        or not isinstance(value, str)
    ):
        raise InvalidExportError(
            "audiobook metadata must contain string values and simple keys",
            code="audioexport.audiobook_metadata_invalid",
        )
    tags[key.lower()] = value


def audiobook_tags(metadata: AudiobookMetadata | Mapping[str, str] | None) -> dict[str, str]:
    """Map canonical metadata to FFmpeg tags and inject the audiobook media kind.

    Mapping inputs accept canonical field names as well as additional FFmpeg
    tag names. ``extra`` on AudiobookMetadata is applied last, allowing an
    explicit expert override such as ``media_type``.
    """
    tags = {"media_type": "2"}
    if metadata is None:
        return tags
    if isinstance(metadata, AudiobookMetadata):
        values = {
            item.name: getattr(metadata, item.name)
            for item in fields(metadata)
            if item.name != "extra" and getattr(metadata, item.name) is not None
        }
        extras = metadata.extra
    elif isinstance(metadata, Mapping):
        values = dict(metadata)
        extras = {}
    else:
        raise InvalidExportError(
            "metadata must be AudiobookMetadata or a string mapping",
            code="audioexport.audiobook_metadata_invalid",
        )

    for key, value in values.items():
        normalized_key = key.lower() if isinstance(key, str) else key
        _put_tag(tags, _CANONICAL_TO_FFMPEG.get(normalized_key, normalized_key), value)
    for key, value in extras.items():
        _put_tag(tags, key, value)
    return dict(sorted(tags.items()))
