"""Safe adapter from a composed ``.ssmdbook`` workspace to audiobook export.

The AudioCompose report is treated as a filesystem hand-off contract. AudioExport
validates selected WAV files and then delegates assembly to ``build_audiobook``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import sys
import wave
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from .audiobook import AudiobookResult, AudiobookTrack, build_audiobook
from .audiobook_profile import AudiobookProfile, load_audiobook_profile
from .errors import InvalidExportError, VerificationError
from .metadata import AudiobookMetadata, audiobook_tags

_DURATION_TOLERANCE_SECONDS = 0.01
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True, slots=True)
class ComposedChapter:
    """A manifest chapter joined to a verified AudioCompose output."""

    id: str
    source_number: int
    title: str
    path: Path
    sha256: str
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float
    render_plan_id: str
    manifest_id: str


@dataclass(frozen=True, slots=True)
class BookExportContext:
    """Resolved inputs for exporting selected chapters from one book variant."""

    workspace: Path
    variant: str
    manifest_path: Path
    compose_report_path: Path
    chapters: tuple[ComposedChapter, ...]
    default_output: Path


@dataclass(frozen=True, slots=True)
class BookExportResult:
    """Result of a variant-aware book export."""

    workspace: Path
    variant: str
    selected_chapter_ids: tuple[str, ...]
    output: Path
    sidecar: Path
    audiobook: AudiobookResult


def _fail(message: str, *, code: str = "audioexport.book_invalid") -> InvalidExportError:
    return InvalidExportError(message, code=code)


def _duplicate_checked_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read_manifest(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise _fail(f"manifest.json must be a regular non-symlink file: {path}")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_duplicate_checked_object
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise _fail(
            f"cannot read book manifest: {exc}", code="audioexport.book_manifest_invalid"
        ) from exc
    if not isinstance(payload, Mapping):
        raise _fail("book manifest must be a JSON object", code="audioexport.book_manifest_invalid")
    return payload


def _nonempty_string(value: Any, field: str, *, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise _fail(f"{field} must be a non-empty string", code=code)
    return value.strip()


def _positive_int(value: Any, field: str, *, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _fail(f"{field} must be a positive integer", code=code)
    return value


def _manifest_chapters(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw_chapters = payload.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise _fail(
            "book manifest must contain a non-empty chapters array",
            code="audioexport.book_manifest_invalid",
        )
    chapters: list[dict[str, Any]] = []
    ids: set[str] = set()
    source_numbers: set[int] = set()
    for index, raw in enumerate(raw_chapters, 1):
        if not isinstance(raw, Mapping):
            raise _fail(
                f"manifest chapter {index} must be an object",
                code="audioexport.book_manifest_invalid",
            )
        chapter_id = _nonempty_string(
            raw.get("id"), f"manifest chapter {index} id", code="audioexport.book_manifest_invalid"
        )
        source_number = _positive_int(
            raw.get("source_number"),
            f"manifest chapter {index} source_number",
            code="audioexport.book_manifest_invalid",
        )
        title = _nonempty_string(
            raw.get("title"),
            f"manifest chapter {index} title",
            code="audioexport.book_manifest_invalid",
        )
        if chapter_id in ids:
            raise _fail(
                f"duplicate manifest chapter id: {chapter_id}",
                code="audioexport.book_manifest_invalid",
            )
        if source_number in source_numbers:
            raise _fail(
                f"duplicate manifest source number: {source_number}",
                code="audioexport.book_manifest_invalid",
            )
        ids.add(chapter_id)
        source_numbers.add(source_number)
        chapters.append({"id": chapter_id, "source_number": source_number, "title": title})
    return tuple(chapters)


def _validate_metadata(payload: Mapping[str, Any]) -> None:
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise _fail("manifest metadata must be an object", code="audioexport.book_manifest_invalid")
    if "title" in metadata and metadata["title"] is not None:
        _nonempty_string(
            metadata["title"], "manifest metadata.title", code="audioexport.book_manifest_invalid"
        )
    if "publisher" in metadata and metadata["publisher"] is not None:
        _nonempty_string(
            metadata["publisher"],
            "manifest metadata.publisher",
            code="audioexport.book_manifest_invalid",
        )
    if "language" in metadata and metadata["language"] is not None:
        _nonempty_string(
            metadata["language"],
            "manifest metadata.language",
            code="audioexport.book_manifest_invalid",
        )
    if "authors" in metadata and metadata["authors"] is not None:
        authors = metadata["authors"]
        if not isinstance(authors, list) or any(
            not isinstance(author, str) or not author.strip() for author in authors
        ):
            raise _fail(
                "manifest metadata.authors must be an array of non-empty strings",
                code="audioexport.book_manifest_invalid",
            )


def _safe_variant(variant: str) -> str:
    if not isinstance(variant, str) or not variant or variant in {".", ".."}:
        raise _fail(
            "variant must be a safe single directory component",
            code="audioexport.book_variant_invalid",
        )
    if (
        "/" in variant
        or "\\" in variant
        or "\x00" in variant
        or ":" in variant
        or PureWindowsPath(variant).is_absolute()
        or any(ord(character) < 32 for character in variant)
    ):
        raise _fail(
            "variant must be a safe single directory component",
            code="audioexport.book_variant_invalid",
        )
    return variant


def _workspace_for(book: str | Path) -> Path:
    if not isinstance(book, (str, Path)):
        raise _fail("book must be a path", code="audioexport.book_workspace_invalid")
    candidate = Path(book).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _fail(
            f"book path does not exist or cannot be resolved: {candidate}",
            code="audioexport.book_workspace_invalid",
        ) from exc
    start = resolved if resolved.is_dir() else resolved.parent
    for directory in (start, *start.parents):
        manifest = directory / "manifest.json"
        chapters = directory / "chapters"
        if manifest.exists() or chapters.exists():
            if (
                directory.is_dir()
                and not chapters.is_symlink()
                and chapters.is_dir()
                and not manifest.is_symlink()
                and manifest.is_file()
            ):
                return directory
            raise _fail(
                f"invalid .ssmdbook workspace at {directory}: expected manifest.json and chapters/",
                code="audioexport.book_workspace_invalid",
            )
    raise _fail(
        f"could not find a .ssmdbook workspace containing manifest.json and chapters/ from {candidate}",
        code="audioexport.book_workspace_invalid",
    )


def _report_root(workspace: Path, audiocompose_root: str | Path | None) -> Path:
    root = (
        Path(audiocompose_root).expanduser()
        if audiocompose_root is not None
        else workspace / "audiocompose"
    )
    if not root.is_absolute():
        root = workspace / root
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _fail(
            f"AudioCompose directory does not exist: {root}",
            code="audioexport.book_compose_report_missing",
        ) from exc
    if not resolved.is_dir() or root.is_symlink():
        raise _fail(
            f"AudioCompose root must be a non-symlink directory: {root}",
            code="audioexport.book_compose_report_invalid",
        )
    return resolved


def _safe_report_file(variant_dir: Path, reported: Any) -> Path:
    if not isinstance(reported, str) or not reported or "\x00" in reported or "\\" in reported:
        raise VerificationError(
            "compose report output must be a relative POSIX path",
            code="audioexport.book_compose_output_invalid",
        )
    relative = Path(reported)
    windows = PureWindowsPath(reported)
    if relative.is_absolute() or windows.is_absolute() or windows.drive:
        raise VerificationError(
            "compose report output must be relative to its variant directory",
            code="audioexport.book_compose_output_invalid",
        )
    if any(part in {"", ".", ".."} for part in reported.split("/")):
        raise VerificationError(
            "compose report output must not contain traversal components",
            code="audioexport.book_compose_output_invalid",
        )
    candidate = variant_dir / relative
    current = variant_dir
    try:
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise VerificationError(
                    "compose report output must not traverse symlinks",
                    code="audioexport.book_compose_output_invalid",
                )
        resolved = candidate.resolve(strict=True)
    except VerificationError:
        raise
    except (OSError, RuntimeError) as exc:
        raise VerificationError(
            f"compose report output does not resolve to a regular file: {reported}",
            code="audioexport.book_compose_output_invalid",
        ) from exc
    try:
        if os.path.commonpath((str(variant_dir), str(resolved))) != str(variant_dir):
            raise VerificationError(
                "compose report output escapes its variant directory",
                code="audioexport.book_compose_output_invalid",
            )
    except ValueError as exc:
        raise VerificationError(
            "compose report output is outside its variant directory",
            code="audioexport.book_compose_output_invalid",
        ) from exc
    try:
        mode = resolved.lstat().st_mode
    except OSError as exc:
        raise VerificationError(
            f"compose report output is unavailable: {reported}",
            code="audioexport.book_compose_output_invalid",
        ) from exc
    if not stat.S_ISREG(mode):
        raise VerificationError(
            f"compose report output must be a regular non-symlink file: {reported}",
            code="audioexport.book_compose_output_invalid",
        )
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise VerificationError(
            f"cannot hash composed WAV {path}", code="audioexport.book_compose_hash_mismatch"
        ) from exc
    return digest.hexdigest()


def _verify_wav(path: Path, item: Mapping[str, Any]) -> tuple[int, int, int, float]:
    expected_rate = _positive_int(
        item.get("sample_rate"),
        "compose item sample_rate",
        code="audioexport.book_compose_report_invalid",
    )
    expected_channels = _positive_int(
        item.get("channels"),
        "compose item channels",
        code="audioexport.book_compose_report_invalid",
    )
    expected_frames = _positive_int(
        item.get("frames"), "compose item frames", code="audioexport.book_compose_report_invalid"
    )
    duration = item.get("duration_seconds")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(float(duration))
        or duration <= 0
    ):
        raise _fail(
            "compose item duration_seconds must be a positive finite number",
            code="audioexport.book_compose_report_invalid",
        )
    try:
        with wave.open(str(path), "rb") as source:
            actual_rate = source.getframerate()
            actual_channels = source.getnchannels()
            actual_frames = source.getnframes()
    except (OSError, EOFError, wave.Error) as exc:
        raise VerificationError(
            f"composed artifact is not a readable WAV file: {path}",
            code="audioexport.book_compose_geometry_mismatch",
        ) from exc
    actual_duration = actual_frames / actual_rate if actual_rate > 0 else 0.0
    if (
        actual_rate != expected_rate
        or actual_channels != expected_channels
        or actual_frames != expected_frames
        or abs(actual_duration - float(duration)) > _DURATION_TOLERANCE_SECONDS
    ):
        raise VerificationError(
            f"composed WAV geometry does not match the report: {path}",
            code="audioexport.book_compose_geometry_mismatch",
        )
    return actual_rate, actual_channels, actual_frames, actual_duration


def _read_compose_report(path: Path, requested_variant: str) -> tuple[Mapping[str, Any], ...]:
    if path.is_symlink() or not path.is_file():
        raise _fail(
            f"compose report must be a regular non-symlink file: {path}",
            code="audioexport.book_compose_report_missing",
        )
    try:
        with path.open("rb") as stream:
            payload = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise _fail(
            f"cannot parse compose report: {exc}", code="audioexport.book_compose_report_invalid"
        ) from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("format") != "audiocompose-book-report"
        or payload.get("version") != 1
        or isinstance(payload.get("version"), bool)
        or payload.get("variant") != requested_variant
    ):
        raise _fail(
            "compose report format, version, or variant does not match the requested export",
            code="audioexport.book_compose_report_invalid",
        )
    has_singular_items = "item" in payload
    has_plural_items = "items" in payload
    if has_singular_items == has_plural_items:
        raise _fail(
            "compose report must contain exactly one item or items array",
            code="audioexport.book_compose_report_invalid",
        )
    items_key = "item" if has_singular_items else "items"
    items = payload[items_key]
    if not isinstance(items, list):
        raise _fail(
            f"compose report {items_key} must be an array",
            code="audioexport.book_compose_report_invalid",
        )
    seen_ids: set[str] = set()
    seen_numbers: set[int] = set()
    normalized: list[Mapping[str, Any]] = []
    for index, raw in enumerate(items, 1):
        if not isinstance(raw, Mapping):
            raise _fail(
                f"compose report item {index} must be a table",
                code="audioexport.book_compose_report_invalid",
            )
        item_id = _nonempty_string(
            raw.get("id"),
            f"compose report item {index} id",
            code="audioexport.book_compose_report_invalid",
        )
        source_number = _positive_int(
            raw.get("source_number"),
            f"compose report item {index} source_number",
            code="audioexport.book_compose_report_invalid",
        )
        status = _nonempty_string(
            raw.get("status"),
            f"compose report item {index} status",
            code="audioexport.book_compose_report_invalid",
        )
        if status not in {"written", "reused", "failed", "skipped", "missing"}:
            raise _fail(
                f"unsupported compose report item status: {status}",
                code="audioexport.book_compose_report_invalid",
            )
        if item_id in seen_ids:
            raise _fail(
                f"duplicate compose report item id: {item_id}",
                code="audioexport.book_compose_report_invalid",
            )
        if source_number in seen_numbers:
            raise _fail(
                f"duplicate compose report source number: {source_number}",
                code="audioexport.book_compose_report_invalid",
            )
        seen_ids.add(item_id)
        seen_numbers.add(source_number)
        normalized.append(raw)
    return tuple(normalized)


def _select_chapters(
    chapters: tuple[dict[str, Any], ...], selector: str
) -> tuple[dict[str, Any], ...]:
    if not isinstance(selector, str) or not selector.strip():
        raise _fail(
            "chapter selector must be a non-empty string", code="audioexport.book_chapters_invalid"
        )
    selector = selector.strip()
    by_number = {chapter["source_number"]: chapter for chapter in chapters}
    if selector == "all":
        return chapters
    selected: set[int] = set()
    for component in selector.split(","):
        component = component.strip()
        if not component:
            raise _fail(
                "chapter selector contains an empty item", code="audioexport.book_chapters_invalid"
            )
        match = re.fullmatch(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?", component)
        if match is None:
            raise _fail(
                f"invalid chapter selector item: {component}",
                code="audioexport.book_chapters_invalid",
            )
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if end < start:
            raise _fail(
                f"chapter range ends before it starts: {component}",
                code="audioexport.book_chapters_invalid",
            )
        chosen = [number for number in by_number if start <= number <= end]
        if not chosen:
            raise _fail(
                f"chapter selector does not match a source number: {component}",
                code="audioexport.book_chapters_invalid",
            )
        selected.update(chosen)
    return tuple(chapter for chapter in chapters if chapter["source_number"] in selected)


def resolve_book_export(
    book: str | Path,
    *,
    variant: str,
    chapters: str = "all",
    audiocompose_root: str | Path | None = None,
    output_root: str | Path | None = None,
) -> BookExportContext:
    """Resolve and verify selected composed chapters from a ``.ssmdbook`` workspace.

    Reported WAV duration is compared with frame-derived duration using a fixed
    0.01-second tolerance to account for decimal serialization precision.
    """
    safe_variant = _safe_variant(variant)
    workspace = _workspace_for(book)
    manifest_path = workspace / "manifest.json"
    manifest = _read_manifest(manifest_path)
    _validate_metadata(manifest)
    manifest_chapters = _manifest_chapters(manifest)
    selected = _select_chapters(manifest_chapters, chapters)

    compose_root = _report_root(workspace, audiocompose_root)
    variant_dir = compose_root / safe_variant
    if variant_dir.is_symlink():
        raise _fail(
            "compose variant directory must not be a symlink",
            code="audioexport.book_compose_report_invalid",
        )
    try:
        resolved_variant = variant_dir.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _fail(
            f"unknown AudioCompose variant: {safe_variant}",
            code="audioexport.book_compose_report_missing",
        ) from exc
    if not resolved_variant.is_dir() or os.path.commonpath(
        (str(compose_root), str(resolved_variant))
    ) != str(compose_root):
        raise _fail(
            "compose variant directory escapes the AudioCompose root",
            code="audioexport.book_compose_report_invalid",
        )
    report_path = resolved_variant / "compose-report.toml"
    items = _read_compose_report(report_path, safe_variant)
    item_by_id = {item["id"]: item for item in items}
    item_by_number = {item["source_number"]: item for item in items}

    composed: list[ComposedChapter] = []
    for chapter in selected:
        item = item_by_id.get(chapter["id"])
        if item is None:
            raise _fail(
                f"compose report is missing selected chapter id {chapter['id']}",
                code="audioexport.book_compose_item_missing",
            )
        if (
            item.get("source_number") != chapter["source_number"]
            or item_by_number.get(chapter["source_number"]) is not item
        ):
            raise _fail(
                f"compose report identity does not match manifest chapter {chapter['id']}",
                code="audioexport.book_compose_report_invalid",
            )
        if item["status"] not in {"written", "reused"}:
            raise _fail(
                f"selected chapter {chapter['id']} has unusable compose status {item['status']!r}",
                code="audioexport.book_compose_item_unusable",
            )
        for required in ("render_plan_id", "manifest_id", "output_policy_id"):
            _nonempty_string(
                item.get(required),
                f"compose item {chapter['id']} {required}",
                code="audioexport.book_compose_report_invalid",
            )
        expected_hash = item.get("sha256")
        if isinstance(expected_hash, str) and expected_hash.startswith("sha256:"):
            expected_hash = expected_hash.removeprefix("sha256:")
        if not isinstance(expected_hash, str) or _SHA256_RE.fullmatch(expected_hash) is None:
            raise _fail(
                f"compose item {chapter['id']} has an invalid sha256",
                code="audioexport.book_compose_report_invalid",
            )
        path = _safe_report_file(resolved_variant, item.get("output"))
        actual_hash = _sha256(path)
        if actual_hash.casefold() != expected_hash.casefold():
            raise VerificationError(
                f"composed WAV checksum does not match the report: {path}",
                code="audioexport.book_compose_hash_mismatch",
            )
        rate, channels, frames, duration = _verify_wav(path, item)
        composed.append(
            ComposedChapter(
                id=chapter["id"],
                source_number=chapter["source_number"],
                title=chapter["title"],
                path=path,
                sha256=actual_hash,
                sample_rate=rate,
                channels=channels,
                frames=frames,
                duration_seconds=duration,
                render_plan_id=str(item["render_plan_id"]),
                manifest_id=str(item["manifest_id"]),
            )
        )
    name = workspace.name
    if name.casefold().endswith(".ssmdbook"):
        name = name[: -len(".ssmdbook")]
    if not name:
        name = "book"
    base_output = (
        Path(output_root).expanduser() if output_root is not None else workspace / "audioexport"
    )
    if not base_output.is_absolute():
        base_output = Path.cwd() / base_output
    default_output = base_output.absolute() / safe_variant / f"{name}.m4b"
    return BookExportContext(
        workspace=workspace,
        variant=safe_variant,
        manifest_path=manifest_path,
        compose_report_path=report_path,
        chapters=tuple(composed),
        default_output=default_output,
    )


_MANIFEST_METADATA_FIELDS = (
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
)


def _resolved_metadata(
    manifest: Mapping[str, Any],
    profile: AudiobookProfile | None,
    overrides: AudiobookMetadata | Mapping[str, str] | None,
) -> AudiobookMetadata:
    metadata = manifest.get("metadata", {})
    authors = metadata.get("authors") or []
    values: dict[str, str | None] = {
        "title": metadata.get("title"),
        "author": ", ".join(authors) if authors else None,
        "publisher": metadata.get("publisher"),
        "language": metadata.get("language"),
    }
    extras: dict[str, str] = {}
    if profile is not None:
        for name in _MANIFEST_METADATA_FIELDS:
            value = getattr(profile.metadata, name)
            if value is not None:
                values[name] = value
        extras.update(profile.metadata.extra)
    if isinstance(overrides, AudiobookMetadata):
        for name in _MANIFEST_METADATA_FIELDS:
            value = getattr(overrides, name)
            if value is not None:
                values[name] = value
        extras.update(overrides.extra)
    elif overrides is not None:
        if not isinstance(overrides, Mapping):
            raise _fail(
                "metadata must be AudiobookMetadata or a string mapping",
                code="audioexport.audiobook_metadata_invalid",
            )
        for key, value in overrides.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise _fail(
                    "metadata overrides must contain string keys and values",
                    code="audioexport.audiobook_metadata_invalid",
                )
            if key in _MANIFEST_METADATA_FIELDS:
                values[key] = value
            else:
                extras[key] = value
    result = AudiobookMetadata(**values, extra=extras)
    audiobook_tags(result)
    return result


def export_book(
    book: str | Path,
    *,
    variant: str,
    chapters: str = "all",
    output: str | Path | None = None,
    profile: str | Path | None = None,
    metadata: AudiobookMetadata | Mapping[str, str] | None = None,
    cover: str | Path | None = None,
    bitrate: str | int | None = None,
    sample_rate: int | None = None,
    channels: int | None = None,
    force: bool = False,
    ffmpeg: str | Path | None = None,
    ffprobe: str | Path | None = None,
) -> BookExportResult:
    """Export selected chapters from a composed variant to a verified M4B."""
    context = resolve_book_export(book, variant=variant, chapters=chapters)
    audiobook_profile = load_audiobook_profile(profile) if profile is not None else None
    manifest = _read_manifest(context.manifest_path)
    resolved_metadata = _resolved_metadata(manifest, audiobook_profile, metadata)

    destination = (
        Path(output).expanduser().absolute()
        if output is not None
        else audiobook_profile.output
        if audiobook_profile is not None
        else context.default_output
    )
    resolved_cover = (
        Path(cover).expanduser()
        if cover is not None
        else audiobook_profile.cover
        if audiobook_profile is not None
        else None
    )
    effective_bitrate = bitrate
    effective_sample_rate = sample_rate
    effective_channels = channels
    if audiobook_profile is not None:
        if effective_bitrate is None:
            effective_bitrate = audiobook_profile.audio.bitrate
        if effective_sample_rate is None:
            effective_sample_rate = audiobook_profile.audio.sample_rate
        if effective_channels is None:
            effective_channels = audiobook_profile.audio.channels

    tracks = tuple(
        AudiobookTrack(chapter.path, title=chapter.title) for chapter in context.chapters
    )
    audiobook = build_audiobook(
        tracks,
        destination,
        metadata=resolved_metadata,
        cover=resolved_cover,
        bitrate=effective_bitrate,
        sample_rate=effective_sample_rate,
        channels=effective_channels,
        force=force,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
    )
    return BookExportResult(
        workspace=context.workspace,
        variant=context.variant,
        selected_chapter_ids=tuple(chapter.id for chapter in context.chapters),
        output=audiobook.path,
        sidecar=audiobook.manifest_path,
        audiobook=audiobook,
    )
