"""Secure adapter from a composed ``.ssmdoc`` workspace to audio export."""

from __future__ import annotations

import json
import re
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from ._workspace import (
    path_is_within,
    resolve_stage_root,
    safe_report_file,
    safe_variant,
    sha256_file,
    verify_reported_wav,
)
from .errors import InvalidExportError, VerificationError
from .formats import FORMATS, normalize_format
from .pipeline import ExportResult, encode

_DOCUMENT_ID = "document-0001"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_REQUIRED_PROVENANCE = (
    "render_profile_id",
    "source_plan_id",
    "render_plan_id",
    "render_manifest_id",
    "output_policy_id",
    "audiojob_id",
    "render_manifest_sha256",
    "render_diagnostics_sha256",
    "pause_policy_id",
    "pause_policy_provenance",
    "audiocompose_version",
)


@dataclass(frozen=True, slots=True)
class ComposedDocument:
    """A verified document WAV produced by AudioCompose."""

    id: str
    path: Path
    sha256: str
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float
    render_profile_id: str
    source_plan_id: str
    render_plan_id: str
    render_manifest_id: str
    output_policy_id: str
    audiojob_id: str


@dataclass(frozen=True, slots=True)
class DocumentExportContext:
    """Resolved, verified inputs and output location for one document variant."""

    workspace: Path
    variant: str
    manifest_path: Path
    compose_report_path: Path
    document: ComposedDocument
    output_root: Path
    default_stem: str


@dataclass(frozen=True, slots=True)
class DocumentExportResult:
    """Final encoded output and its verified AudioCompose source context."""

    workspace: Path
    variant: str
    document_id: str
    input_compose_report: Path
    source: Path
    output: Path
    sidecar: Path
    export: ExportResult
    document: ComposedDocument

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "variant": self.variant,
            "document_id": self.document_id,
            "input_compose_report": str(self.input_compose_report),
            "source": str(self.source),
            "output": str(self.output),
            "sidecar": str(self.sidecar),
            "export_identity": self.export.export_id,
            "output_sha256": self.export.output_sha256,
            "format": self.export.format,
            "codec": self.export.codec,
            "duration_ms": round(self.document.duration_seconds * 1000),
            "sample_rate": self.document.sample_rate,
            "channels": self.document.channels,
            "reused": self.export.reused,
            "render_profile_id": self.document.render_profile_id,
            "source_plan_id": self.document.source_plan_id,
            "render_plan_id": self.document.render_plan_id,
            "render_manifest_id": self.document.render_manifest_id,
        }


def _fail(message: str, *, code: str) -> InvalidExportError:
    return InvalidExportError(message, code=code)


def _duplicate_checked_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _workspace_for_document(document: str | Path) -> Path:
    if not isinstance(document, (str, Path)):
        raise _fail("document must be a path", code="audioexport.document_workspace_invalid")
    candidate = Path(document).expanduser()
    if candidate.is_symlink():
        raise _fail(
            f"document workspace/source must not be a symlink: {candidate}",
            code="audioexport.document_workspace_invalid",
        )
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _fail(
            f"document path does not exist or cannot be resolved: {candidate}",
            code="audioexport.document_workspace_invalid",
        ) from exc
    if not candidate.is_symlink() and resolved.is_dir():
        workspace = resolved
    elif candidate.name == "document.ssmd.md" and resolved.is_file():
        workspace = resolved.parent
    else:
        raise _fail(
            "document must be an SSMDDoc workspace or its canonical document.ssmd.md file",
            code="audioexport.document_workspace_invalid",
        )
    if candidate.is_dir() and candidate.is_symlink():
        raise _fail(
            f"document workspace must not be a symlink: {candidate}",
            code="audioexport.document_workspace_invalid",
        )
    return workspace


def _read_manifest(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise _fail(
            f"manifest.json must be a regular non-symlink file: {path}",
            code="audioexport.document_manifest_invalid",
        )
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_duplicate_checked_object
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise _fail(
            f"cannot read document manifest: {exc}", code="audioexport.document_manifest_invalid"
        ) from exc
    if not isinstance(payload, Mapping):
        raise _fail(
            "document manifest must be a JSON object",
            code="audioexport.document_manifest_invalid",
        )
    if (
        payload.get("format") != "ssmdconvert.document"
        or isinstance(payload.get("schema_version"), bool)
        or payload.get("schema_version") != 1
    ):
        raise _fail(
            "document manifest format or schema_version is invalid",
            code="audioexport.document_manifest_invalid",
        )
    document = payload.get("document")
    if not isinstance(document, Mapping):
        raise _fail(
            "document manifest must contain a document object",
            code="audioexport.document_manifest_invalid",
        )
    if document.get("id") != _DOCUMENT_ID:
        raise _fail(
            f"document manifest id must be {_DOCUMENT_ID!r}",
            code="audioexport.document_manifest_invalid",
        )
    if document.get("path") != "document.ssmd.md":
        raise _fail(
            "document manifest path must be the canonical 'document.ssmd.md'",
            code="audioexport.document_manifest_invalid",
        )
    source = path.parent / "document.ssmd.md"
    if source.is_symlink() or not source.is_file():
        raise _fail(
            f"canonical document source must be a regular non-symlink file: {source}",
            code="audioexport.document_manifest_invalid",
        )
    try:
        resolved_source = source.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _fail(
            f"canonical document source cannot be resolved: {source}",
            code="audioexport.document_manifest_invalid",
        ) from exc
    if not path_is_within(path.parent.resolve(), resolved_source):
        raise _fail(
            "canonical document source escapes its workspace",
            code="audioexport.document_manifest_invalid",
        )
    try:
        if not stat.S_ISREG(source.lstat().st_mode):
            raise OSError("not a regular file")
    except OSError as exc:
        raise _fail(
            f"canonical document source must be a regular file: {source}",
            code="audioexport.document_manifest_invalid",
        ) from exc
    return payload


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise _fail(
            f"compose report {field} must be a non-empty string",
            code="audioexport.document_compose_report_invalid",
        )
    return value.strip()


def _variant_directory(compose_root: Path, variant: str) -> Path:
    variant_dir = compose_root / variant
    if variant_dir.is_symlink():
        raise _fail(
            "AudioCompose variant directory must not be a symlink",
            code="audioexport.document_compose_report_invalid",
        )
    try:
        resolved = variant_dir.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _fail(
            f"unknown AudioCompose variant: {variant}",
            code="audioexport.document_compose_report_missing",
        ) from exc
    if not resolved.is_dir() or not path_is_within(compose_root, resolved):
        raise _fail(
            "AudioCompose variant directory escapes its stage root or is not a directory",
            code="audioexport.document_compose_report_invalid",
        )
    return resolved


def _read_compose_report(path: Path, variant: str) -> Mapping[str, Any]:
    if path.is_symlink():
        raise _fail(
            f"compose report must not be a symlink: {path}",
            code="audioexport.document_compose_report_invalid",
        )
    if not path.is_file():
        raise _fail(
            f"AudioCompose document report does not exist: {path}",
            code="audioexport.document_compose_report_missing",
        )
    try:
        with path.open("rb") as stream:
            payload = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise _fail(
            f"cannot parse AudioCompose document report: {exc}",
            code="audioexport.document_compose_report_invalid",
        ) from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("format") != "audiocompose-document-report"
        or isinstance(payload.get("version"), bool)
        or payload.get("version") != 1
        or payload.get("document_id") != _DOCUMENT_ID
        or payload.get("variant") != variant
    ):
        raise _fail(
            "compose report format, version, document_id, or variant is invalid",
            code="audioexport.document_compose_report_invalid",
        )
    if payload.get("status") not in {"written", "reused"}:
        raise _fail(
            f"document compose status is unusable: {payload.get('status')!r}",
            code="audioexport.document_compose_item_unusable",
        )
    for field in _REQUIRED_PROVENANCE:
        _nonempty_string(payload.get(field), field)
    schema_version = payload.get("audiojob_schema_version")
    if isinstance(schema_version, bool) or schema_version != 2:
        raise _fail(
            "compose report audiojob_schema_version must be 2",
            code="audioexport.document_compose_report_invalid",
        )
    return payload


def _reported_sha256(value: Any) -> str:
    if not isinstance(value, str):
        raise _fail(
            "compose report sha256 must be a 64-character digest",
            code="audioexport.document_compose_report_invalid",
        )
    digest = value.removeprefix("sha256:")
    if _SHA256_RE.fullmatch(digest) is None:
        raise _fail(
            "compose report sha256 must be a 64-character hexadecimal digest",
            code="audioexport.document_compose_report_invalid",
        )
    return digest


def _verified_document(variant_dir: Path, report: Mapping[str, Any]) -> ComposedDocument:
    source = safe_report_file(
        variant_dir,
        report.get("output"),
        error_code="audioexport.document_compose_output_invalid",
    )
    if source.name != f"{_DOCUMENT_ID}.wav":
        raise VerificationError(
            f"compose report output must resolve to {_DOCUMENT_ID}.wav",
            code="audioexport.document_compose_output_invalid",
        )
    actual_sha256 = sha256_file(source, error_code="audioexport.document_compose_hash_mismatch")
    expected_sha256 = _reported_sha256(report.get("sha256"))
    if actual_sha256.casefold() != expected_sha256.casefold():
        raise VerificationError(
            f"composed document checksum does not match the report: {source}",
            code="audioexport.document_compose_hash_mismatch",
        )
    rate, channels, frames, duration = verify_reported_wav(
        source,
        report,
        report_code="audioexport.document_compose_report_invalid",
        geometry_code="audioexport.document_compose_geometry_mismatch",
        item_label="document compose report",
    )
    return ComposedDocument(
        id=_DOCUMENT_ID,
        path=source,
        sha256=actual_sha256,
        sample_rate=rate,
        channels=channels,
        frames=frames,
        duration_seconds=duration,
        render_profile_id=str(report["render_profile_id"]).strip(),
        source_plan_id=str(report["source_plan_id"]).strip(),
        render_plan_id=str(report["render_plan_id"]).strip(),
        render_manifest_id=str(report["render_manifest_id"]).strip(),
        output_policy_id=str(report["output_policy_id"]).strip(),
        audiojob_id=str(report["audiojob_id"]).strip(),
    )


def resolve_document_export(
    document: str | Path,
    *,
    variant: str,
    audiocompose_root: str | Path | None = None,
    output_root: str | Path | None = None,
) -> DocumentExportContext:
    """Resolve and verify the immediate AudioCompose handoff, without writing files."""
    safe = safe_variant(variant, error_code="audioexport.document_variant_invalid")
    workspace = _workspace_for_document(document)
    manifest_path = workspace / "manifest.json"
    _read_manifest(manifest_path)
    compose_root = resolve_stage_root(
        workspace,
        audiocompose_root,
        stage_name="audiocompose",
        missing_code="audioexport.document_compose_report_missing",
        invalid_code="audioexport.document_compose_report_invalid",
    )
    variant_dir = _variant_directory(compose_root, safe)
    report_path = variant_dir / "compose-report.toml"
    report = _read_compose_report(report_path, safe)
    composed = _verified_document(variant_dir, report)
    selected_output_root = (
        Path(output_root).expanduser() if output_root is not None else workspace / "audioexport"
    )
    if not selected_output_root.is_absolute():
        selected_output_root = Path.cwd() / selected_output_root
    name = workspace.name
    if name.casefold().endswith(".ssmdoc"):
        name = name[: -len(".ssmdoc")]
    if not name:
        name = "document"
    return DocumentExportContext(
        workspace=workspace,
        variant=safe,
        manifest_path=manifest_path,
        compose_report_path=report_path,
        document=composed,
        output_root=selected_output_root.absolute() / safe,
        default_stem=name,
    )


def export_document(
    document: str | Path,
    *,
    variant: str,
    output: str | Path | None = None,
    format: str | None = None,
    bitrate: str | int | None = None,
    metadata: Mapping[str, str] | None = None,
    cover: str | Path | None = None,
    timeline: str | Path | None = None,
    audiocompose_root: str | Path | None = None,
    output_root: str | Path | None = None,
    force: bool = False,
    ffmpeg: str | Path | None = None,
    ffprobe: str | Path | None = None,
) -> DocumentExportResult:
    """Encode one verified document WAV through the generic AudioExport pipeline."""
    context = resolve_document_export(
        document,
        variant=variant,
        audiocompose_root=audiocompose_root,
        output_root=output_root,
    )
    if format is not None:
        selected_format = normalize_format(format)
    elif output is not None:
        selected_format = normalize_format(Path(output).suffix)
    else:
        raise _fail(
            "document export requires a format or output filename extension",
            code="audioexport.document_format_required",
        )
    destination = (
        Path(output).expanduser()
        if output is not None
        else context.output_root / f"{context.default_stem}{FORMATS[selected_format].extension}"
    )
    result = encode(
        context.document.path,
        destination,
        format=selected_format,
        bitrate=bitrate,
        metadata=metadata,
        cover=cover,
        timeline=timeline,
        force=force,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
    )
    return DocumentExportResult(
        workspace=context.workspace,
        variant=context.variant,
        document_id=context.document.id,
        input_compose_report=context.compose_report_path,
        source=context.document.path,
        output=result.path,
        sidecar=result.manifest_path,
        export=result,
        document=context.document,
    )
