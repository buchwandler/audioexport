from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from audioexport.errors import AudioExportError, InvalidExportError
from audioexport.variants import BundleVariantInfo, list_bundle_variants


def _bundle(tmp_path: Path, kind: str = "document", *, with_root: bool = True) -> Path:
    workspace = tmp_path / "story.ssmd-bundle"
    workspace.mkdir(parents=True)
    (workspace / "manifest.json").write_text(
        json.dumps({"format": f"ssmdconvert.{kind}", "schema_version": 1}),
        encoding="utf-8",
    )
    if with_root:
        (workspace / "audiocompose").mkdir()
    return workspace


def _document_report(
    variant: str,
    *,
    status: str = "written",
    report_format: str = "audiocompose-document-report",
    report_variant: str | None = None,
    version: int | bool = 1,
) -> str:
    return "\n".join(
        (
            f"format = {json.dumps(report_format)}",
            f"version = {json.dumps(version)}",
            'document_id = "document-0001"',
            f"variant = {json.dumps(report_variant if report_variant is not None else variant)}",
            'render_profile_id = "profile-123"',
            'audiocompose_version = "0.3.1"',
            f"status = {json.dumps(status)}",
            'output = "document-0001.wav"',
            "",
            "[output_geometry]",
            'sha256 = "sha256:abcdef"',
            "sample_rate = 24000",
            "channels = 1",
            "frames = 48000",
            "duration_seconds = 2.0",
            "",
        )
    )


def _write_report(variant_dir: Path, text: str) -> Path:
    variant_dir.mkdir(parents=True, exist_ok=True)
    report = variant_dir / "compose-report.toml"
    report.write_text(text, encoding="utf-8")
    return report


def _variant_map(result: Any) -> dict[str, BundleVariantInfo]:
    return {item.name: item for item in result.variants}


def test_lists_document_variants_in_name_order_with_report_fields(tmp_path: Path) -> None:
    workspace = _bundle(tmp_path)
    root = workspace / "audiocompose"
    _write_report(
        root / "zeta--bbbbbbbbbbbb", _document_report("zeta--bbbbbbbbbbbb", status="reused")
    )
    _write_report(root / "alpha--aaaaaaaaaaaa", _document_report("alpha--aaaaaaaaaaaa"))

    result = list_bundle_variants(workspace)

    assert result.kind == "document"
    assert result.workspace == workspace
    assert result.audiocompose_root == root
    assert [item.name for item in result.variants] == [
        "alpha--aaaaaaaaaaaa",
        "zeta--bbbbbbbbbbbb",
    ]
    first = result.variants[0]
    assert first.state == "ready"
    assert first.report_path == root / first.name / "compose-report.toml"
    assert first.render_profile_id == "profile-123"
    assert first.audiocompose_version == "0.3.1"
    assert first.status == "written"
    assert first.usable_items == first.total_items == 1
    assert first.output == "document-0001.wav"
    assert result.to_dict()["format"] == "audioexport-bundle-variants"
    assert result.to_dict()["count"] == 2
    assert not (workspace / "audioexport").exists()


def test_empty_root_is_valid_and_missing_root_has_stable_error(tmp_path: Path) -> None:
    workspace = _bundle(tmp_path / "empty")
    result = list_bundle_variants(workspace)
    assert result.variants == ()
    assert result.to_dict()["count"] == 0

    missing = _bundle(tmp_path / "missing", with_root=False)
    with pytest.raises(InvalidExportError) as error:
        list_bundle_variants(missing)
    assert error.value.code == "audioexport.bundle_variants_compose_root_missing"


def test_variant_without_report_is_reported_as_missing(tmp_path: Path) -> None:
    workspace = _bundle(tmp_path)
    missing_dir = workspace / "audiocompose" / "empty-variant"
    missing_dir.mkdir()

    result = list_bundle_variants(workspace)

    variant = result.variants[0]
    assert variant.name == "empty-variant"
    assert variant.state == "missing_report"
    assert variant.report_path == missing_dir / "compose-report.toml"


def test_malformed_and_identity_mismatch_candidates_are_invalid_without_aborting(
    tmp_path: Path,
) -> None:
    workspace = _bundle(tmp_path)
    root = workspace / "audiocompose"
    _write_report(root / "alpha", "format = [\n")
    _write_report(root / "beta", _document_report("beta", report_variant="not-beta"))
    _write_report(root / "good", _document_report("good"))

    result = list_bundle_variants(workspace)
    variants = _variant_map(result)

    assert variants["alpha"].state == "invalid"
    assert variants["alpha"].error
    assert variants["beta"].state == "invalid"
    assert variants["good"].state == "ready"


@pytest.mark.parametrize(
    ("report_format", "version"),
    [("audiocompose-book-report", 1), ("audiocompose-document-report", True)],
)
def test_wrong_format_or_boolean_version_is_invalid(
    tmp_path: Path, report_format: str, version: int | bool
) -> None:
    workspace = _bundle(tmp_path)
    variant = "profile--aaaaaaaaaaaa"
    _write_report(
        workspace / "audiocompose" / variant,
        _document_report(variant, report_format=report_format, version=version),
    )

    result = list_bundle_variants(workspace)

    assert result.variants[0].state == "invalid"


def test_book_variant_item_counts_and_states(tmp_path: Path) -> None:
    workspace = _bundle(tmp_path, "book")
    root = workspace / "audiocompose"
    ready = root / "a-ready"
    partial = root / "b-partial"
    failed = root / "c-failed"
    _write_report(
        ready,
        'format = "audiocompose-book-report"\nversion = 1\nvariant = "a-ready"\n'
        'render_profile_id = "book-profile"\n\n[[item]]\nstatus = "written"\n'
        '\n[[item]]\nstatus = "reused"\n',
    )
    _write_report(
        partial,
        'format = "audiocompose-book-report"\nversion = 1\nvariant = "b-partial"\n'
        '\n[[item]]\nstatus = "written"\n\n[[item]]\nstatus = "skipped"\n',
    )
    _write_report(
        failed,
        'format = "audiocompose-book-report"\nversion = 1\nvariant = "c-failed"\n'
        '\n[[item]]\nstatus = "failed"\n\n[[item]]\nstatus = "written"\n',
    )

    variants = _variant_map(list_bundle_variants(workspace))

    assert variants["a-ready"].state == "ready"
    assert (variants["a-ready"].usable_items, variants["a-ready"].total_items) == (2, 2)
    assert variants["b-partial"].state == "partial"
    assert (variants["b-partial"].usable_items, variants["b-partial"].total_items) == (1, 2)
    assert variants["c-failed"].state == "failed"
    assert (variants["c-failed"].usable_items, variants["c-failed"].total_items) == (1, 2)


def test_symlinked_variant_and_report_are_never_followed(tmp_path: Path) -> None:
    workspace = _bundle(tmp_path)
    root = workspace / "audiocompose"
    outside = tmp_path / "outside"
    _write_report(outside, _document_report("symlink-variant"))
    (root / "symlink-variant").symlink_to(outside, target_is_directory=True)

    real_variant = root / "symlink-report"
    real_variant.mkdir()
    outside_report = tmp_path / "outside-report.toml"
    outside_report.write_text(_document_report("symlink-report"), encoding="utf-8")
    (real_variant / "compose-report.toml").symlink_to(outside_report)

    variants = _variant_map(list_bundle_variants(workspace))

    assert variants["symlink-variant"].state == "invalid"
    assert "symlink" in (variants["symlink-variant"].error or "")
    assert variants["symlink-report"].state == "invalid"
    assert "symlink" in (variants["symlink-report"].error or "")


def test_unsafe_names_and_non_regular_reports_are_invalid(tmp_path: Path) -> None:
    workspace = _bundle(tmp_path)
    root = workspace / "audiocompose"
    unsafe_name = "bad\\variant"
    _write_report(root / unsafe_name, _document_report(unsafe_name))
    directory_report = root / "directory-report"
    directory_report.mkdir()
    (directory_report / "compose-report.toml").mkdir()

    variants = _variant_map(list_bundle_variants(workspace))

    assert variants[unsafe_name].state == "invalid"
    assert variants["directory-report"].state == "invalid"


def test_symlinked_compose_root_is_rejected_and_inventory_is_read_only(tmp_path: Path) -> None:
    workspace = _bundle(tmp_path)
    real_root = tmp_path / "real-audiocompose"
    real_root.mkdir()
    link = tmp_path / "compose-link"
    link.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(InvalidExportError) as error:
        list_bundle_variants(workspace, audiocompose_root=link)
    assert error.value.code == "audioexport.bundle_variants_compose_root_invalid"
    assert not (workspace / "audioexport").exists()


def test_bundle_root_symlink_uses_canonical_bundle_protection(tmp_path: Path) -> None:
    workspace = _bundle(tmp_path)
    link = tmp_path / "story-link.ssmd-bundle"
    link.symlink_to(workspace, target_is_directory=True)

    with pytest.raises(AudioExportError) as error:
        list_bundle_variants(link)
    assert error.value.code == "audioexport.bundle_invalid"
