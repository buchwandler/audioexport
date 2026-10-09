from __future__ import annotations

import json
from pathlib import Path

import pytest

from audioexport.chapters import ffmetadata_text, load_chapters, normalize_chapters


@pytest.mark.parametrize(
    ("sample_rate", "chapter_start"),
    ((44_100, 44_117), (48_000, 48_031)),
)
def test_sample_chapter_precision_and_escaping(
    tmp_path: Path, sample_rate: int, chapter_start: int
) -> None:
    timeline = tmp_path / "timeline.json"
    timeline.write_text(
        json.dumps(
            {
                "format": "readio.composition-timeline",
                "sample_rate": sample_rate,
                "chapters": [
                    {"title": "Intro", "start_sample": 0},
                    {"title": "Second;#=\\\nλ", "start_sample": chapter_start},
                ],
            }
        ),
        encoding="utf-8",
    )

    chapters = normalize_chapters(load_chapters(timeline), duration_ms=2100)
    metadata = ffmetadata_text(chapters)

    assert f"TIMEBASE=1/{sample_rate}" in metadata
    assert f"START={chapter_start}" in metadata
    assert f"END={round(2100 * sample_rate / 1000)}" in metadata
    assert "title=Second\\;\\#\\=\\\\\\nλ" in metadata
