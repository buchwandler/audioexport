from __future__ import annotations

import subprocess
from pathlib import Path

from audioexport.audiobook import build_audiobook


def _write_cover(path: Path, color: str) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=32x32:d=0.1",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_cache_identity_tracks_source_options_metadata_cover_and_chapters(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(wav.read_bytes())
    output = tmp_path / "book.m4b"

    baseline = build_audiobook([source], output, bitrate="96k", metadata={"title": "First"})
    assert build_audiobook([source], output, bitrate="96k", metadata={"title": "First"}).reused
    (tmp_path / "irrelevant.txt").write_text("not an input", encoding="utf-8")
    assert build_audiobook([source], output, bitrate="96k", metadata={"title": "First"}).reused

    bitrate_changed = build_audiobook([source], output, bitrate="128k", metadata={"title": "First"})
    assert not bitrate_changed.reused and bitrate_changed.export_id != baseline.export_id
    metadata_changed = build_audiobook(
        [source], output, bitrate="128k", metadata={"title": "Second"}
    )
    assert not metadata_changed.reused and metadata_changed.export_id != bitrate_changed.export_id

    cover = tmp_path / "cover.jpg"
    _write_cover(cover, "red")
    cover_added = build_audiobook(
        [source], output, bitrate="128k", metadata={"title": "Second"}, cover=cover
    )
    assert not cover_added.reused
    _write_cover(cover, "blue")
    cover_changed = build_audiobook(
        [source], output, bitrate="128k", metadata={"title": "Second"}, cover=cover
    )
    assert not cover_changed.reused and cover_changed.export_id != cover_added.export_id

    chapters_file = tmp_path / "chapters.txt"
    chapters_file.write_text("00:00:00.000 First chapter\n", encoding="utf-8")
    chapters_added = build_audiobook(
        [source],
        output,
        bitrate="128k",
        metadata={"title": "Second"},
        cover=cover,
        chapters_file=chapters_file,
    )
    assert not chapters_added.reused
    chapters_file.write_text("00:00:00.000 Updated chapter\n", encoding="utf-8")
    chapters_changed = build_audiobook(
        [source],
        output,
        bitrate="128k",
        metadata={"title": "Second"},
        cover=cover,
        chapters_file=chapters_file,
    )
    assert not chapters_changed.reused and chapters_changed.export_id != chapters_added.export_id

    content = bytearray(source.read_bytes())
    content[-1] ^= 1
    source.write_bytes(content)
    source_changed = build_audiobook(
        [source],
        output,
        bitrate="128k",
        metadata={"title": "Second"},
        cover=cover,
        chapters_file=chapters_file,
    )
    assert not source_changed.reused and source_changed.export_id != chapters_changed.export_id
