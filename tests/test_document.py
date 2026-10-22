from __future__ import annotations

import hashlib
import json
import math
import shutil
import wave
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from audioexport.document import export_document, resolve_document_export
from audioexport.errors import AudioExportError
from audioexport.pipeline import ExportResult

VARIANT = "printer-cast--28f616b6fe3b"
DOCUMENT_ID = "document-0001"


def _write_wav(
    path: Path, *, sample_rate: int = 8000, channels: int = 1, frames: int = 800
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(b"\0\0" * frames * channels)


def _workspace(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    workspace = tmp_path / "story.ssmd-bundle"
    workspace.mkdir(parents=True)
    (workspace / "manifest.json").write_text(
        json.dumps(
            {
                "format": "ssmdconvert.document",
                "schema_version": 1,
                "document": {"id": DOCUMENT_ID, "path": "document.ssmd.md"},
            }
        ),
        encoding="utf-8",
    )
    (workspace / "document.ssmd.md").write_text("# Story\n", encoding="utf-8")
    variant_dir = workspace / "audiocompose" / VARIANT
    wav_path = variant_dir / f"{DOCUMENT_ID}.wav"
    _write_wav(wav_path)
    report: dict[str, Any] = {
        "format": "audiocompose-document-report",
        "version": 1,
        "document_id": DOCUMENT_ID,
        "variant": VARIANT,
        "render_profile_id": "sha256:render-profile",
        "source_plan_id": "sha256:source-plan",
        "render_plan_id": "sha256:render-plan",
        "render_manifest_id": "sha256:render-manifest",
        "render_manifest_sha256": "sha256:render-manifest-hash",
        "render_diagnostics_sha256": "sha256:render-diagnostics-hash",
        "pause_policy_id": "sha256:pause-policy",
        "pause_policy_provenance": "renderprofile",
        "output_policy_id": "sha256:output-policy",
        "audiojob_id": "sha256:audiojob",
        "audiojob_schema_version": 2,
        "audiocompose_version": "0.3.1",
        "status": "written",
        "output": f"{DOCUMENT_ID}.wav",
        "output_geometry": {
            "sha256": f"sha256:{hashlib.sha256(wav_path.read_bytes()).hexdigest()}",
            "sample_rate": 8000,
            "channels": 1,
            "frames": 800,
            "duration_seconds": 0.1,
        },
    }
    _write_report(variant_dir / "compose-report.toml", report)
    return workspace, wav_path, report


def _toml_scalar(value: Any) -> str:
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "nan"
        return "inf" if value > 0 else "-inf"
    return json.dumps(value)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for key, value in report.items():
        if isinstance(value, Mapping):
            lines.extend(("", f"[{key}]"))
            lines.extend(f"{field} = {_toml_scalar(item)}" for field, item in value.items())
        else:
            lines.append(f"{key} = {_toml_scalar(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _resolve(workspace: Path) -> Any:
    return resolve_document_export(workspace, variant=VARIANT)


def _assert_code(call: Any, expected: str) -> None:
    with pytest.raises(AudioExportError) as error:
        call()
    assert error.value.code == expected


def test_resolves_bundle_root_without_creating_output(tmp_path: Path) -> None:
    workspace, wav_path, _ = _workspace(tmp_path)

    context = resolve_document_export(workspace, variant=VARIANT)
    _assert_code(
        lambda: resolve_document_export(workspace / "document.ssmd.md", variant=VARIANT),
        "audioexport.bundle_invalid",
    )

    assert context.workspace == workspace
    assert context.document.id == DOCUMENT_ID
    assert context.document.path == wav_path
    assert context.document.sample_rate == 8000
    assert context.document.channels == 1
    assert context.document.frames == 800
    assert context.document.duration_seconds == pytest.approx(0.1)
    assert context.output_root == workspace / "audioexport" / VARIANT
    assert context.default_stem == "story"
    assert not (workspace / "audioexport").exists()


def test_accepts_bare_sha256_and_reused_status(tmp_path: Path) -> None:
    workspace, wav_path, report = _workspace(tmp_path)
    report["output_geometry"]["sha256"] = hashlib.sha256(wav_path.read_bytes()).hexdigest()
    report["status"] = "reused"
    _write_report(workspace / "audiocompose" / VARIANT / "compose-report.toml", report)

    assert _resolve(workspace).document.sha256 == report["output_geometry"]["sha256"]


def test_current_audiocompose_toml_shape_uses_output_geometry(tmp_path: Path) -> None:
    workspace, _, _ = _workspace(tmp_path)
    report_path = workspace / "audiocompose" / VARIANT / "compose-report.toml"
    report_text = report_path.read_text(encoding="utf-8")

    assert "\n[output_geometry]\n" in report_text
    assert 'sha256 = "sha256:' in report_text
    assert _resolve(workspace).document.sha256


@pytest.mark.parametrize("bare_digest", [False, True])
def test_accepts_legacy_flat_geometry(tmp_path: Path, bare_digest: bool) -> None:
    workspace, wav_path, report = _workspace(tmp_path)
    geometry = report.pop("output_geometry")
    report.update(geometry)
    if bare_digest:
        report["sha256"] = hashlib.sha256(wav_path.read_bytes()).hexdigest()
    _write_report(workspace / "audiocompose" / VARIANT / "compose-report.toml", report)

    assert _resolve(workspace).document.sha256 == report["sha256"].removeprefix("sha256:")


def test_rejects_non_table_output_geometry(tmp_path: Path) -> None:
    workspace, _, report = _workspace(tmp_path)
    report["output_geometry"] = "not a table"
    _write_report(workspace / "audiocompose" / VARIANT / "compose-report.toml", report)

    _assert_code(lambda: _resolve(workspace), "audioexport.document_compose_report_invalid")


@pytest.mark.parametrize("sha256", [None, "invalid"])
def test_rejects_missing_or_invalid_nested_sha256(tmp_path: Path, sha256: Any) -> None:
    workspace, _, report = _workspace(tmp_path)
    if sha256 is None:
        del report["output_geometry"]["sha256"]
    else:
        report["output_geometry"]["sha256"] = sha256
    _write_report(workspace / "audiocompose" / VARIANT / "compose-report.toml", report)

    _assert_code(lambda: _resolve(workspace), "audioexport.document_compose_report_invalid")


def test_rejects_mixed_nested_and_legacy_geometry(tmp_path: Path) -> None:
    workspace, _, report = _workspace(tmp_path)
    report["sample_rate"] = 8000
    _write_report(workspace / "audiocompose" / VARIANT / "compose-report.toml", report)

    _assert_code(lambda: _resolve(workspace), "audioexport.document_compose_report_invalid")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format", "audiocompose-book-report"),
        ("version", 2),
        ("version", True),
        ("document_id", "document-0002"),
        ("variant", "other-variant"),
        ("render_profile_id", ""),
        ("audiojob_id", None),
        ("audiojob_schema_version", 1),
    ],
)
def test_rejects_invalid_compose_report_identity_or_provenance(
    tmp_path: Path, field: str, value: Any
) -> None:
    workspace, _, report = _workspace(tmp_path)
    report[field] = value
    _write_report(workspace / "audiocompose" / VARIANT / "compose-report.toml", report)

    expected = (
        "audioexport.document_compose_item_unusable"
        if field == "status"
        else "audioexport.document_compose_report_invalid"
    )
    _assert_code(lambda: _resolve(workspace), expected)


def test_rejects_unusable_status_and_missing_status(tmp_path: Path) -> None:
    workspace, _, report = _workspace(tmp_path)
    report["status"] = "failed"
    _write_report(workspace / "audiocompose" / VARIANT / "compose-report.toml", report)
    _assert_code(lambda: _resolve(workspace), "audioexport.document_compose_item_unusable")

    del report["status"]
    _write_report(workspace / "audiocompose" / VARIANT / "compose-report.toml", report)
    _assert_code(lambda: _resolve(workspace), "audioexport.document_compose_item_unusable")


@pytest.mark.parametrize(
    "manifest",
    [
        {
            "format": "wrong",
            "schema_version": 1,
            "document": {"id": DOCUMENT_ID, "path": "document.ssmd.md"},
        },
        {
            "format": "ssmdconvert.document",
            "schema_version": True,
            "document": {"id": DOCUMENT_ID, "path": "document.ssmd.md"},
        },
        {"format": "ssmdconvert.document", "schema_version": 1},
        {
            "format": "ssmdconvert.document",
            "schema_version": 1,
            "document": {"id": "story", "path": "document.ssmd.md"},
        },
        {
            "format": "ssmdconvert.document",
            "schema_version": 1,
            "document": {"id": DOCUMENT_ID, "path": "../document.ssmd.md"},
        },
        {
            "format": "ssmdconvert.document",
            "schema_version": 1,
            "document": {"id": DOCUMENT_ID, "path": "/document.ssmd.md"},
        },
        {
            "format": "ssmdconvert.document",
            "schema_version": 1,
            "document": {"id": DOCUMENT_ID, "path": "C:document.ssmd.md"},
        },
    ],
)
def test_rejects_invalid_manifest_contract(tmp_path: Path, manifest: dict[str, Any]) -> None:
    workspace, _, _ = _workspace(tmp_path)
    (workspace / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    expected = (
        "audioexport.bundle_invalid"
        if manifest.get("format") != "ssmdconvert.document"
        or isinstance(manifest.get("schema_version"), bool)
        or manifest.get("schema_version") != 1
        else "audioexport.document_manifest_invalid"
    )
    _assert_code(lambda: _resolve(workspace), expected)


def test_rejects_duplicate_manifest_keys(tmp_path: Path) -> None:
    workspace, _, _ = _workspace(tmp_path)
    (workspace / "manifest.json").write_text(
        '{"format":"ssmdconvert.document","format":"ssmdconvert.document",'
        '"schema_version":1,"document":{"id":"document-0001",'
        '"path":"document.ssmd.md"}}',
        encoding="utf-8",
    )

    _assert_code(lambda: _resolve(workspace), "audioexport.bundle_invalid")


def test_rejects_missing_or_symlinked_manifest_and_source(tmp_path: Path) -> None:
    workspace, _, _ = _workspace(tmp_path / "missing-manifest")
    (workspace / "manifest.json").unlink()
    _assert_code(lambda: _resolve(workspace), "audioexport.bundle_invalid")

    workspace, _, _ = _workspace(tmp_path / "symlink-manifest")
    manifest = workspace / "manifest.json"
    saved_manifest = tmp_path / "manifest-backup.json"
    saved_manifest.write_bytes(manifest.read_bytes())
    manifest.unlink()
    manifest.symlink_to(saved_manifest)
    _assert_code(lambda: _resolve(workspace), "audioexport.bundle_invalid")

    workspace, _, _ = _workspace(tmp_path / "symlink-source")
    source = workspace / "document.ssmd.md"
    saved_source = tmp_path / "document-backup.md"
    saved_source.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(saved_source)
    _assert_code(lambda: _resolve(workspace), "audioexport.document_manifest_invalid")

    workspace, _, _ = _workspace(tmp_path / "missing-source")
    (workspace / "document.ssmd.md").unlink()
    _assert_code(lambda: _resolve(workspace), "audioexport.document_manifest_invalid")


def test_rejects_unrecognized_workspace_input_and_zip(tmp_path: Path) -> None:
    workspace, _, _ = _workspace(tmp_path)
    arbitrary = workspace / "notes.txt"
    arbitrary.write_text("notes", encoding="utf-8")
    archive = tmp_path / "story.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", "{}")

    _assert_code(
        lambda: resolve_document_export(arbitrary, variant=VARIANT),
        "audioexport.bundle_invalid",
    )
    _assert_code(
        lambda: resolve_document_export(archive, variant=VARIANT),
        "audioexport.bundle_invalid",
    )


@pytest.mark.parametrize(
    "variant",
    [
        "",
        ".",
        "..",
        "../other",
        "/tmp/variant",
        "foo/bar",
        "foo\\bar",
        "C:variant",
        "\x00bad",
        "\x01bad",
        "\x7f",
    ],
)
def test_rejects_unsafe_variant_without_output_creation(tmp_path: Path, variant: str) -> None:
    workspace, _, _ = _workspace(tmp_path)

    _assert_code(
        lambda: resolve_document_export(workspace, variant=variant),
        "audioexport.document_variant_invalid",
    )
    assert not (workspace / "audioexport").exists()


def test_unknown_variant_does_not_create_output_root(tmp_path: Path) -> None:
    workspace, _, _ = _workspace(tmp_path)

    _assert_code(
        lambda: resolve_document_export(workspace, variant="unknown"),
        "audioexport.document_compose_report_missing",
    )
    assert not (workspace / "audioexport").exists()


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("output", "../document.wav", "audioexport.document_compose_output_invalid"),
        ("output", "/subdir/document.wav", "audioexport.document_compose_output_invalid"),
        ("output", "C:\\document-0001.wav", "audioexport.document_compose_output_invalid"),
        ("output", "folder/../document-0001.wav", "audioexport.document_compose_output_invalid"),
        ("output", "wrong.wav", "audioexport.document_compose_output_invalid"),
        ("output", "missing/document-0001.wav", "audioexport.document_compose_output_invalid"),
        ("sha256", "xyz", "audioexport.document_compose_report_invalid"),
    ],
)
def test_rejects_unsafe_or_invalid_report_output(
    tmp_path: Path, field: str, value: Any, expected: str
) -> None:
    workspace, _, report = _workspace(tmp_path)
    if field == "sha256":
        report["output_geometry"][field] = value
    else:
        report[field] = value
    _write_report(workspace / "audiocompose" / VARIANT / "compose-report.toml", report)

    _assert_code(lambda: _resolve(workspace), expected)


def test_rejects_output_symlink_and_directory(tmp_path: Path) -> None:
    workspace, wav_path, report = _workspace(tmp_path)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(wav_path.read_bytes())
    wav_path.unlink()
    wav_path.symlink_to(outside)
    _write_report(workspace / "audiocompose" / VARIANT / "compose-report.toml", report)
    _assert_code(lambda: _resolve(workspace), "audioexport.document_compose_output_invalid")

    wav_path.unlink()
    wav_path.mkdir()
    _write_report(workspace / "audiocompose" / VARIANT / "compose-report.toml", report)
    _assert_code(lambda: _resolve(workspace), "audioexport.document_compose_output_invalid")


def test_missing_symlinked_and_malformed_compose_reports(tmp_path: Path) -> None:
    workspace, _, _ = _workspace(tmp_path / "missing")
    report_path = workspace / "audiocompose" / VARIANT / "compose-report.toml"
    report_path.unlink()
    _assert_code(lambda: _resolve(workspace), "audioexport.document_compose_report_missing")

    workspace, _, _ = _workspace(tmp_path / "malformed")
    report_path = workspace / "audiocompose" / VARIANT / "compose-report.toml"
    report_path.write_text("format = [", encoding="utf-8")
    _assert_code(lambda: _resolve(workspace), "audioexport.document_compose_report_invalid")

    workspace, _, _ = _workspace(tmp_path / "symlink")
    report_path = workspace / "audiocompose" / VARIANT / "compose-report.toml"
    target = tmp_path / "report-backup.toml"
    target.write_bytes(report_path.read_bytes())
    report_path.unlink()
    report_path.symlink_to(target)
    _assert_code(lambda: _resolve(workspace), "audioexport.document_compose_report_invalid")


def test_rejects_report_hash_mismatch_before_encoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, wav_path, _ = _workspace(tmp_path)
    wav_path.write_bytes(wav_path.read_bytes() + b"tamper")
    invoked = False

    def should_not_encode(*args: Any, **kwargs: Any) -> None:
        nonlocal invoked
        invoked = True
        raise AssertionError("encoder must not run for an invalid composed source")

    monkeypatch.setattr("audioexport.document.encode", should_not_encode)
    _assert_code(
        lambda: export_document(workspace, variant=VARIANT, format="mp3"),
        "audioexport.document_compose_hash_mismatch",
    )
    assert not invoked


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("sample_rate", 16000, "audioexport.document_compose_geometry_mismatch"),
        ("channels", 2, "audioexport.document_compose_geometry_mismatch"),
        ("frames", 801, "audioexport.document_compose_geometry_mismatch"),
        ("duration_seconds", 0.2, "audioexport.document_compose_geometry_mismatch"),
        ("duration_seconds", -1, "audioexport.document_compose_report_invalid"),
        ("duration_seconds", float("inf"), "audioexport.document_compose_report_invalid"),
        ("duration_seconds", float("nan"), "audioexport.document_compose_report_invalid"),
    ],
)
def test_rejects_reported_wav_geometry_mismatch(
    tmp_path: Path, field: str, value: Any, expected: str
) -> None:
    workspace, _, report = _workspace(tmp_path)
    report["output_geometry"][field] = value
    _write_report(workspace / "audiocompose" / VARIANT / "compose-report.toml", report)

    _assert_code(lambda: _resolve(workspace), expected)


def test_rejects_non_wav_and_truncated_wav(tmp_path: Path) -> None:
    workspace, wav_path, report = _workspace(tmp_path / "not-wav")
    wav_path.write_bytes(b"not a wav")
    report["output_geometry"]["sha256"] = hashlib.sha256(wav_path.read_bytes()).hexdigest()
    _write_report(workspace / "audiocompose" / VARIANT / "compose-report.toml", report)
    _assert_code(lambda: _resolve(workspace), "audioexport.document_compose_geometry_mismatch")

    workspace, wav_path, report = _workspace(tmp_path / "truncated")
    wav_path.write_bytes(wav_path.read_bytes()[:-4])
    report["output_geometry"]["sha256"] = hashlib.sha256(wav_path.read_bytes()).hexdigest()
    _write_report(workspace / "audiocompose" / VARIANT / "compose-report.toml", report)
    _assert_code(lambda: _resolve(workspace), "audioexport.document_compose_geometry_mismatch")


def _fake_export(path: Path, format: str) -> ExportResult:
    return ExportResult(
        path=path,
        format=format,
        codec="mp3" if format == "mp3" else "opus",
        export_id="sha256:identity",
        output_sha256="output-hash",
        manifest_path=path.with_name(path.name + ".audioexport.json"),
        reused=False,
        chapter_count=0,
    )


def test_export_defaults_by_workspace_stem_and_forwards_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, wav_path, _ = _workspace(tmp_path)
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_encode(*args: Any, **kwargs: Any) -> ExportResult:
        calls.append((args, kwargs))
        return _fake_export(Path(args[1]), kwargs["format"])

    monkeypatch.setattr("audioexport.document.encode", fake_encode)
    cover = tmp_path / "cover.jpg"
    timeline = tmp_path / "timeline.json"
    metadata = {"title": "Story", "artist": "Author"}
    result = export_document(
        workspace,
        variant=VARIANT,
        format="MP3",
        bitrate="192k",
        metadata=metadata,
        cover=cover,
        timeline=timeline,
        force=True,
        ffmpeg="ffmpeg-custom",
        ffprobe="ffprobe-custom",
    )

    expected = workspace / "audioexport" / VARIANT / "story.mp3"
    assert result.source == wav_path
    assert result.output == expected
    assert result.sidecar == expected.with_name("story.mp3.audioexport.json")
    assert result.to_dict()["export_identity"] == "sha256:identity"
    assert result.to_dict()["render_manifest_id"] == "sha256:render-manifest"
    assert calls == [
        (
            (wav_path, expected),
            {
                "format": "mp3",
                "bitrate": "192k",
                "metadata": metadata,
                "cover": cover,
                "timeline": timeline,
                "force": True,
                "ffmpeg": "ffmpeg-custom",
                "ffprobe": "ffprobe-custom",
            },
        )
    ]


def test_format_infers_from_explicit_output_and_preserves_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, wav_path, _ = _workspace(tmp_path)
    custom_output = tmp_path / "exports" / "custom.opus"
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_encode(*args: Any, **kwargs: Any) -> ExportResult:
        calls.append((args, kwargs))
        return _fake_export(Path(args[1]), kwargs["format"])

    monkeypatch.setattr("audioexport.document.encode", fake_encode)
    result = export_document(workspace, variant=VARIANT, output=custom_output)

    assert result.output == custom_output
    assert calls[0][0] == (wav_path, custom_output)
    assert calls[0][1]["format"] == "opus"


def test_format_is_required_without_format_or_output(tmp_path: Path) -> None:
    workspace, _, _ = _workspace(tmp_path)

    _assert_code(
        lambda: export_document(workspace, variant=VARIANT),
        "audioexport.document_format_required",
    )


def test_root_overrides_are_stage_roots(tmp_path: Path) -> None:
    workspace, _, _ = _workspace(tmp_path)
    custom_compose = tmp_path / "custom-compose"
    custom_variant = custom_compose / VARIANT
    shutil.copytree(workspace / "audiocompose" / VARIANT, custom_variant)
    custom_output = tmp_path / "custom-export"

    context = resolve_document_export(
        workspace,
        variant=VARIANT,
        audiocompose_root=custom_compose,
        output_root=custom_output,
    )

    assert context.compose_report_path == custom_variant / "compose-report.toml"
    assert context.output_root == custom_output / VARIANT


def test_relative_compose_root_is_resolved_under_workspace(tmp_path: Path) -> None:
    workspace, _, _ = _workspace(tmp_path)
    context = resolve_document_export(
        workspace,
        variant=VARIANT,
        audiocompose_root=Path("audiocompose"),
    )

    assert (
        context.compose_report_path == workspace / "audiocompose" / VARIANT / "compose-report.toml"
    )
