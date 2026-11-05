from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from test_book import VARIANT as BOOK_VARIANT
from test_book import _workspace as book_workspace
from test_document import VARIANT as DOCUMENT_VARIANT
from test_document import _workspace as document_workspace

from audioexport import audiobook, book, pipeline
from audioexport.book import resolve_book_export
from audioexport.bundle_inspection import inspect_bundle_export
from audioexport.document import resolve_document_export
from audioexport.metadata import audiobook_tags


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(root: Path) -> dict[str, bytes | None]:
    snapshot: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        key = path.relative_to(root).as_posix()
        snapshot[key] = path.read_bytes() if path.is_file() and not path.is_symlink() else None
    return snapshot


def _book_cache(workspace: Path) -> tuple[Path, Path]:
    context = resolve_book_export(workspace, variant=BOOK_VARIANT)
    output = context.default_output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"cached audiobook artifact")
    sidecar = pipeline._manifest_path(output)
    old_tools = {"ffmpeg": "ffmpeg test version", "ffprobe": "ffprobe test version"}
    source_records = [
        {
            "name": chapter.path.name,
            "sha256": _sha(chapter.path),
            "explicit_title": chapter.title,
            "source_title": None,
            "chapter_title": chapter.title,
        }
        for chapter in context.chapters
    ]
    request_options = {
        "format": "m4b",
        "codec": "aac",
        "encoder": "aac",
        "bitrate": "192k",
        "sample_rate": 8000,
        "channels": 1,
        "metadata": audiobook_tags(
            book._resolved_metadata(book._read_manifest(context.manifest_path), None, None)
        ),
        "resources": {
            "cover_sha256": None,
            "chapters_file_sha256": None,
            "chapters_file_name": None,
            "discovered_resources": [],
        },
        "chapter_request": {
            "mode": "tracks",
            "title_source": "tag",
            "titles": [chapter.title for chapter in context.chapters],
        },
    }
    producer_version = audiobook._producer_version()
    request_id = pipeline._canonical_hash(
        {
            "schema": "audioexport.audiobook-request.v1",
            "sources": source_records,
            "options": request_options,
            "ffmpeg": old_tools["ffmpeg"],
            "ffprobe": old_tools["ffprobe"],
            "producer_version": producer_version,
        }
    )
    durations = [100] * len(context.chapters)
    chapter_rows = [
        {
            "title": chapter.title,
            "start_ms": index * 100,
            "end_ms": (index + 1) * 100,
            "start_sample": None,
            "end_sample": None,
            "sample_rate": None,
        }
        for index, chapter in enumerate(context.chapters)
    ]
    export_id = pipeline._canonical_hash(
        {
            "schema": "audioexport.audiobook-identity.v1",
            "request_id": request_id,
            "chapters": chapter_rows,
            "normalized_segment_durations_ms": durations,
        }
    )
    payload: dict[str, Any] = {
        "schema": "audioexport.audiobook-manifest.v1",
        "schema_version": 1,
        "operation": "audiobook",
        "producer": "audioexport",
        "producer_version": producer_version,
        "export_id": export_id,
        "request_id": request_id,
        "output": output.name,
        "output_sha256": _sha(output),
        "sources": source_records,
        "options": {**request_options, "chapters": chapter_rows},
        "resources": {"cover": None, "chapters_file": None, "auto_discovered": []},
        "tools": old_tools,
        "verified": {"chapters": len(chapter_rows), "track_count": len(context.chapters)},
    }
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    return output, sidecar


def _document_cache(workspace: Path) -> tuple[Path, Path]:
    context = resolve_document_export(workspace, variant=DOCUMENT_VARIANT)
    output = context.output_root / f"{context.default_stem}.mp3"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"cached document artifact")
    sidecar = pipeline._manifest_path(output)
    options = {
        "format": "mp3",
        "codec": "libmp3lame",
        "codec_args": [],
        "bitrate": "192k",
        "metadata": {},
        "cover_sha256": None,
        "timeline_sha256": None,
        "chapters": [],
    }
    tools = {"ffmpeg": "ffmpeg test version", "ffprobe": "ffprobe test version"}
    producer_version = pipeline._producer_version()
    export_id = pipeline._canonical_hash(
        {
            "schema": "audioexport.identity.v1",
            "source_sha256": _sha(context.document.path),
            "options": options,
            "ffmpeg": tools["ffmpeg"],
            "ffprobe": tools["ffprobe"],
            "producer_version": producer_version,
        }
    )
    payload = {
        "schema": "audioexport.manifest.v1",
        "schema_version": 1,
        "producer": "audioexport",
        "producer_version": producer_version,
        "export_id": export_id,
        "source_sha256": _sha(context.document.path),
        "output": output.name,
        "output_sha256": _sha(output),
        "options": options,
        "tools": tools,
    }
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    return output, sidecar


def test_document_export_inspection_reports_not_started_without_running_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _, _ = document_workspace(tmp_path)
    monkeypatch.setattr(
        "audioexport.fftools.run",
        lambda *_args, **_kwargs: pytest.fail("inspection must not launch FFmpeg or FFprobe"),
    )

    inspection = inspect_bundle_export(workspace, variant=DOCUMENT_VARIANT, format="mp3")

    assert inspection.state == "not_started"
    assert not inspection.reusable
    assert not (workspace / "audioexport").exists()


def test_document_export_inspection_matches_identity_and_detects_option_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _, _ = document_workspace(tmp_path)
    output, sidecar = _document_cache(workspace)
    before = _snapshot(workspace)
    monkeypatch.setattr(
        "audioexport.fftools.run",
        lambda *_args, **_kwargs: pytest.fail("inspection must not launch FFmpeg or FFprobe"),
    )

    inspection = inspect_bundle_export(workspace, variant=DOCUMENT_VARIANT, format="mp3")
    changed = inspect_bundle_export(
        workspace, variant=DOCUMENT_VARIANT, format="mp3", metadata={"title": "Changed"}
    )

    assert inspection.state == "complete"
    assert inspection.reusable
    assert inspection.export_identity
    assert inspection.output == output
    assert inspection.sidecar == sidecar
    assert inspection.tool_versions_checked is False
    assert inspection.replacement_safe
    assert changed.state == "stale"
    assert not changed.reusable
    assert changed.replacement_safe
    assert _snapshot(workspace) == before


def test_book_export_inspection_matches_identity_and_detects_option_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _ = book_workspace(tmp_path)
    output, sidecar = _book_cache(workspace)
    before = _snapshot(workspace)
    monkeypatch.setattr(
        "audioexport.fftools.run",
        lambda *_args, **_kwargs: pytest.fail("inspection must not launch FFmpeg or FFprobe"),
    )

    inspection = inspect_bundle_export(workspace, variant=BOOK_VARIANT)
    changed = inspect_bundle_export(workspace, variant=BOOK_VARIANT, bitrate="128k")

    assert inspection.state == "complete"
    assert inspection.reusable
    assert inspection.export_identity
    assert inspection.output == output
    assert inspection.sidecar == sidecar
    assert changed.state == "stale"
    assert changed.replacement_safe
    assert not changed.reusable
    assert _snapshot(workspace) == before


def test_export_inspection_rejects_replacement_through_sidecar_symlink(
    tmp_path: Path,
) -> None:
    workspace, _, _ = document_workspace(tmp_path)
    _, sidecar = _document_cache(workspace)
    sidecar.unlink()
    sidecar.symlink_to(tmp_path / "outside.json")

    inspection = inspect_bundle_export(workspace, variant=DOCUMENT_VARIANT, format="mp3")
    assert inspection.state == "stale"
    assert not inspection.replacement_safe
