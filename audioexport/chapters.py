"""Portable chapter input plus optional Readio composition-timeline adapter."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import InvalidExportError


@dataclass(frozen=True, slots=True)
class Chapter:
    title: str
    start_ms: int
    end_ms: int | None = None
    start_sample: int | None = None
    end_sample: int | None = None
    sample_rate: int | None = None


def escape_ffmetadata_value(value: str) -> str:
    """Ported from Readio: escape FFmetadata delimiters and newlines."""
    value = value.replace("\\", "\\\\").replace("=", "\\=")
    value = value.replace(";", "\\;").replace("#", "\\#")
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def _milliseconds(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise InvalidExportError(f"invalid chapter {field}", code="audioexport.chapters_invalid")
    # generic chapter schema uses integer millisecond offsets
    if int(value) != value:
        raise InvalidExportError(
            f"chapter {field} must be whole milliseconds", code="audioexport.chapters_invalid"
        )
    return int(value)


def load_chapters(timeline: Path | str) -> tuple[Chapter, ...]:
    """Read JSON {chapters: [{title,start_ms,end_ms?}]} or Readio start_sample timeline."""
    path = Path(timeline)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvalidExportError(
            f"invalid timeline {path}: {exc}", code="audioexport.timeline_invalid"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("chapters"), list):
        raise InvalidExportError(
            "timeline must have chapters array", code="audioexport.timeline_invalid"
        )
    chapters = []
    sample_rate = data.get("sample_rate")
    for index, row in enumerate(data["chapters"], 1):
        if not isinstance(row, dict):
            raise InvalidExportError("invalid chapter row", code="audioexport.chapters_invalid")
        title = row.get("title") or f"Chapter {index}"
        if not isinstance(title, str) or not title.strip():
            raise InvalidExportError("invalid chapter title", code="audioexport.chapters_invalid")
        if "start_sample" in row:
            if (
                isinstance(sample_rate, bool)
                or not isinstance(sample_rate, int)
                or sample_rate <= 0
            ):
                raise InvalidExportError(
                    "sample chapter requires sample_rate", code="audioexport.chapters_invalid"
                )
            sample = row["start_sample"]
            if isinstance(sample, bool) or not isinstance(sample, int):
                raise InvalidExportError(
                    "invalid start_sample", code="audioexport.chapters_invalid"
                )
            start_ms = round(sample * 1000 / sample_rate)
            end_ms = None
            chapters.append(Chapter(title.strip(), start_ms, end_ms, sample, None, sample_rate))
            continue
        else:
            start_ms = _milliseconds(row.get("start_ms"), "start_ms")
            end_ms = _milliseconds(row["end_ms"], "end_ms") if "end_ms" in row else None
        chapters.append(Chapter(title.strip(), start_ms, end_ms))
    return tuple(chapters)


def normalize_chapters(chapters: Sequence[Chapter], duration_ms: int) -> tuple[Chapter, ...]:
    if not chapters:
        return ()
    if duration_ms <= 0:
        raise InvalidExportError(
            "audio duration must be positive", code="audioexport.chapters_invalid"
        )
    normalized = []
    for index, chapter in enumerate(chapters):
        next_start = chapters[index + 1].start_ms if index + 1 < len(chapters) else duration_ms
        end_ms = chapter.end_ms if chapter.end_ms is not None else next_start
        if (
            not isinstance(chapter.title, str)
            or not chapter.title.strip()
            or chapter.start_ms < 0
            or chapter.start_ms >= duration_ms
            or end_ms <= chapter.start_ms
            or end_ms > duration_ms
            or (index > 0 and chapter.start_ms <= chapters[index - 1].start_ms)
            or (index + 1 < len(chapters) and end_ms > next_start)
        ):
            raise InvalidExportError(
                "chapters must be ordered, non-overlapping and within audio duration",
                code="audioexport.chapters_invalid",
            )
        end_sample = None
        if chapter.start_sample is not None:
            if chapter.sample_rate is None or chapter.sample_rate <= 0:
                raise InvalidExportError(
                    "sample chapter requires sample_rate", code="audioexport.chapters_invalid"
                )
            if index + 1 < len(chapters) and chapters[index + 1].start_sample is not None:
                end_sample = chapters[index + 1].start_sample
            else:
                end_sample = round(end_ms * chapter.sample_rate / 1000)
        normalized.append(
            Chapter(
                chapter.title,
                chapter.start_ms,
                end_ms,
                chapter.start_sample,
                end_sample,
                chapter.sample_rate,
            )
        )
    return tuple(normalized)


def ffmetadata_text(chapters: Sequence[Chapter]) -> str:
    lines = [";FFMETADATA1", ""]
    for chapter in chapters:
        assert chapter.end_ms is not None
        if (
            chapter.sample_rate
            and chapter.start_sample is not None
            and chapter.end_sample is not None
        ):
            timebase, start, end = chapter.sample_rate, chapter.start_sample, chapter.end_sample
        else:
            timebase, start, end = 1000, chapter.start_ms, chapter.end_ms
        lines.extend(
            (
                "[CHAPTER]",
                f"TIMEBASE=1/{timebase}",
                f"START={start}",
                f"END={end}",
                f"title={escape_ffmetadata_value(chapter.title)}",
                "",
            )
        )
    return "\n".join(lines)


_CHAPTERS_TXT_LINE = re.compile(r"^(\d{2,}):([0-5]\d):([0-5]\d)\.(\d{3})\s+(.+)$")


def load_chapters_txt(path: Path | str) -> tuple[Chapter, ...]:
    """Load timestamp/title rows from a UTF-8 chapters.txt file."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise InvalidExportError(
            f"invalid chapters file {source}: {exc}", code="audioexport.chapters_invalid"
        ) from exc

    chapters: list[Chapter] = []
    previous_start = -1
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _CHAPTERS_TXT_LINE.fullmatch(line)
        if match is None:
            raise InvalidExportError(
                f"invalid chapters.txt line {line_number}", code="audioexport.chapters_invalid"
            )
        hours, minutes, seconds, milliseconds, raw_title = match.groups()
        start_ms = ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(milliseconds)
        title = raw_title.strip()
        if not title or start_ms <= previous_start:
            raise InvalidExportError(
                f"chapters.txt line {line_number} has an empty title or non-monotonic timestamp",
                code="audioexport.chapters_invalid",
            )
        chapters.append(Chapter(title, start_ms))
        previous_start = start_ms
    if not chapters:
        raise InvalidExportError(
            "chapters.txt contains no chapter entries", code="audioexport.chapters_invalid"
        )
    return tuple(chapters)


def build_track_chapters(rows: Sequence[tuple[str, int]]) -> tuple[Chapter, ...]:
    """Build one chapter per (title, normalized duration_ms) pair."""
    if not rows:
        raise InvalidExportError(
            "cannot build chapters without tracks", code="audioexport.chapters_invalid"
        )
    chapters: list[Chapter] = []
    offset_ms = 0
    for title, duration_ms in rows:
        if (
            not isinstance(title, str)
            or not title.strip()
            or isinstance(duration_ms, bool)
            or not isinstance(duration_ms, int)
            or duration_ms <= 0
        ):
            raise InvalidExportError(
                "track chapter title and duration are invalid", code="audioexport.chapters_invalid"
            )
        chapters.append(Chapter(title.strip(), offset_ms))
        offset_ms += duration_ms
    return tuple(chapters)
