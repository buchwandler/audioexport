"""Create a standalone two-second test tone WAV using only Python stdlib."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path


def main() -> None:
    path = Path("demo.wav")
    rate = 24_000
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(
            b"".join(
                struct.pack("<h", round(4000 * math.sin(2 * math.pi * 440 * sample / rate)))
                for sample in range(rate * 2)
            )
        )
    print(f"Created {path} ({rate} Hz PCM16, 2 seconds)")


if __name__ == "__main__":
    main()
