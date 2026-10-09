from __future__ import annotations

from pathlib import Path

import pytest

from audioexport.chapters import build_track_chapters, load_chapters_txt
from audioexport.errors import InvalidExportError


def test_chapters_txt_parses_times_comments_and_unicode(tmp_path: Path) -> None:
    path = tmp_path / "chapters.txt"
    path.write_text(
        "# table of contents\n\n00:00:00.000 Intro\n00:00:02.125 Deuxième 章\n",
        encoding="utf-8",
    )

    # Explicit chapters have open ends; final duration supplies the last end.
    chapters = load_chapters_txt(path)
    assert [(chapter.title, chapter.start_ms, chapter.end_ms) for chapter in chapters] == [
        ("Intro", 0, None),
        ("Deuxième 章", 2125, None),
    ]


def test_chapters_txt_rejects_non_monotonic_and_malformed_times(tmp_path: Path) -> None:
    path = tmp_path / "chapters.txt"
    path.write_text("00:00:02.000 Later\n00:00:01.000 Earlier\n", encoding="utf-8")
    with pytest.raises(InvalidExportError) as error:
        load_chapters_txt(path)
    assert error.value.code == "audioexport.chapters_invalid"

    path.write_text("00:00:60.000 Invalid\n", encoding="utf-8")
    with pytest.raises(InvalidExportError):
        load_chapters_txt(path)


def test_track_chapters_use_cumulative_durations() -> None:
    chapters = build_track_chapters((("One", 1000), ("Two", 2500)))
    assert [(chapter.title, chapter.start_ms, chapter.end_ms) for chapter in chapters] == [
        ("One", 0, None),
        ("Two", 1000, None),
    ]


def test_track_chapters_reject_invalid_rows() -> None:
    with pytest.raises(InvalidExportError):
        build_track_chapters((("", 1000),))
    with pytest.raises(InvalidExportError):
        build_track_chapters((("Empty", 0),))
