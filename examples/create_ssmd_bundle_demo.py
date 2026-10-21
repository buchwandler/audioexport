"""Generate a tiny, checksum-valid composed-book workspace for bundle export."""

from __future__ import annotations

import argparse
import hashlib
import json
import wave
from pathlib import Path

VARIANT = "demo-variant"
SAMPLE_RATE = 8_000
FRAME_COUNT = 8_000


def create_demo_workspace(destination: Path) -> Path:
    workspace = destination.expanduser().absolute()
    if workspace.exists():
        raise FileExistsError(f"refusing to overwrite existing workspace: {workspace}")
    (workspace / "chapters").mkdir(parents=True)

    variant_dir = workspace / "audiocompose" / VARIANT
    variant_dir.mkdir(parents=True)
    wav_path = variant_dir / "chapter-0001.wav"
    with wave.open(str(wav_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(b"\0\0" * FRAME_COUNT)

    digest = hashlib.sha256(wav_path.read_bytes()).hexdigest()
    manifest = {
        "format": "ssmdconvert.book",
        "schema_version": 1,
        "metadata": {
            "title": "AudioExport Demo Book",
            "authors": ["AudioExport"],
            "language": "en",
        },
        "chapters": [
            {"id": "opening", "source_number": 1, "title": "Opening"},
        ],
    }
    (workspace / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    report = (
        'format = "audiocompose-book-report"\n'
        "version = 1\n"
        f'variant = "{VARIANT}"\n\n'
        "[[item]]\n"
        'status = "written"\n'
        'id = "opening"\n'
        "source_number = 1\n"
        'render_plan_id = "example-render-plan"\n'
        'manifest_id = "example-manifest"\n'
        'output_policy_id = "example-wav-policy"\n'
        'output = "chapter-0001.wav"\n'
        f'sha256 = "sha256:{digest}"\n'
        f"sample_rate = {SAMPLE_RATE}\n"
        "channels = 1\n"
        f"frames = {FRAME_COUNT}\n"
        "duration_seconds = 1.0\n"
    )
    (variant_dir / "compose-report.toml").write_text(report, encoding="utf-8")
    return workspace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "destination",
        nargs="?",
        type=Path,
        default=Path("demo.ssmd-bundle"),
        help="new workspace directory (default: ./demo.ssmd-bundle)",
    )
    args = parser.parse_args()
    workspace = create_demo_workspace(args.destination)
    print(f"Created {workspace} with variant {VARIANT}")


if __name__ == "__main__":
    main()
