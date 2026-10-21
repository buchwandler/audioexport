from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import audioexport.bundle as bundle_module
from audioexport.bundle import BundleExportResult, export_bundle
from audioexport.errors import InvalidExportError


def _bundle(tmp_path: Path, kind: str) -> Path:
    root = tmp_path / "story.ssmd-bundle"
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "format": f"ssmdconvert.{kind}",
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.mark.parametrize("kind", ["book", "document"])
def test_export_bundle_dispatches_by_manifest_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root = _bundle(tmp_path, kind)
    calls: list[tuple[str, Path, dict[str, Any]]] = []

    def fake_export(name: str):
        def call(workspace: Path, **kwargs: Any) -> SimpleNamespace:
            calls.append((name, workspace, kwargs))
            return SimpleNamespace(output=workspace / "final.m4a")

        return call

    monkeypatch.setattr(bundle_module, "export_book", fake_export("book"))
    monkeypatch.setattr(bundle_module, "export_document", fake_export("document"))

    result = export_bundle(root, variant="narration", metadata={"author": "A. Writer"})

    assert result.kind == kind
    assert result.output == root / "final.m4a"
    assert len(calls) == 1
    called_kind, called_root, options = calls[0]
    assert called_kind == kind
    assert called_root == root.resolve()
    assert options["metadata"] == (
        {"author": "A. Writer"} if kind == "book" else {"artist": "A. Writer"}
    )


def test_export_bundle_normalizes_book_artist_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _bundle(tmp_path, "book")
    captured: dict[str, Any] = {}

    def fake_export(workspace: Path, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(output=workspace / "final.m4b")

    monkeypatch.setattr(bundle_module, "export_book", fake_export)

    export_bundle(root, variant="narration", metadata={"artist": "A. Writer"})

    assert captured["metadata"] == {"author": "A. Writer"}


@pytest.mark.parametrize(
    ("kind", "options", "message"),
    [
        ("book", {"format": "mp3"}, "only --format m4b"),
        ("book", {"timeline": "timeline.json"}, "--timeline is not supported"),
        ("document", {"chapters": "2"}, "only --chapters all"),
        ("document", {"profile": "profile.toml"}, "supported only for book"),
        ("document", {"sample_rate": 24000}, "supported only for book"),
        ("document", {"channels": 2}, "supported only for book"),
    ],
)
def test_export_bundle_rejects_cross_kind_options_before_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    options: dict[str, Any],
    message: str,
) -> None:
    root = _bundle(tmp_path, kind)

    def unexpected_export(*args: Any, **kwargs: Any) -> None:
        pytest.fail("kind-specific options must be validated before export")

    monkeypatch.setattr(bundle_module, "export_book", unexpected_export)
    monkeypatch.setattr(bundle_module, "export_document", unexpected_export)

    with pytest.raises(InvalidExportError, match=message):
        export_bundle(root, variant="narration", **options)


def test_document_format_is_forwarded_to_document_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _bundle(tmp_path, "document")
    captured: dict[str, Any] = {}

    def fake_export(workspace: Path, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(output=workspace / "final.mp3")

    monkeypatch.setattr(bundle_module, "export_document", fake_export)

    result = export_bundle(root, variant="narration", format="mp3")

    assert result.kind == "document"
    assert captured["format"] == "mp3"


def test_bundle_result_uses_versioned_json_envelope() -> None:
    from audioexport.audiobook import AudiobookResult
    from audioexport.book import BookExportResult

    audiobook = AudiobookResult(
        path=Path("result.m4b"),
        export_id="sha256:test",
        output_sha256="digest",
        manifest_path=Path("result.m4b.audioexport.json"),
        reused=False,
        chapter_count=1,
        track_count=1,
        duration_ms=1000,
        codec="aac",
    )
    value = BookExportResult(
        workspace=Path("story.ssmd-bundle"),
        variant="narration",
        selected_chapter_ids=("chapter-1",),
        output=Path("result.m4b"),
        sidecar=Path("result.m4b.audioexport.json"),
        audiobook=audiobook,
        input_compose_report=Path("compose-report.toml"),
    )
    payload = BundleExportResult(kind="book", value=value).to_dict()

    assert payload["format"] == "audioexport-bundle-result"
    assert payload["version"] == 1
    assert payload["kind"] == "book"
    assert payload["result"]["output"] == "result.m4b"
