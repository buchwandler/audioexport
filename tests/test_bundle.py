from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import audioexport.bundle as bundle_module
from audioexport.bundle import (
    BundleExportBatchResult,
    BundleExportResult,
    export_bundle,
    export_bundle_all,
)
from audioexport.bundle_contract import BundleKind
from audioexport.document import ComposedDocument, DocumentExportResult
from audioexport.errors import InvalidExportError
from audioexport.pipeline import ExportResult
from audioexport.variants import BundleVariantInfo, BundleVariantsResult


def _bundle(tmp_path: Path, kind: str) -> Path:
    root = tmp_path / "story.ssmd-bundle"
    root.mkdir(parents=True)
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


def _discovered(
    root: Path, names: list[str], kind: BundleKind = "document"
) -> BundleVariantsResult:
    return BundleVariantsResult(
        workspace=root.resolve(),
        kind=kind,
        audiocompose_root=root.resolve() / "audiocompose",
        variants=tuple(
            BundleVariantInfo(
                name=name,
                state="ready",
                report_path=None,
                render_profile_id=None,
                audiocompose_version=None,
                status="written",
                usable_items=1,
                total_items=1,
                output=None,
            )
            for name in names
        ),
    )


def _document_result(root: Path, variant: str) -> BundleExportResult:
    output = root / "audioexport" / variant / "story.mp3"
    sidecar = output.with_name(output.name + ".audioexport.json")
    exported = ExportResult(
        path=output,
        format="mp3",
        codec="mp3",
        export_id=f"sha256:{variant}",
        output_sha256="output-digest",
        manifest_path=sidecar,
        reused=False,
        chapter_count=0,
    )
    document = ComposedDocument(
        id="document-0001",
        path=root / "audiocompose" / variant / "document-0001.wav",
        sha256="source-digest",
        sample_rate=24000,
        channels=1,
        frames=48000,
        duration_seconds=2.0,
        render_profile_id="profile-id",
        source_plan_id="source-plan",
        render_plan_id="render-plan",
        render_manifest_id="render-manifest",
        output_policy_id="output-policy",
        audiojob_id="audiojob",
    )
    value = DocumentExportResult(
        workspace=root,
        variant=variant,
        document_id="document-0001",
        input_compose_report=root / "audiocompose" / variant / "compose-report.toml",
        source=document.path,
        output=output,
        sidecar=sidecar,
        export=exported,
        document=document,
    )
    return BundleExportResult(kind="document", value=value)


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


def test_export_bundle_all_sorts_variants_and_uses_batch_result_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _bundle(tmp_path, "document")
    discovered = _discovered(root, ["zeta", "alpha"])
    monkeypatch.setattr(bundle_module, "list_bundle_variants", lambda *args, **kwargs: discovered)
    calls: list[dict[str, Any]] = []

    def fake_export(bundle: Path, **options: Any) -> BundleExportResult:
        calls.append(options)
        return _document_result(bundle.resolve(), options["variant"])

    monkeypatch.setattr(bundle_module, "export_bundle", fake_export)
    result = export_bundle_all(root, format="mp3", bitrate="128k", force=True)

    assert isinstance(result, BundleExportBatchResult)
    assert [options["variant"] for options in calls] == ["alpha", "zeta"]
    assert all(options["format"] == "mp3" for options in calls)
    assert all(options["bitrate"] == "128k" and options["force"] for options in calls)
    assert result.workspace == root.resolve()
    assert result.outputs == (
        root.resolve() / "audioexport" / "alpha" / "story.mp3",
        root.resolve() / "audioexport" / "zeta" / "story.mp3",
    )
    payload = result.to_dict()
    assert payload["format"] == "audioexport-bundle-batch-result"
    assert payload["version"] == 1
    assert payload["count"] == 2
    assert [item["variant"] for item in payload["results"]] == ["alpha", "zeta"]


def test_batch_option_errors_precede_discovery_or_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _bundle(tmp_path, "document")

    def unexpected_discovery(*args: Any, **kwargs: Any) -> None:
        pytest.fail("invalid batch options must be rejected before discovery")

    monkeypatch.setattr(bundle_module, "list_bundle_variants", unexpected_discovery)
    with pytest.raises(InvalidExportError) as output_error:
        export_bundle_all(root, format="mp3", output="one.mp3")
    assert output_error.value.code == "audioexport.bundle_option_invalid"
    assert "--output requires --variant" in str(output_error.value)

    with pytest.raises(InvalidExportError) as format_error:
        export_bundle_all(root)
    assert format_error.value.code == "audioexport.document_format_required"

    book = _bundle(tmp_path / "book", "book")
    with pytest.raises(InvalidExportError, match="only --format m4b"):
        export_bundle_all(book, format="mp3")
    with pytest.raises(InvalidExportError, match="--timeline is not supported"):
        export_bundle_all(book, timeline="timeline.json")


def test_batch_with_no_discovered_variants_has_stable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _bundle(tmp_path, "document")
    monkeypatch.setattr(
        bundle_module, "list_bundle_variants", lambda *args, **kwargs: _discovered(root, [])
    )

    with pytest.raises(InvalidExportError) as error:
        export_bundle_all(root, format="mp3")

    assert error.value.code == "audioexport.bundle_no_variants"


def test_batch_fails_fast_without_removing_prior_variant_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _bundle(tmp_path, "document")
    discovered = _discovered(root, ["b-broken", "a-success"])
    monkeypatch.setattr(bundle_module, "list_bundle_variants", lambda *args, **kwargs: discovered)
    successful_output = tmp_path / "prior.mp3"
    calls: list[str] = []

    def fake_export(bundle: Path, **options: Any) -> BundleExportResult:
        variant = options["variant"]
        calls.append(variant)
        if variant == "b-broken":
            raise InvalidExportError(
                "broken compose report", code="audioexport.test_broken_variant"
            )
        successful_output.write_bytes(b"encoded")
        return _document_result(bundle.resolve(), variant)

    monkeypatch.setattr(bundle_module, "export_bundle", fake_export)

    with pytest.raises(InvalidExportError) as error:
        export_bundle_all(root, format="mp3")

    assert error.value.code == "audioexport.test_broken_variant"
    assert calls == ["a-success", "b-broken"]
    assert successful_output.read_bytes() == b"encoded"
