"""CLI delegates to the same public library API."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .errors import AudioExportError
from .fftools import doctor, probe
from .formats import FORMATS
from .pipeline import encode, run_profile


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audioexport", description="Standalone FFmpeg audio encoder"
    )
    parser.add_argument("--version", action="version", version=f"audioexport {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    encode_parser = sub.add_parser(
        "encode", help="Encode one input to MP3/FLAC/M4A/M4B/OGG/Opus/WAV"
    )
    encode_parser.add_argument("input", type=Path)
    encode_parser.add_argument("-o", "--output", type=Path)
    encode_parser.add_argument("--format", choices=tuple(FORMATS))
    encode_parser.add_argument("--bitrate", help="Bitrate e.g. 192k (lossy only)")
    encode_parser.add_argument("--title")
    encode_parser.add_argument("--artist")
    encode_parser.add_argument("--album")
    encode_parser.add_argument("--metadata", action="append", default=[], metavar="KEY=VALUE")
    encode_parser.add_argument("--cover", type=Path)
    encode_parser.add_argument(
        "--timeline", type=Path, help="Generic JSON chapters or Readio timeline"
    )
    encode_parser.add_argument("--force", action="store_true")
    encode_parser.add_argument("--json", action="store_true")
    encode_parser.add_argument("--ffmpeg")
    encode_parser.add_argument("--ffprobe")

    run_parser = sub.add_parser("run", help="Encode several outputs from an export TOML profile")
    run_parser.add_argument("input", type=Path)
    run_parser.add_argument("--profile", type=Path, required=True)
    run_parser.add_argument("--out-dir", type=Path, required=True)
    run_parser.add_argument("--force", action="store_true")
    run_parser.add_argument("--json", action="store_true")
    run_parser.add_argument("--ffmpeg")
    run_parser.add_argument("--ffprobe")

    inspect = sub.add_parser("inspect", help="Inspect audio and audioexport sidecar")
    inspect.add_argument("input", type=Path)
    inspect.add_argument("--json", action="store_true")
    inspect.add_argument("--ffprobe")
    doctor_parser = sub.add_parser("doctor", help="Check FFmpeg tools and encoders")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.add_argument("--ffmpeg")
    doctor_parser.add_argument("--ffprobe")
    return parser


def _print(payload: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            data = doctor(ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
            _print(data, as_json=args.json)
            return 0 if data["ready"] else 1
        if args.command == "inspect":
            data = probe(args.input, args.ffprobe)
            manifest = args.input.with_name(args.input.name + ".audioexport.json")
            if manifest.is_file():
                try:
                    data["audioexport"] = json.loads(manifest.read_text(encoding="utf-8"))
                except ValueError:
                    data["audioexport"] = {"invalid_manifest": True}
            _print(data, as_json=args.json)
            return 0
        if args.command == "encode":
            fmt = args.format or (args.output.suffix.lstrip(".").lower() if args.output else None)
            if fmt is None:
                parser.error("encode requires --format or --output with an audio extension")
            output = args.output or args.input.with_suffix(FORMATS[fmt].extension)
            metadata = {}
            for item in args.metadata:
                if "=" not in item:
                    parser.error("--metadata requires KEY=VALUE")
                key, value = item.split("=", 1)
                metadata[key] = value
            for field in ("title", "artist", "album"):
                value = getattr(args, field)
                if value is not None:
                    metadata[field] = value
            result = encode(
                args.input,
                output,
                format=fmt,
                bitrate=args.bitrate,
                metadata=metadata,
                cover=args.cover,
                timeline=args.timeline,
                force=args.force,
                ffmpeg=args.ffmpeg,
                ffprobe=args.ffprobe,
            )
            _print(result.to_dict(), as_json=args.json)
            return 0
        if args.command == "run":
            results = run_profile(
                args.input,
                args.profile,
                args.out_dir,
                force=args.force,
                ffmpeg=args.ffmpeg,
                ffprobe=args.ffprobe,
            )
            _print([item.to_dict() for item in results], as_json=args.json)
            return 0
    except (AudioExportError, OSError, FileExistsError, ValueError, KeyError) as exc:
        code = exc.code if isinstance(exc, AudioExportError) else "audioexport.error"
        if getattr(args, "json", False):
            _print({"error": {"code": code, "message": str(exc)}}, as_json=True)
        else:
            print(f"audioexport: {code}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
