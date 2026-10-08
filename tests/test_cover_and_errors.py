from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from audioexport import InvalidExportError, ToolNotFoundError, encode, probe


@pytest.mark.parametrize("extension", ("jpg", "png"))
@pytest.mark.parametrize("fmt", ("mp3", "m4a", "m4b"))
def test_real_cover_art(
    extension: str, fmt: str, wav: Path, tmp_path: Path, have_tools: None
) -> None:
    cover = tmp_path / ("cover." + extension)
    result = subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:d=0.1",
            "-frames:v",
            "1",
            str(cover),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    target = tmp_path / ("encoded." + fmt)
    export = encode(wav, target, cover=cover)
    assert export.path.is_file()
    info = probe(target)
    assert any(
        s.get("disposition", {}).get("attached_pic") == 1
        for s in info["streams"]
        if s.get("codec_type") == "video"
    )


def test_invalid_cover_and_format(wav: Path, tmp_path: Path, have_tools: None) -> None:
    bad_cover = tmp_path / "cover.jpg"
    bad_cover.write_text("not a jpeg")
    with pytest.raises(InvalidExportError, match="JPEG or PNG"):
        encode(wav, tmp_path / "bad.m4b", cover=bad_cover)
    with pytest.raises(InvalidExportError, match="MP3/M4A/M4B"):
        encode(wav, tmp_path / "bad.flac", cover=bad_cover)
    with pytest.raises(InvalidExportError, match="conflicts"):
        encode(wav, tmp_path / "bad.mp3", format="flac")
    with pytest.raises(ToolNotFoundError):
        encode(wav, tmp_path / "bad.mp3", ffmpeg="audioexport-no-such-ffmpeg-command")
    assert not (tmp_path / "bad.mp3").exists()


def test_manifest_is_content_addressed(wav: Path, tmp_path: Path, have_tools: None) -> None:
    a = encode(wav, tmp_path / "one.mp3")
    b = encode(wav, tmp_path / "two.mp3")
    assert a.export_id == b.export_id
    payload = json.loads(a.manifest_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "audioexport.manifest.v1"
    assert payload["source_sha256"] != payload["output_sha256"]
    assert payload["output"] == "one.mp3"
    assert not any(str(wav.parent) in str(x) for x in payload.values())
