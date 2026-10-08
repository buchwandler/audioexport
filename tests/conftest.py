from __future__ import annotations

import math
import shutil
import struct
import wave
from pathlib import Path

import pytest


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    path = tmp_path / "speech.wav"
    sample_rate = 24000
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(
            b"".join(
                struct.pack("<h", round(3000 * math.sin(2 * math.pi * 440 * t / sample_rate)))
                for t in range(sample_rate * 2)
            )
        )
    return path


@pytest.fixture
def have_tools() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("requires system FFmpeg and FFprobe")
