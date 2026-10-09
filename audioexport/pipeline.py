"""Standalone encoding, identity, atomic writing and exported-artifact cache."""

from __future__ import annotations

import getpass
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Any

from .chapters import Chapter, ffmetadata_text, load_chapters, normalize_chapters
from .errors import EncodingError, InvalidExportError, VerificationError
from .fftools import doctor as fftool_doctor
from .fftools import encoder_for_format, executable, probe, run, version
from .formats import EXPECTED_CODECS, FORMATS, normalize_bitrate, normalize_format
from .profile import ExportProfile, ResolvedOutput, load_profile, resolve_output


@dataclass(frozen=True, slots=True)
class ExportResult:
    path: Path
    format: str
    codec: str
    export_id: str
    output_sha256: str
    manifest_path: Path
    reused: bool
    chapter_count: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        data["manifest_path"] = str(self.manifest_path)
        return data


def _producer_version() -> str:
    try:
        return distribution_version("audioexport")
    except PackageNotFoundError:
        return "0+uninstalled"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _duration_ms(info: Mapping[str, Any]) -> int:
    try:
        streams = info["streams"]
        stream = next(row for row in streams if row.get("codec_type") == "audio")
        raw = stream.get("duration") or info["format"].get("duration")
        seconds = float(raw)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("non-positive duration")
        return round(seconds * 1000)
    except (ValueError, TypeError, KeyError, StopIteration) as exc:
        raise InvalidExportError(
            "source has no valid positive duration", code="audioexport.duration_invalid"
        ) from exc


def _validate_metadata(metadata: Mapping[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in (metadata or {}).items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key)
            or not isinstance(value, str)
        ):
            raise InvalidExportError(
                "metadata must be string key/value pairs with simple keys",
                code="audioexport.metadata_invalid",
            )
        result[key.lower()] = value
    return dict(sorted(result.items()))


def _cover_kind(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            signature = stream.read(8)
    except OSError as exc:
        raise InvalidExportError(
            f"cover not readable: {path}", code="audioexport.cover_invalid"
        ) from exc
    if signature.startswith(b"\xff\xd8\xff"):
        return "mjpeg"
    if signature == b"\x89PNG\r\n\x1a\n":
        return "png"
    raise InvalidExportError("cover must be JPEG or PNG", code="audioexport.cover_invalid")


def _manifest_path(output: Path) -> Path:
    return output.with_name(output.name + ".audioexport.json")


def _output_lock_path(output: Path) -> Path:
    owner = str(os.getuid()) if hasattr(os, "getuid") else getpass.getuser()
    owner_key = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:16]
    root = Path(tempfile.gettempdir()) / f"audioexport-locks-{owner_key}"
    if root.is_symlink():
        raise EncodingError(
            "output lock directory must not be a symlink", code="audioexport.output_lock_failed"
        )
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise EncodingError(
            "could not create output lock directory", code="audioexport.output_lock_failed"
        ) from exc
    if root.is_symlink() or not root.is_dir():
        raise EncodingError(
            "output lock directory is invalid", code="audioexport.output_lock_failed"
        )
    key = os.path.normcase(str(output.resolve()))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return root / f"{digest}.lock"


@contextmanager
def _output_lock(output: Path) -> Iterator[None]:
    lock_path = _output_lock_path(output)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise EncodingError(
            f"another audioexport operation holds {lock_path}; verify no writer is active "
            "before removing a stale lock",
            code="audioexport.output_busy",
        ) from exc
    except OSError as exc:
        raise EncodingError(
            "could not acquire output lock", code="audioexport.output_lock_failed"
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "output": str(output.absolute())}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _write_json_temp(path: Path, payload: Mapping[str, Any]) -> Path:
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _backup_existing(path: Path) -> Path | None:
    if path.is_symlink():
        raise InvalidExportError(
            f"refusing to replace symlink artifact: {path}", code="audioexport.output_invalid"
        )
    if not path.exists():
        return None
    if not path.is_file():
        raise InvalidExportError(
            f"artifact must be a regular file: {path}", code="audioexport.output_invalid"
        )
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".backup", dir=path.parent)
    os.close(fd)
    backup = Path(name)
    backup.unlink()
    try:
        link_file = getattr(os, "link", None)
        if link_file is None:
            shutil.copy2(path, backup)
        else:
            try:
                link_file(path, backup)
            except (OSError, NotImplementedError):
                shutil.copy2(path, backup)
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    return backup


def _commit_export(
    audio_temp: Path,
    output: Path,
    sidecar: Path,
    manifest: Mapping[str, Any],
) -> None:
    try:
        manifest_temp = _write_json_temp(sidecar, manifest)
    except OSError as exc:
        raise EncodingError(
            "could not stage export manifest", code="audioexport.manifest_write_failed"
        ) from exc
    audio_backup: Path | None = None
    manifest_backup: Path | None = None
    preserved_backups: list[Path] = []
    try:
        audio_backup = _backup_existing(output)
        manifest_backup = _backup_existing(sidecar)
        try:
            os.replace(audio_temp, output)
        except OSError as exc:
            raise EncodingError(
                "could not commit encoded audio", code="audioexport.output_commit_failed"
            ) from exc
        try:
            os.replace(manifest_temp, sidecar)
        except OSError as exc:
            rollback_errors = []
            for target, backup in ((output, audio_backup), (sidecar, manifest_backup)):
                try:
                    if backup is None:
                        target.unlink(missing_ok=True)
                    else:
                        os.replace(backup, target)
                except OSError as rollback_exc:
                    rollback_errors.append(rollback_exc)
                    if backup is not None and backup.exists():
                        preserved_backups.append(backup)
            if rollback_errors:
                raise EncodingError(
                    "manifest commit failed and automatic recovery was incomplete; "
                    f"preserved backups: {', '.join(map(str, preserved_backups)) or 'none'}. "
                    f"Inspect {output} and {sidecar} before retrying",
                    code="audioexport.commit_recovery_failed",
                ) from exc
            raise EncodingError(
                "manifest commit failed; the previous export was restored",
                code="audioexport.manifest_commit_failed",
            ) from exc
    finally:
        manifest_temp.unlink(missing_ok=True)
        if audio_backup is not None and audio_backup not in preserved_backups:
            audio_backup.unlink(missing_ok=True)
        if manifest_backup is not None and manifest_backup not in preserved_backups:
            manifest_backup.unlink(missing_ok=True)


def _load_manifest(path: Path) -> dict[str, Any] | None:
    if path.is_symlink():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (
            data
            if isinstance(data, dict) and data.get("schema") == "audioexport.manifest.v1"
            else None
        )
    except (OSError, ValueError):
        return None


def _verify_output(
    path: Path,
    *,
    fmt: str,
    chapters: Sequence[Chapter],
    cover: Path | None,
    metadata: Mapping[str, str],
    ffprobe: str,
    source_duration_ms: int,
) -> dict[str, Any]:
    info = probe(path, ffprobe)
    streams = info["streams"]
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    if len(audio_streams) != 1:
        raise VerificationError(
            "encoded output must contain exactly one audio stream",
            code="audioexport.stream_mismatch",
        )
    audio = audio_streams[0]
    if audio.get("codec_name") != EXPECTED_CODECS[fmt]:
        raise VerificationError(
            f"expected {EXPECTED_CODECS[fmt]} output, found {audio.get('codec_name')}",
            code="audioexport.codec_mismatch",
        )
    try:
        channels = int(audio["channels"])
        sample_rate = int(audio["sample_rate"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VerificationError(
            "encoded output lacks valid channels/sample rate", code="audioexport.probe_invalid"
        ) from exc
    if channels <= 0 or sample_rate <= 0:
        raise VerificationError(
            "encoded output lacks valid channels/sample rate", code="audioexport.probe_invalid"
        )
    output_duration = _duration_ms(info)
    if abs(output_duration - source_duration_ms) > max(200, round(source_duration_ms * 0.02)):
        raise VerificationError(
            "encoded duration differs substantially from source",
            code="audioexport.duration_mismatch",
        )
    format_tags = info["format"].get("tags", {})
    normalized_tags = {key.lower(): value for key, value in format_tags.items()}
    for stream in audio_streams:
        normalized_tags.update(
            {key.lower(): value for key, value in stream.get("tags", {}).items()}
        )
    for key, expected_value in metadata.items():
        candidates = ("artist", "author") if key.lower() in {"artist", "author"} else (key.lower(),)
        if not any(normalized_tags.get(candidate) == expected_value for candidate in candidates):
            raise VerificationError(
                f"encoded metadata tag {key!r} does not match requested value",
                code="audioexport.metadata_mismatch",
            )
    emitted = info.get("chapters", [])
    if chapters:
        if len(emitted) != len(chapters):
            raise VerificationError(
                "encoded chapter count mismatch", code="audioexport.chapter_mismatch"
            )
        for actual, expected_chapter in zip(emitted, chapters):
            try:
                start_ms = float(actual["start_time"]) * 1000
                end_ms = float(actual["end_time"]) * 1000
            except (KeyError, TypeError, ValueError) as exc:
                raise VerificationError(
                    "encoded chapter timing is invalid", code="audioexport.chapter_mismatch"
                ) from exc
            if abs(start_ms - expected_chapter.start_ms) > 30:
                raise VerificationError(
                    "encoded chapter start mismatch", code="audioexport.chapter_mismatch"
                )
            if expected_chapter.end_ms is None or abs(end_ms - expected_chapter.end_ms) > 35:
                raise VerificationError(
                    "encoded chapter end mismatch", code="audioexport.chapter_mismatch"
                )
            actual_tags = {key.lower(): value for key, value in actual.get("tags", {}).items()}
            if actual_tags.get("title") != expected_chapter.title:
                raise VerificationError(
                    "encoded chapter title mismatch", code="audioexport.chapter_mismatch"
                )
    pictures = [
        item
        for item in streams
        if item.get("codec_type") == "video"
        and item.get("disposition", {}).get("attached_pic") == 1
    ]
    if cover is not None:
        if len(pictures) != 1 or pictures[0].get("codec_name") != _cover_kind(cover):
            raise VerificationError(
                "encoded cover art is missing or has the wrong codec",
                code="audioexport.cover_mismatch",
            )
    elif pictures:
        raise VerificationError(
            "encoded output contains unexpected cover art", code="audioexport.cover_mismatch"
        )
    return info


def _encode_unlocked(
    source: Path | str,
    output: Path | str,
    *,
    format: str | None = None,
    bitrate: str | int | None = None,
    metadata: Mapping[str, str] | None = None,
    cover: Path | str | None = None,
    timeline: Path | str | None = None,
    chapters: Sequence[Chapter] | None = None,
    force: bool = False,
    ffmpeg: Path | str | None = None,
    ffprobe: Path | str | None = None,
) -> ExportResult:
    """Encode media to an audio format; verify using FFprobe and save sidecar manifest.

    Reuses a previously produced unmodified output with matching source/options/tools.
    Refuses to overwrite an unknown or manually changed output unless force=True.
    """
    src = Path(source).expanduser().absolute()
    dest = Path(output).expanduser().absolute()
    if not src.is_file():
        raise InvalidExportError(f"source audio missing: {src}", code="audioexport.source_missing")
    if src.resolve() == dest.resolve():
        raise InvalidExportError(
            "input and output must be different files", code="audioexport.same_path"
        )
    inferred = dest.suffix.lower().removeprefix(".")
    fmt = normalize_format(format or inferred)
    if inferred != fmt:
        raise InvalidExportError(
            f"format {fmt!r} conflicts with output extension {dest.suffix!r}",
            code="audioexport.format_mismatch",
        )
    rate = normalize_bitrate(bitrate, fmt)
    tags = _validate_metadata(metadata)
    if timeline is not None and chapters is not None:
        raise InvalidExportError(
            "use timeline or chapters, not both", code="audioexport.chapters_invalid"
        )
    if timeline is not None:
        chapters = load_chapters(timeline)
    chapters = tuple(chapters or ())
    if chapters and fmt not in {"m4b", "m4a"}:
        raise InvalidExportError(
            "chapter marks require M4B or M4A", code="audioexport.chapters_unsupported"
        )
    artwork = Path(cover).expanduser().absolute() if cover is not None else None
    cover_codec = None
    if artwork is not None:
        if fmt not in {"mp3", "m4a", "m4b"}:
            raise InvalidExportError(
                "cover art requires MP3/M4A/M4B", code="audioexport.cover_unsupported"
            )
        cover_codec = _cover_kind(artwork)
    ffmpeg_exe = executable("ffmpeg", ffmpeg)
    ffprobe_exe = executable("ffprobe", ffprobe)
    ffmpeg_version = version(ffmpeg_exe)
    ffprobe_version = version(ffprobe_exe)
    source_info = probe(src, ffprobe_exe)
    duration_ms = _duration_ms(source_info)
    resolved_chapters = normalize_chapters(chapters, duration_ms)
    source_sha = sha256_file(src)
    cover_sha = sha256_file(artwork) if artwork is not None else None
    timeline_sha = sha256_file(Path(timeline)) if timeline is not None else None
    spec = FORMATS[fmt]
    codec = encoder_for_format(fmt, ffmpeg_exe)
    options = {
        "format": fmt,
        "codec": codec,
        "bitrate": rate,
        "metadata": tags,
        "cover_sha256": cover_sha,
        "timeline_sha256": timeline_sha,
        "chapters": [asdict(row) for row in resolved_chapters],
    }
    producer_version = _producer_version()
    identity = _canonical_hash(
        {
            "schema": "audioexport.identity.v1",
            "source_sha256": source_sha,
            "options": options,
            "ffmpeg": ffmpeg_version,
            "ffprobe": ffprobe_version,
            "producer_version": producer_version,
        }
    )
    sidecar = _manifest_path(dest)
    if dest.is_symlink():
        raise InvalidExportError(
            "refusing to replace symlink output", code="audioexport.output_invalid"
        )
    if sidecar.is_symlink() or (sidecar.exists() and not sidecar.is_file()):
        raise InvalidExportError(
            "manifest sidecar must be a regular non-symlink file",
            code="audioexport.output_invalid",
        )
    if dest.exists() and not dest.is_file():
        raise InvalidExportError("output must be a regular file", code="audioexport.output_invalid")
    old = _load_manifest(sidecar)
    intact = dest.is_file() and old is not None and old.get("output_sha256") == sha256_file(dest)
    if dest.exists() and not force and not intact:
        raise FileExistsError(
            f"existing output is not an intact audioexport artifact: {dest}; use --force"
        )
    if not force and intact and old is not None and old.get("export_id") == identity:
        return ExportResult(
            dest,
            fmt,
            EXPECTED_CODECS[fmt],
            identity,
            old["output_sha256"],
            sidecar,
            True,
            len(resolved_chapters),
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{dest.stem}.", suffix=spec.extension, dir=dest.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    temporary.unlink()  # let ffmpeg create the file; avoids a preexisting input/output.
    meta_path: Path | None = None
    try:
        command = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(src),
        ]
        if resolved_chapters:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                suffix=".ffmeta",
                prefix=".audioexport-",
                dir=dest.parent,
                delete=False,
            ) as stream:
                meta_path = Path(stream.name)
                stream.write(ffmetadata_text(resolved_chapters))
            command.extend(["-f", "ffmetadata", "-i", str(meta_path)])
        cover_index = 2 if resolved_chapters else 1
        if artwork is not None:
            command.extend(["-i", str(artwork)])
        command.extend(["-map", "0:a:0", "-map_metadata", "0"])
        if resolved_chapters:
            command.extend(["-map_chapters", "1"])
        else:
            command.extend(["-map_chapters", "-1"])
        if artwork is not None:
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
            if fmt == "mp3":
                command.extend(
                    ["-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
                )
        command.extend(["-c:a", codec])
        if rate is not None:
            command.extend(["-b:a", rate])
        elif fmt == "ogg":
            command.extend(["-qscale:a", "4"])
        for key, value in tags.items():
            command.extend(["-metadata", f"{key}={value}"])
        command.extend([*spec.extra_args, "-f", spec.muxer, str(temporary)])
        run(command)
        info = _verify_output(
            temporary,
            fmt=fmt,
            chapters=resolved_chapters,
            cover=artwork,
            metadata=tags,
            ffprobe=ffprobe_exe,
            source_duration_ms=duration_ms,
        )
        output_sha = sha256_file(temporary)
        payload = {
            "schema": "audioexport.manifest.v1",
            "schema_version": 1,
            "producer": "audioexport",
            "producer_version": producer_version,
            "export_id": identity,
            "source_sha256": source_sha,
            "output": dest.name,
            "output_sha256": output_sha,
            "options": options,
            "tools": {"ffmpeg": ffmpeg_version, "ffprobe": ffprobe_version},
            "verified": {
                "codec": EXPECTED_CODECS[fmt],
                "duration_ms": _duration_ms(info),
                "channels": next(
                    x["channels"] for x in info["streams"] if x.get("codec_type") == "audio"
                ),
                "chapters": len(info.get("chapters", [])),
            },
        }
        _commit_export(temporary, dest, sidecar, payload)
        return ExportResult(
            dest,
            fmt,
            EXPECTED_CODECS[fmt],
            identity,
            output_sha,
            sidecar,
            False,
            len(resolved_chapters),
        )
    finally:
        temporary.unlink(missing_ok=True)
        if meta_path is not None:
            meta_path.unlink(missing_ok=True)


def encode(
    source: Path | str,
    output: Path | str,
    *,
    format: str | None = None,
    bitrate: str | int | None = None,
    metadata: Mapping[str, str] | None = None,
    cover: Path | str | None = None,
    timeline: Path | str | None = None,
    chapters: Sequence[Chapter] | None = None,
    force: bool = False,
    ffmpeg: Path | str | None = None,
    ffprobe: Path | str | None = None,
) -> ExportResult:
    """Encode under a per-output inter-process lock and verify the final artifacts."""
    destination = Path(output).expanduser().absolute()
    with _output_lock(destination):
        return _encode_unlocked(
            source,
            output,
            format=format,
            bitrate=bitrate,
            metadata=metadata,
            cover=cover,
            timeline=timeline,
            chapters=chapters,
            force=force,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )


def _preflight_profile(
    profile: ExportProfile,
    source: Path | str,
    out_dir: Path,
    *,
    ffprobe: Path | str | None,
    ffmpeg: Path | str | None,
) -> tuple[tuple[ResolvedOutput, Path], ...]:
    """Resolve and validate every profile output before any encoder can write."""
    src = Path(source).expanduser().absolute()
    if not src.is_file():
        raise InvalidExportError(f"source audio missing: {src}", code="audioexport.source_missing")
    source_real = src.resolve()
    seen_names: set[str] = set()
    planned: list[tuple[ResolvedOutput, Path]] = []
    checked_timeline: Path | None = None
    for spec in profile.outputs:
        resolved = resolve_output(profile, spec, src.stem)
        key = resolved.filename.casefold()
        if key in seen_names:
            raise InvalidExportError(
                "duplicate profile output filenames", code="audioexport.profile_invalid"
            )
        seen_names.add(key)
        destination = out_dir / resolved.filename
        if destination.resolve() == source_real:
            raise InvalidExportError(
                "input and output must be different files", code="audioexport.same_path"
            )
        if resolved.cover is not None:
            _cover_kind(resolved.cover)
        if resolved.timeline is not None and resolved.timeline != checked_timeline:
            chapters = load_chapters(resolved.timeline)
            source_info = probe(src, ffprobe)
            normalize_chapters(chapters, _duration_ms(source_info))
            checked_timeline = resolved.timeline
        planned.append((resolved, destination))
    formats = tuple(spec.format for spec in profile.outputs)
    capabilities = fftool_doctor(ffmpeg=ffmpeg, ffprobe=ffprobe, requested_formats=formats)
    for name, override in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        if not capabilities["tools"][name]["available"]:
            executable(name, override)
            raise EncodingError(
                f"{name} is not ready: {capabilities['tools'][name].get('error', 'unavailable')}",
                code="audioexport.tool_failed",
            )
    if not capabilities["requested_formats_ready"]:
        unavailable = [
            f"{fmt}: {capabilities['formats'][fmt].get('reason', 'encoder unavailable')}"
            for fmt in formats
            if capabilities["formats"][fmt]["available"] is not True
        ]
        raise EncodingError(
            "required FFmpeg encoders unavailable: " + "; ".join(unavailable),
            code="audioexport.encoder_unavailable",
        )
    return tuple(planned)


def run_profile(
    source: Path | str,
    profile: Path | str,
    out_dir: Path | str,
    *,
    force: bool = False,
    ffmpeg: Path | str | None = None,
    ffprobe: Path | str | None = None,
) -> tuple[ExportResult, ...]:
    """Batch-export an ordinary audio file using a portable TOML export profile."""
    configuration = load_profile(profile)
    root = Path(out_dir).expanduser().absolute()
    planned = _preflight_profile(configuration, source, root, ffprobe=ffprobe, ffmpeg=ffmpeg)
    outputs = []
    for item, destination in planned:
        result = encode(
            source,
            destination,
            format=item.format,
            bitrate=item.bitrate,
            metadata=configuration.metadata,
            cover=item.cover,
            timeline=item.timeline,
            force=force,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
        outputs.append(result)
    return tuple(outputs)
