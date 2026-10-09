from __future__ import annotations

import json
from pathlib import Path

import pytest

from audioexport import Chapter, InvalidExportError, encode, normalize_bitrate, probe, run_profile
from audioexport.chapters import (
    escape_ffmetadata_value,
    ffmetadata_text,
    load_chapters,
    normalize_chapters,
)
from audioexport.formats import FORMATS, encoder_options


def test_bitrate_and_escape() -> None:
    assert normalize_bitrate("192k", "mp3") == "192k"
    assert normalize_bitrate("192000", "m4a") == "192k"
    assert normalize_bitrate("2M", "opus") == "2000k"
    assert escape_ffmetadata_value("one;#=two\\three\nhello") == "one\\;\\#\\=two\\\\three\\nhello"
    with pytest.raises(InvalidExportError):
        normalize_bitrate("192k", "wav")
    with pytest.raises(InvalidExportError):
        normalize_bitrate("1;rm -rf /", "mp3")


def test_native_vorbis_encoder_options() -> None:
    assert encoder_options("vorbis") == ("-strict", "-2", "-ac", "2")
    assert encoder_options("libvorbis") == ()


def test_timeline_and_chapters(tmp_path: Path) -> None:
    file = tmp_path / "chapters.json"
    file.write_text(
        json.dumps(
            {
                "chapters": [
                    {"title": "Part #1", "start_ms": 0},
                    {"title": "Second", "start_ms": 900},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert normalize_chapters(load_chapters(file), 2000) == (
        Chapter("Part #1", 0, 900),
        Chapter("Second", 900, 2000),
    )
    file.write_text(
        json.dumps(
            {
                "format": "readio.composition-timeline",
                "sample_rate": 24000,
                "chapters": [
                    {"scope_id": "a", "title": "Hello", "start_sample": 0},
                    {"scope_id": "b", "title": "World", "start_sample": 24000},
                ],
            }
        )
    )
    assert [c.start_ms for c in load_chapters(file)] == [0, 1000]
    sample_chapters = normalize_chapters(load_chapters(file), 2000)
    assert "TIMEBASE=1/24000" in ffmetadata_text(sample_chapters)
    assert "START=24000" in ffmetadata_text(sample_chapters)
    with pytest.raises(InvalidExportError):
        normalize_chapters([Chapter("A", 1000), Chapter("B", 900)], 2000)
    with pytest.raises(InvalidExportError):
        normalize_chapters([Chapter("A", 2100)], 2000)


@pytest.mark.parametrize("fmt", tuple(FORMATS))
def test_real_ffmpeg_formats(fmt: str, wav: Path, have_tools: None) -> None:
    target = wav.with_name(f"encoded.{fmt}")
    result = encode(wav, target, format=fmt)
    assert result.path.is_file()
    assert result.manifest_path.is_file()
    assert result.output_sha256
    assert result.reused is False
    repeat = encode(wav, target, format=fmt)
    assert repeat.reused
    assert result.export_id == repeat.export_id
    assert any(x["codec_type"] == "audio" for x in probe(target)["streams"])


def test_source_options_and_no_clobber(wav: Path, have_tools: None) -> None:
    output = wav.with_name("target.mp3")
    output.write_text("a file owned by somebody else")
    with pytest.raises(FileExistsError):
        encode(wav, output)
    result = encode(wav, output, force=True)
    second = encode(wav, output, bitrate="128k")
    assert second.export_id != result.export_id
    assert not second.reused
    output.write_bytes(b"user edited file")
    with pytest.raises(FileExistsError):
        encode(wav, output)
    assert encode(wav, output, force=True).path.is_file()
    with pytest.raises(InvalidExportError):
        encode(wav, wav, format="wav")


def test_m4b_chapters_reado_and_metadata(wav: Path, tmp_path: Path, have_tools: None) -> None:
    timeline = tmp_path / "timeline.json"
    timeline.write_text(
        json.dumps(
            {
                "format": "readio.composition-timeline",
                "sample_rate": 24000,
                "chapters": [
                    {"title": "First", "start_sample": 0},
                    {"title": "Second;#=", "start_sample": 24000},
                ],
            }
        ),
        encoding="utf-8",
    )
    target = tmp_path / "audiobook.m4b"
    result = encode(
        wav, target, timeline=timeline, metadata={"title": "My Book", "artist": "The Author"}
    )
    assert result.chapter_count == 2
    info = probe(target)
    assert len(info["chapters"]) == 2
    assert info["chapters"][1]["tags"]["title"] == "Second;#="
    assert info["format"]["tags"]["title"] == "My Book"


def test_profiles(tmp_path: Path, wav: Path, have_tools: None) -> None:
    profile = tmp_path / "export.toml"
    profile.write_text(
        """schema = "audioexport.profile.v1"
[metadata]
title = "Independent export"
[[outputs]]
format = "mp3"
bitrate = "128k"
[[outputs]]
format = "flac"
filename = "lossless.flac"
""",
        encoding="utf-8",
    )
    results = run_profile(wav, profile, tmp_path / "out")
    assert len(results) == 2
    assert all(r.path.is_file() for r in results)
    assert all(r.reused for r in run_profile(wav, profile, tmp_path / "out"))


def test_bad_profile(tmp_path: Path) -> None:
    from audioexport import load_profile

    f = tmp_path / "x.toml"
    f.write_text(
        'schema = "audioexport.profile.v1"\n[[outputs]]\nformat = "mp3"\nfilename = "../escape.mp3"\n'
    )
    with pytest.raises(InvalidExportError):
        load_profile(f)
