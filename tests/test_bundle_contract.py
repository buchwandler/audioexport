from __future__ import annotations

import json
from pathlib import Path

import pytest

from audioexport.bundle_contract import BundleIdentity, load_bundle_identity
from audioexport.errors import InvalidExportError


def _write_manifest(root: Path, bundle_format: str, schema_version: int = 1) -> None:
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps({"format": bundle_format, "schema_version": schema_version}),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("bundle_format", "kind"),
    [("ssmdconvert.book", "book"), ("ssmdconvert.document", "document")],
)
def test_loads_canonical_bundle_identity(tmp_path: Path, bundle_format: str, kind: str) -> None:
    root = tmp_path / "novel.ssmd-bundle"
    _write_manifest(root, bundle_format)

    identity = load_bundle_identity(root)

    assert isinstance(identity, BundleIdentity)
    assert identity.root == root.resolve()
    assert identity.manifest_path == root / "manifest.json"
    assert identity.kind == kind


def test_rejects_non_root_bundle_inputs(tmp_path: Path) -> None:
    root = tmp_path / "novel.ssmd-bundle"
    _write_manifest(root, "ssmdconvert.book")
    (root / "chapters").mkdir()
    (root / "document.ssmd.md").write_text("# Novel\n", encoding="utf-8")

    for path in (root / "chapters", root / "document.ssmd.md"):
        with pytest.raises(InvalidExportError, match="canonical .ssmd-bundle directory root"):
            load_bundle_identity(path)


def test_unknown_format_fails_with_actionable_error_before_stages(tmp_path: Path) -> None:
    root = tmp_path / "novel.ssmd-bundle"
    _write_manifest(root, "unknown.bundle")
    (root / "audiocompose").mkdir()

    with pytest.raises(InvalidExportError, match="unsupported bundle format 'unknown.bundle'"):
        load_bundle_identity(root)


def test_rejects_invalid_schema_version(tmp_path: Path) -> None:
    root = tmp_path / "novel.ssmd-bundle"
    _write_manifest(root, "ssmdconvert.document", schema_version=2)

    with pytest.raises(InvalidExportError, match="requires schema_version 1"):
        load_bundle_identity(root)


def test_rejects_duplicate_manifest_keys(tmp_path: Path) -> None:
    root = tmp_path / "novel.ssmd-bundle"
    root.mkdir()
    (root / "manifest.json").write_text(
        '{"format":"ssmdconvert.book","format":"ssmdconvert.document","schema_version":1}',
        encoding="utf-8",
    )

    with pytest.raises(InvalidExportError, match="duplicate JSON key 'format'"):
        load_bundle_identity(root)


def test_rejects_non_directory_or_noncanonical_path(tmp_path: Path) -> None:
    archive = tmp_path / "novel.ssmd-bundle.zip"
    archive.write_bytes(b"not an editable workspace")
    old_suffix = tmp_path / "novel_workspace"
    old_suffix.mkdir()

    for path in (archive, old_suffix):
        with pytest.raises(InvalidExportError, match="canonical .ssmd-bundle directory root"):
            load_bundle_identity(path)


@pytest.mark.parametrize(
    ("root_name", "fallback", "expected"),
    [
        ("novel.ssmd-bundle", "book", "novel"),
        ("NOVEL.SSMD-BUNDLE", "document", "NOVEL"),
        (".ssmd-bundle", "document", "document"),
    ],
)
def test_bundle_stem_uses_only_canonical_suffix(
    tmp_path: Path, root_name: str, fallback: str, expected: str
) -> None:
    from audioexport.bundle_contract import bundle_stem

    assert bundle_stem(tmp_path / root_name, fallback=fallback) == expected
