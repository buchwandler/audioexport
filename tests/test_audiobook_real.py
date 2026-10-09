from __future__ import annotations

import subprocess
from pathlib import Path

from audioexport.audiobook import AudiobookTrack, build_audiobook
from audioexport.fftools import probe


def _make_audio(
    path: Path, *, sample_rate: int, channels: int, codec: str, title: str | None = None
) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate={sample_rate}",
        "-t",
        "0.8",
        "-ac",
        str(channels),
        "-c:a",
        codec,
    ]
    if title is not None:
        command.extend(["-metadata", f"title={title}"])
    command.append(str(path))
    subprocess.run(command, check=True, capture_output=True, text=True)


def test_mixed_tracks_build_aac_audiobook_and_cache_respects_order(
    tmp_path: Path, have_tools: None
) -> None:
    first = tmp_path / "01-tagged.wav"
    second = tmp_path / "10 fallback.flac"
    _make_audio(first, sample_rate=22050, channels=1, codec="pcm_s16le", title="Tagged intro")
    _make_audio(second, sample_rate=44100, channels=2, codec="flac")
    output = tmp_path / "book.m4b"

    result = build_audiobook(
        [AudiobookTrack(second, title="Explicit second"), first],
        output,
        metadata={"title": "The Book", "author": "A. Author"},
    )
    info = probe(output)
    audio = [stream for stream in info["streams"] if stream.get("codec_type") == "audio"]
    assert len(audio) == 1
    assert audio[0]["codec_name"] == "aac"
    assert audio[0]["sample_rate"] == "44100"
    assert audio[0]["channels"] == 2
    assert result.track_count == 2
    assert result.chapter_count == 2
    assert [chapter["tags"]["title"] for chapter in info["chapters"]] == [
        "Explicit second",
        "Tagged intro",
    ]
    tags = {key.lower(): value for key, value in info["format"].get("tags", {}).items()}
    assert tags["title"] == "The Book"
    assert tags["artist"] == "A. Author"
    assert tags["media_type"] == "2"

    reused = build_audiobook(
        [AudiobookTrack(second, title="Explicit second"), first],
        output,
        metadata={"title": "The Book", "author": "A. Author"},
    )
    assert reused.reused
    assert reused.export_id == result.export_id

    reordered = build_audiobook(
        [first, AudiobookTrack(second, title="Explicit second")],
        output,
        metadata={"title": "The Book", "author": "A. Author"},
    )
    assert not reordered.reused
    assert reordered.export_id != result.export_id
    assert [chapter["tags"]["title"] for chapter in probe(output)["chapters"]] == [
        "Tagged intro",
        "Explicit second",
    ]


def test_explicit_chapters_file_overrides_automatic_track_chapters(
    tmp_path: Path, wav: Path, have_tools: None
) -> None:
    chapters_file = tmp_path / "chapters.txt"
    chapters_file.write_text("00:00:00.000 Entire work\n", encoding="utf-8")

    result = build_audiobook([wav], tmp_path / "explicit.m4b", chapters_file=chapters_file)
    assert result.chapter_count == 1
    assert probe(result.path)["chapters"][0]["tags"]["title"] == "Entire work"
