"""Standalone multi-track audiobook assembly using FFmpeg and FFprobe."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .chapters import (
    Chapter,
    build_track_chapters,
    ffmetadata_text,
    load_chapters,
    load_chapters_txt,
    normalize_chapters,
)
from .errors import EncodingError, InvalidExportError, VerificationError
from .fftools import encoder_for_format, executable, probe, run, version
from .formats import EXPECTED_CODECS, encoder_options, normalize_bitrate
from .inputs import discover_audio_inputs, source_title
from .metadata import AudiobookMetadata, audiobook_tags
from .pipeline import (
    _canonical_hash,
    _commit_export,
    _manifest_path,
    _output_lock,
    _producer_version,
    _verify_output,
    sha256_file,
)
from .validation import cover_kind as _cover_kind
from .validation import duration_ms as _duration_ms


@dataclass(frozen=True, slots=True)
class AudiobookTrack:
    """One ordered source track and optional explicit chapter title."""

    path: Path | str
    title: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.path, (str, Path)):
            raise InvalidExportError(
                "track path must be a file path", code="audioexport.audiobook_input_invalid"
            )
        object.__setattr__(self, "path", Path(self.path))
        if self.title is not None and (not isinstance(self.title, str) or not self.title.strip()):
            raise InvalidExportError(
                "track title must be a non-empty string", code="audioexport.audiobook_input_invalid"
            )


@dataclass(frozen=True, slots=True)
class AudiobookResult:
    path: Path
    export_id: str
    output_sha256: str
    manifest_path: Path
    reused: bool
    chapter_count: int
    track_count: int
    duration_ms: int
    codec: str
    discovered_resources: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "path": str(self.path),
            "manifest_path": str(self.manifest_path),
            "discovered_resources": list(self.discovered_resources),
        }


def _audio_stream(info: Mapping[str, Any], *, label: str) -> Mapping[str, Any]:
    streams = info.get("streams")
    if not isinstance(streams, list):
        raise VerificationError(
            f"{label} has invalid stream data", code="audioexport.probe_invalid"
        )
    audio = [
        item for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "audio"
    ]
    if len(audio) != 1:
        raise VerificationError(
            f"{label} must contain exactly one audio stream", code="audioexport.stream_mismatch"
        )
    return audio[0]


def _audio_properties(info: Mapping[str, Any], *, label: str) -> tuple[str, int, int, int]:
    stream = _audio_stream(info, label=label)
    try:
        codec = str(stream["codec_name"])
        sample_rate = int(stream["sample_rate"])
        channels = int(stream["channels"])
        duration_ms = _duration_ms(info)
    except (KeyError, TypeError, ValueError) as exc:
        raise VerificationError(
            f"{label} lacks valid audio properties", code="audioexport.probe_invalid"
        ) from exc
    if sample_rate <= 0 or channels <= 0 or duration_ms <= 0:
        raise VerificationError(
            f"{label} lacks valid audio properties", code="audioexport.probe_invalid"
        )
    return codec, sample_rate, channels, duration_ms


def _load_audiobook_manifest(path: Path) -> dict[str, Any] | None:
    if path.is_symlink():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return (
        payload
        if isinstance(payload, dict)
        and payload.get("schema") == "audioexport.audiobook-manifest.v1"
        else None
    )


def _validate_chapter_rows(chapters: Sequence[Chapter]) -> tuple[Chapter, ...]:
    rows = tuple(chapters)
    previous_start = -1
    for row in rows:
        if (
            not isinstance(row, Chapter)
            or not isinstance(row.title, str)
            or not row.title.strip()
            or isinstance(row.start_ms, bool)
            or not isinstance(row.start_ms, int)
            or row.start_ms < 0
            or row.start_ms <= previous_start
        ):
            raise InvalidExportError(
                "chapters must have non-empty titles and strictly increasing millisecond starts",
                code="audioexport.chapters_invalid",
            )
        if row.end_ms is not None and (
            isinstance(row.end_ms, bool)
            or not isinstance(row.end_ms, int)
            or row.end_ms <= row.start_ms
        ):
            raise InvalidExportError(
                "chapter end must be a later millisecond offset",
                code="audioexport.chapters_invalid",
            )
        previous_start = row.start_ms
    return rows


def _resolve_tracks(
    inputs: Sequence[Path | str | AudiobookTrack] | Path | str | AudiobookTrack,
) -> tuple[tuple[Path, ...], tuple[str | None, ...], bool]:
    if isinstance(inputs, (Path, str, AudiobookTrack)):
        requested: tuple[Path | str | AudiobookTrack, ...] = (inputs,)
    else:
        requested = tuple(inputs)
    if not requested:
        raise InvalidExportError(
            "at least one audiobook input is required", code="audioexport.audiobook_no_inputs"
        )
    paths: list[Path | str] = []
    title_by_path: dict[Path, str] = {}
    for item in requested:
        if isinstance(item, AudiobookTrack):
            candidate = Path(item.path).expanduser()
            if candidate.is_dir() and item.title is not None:
                raise InvalidExportError(
                    "an explicit track title cannot be applied to a directory input",
                    code="audioexport.audiobook_input_invalid",
                )
            resolved = candidate.resolve()
            if item.title is not None:
                title_by_path[resolved] = item.title.strip()
            paths.append(candidate)
        elif isinstance(item, (Path, str)):
            paths.append(item)
        else:
            raise InvalidExportError(
                "audiobook inputs must be paths or AudiobookTrack objects",
                code="audioexport.audiobook_input_invalid",
            )
    discovered = discover_audio_inputs(paths)
    explicit_titles = tuple(title_by_path.get(path) for path in discovered)
    single_directory = (
        len(requested) == 1
        and Path(requested[0].path if isinstance(requested[0], AudiobookTrack) else requested[0])
        .expanduser()
        .is_dir()
    )
    return discovered, explicit_titles, single_directory


def _verify_normalized(
    path: Path,
    *,
    ffprobe: str,
    sample_rate: int,
    channels: int,
    label: str,
) -> int:
    info = probe(path, ffprobe)
    codec, actual_rate, actual_channels, duration = _audio_properties(info, label=label)
    if codec != "aac" or actual_rate != sample_rate or actual_channels != channels:
        raise VerificationError(
            f"{label} does not match the selected AAC settings", code="audioexport.segment_mismatch"
        )
    if any(item.get("codec_type") != "audio" for item in info["streams"]):
        raise VerificationError(
            f"{label} unexpectedly contains non-audio streams", code="audioexport.segment_mismatch"
        )
    return duration


def _normalize_segment(
    source: Path,
    destination: Path,
    *,
    ffmpeg: str,
    ffprobe: str,
    bitrate: str,
    sample_rate: int,
    channels: int,
    encoder: str,
) -> int:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-c:a",
        encoder,
        *encoder_options(encoder),
        "-b:a",
        bitrate,
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-movflags",
        "+faststart",
        "-f",
        "ipod",
        str(destination),
    ]
    try:
        run(command)
    except EncodingError as exc:
        raise EncodingError(
            f"could not normalize audiobook track {source.name}: {exc}",
            code="audioexport.audiobook_segment_failed",
        ) from exc
    return _verify_normalized(
        destination,
        ffprobe=ffprobe,
        sample_rate=sample_rate,
        channels=channels,
        label=f"normalized segment {source.name}",
    )


def _concat_file_line(path: Path) -> str:
    if "\n" in str(path) or "\r" in str(path):
        raise InvalidExportError(
            "track paths containing line breaks cannot be concatenated",
            code="audioexport.audiobook_input_invalid",
        )
    escaped = str(path).replace("\\", "\\\\").replace("'", "'\\''")
    return f"file '{escaped}'\n"


def _concat_segments(
    segments: Sequence[Path],
    destination: Path,
    *,
    ffmpeg: str,
    ffprobe: str,
    sample_rate: int,
    channels: int,
    expected_duration_ms: int,
) -> int:
    list_path = destination.with_name("segments.txt")
    try:
        list_path.write_text(
            "".join(_concat_file_line(item) for item in segments), encoding="utf-8"
        )
    except OSError as exc:
        raise EncodingError(
            f"could not stage audiobook segment list: {exc}",
            code="audioexport.audiobook_concat_failed",
        ) from exc
    try:
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-map",
                "0:a:0",
                "-c:a",
                "copy",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-movflags",
                "+faststart",
                "-f",
                "ipod",
                str(destination),
            ]
        )
    except EncodingError as exc:
        raise EncodingError(
            f"could not concatenate audiobook segments: {exc}",
            code="audioexport.audiobook_concat_failed",
        ) from exc
    info = probe(destination, ffprobe)
    codec, actual_rate, actual_channels, duration = _audio_properties(
        info, label="concatenated audiobook"
    )
    if codec != "aac" or actual_rate != sample_rate or actual_channels != channels:
        raise VerificationError(
            "concatenated audio does not match the selected AAC settings",
            code="audioexport.audiobook_concat_mismatch",
        )
    if any(item.get("codec_type") != "audio" for item in info["streams"]):
        raise VerificationError(
            "concatenated audio contains non-audio streams",
            code="audioexport.audiobook_concat_mismatch",
        )
    tolerance = max(500, len(segments) * 50, round(expected_duration_ms * 0.02))
    if abs(duration - expected_duration_ms) > tolerance:
        raise VerificationError(
            "concatenated duration differs substantially from normalized segments",
            code="audioexport.audiobook_concat_mismatch",
        )
    return duration


def _auto_sidecars(directory: Path) -> tuple[Path | None, Path | None]:
    cover = next(
        (
            directory / name
            for name in ("cover.jpg", "cover.jpeg", "cover.png")
            if (directory / name).is_file()
        ),
        None,
    )
    chapters = directory / "chapters.txt"
    return cover, chapters if chapters.is_file() else None


def _read_explicit_chapters(
    chapters: Sequence[Chapter] | None, chapters_file: Path | None
) -> tuple[Chapter, ...] | None:
    if chapters is not None:
        return _validate_chapter_rows(chapters)
    if chapters_file is None:
        return None
    loaded = (
        load_chapters_txt(chapters_file)
        if chapters_file.suffix.casefold() == ".txt"
        else load_chapters(chapters_file)
    )
    return _validate_chapter_rows(loaded)


def _build_unlocked(
    inputs: Sequence[Path | str | AudiobookTrack] | Path | str | AudiobookTrack,
    output: Path,
    *,
    metadata: AudiobookMetadata | Mapping[str, str] | None,
    cover: Path | str | None,
    chapters: Sequence[Chapter] | None,
    chapters_file: Path | str | None,
    bitrate: str | int | None,
    sample_rate: int | None,
    channels: int | None,
    force: bool,
    ffmpeg: Path | str | None,
    ffprobe: Path | str | None,
    discover_sidecars: bool,
    use_filenames_as_chapters: bool,
) -> AudiobookResult:
    destination = output.expanduser().absolute()
    if destination.suffix.casefold() != ".m4b":
        raise InvalidExportError(
            "audiobook output must use the .m4b extension",
            code="audioexport.audiobook_output_invalid",
        )
    if chapters is not None and chapters_file is not None:
        raise InvalidExportError(
            "use chapters or chapters_file, not both",
            code="audioexport.audiobook_chapters_conflict",
        )

    requested_inputs = (
        (inputs,) if isinstance(inputs, (Path, str, AudiobookTrack)) else tuple(inputs)
    )
    track_paths, explicit_titles, single_directory = _resolve_tracks(requested_inputs)
    destination_real = destination.resolve()
    for source in track_paths:
        if source.resolve() == destination_real:
            raise InvalidExportError(
                "input and output must be different files", code="audioexport.same_path"
            )

    discovered_resources: list[str] = []
    selected_cover = Path(cover).expanduser() if cover is not None else None
    selected_chapters_file = Path(chapters_file).expanduser() if chapters_file is not None else None
    if discover_sidecars and single_directory:
        directory_input = requested_inputs[0]
        directory_path = (
            directory_input.path if isinstance(directory_input, AudiobookTrack) else directory_input
        )
        directory = Path(directory_path).expanduser().resolve()
        auto_cover, auto_chapters = _auto_sidecars(directory)
        if selected_cover is None and auto_cover is not None:
            selected_cover = auto_cover
            discovered_resources.append(auto_cover.name)
        if chapters is None and selected_chapters_file is None and auto_chapters is not None:
            selected_chapters_file = auto_chapters
            discovered_resources.append(auto_chapters.name)

    cover_path: Path | None = None
    cover_codec: str | None = None
    if selected_cover is not None:
        try:
            cover_path = selected_cover.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise InvalidExportError(
                f"cover image does not exist: {selected_cover}", code="audioexport.cover_invalid"
            ) from exc
        if not cover_path.is_file() or cover_path == destination_real:
            raise InvalidExportError("cover image is invalid", code="audioexport.cover_invalid")
        cover_codec = _cover_kind(cover_path)

    chapters_path: Path | None = None
    chapters_file_sha: str | None = None
    if selected_chapters_file is not None:
        try:
            chapters_path = selected_chapters_file.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise InvalidExportError(
                f"chapter file does not exist: {selected_chapters_file}",
                code="audioexport.chapters_invalid",
            ) from exc
        if not chapters_path.is_file() or chapters_path == destination_real:
            raise InvalidExportError("chapter file is invalid", code="audioexport.chapters_invalid")
        chapters_file_sha = sha256_file(chapters_path)

    explicit_chapters = _read_explicit_chapters(chapters, chapters_path)
    tags = audiobook_tags(metadata)
    if sample_rate is not None and (
        isinstance(sample_rate, bool)
        or not isinstance(sample_rate, int)
        or not 8000 <= sample_rate <= 384000
    ):
        raise InvalidExportError(
            "sample rate must be an integer between 8000 and 384000",
            code="audioexport.audiobook_input_invalid",
        )
    if channels is not None and (
        isinstance(channels, bool) or not isinstance(channels, int) or channels not in (1, 2)
    ):
        raise InvalidExportError(
            "audiobook channels must be 1 or 2", code="audioexport.audiobook_input_invalid"
        )
    rate = normalize_bitrate(bitrate, "m4b")
    assert rate is not None

    if destination.is_symlink():
        raise InvalidExportError(
            "refusing to replace symlink output", code="audioexport.output_invalid"
        )
    sidecar = _manifest_path(destination)
    if sidecar.is_symlink() or (sidecar.exists() and not sidecar.is_file()):
        raise InvalidExportError(
            "manifest sidecar must be a regular non-symlink file", code="audioexport.output_invalid"
        )
    if destination.exists() and not destination.is_file():
        raise InvalidExportError("output must be a regular file", code="audioexport.output_invalid")

    ffmpeg_exe = executable("ffmpeg", ffmpeg)
    ffprobe_exe = executable("ffprobe", ffprobe)
    ffmpeg_version = version(ffmpeg_exe)
    ffprobe_version = version(ffprobe_exe)
    encoder = encoder_for_format("m4b", ffmpeg_exe)

    source_characteristics: list[tuple[int, int]] = []
    source_hashes: list[str] = []
    source_titles: list[str | None] = []
    for source in track_paths:
        info = probe(source, ffprobe_exe)
        _, source_rate, source_channels, _ = _audio_properties(info, label=f"source {source.name}")
        source_characteristics.append((source_rate, source_channels))
        source_hashes.append(sha256_file(source))
        source_titles.append(source_title(info))

    target_rate = sample_rate if sample_rate is not None else source_characteristics[0][0]
    target_channels = channels if channels is not None else source_characteristics[0][1]
    if not 8000 <= target_rate <= 384000:
        raise InvalidExportError(
            "selected sample rate must be between 8000 and 384000",
            code="audioexport.audiobook_input_invalid",
        )
    if target_channels not in (1, 2):
        raise InvalidExportError(
            "selected channel count must be 1 or 2", code="audioexport.audiobook_input_invalid"
        )

    effective_titles = tuple(
        explicit or (tag_title if not use_filenames_as_chapters else None) or source.stem
        for source, explicit, tag_title in zip(track_paths, explicit_titles, source_titles)
    )
    if explicit_chapters is None:
        chapter_request: Any = {
            "mode": "tracks",
            "title_source": "filename" if use_filenames_as_chapters else "tag",
            "titles": list(effective_titles),
        }
    else:
        chapter_request = [asdict(row) for row in explicit_chapters]

    resource_options = {
        "cover_sha256": sha256_file(cover_path) if cover_path is not None else None,
        "chapters_file_sha256": chapters_file_sha,
        "chapters_file_name": chapters_path.name if chapters_path is not None else None,
        "discovered_resources": sorted(discovered_resources),
    }
    producer_version = _producer_version()
    source_records = [
        {
            "name": path.name,
            "sha256": digest,
            "explicit_title": explicit_titles[index],
            "source_title": source_titles[index],
            "chapter_title": effective_titles[index],
        }
        for index, (path, digest) in enumerate(zip(track_paths, source_hashes))
    ]
    options = {
        "format": "m4b",
        "codec": "aac",
        "encoder": encoder,
        "bitrate": rate,
        "sample_rate": target_rate,
        "channels": target_channels,
        "metadata": tags,
        "resources": resource_options,
        "chapter_request": chapter_request,
    }
    request_id = _canonical_hash(
        {
            "schema": "audioexport.audiobook-request.v1",
            "sources": source_records,
            "options": options,
            "ffmpeg": ffmpeg_version,
            "ffprobe": ffprobe_version,
            "producer_version": producer_version,
        }
    )

    old = _load_audiobook_manifest(sidecar)
    intact = (
        destination.is_file()
        and old is not None
        and old.get("output_sha256") == sha256_file(destination)
    )
    if destination.exists() and not force and not intact:
        raise FileExistsError(
            f"existing output is not an intact audiobook artifact: {destination}; use --force"
        )
    if not force and intact and old is not None and old.get("request_id") == request_id:
        verified = old.get("verified", {})
        return AudiobookResult(
            destination,
            str(old.get("export_id", "")),
            str(old["output_sha256"]),
            sidecar,
            True,
            int(verified.get("chapters", 0)),
            int(verified.get("track_count", len(track_paths))),
            int(verified.get("duration_ms", 0)),
            str(verified.get("codec", "aac")),
            tuple(discovered_resources),
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(
        tempfile.mkdtemp(prefix=f".{destination.stem}.audioexport-", dir=destination.parent)
    )
    final_temp: Path | None = None
    try:
        segments: list[Path] = []
        normalized_durations: list[int] = []
        for index, source in enumerate(track_paths, 1):
            segment = work_dir / f"segment-{index:04d}.m4a"
            duration = _normalize_segment(
                source,
                segment,
                ffmpeg=ffmpeg_exe,
                ffprobe=ffprobe_exe,
                bitrate=rate,
                sample_rate=target_rate,
                channels=target_channels,
                encoder=encoder,
            )
            segments.append(segment)
            normalized_durations.append(duration)

        concat_audio = work_dir / "concatenated.m4a"
        concat_duration = _concat_segments(
            segments,
            concat_audio,
            ffmpeg=ffmpeg_exe,
            ffprobe=ffprobe_exe,
            sample_rate=target_rate,
            channels=target_channels,
            expected_duration_ms=sum(normalized_durations),
        )
        if explicit_chapters is None:
            requested_chapters = build_track_chapters(
                tuple(zip(effective_titles, normalized_durations))
            )
        else:
            requested_chapters = explicit_chapters
        resolved_chapters = normalize_chapters(requested_chapters, concat_duration)

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{destination.stem}.", suffix=".m4b", dir=destination.parent
        )
        os.close(fd)
        final_temp = Path(temp_name)
        final_temp.unlink()
        chapter_metadata: Path | None = None
        if resolved_chapters:
            chapter_metadata = work_dir / "chapters.ffmeta"
            chapter_metadata.write_text(ffmetadata_text(resolved_chapters), encoding="utf-8")

        command = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(concat_audio),
        ]
        if chapter_metadata is not None:
            command.extend(["-f", "ffmetadata", "-i", str(chapter_metadata)])
        cover_index = 2 if chapter_metadata is not None else 1
        if cover_path is not None:
            command.extend(["-i", str(cover_path)])
        command.extend(["-map", "0:a:0", "-map_metadata", "0"])
        if chapter_metadata is not None:
            command.extend(["-map_chapters", "1"])
        else:
            command.extend(["-map_chapters", "-1"])
        if cover_path is not None:
            command.extend(
                [
                    "-map",
                    f"{cover_index}:v:0",
                    "-c:v",
                    cover_codec or "mjpeg",
                    "-disposition:v:0",
                    "attached_pic",
                ]
            )
        command.extend(["-c:a", "copy"])
        for key, value in tags.items():
            command.extend(["-metadata", f"{key}={value}"])
        command.extend(["-movflags", "+faststart", "-f", "ipod", str(final_temp)])
        try:
            run(command)
        except EncodingError as exc:
            raise EncodingError(
                f"could not mux final audiobook: {exc}", code="audioexport.audiobook_mux_failed"
            ) from exc

        final_info = _verify_output(
            final_temp,
            fmt="m4b",
            chapters=resolved_chapters,
            cover=cover_path,
            metadata=tags,
            ffprobe=ffprobe_exe,
            source_duration_ms=concat_duration,
        )
        _, final_rate, final_channels, final_duration = _audio_properties(
            final_info, label="final audiobook"
        )
        if final_rate != target_rate or final_channels != target_channels:
            raise VerificationError(
                "final audiobook does not match the selected AAC settings",
                code="audioexport.audiobook_output_mismatch",
            )

        export_id = _canonical_hash(
            {
                "schema": "audioexport.audiobook-identity.v1",
                "request_id": request_id,
                "chapters": [asdict(row) for row in resolved_chapters],
                "normalized_segment_durations_ms": normalized_durations,
            }
        )
        output_sha = sha256_file(final_temp)
        manifest = {
            "schema": "audioexport.audiobook-manifest.v1",
            "schema_version": 1,
            "operation": "audiobook",
            "producer": "audioexport",
            "producer_version": producer_version,
            "export_id": export_id,
            "request_id": request_id,
            "output": destination.name,
            "output_sha256": output_sha,
            "sources": source_records,
            "options": {
                **options,
                "chapters": [asdict(row) for row in resolved_chapters],
            },
            "resources": {
                "cover": cover_path.name if cover_path is not None else None,
                "chapters_file": chapters_path.name if chapters_path is not None else None,
                "auto_discovered": sorted(discovered_resources),
            },
            "tools": {"ffmpeg": ffmpeg_version, "ffprobe": ffprobe_version},
            "verified": {
                "codec": EXPECTED_CODECS["m4b"],
                "duration_ms": final_duration,
                "channels": final_channels,
                "sample_rate": final_rate,
                "chapters": len(final_info.get("chapters", [])),
                "track_count": len(track_paths),
            },
        }
        _commit_export(final_temp, destination, sidecar, manifest)
        return AudiobookResult(
            destination,
            export_id,
            output_sha,
            sidecar,
            False,
            len(resolved_chapters),
            len(track_paths),
            final_duration,
            EXPECTED_CODECS["m4b"],
            tuple(discovered_resources),
        )
    finally:
        if final_temp is not None:
            final_temp.unlink(missing_ok=True)
        shutil.rmtree(work_dir, ignore_errors=True)


def build_audiobook(
    inputs: Sequence[Path | str | AudiobookTrack] | Path | str | AudiobookTrack,
    output: Path | str,
    *,
    metadata: AudiobookMetadata | Mapping[str, str] | None = None,
    cover: Path | str | None = None,
    chapters: Sequence[Chapter] | None = None,
    chapters_file: Path | str | None = None,
    bitrate: str | int | None = None,
    sample_rate: int | None = None,
    channels: int | None = None,
    force: bool = False,
    ffmpeg: Path | str | None = None,
    ffprobe: Path | str | None = None,
    discover_sidecars: bool = False,
    use_filenames_as_chapters: bool = False,
) -> AudiobookResult:
    """Assemble ordered source tracks into a verified, transactionally committed M4B."""
    destination = Path(output).expanduser().absolute()
    with _output_lock(destination):
        return _build_unlocked(
            inputs,
            destination,
            metadata=metadata,
            cover=cover,
            chapters=chapters,
            chapters_file=chapters_file,
            bitrate=bitrate,
            sample_rate=sample_rate,
            channels=channels,
            force=force,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            discover_sidecars=discover_sidecars,
            use_filenames_as_chapters=use_filenames_as_chapters,
        )
