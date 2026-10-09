from __future__ import annotations

import shutil
import struct
import wave
from pathlib import Path

from audioexport.audiobook import build_audiobook
from audioexport.fftools import probe


def test_one_hundred_tracks_have_natural_order_and_chapters(
    tmp_path: Path, have_tools: None
) -> None:
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    source = tmp_path / "short.wav"
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(22050)
        stream.writeframes(struct.pack("<h", 0) * 2205)
    for index in range(1, 101):
        shutil.copyfile(source, tracks / f"chapter {index}.wav")

    result = build_audiobook(tracks, tmp_path / "hundred.m4b", bitrate="32k")
    info = probe(result.path)
    titles = [chapter["tags"]["title"] for chapter in info["chapters"]]
    assert result.track_count == 100
    assert result.chapter_count == 100
    assert titles == [f"chapter {index}" for index in range(1, 101)]
