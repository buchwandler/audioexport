"""CLI delegates to the same public library API."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .audiobook import build_audiobook
from .audiobook_profile import load_audiobook_profile, resolve_profile_overrides
from .bundle import export_bundle, export_bundle_all
from .errors import AudioExportError
from .fftools import doctor, probe
from .formats import FORMATS
from .metadata import AudiobookMetadata
from .pipeline import encode, run_profile
from .variants import list_bundle_variants


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
    encode_parser.add_argument("--timeline", type=Path, help="Generic JSON chapter timeline")
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

    audiobook = sub.add_parser(
        "audiobook", help="Assemble ordered tracks or a directory into one audiobook M4B"
    )
    audiobook.add_argument("inputs", type=Path, nargs="*")
    audiobook.add_argument("-o", "--output", type=Path)
    audiobook.add_argument("--profile", type=Path)
    audiobook.add_argument("--bitrate")
    audiobook.add_argument("--sample-rate", type=int)
    audiobook.add_argument("--channels", type=int, choices=(1, 2))
    audiobook.add_argument("--title")
    audiobook.add_argument("--author", "--artist", dest="author")
    audiobook.add_argument("--album")
    audiobook.add_argument("--album-artist")
    audiobook.add_argument("--writer")
    audiobook.add_argument("--genre")
    audiobook.add_argument("--description")
    audiobook.add_argument("--long-description")
    audiobook.add_argument("--comment")
    audiobook.add_argument("--copyright")
    audiobook.add_argument("--encoded-by")
    audiobook.add_argument("--language")
    audiobook.add_argument("--publisher")
    audiobook.add_argument("--grouping")
    audiobook.add_argument("--metadata", action="append", default=[], metavar="KEY=VALUE")
    audiobook.add_argument("--cover", type=Path)
    audiobook.add_argument("--chapters-file", type=Path)
    audiobook.add_argument("--use-filenames-as-chapters", action="store_true")
    audiobook.add_argument("--no-auto-sidecars", action="store_true")
    audiobook.add_argument("--force", action="store_true")
    audiobook.add_argument("--json", action="store_true")
    audiobook.add_argument("--ffmpeg")
    audiobook.add_argument("--ffprobe")

    bundle_parser = sub.add_parser("bundle", help="Export an SSMD bundle")
    bundle_commands = bundle_parser.add_subparsers(dest="bundle_command", required=True)
    bundle_export = bundle_commands.add_parser(
        "export", help="Export one or all composed SSMD bundle variants"
    )
    bundle_export.add_argument("bundle", type=Path, metavar="BUNDLE")
    bundle_export.add_argument(
        "--variant", help="export one variant; omit to export every AudioCompose variant"
    )
    bundle_export.add_argument("--chapters", default="all", metavar="SELECTOR")
    bundle_export.add_argument("-o", "--output", type=Path)
    bundle_export.add_argument("--format", choices=tuple(FORMATS))
    bundle_export.add_argument("--profile", type=Path)
    bundle_export.add_argument("--bitrate")
    bundle_export.add_argument("--sample-rate", type=int)
    bundle_export.add_argument("--channels", type=int, choices=(1, 2))
    bundle_export.add_argument("--title")
    bundle_export.add_argument("--author", "--artist", dest="author")
    bundle_export.add_argument("--album")
    bundle_export.add_argument("--album-artist")
    bundle_export.add_argument("--writer")
    bundle_export.add_argument("--genre")
    bundle_export.add_argument("--description")
    bundle_export.add_argument("--long-description")
    bundle_export.add_argument("--comment")
    bundle_export.add_argument("--copyright")
    bundle_export.add_argument("--encoded-by")
    bundle_export.add_argument("--language")
    bundle_export.add_argument("--publisher")
    bundle_export.add_argument("--grouping")
    bundle_export.add_argument("--metadata", action="append", default=[], metavar="KEY=VALUE")
    bundle_export.add_argument("--cover", type=Path)
    bundle_export.add_argument("--timeline", type=Path)
    bundle_export.add_argument("--audiocompose-root", type=Path)
    bundle_export.add_argument("--output-root", type=Path)
    bundle_export.add_argument("--force", action="store_true")
    bundle_export.add_argument("--json", action="store_true")
    bundle_export.add_argument("--ffmpeg")
    bundle_export.add_argument("--ffprobe")

    bundle_variants = bundle_commands.add_parser(
        "variants", help="List AudioCompose variants available to AudioExport"
    )
    bundle_variants.add_argument(
        "bundle", type=Path, nargs="?", default=Path("."), metavar="BUNDLE"
    )
    bundle_variants.add_argument("--audiocompose-root", type=Path)
    bundle_variants.add_argument("--json", action="store_true")
    inspect = sub.add_parser("inspect", help="Inspect audio and audioexport sidecar")
    inspect.add_argument("input", type=Path)
    inspect.add_argument("--json", action="store_true")
    inspect.add_argument("--ffprobe")
    doctor_parser = sub.add_parser("doctor", help="Check FFmpeg tools and encoders")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.add_argument("--ffmpeg")
    doctor_parser.add_argument("--ffprobe")
    return parser


def _metadata_values(
    items: Sequence[str],
    *,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
) -> dict[str, str]:
    """Combine repeatable KEY=VALUE options and common tag overrides."""
    metadata: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise AudioExportError(
                "--metadata requires KEY=VALUE", code="audioexport.metadata_invalid"
            )
        key, value = item.split("=", 1)
        metadata[key] = value
    for field, tag_value in (("title", title), ("artist", artist), ("album", album)):
        if tag_value is None:
            continue
        metadata[field] = tag_value
    return metadata


def _bundle_metadata_values(args: argparse.Namespace) -> dict[str, str]:
    metadata = _metadata_values(
        args.metadata,
        title=args.title,
        artist=args.author,
        album=args.album,
    )
    for field in (
        "album_artist",
        "writer",
        "genre",
        "description",
        "long_description",
        "comment",
        "copyright",
        "encoded_by",
        "language",
        "publisher",
        "grouping",
    ):
        value = getattr(args, field)
        if value is not None:
            metadata[field] = value
    return metadata


def _print(payload: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))


def _print_bundle_export(payload: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        _print(payload, as_json=True)
        return
    result = payload["result"]
    kind = payload["kind"]
    print("AudioExport bundle export")
    print(f"kind: {kind}")
    print(f"workspace: {result['workspace']}")
    print(f"variant: {result['variant']}")
    if kind == "book":
        selected = result.get("selected_chapter_ids", [])
        print(f"selected chapters: {len(selected)}")
    else:
        print(f"document: {result['document_id']}")
    if result.get("input_compose_report") is not None:
        print(f"input compose report: {result['input_compose_report']}")
    if result.get("source") is not None:
        print(f"source: {result['source']}")
    print(f"output: {result['output']}")
    if result.get("codec") is not None:
        print(f"codec: {result['codec']}")
    if isinstance(result.get("duration_ms"), (int, float)):
        print(f"duration: {result['duration_ms'] / 1000:.3f} s")
    print(f"reused: {'yes' if result['reused'] else 'no'}")


def _print_bundle_variants(payload: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        _print(payload, as_json=True)
        return
    print("AudioExport bundle variants")
    print(f"kind: {payload['kind']}")
    print(f"workspace: {payload['workspace']}")
    print(f"audiocompose root: {payload['audiocompose_root']}")
    variants = payload["variants"]
    print(f"variants: {len(variants)}")
    if not variants:
        return
    print()
    print("VARIANT                       STATE          STATUS   ITEMS  OUTPUT")
    for item in variants:
        name = item["name"]
        state = item["state"]
        status = item.get("status") or "-"
        items = f"{item['usable_items']}/{item['total_items']}"
        output = item.get("output") or "-"
        print(f"{name:<29} {state:<14} {status:<8} {items:<6} {output}")
        if item.get("error"):
            print(f"  error: {item['error']}")


def _print_bundle_export_batch(payload: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        _print(payload, as_json=True)
        return
    results = payload["results"]
    count = len(results)
    print("AudioExport bundle export")
    print(f"kind: {payload['kind']}")
    print(f"workspace: {payload['workspace']}")
    print(f"variants: {count}")
    for index, result in enumerate(results, 1):
        print()
        print(f"[{index}/{count}] {result['variant']}")
        print(f"output: {result['output']}")
        if result.get("codec") is not None:
            print(f"codec: {result['codec']}")
        duration_ms = result.get("duration_ms")
        if isinstance(duration_ms, (int, float)):
            print(f"duration: {duration_ms / 1000:.3f} s")
        elif isinstance(result.get("duration_seconds"), (int, float)):
            print(f"duration: {result['duration_seconds']:.3f} s")
        print(f"reused: {'yes' if result['reused'] else 'no'}")


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
            metadata = _metadata_values(
                args.metadata,
                title=args.title,
                artist=args.artist,
                album=args.album,
            )
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
        if args.command == "bundle" and args.bundle_command == "variants":
            variants_result = list_bundle_variants(
                args.bundle,
                audiocompose_root=args.audiocompose_root,
            )
            _print_bundle_variants(variants_result.to_dict(), as_json=args.json)
            return 0
        if args.command == "bundle" and args.bundle_command == "export":
            export_options = {
                "bundle": args.bundle,
                "chapters": args.chapters,
                "output": args.output,
                "format": args.format,
                "profile": args.profile,
                "bitrate": args.bitrate,
                "sample_rate": args.sample_rate,
                "channels": args.channels,
                "metadata": _bundle_metadata_values(args),
                "cover": args.cover,
                "timeline": args.timeline,
                "audiocompose_root": args.audiocompose_root,
                "output_root": args.output_root,
                "force": args.force,
                "ffmpeg": args.ffmpeg,
                "ffprobe": args.ffprobe,
            }
            if args.variant is None:
                batch_result = export_bundle_all(**export_options)
                _print_bundle_export_batch(batch_result.to_dict(), as_json=args.json)
            else:
                bundle_result = export_bundle(variant=args.variant, **export_options)
                _print_bundle_export(bundle_result.to_dict(), as_json=args.json)
            return 0
        if args.command == "audiobook":
            metadata_values: dict[str, str] = {}
            for item in args.metadata:
                if "=" not in item:
                    raise AudioExportError(
                        "--metadata requires KEY=VALUE",
                        code="audioexport.audiobook_metadata_invalid",
                    )
                key, value = item.split("=", 1)
                metadata_values[key] = value
            for field in (
                "title",
                "author",
                "album",
                "album_artist",
                "writer",
                "genre",
                "description",
                "long_description",
                "comment",
                "copyright",
                "encoded_by",
                "language",
                "publisher",
                "grouping",
            ):
                value = getattr(args, field)
                if value is not None:
                    metadata_values[field] = value

            audiobook_metadata: AudiobookMetadata | Mapping[str, str]
            if args.profile is not None:
                if args.inputs:
                    raise AudioExportError(
                        "positional inputs cannot be combined with --profile",
                        code="audioexport.audiobook_profile_invalid",
                    )
                profile = load_audiobook_profile(args.profile)
                profile = resolve_profile_overrides(
                    profile,
                    output=args.output,
                    metadata=metadata_values,
                    cover=args.cover,
                    chapters_file=args.chapters_file,
                    bitrate=args.bitrate,
                    sample_rate=args.sample_rate,
                    channels=args.channels,
                    use_filenames_as_chapters=args.use_filenames_as_chapters,
                )
                inputs = profile.inputs
                output = profile.output
                audiobook_metadata = profile.metadata
                cover = profile.cover
                chapters_file = profile.chapters_file
                chapters = () if profile.chapter_mode == "none" else None
                use_filenames = profile.title_source == "filename"
                discover_sidecars = False
                audio_bitrate = profile.audio.bitrate
                audio_sample_rate = profile.audio.sample_rate
                audio_channels = profile.audio.channels
            else:
                if not args.inputs:
                    raise AudioExportError(
                        "audioexport audiobook requires at least one input",
                        code="audioexport.audiobook_no_inputs",
                    )
                if args.output is None:
                    raise AudioExportError(
                        "audioexport audiobook requires --output unless --profile supplies it",
                        code="audioexport.audiobook_output_invalid",
                    )
                inputs = args.inputs
                output = args.output
                audiobook_metadata = metadata_values
                cover = args.cover
                chapters_file = args.chapters_file
                chapters = None
                use_filenames = args.use_filenames_as_chapters
                discover_sidecars = not args.no_auto_sidecars
                audio_bitrate = args.bitrate
                audio_sample_rate = args.sample_rate
                audio_channels = args.channels

            audiobook_result = build_audiobook(
                inputs,
                output,
                metadata=audiobook_metadata,
                cover=cover,
                chapters=chapters,
                chapters_file=chapters_file,
                bitrate=audio_bitrate,
                sample_rate=audio_sample_rate,
                channels=audio_channels,
                force=args.force,
                ffmpeg=args.ffmpeg,
                ffprobe=args.ffprobe,
                discover_sidecars=discover_sidecars,
                use_filenames_as_chapters=use_filenames,
            )
            _print(audiobook_result.to_dict(), as_json=args.json)
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
