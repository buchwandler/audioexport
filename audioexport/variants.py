"""Read-only inventory of AudioCompose variants available to AudioExport."""

from __future__ import annotations

import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from ._workspace import path_is_within, resolve_stage_root, safe_variant
from .bundle_contract import BundleKind, load_bundle_identity
from .errors import InvalidExportError

VariantState = Literal["ready", "partial", "failed", "missing_report", "invalid"]


@dataclass(frozen=True, slots=True)
class BundleVariantInfo:
    """A report-only summary of one AudioCompose variant directory."""

    name: str
    state: VariantState
    report_path: Path | None
    render_profile_id: str | None
    audiocompose_version: str | None
    status: str | None
    usable_items: int
    total_items: int
    output: str | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "report_path": str(self.report_path) if self.report_path is not None else None,
            "render_profile_id": self.render_profile_id,
            "audiocompose_version": self.audiocompose_version,
            "status": self.status,
            "usable_items": self.usable_items,
            "total_items": self.total_items,
            "output": self.output,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class BundleVariantsResult:
    """All immediate AudioCompose variant summaries for one canonical bundle."""

    workspace: Path
    kind: BundleKind
    audiocompose_root: Path
    variants: tuple[BundleVariantInfo, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "audioexport-bundle-variants",
            "version": 1,
            "workspace": str(self.workspace),
            "kind": self.kind,
            "audiocompose_root": str(self.audiocompose_root),
            "count": len(self.variants),
            "variants": [variant.to_dict() for variant in self.variants],
        }


def _invalid_info(name: str, report_path: Path | None, message: str) -> BundleVariantInfo:
    return BundleVariantInfo(
        name=name,
        state="invalid",
        report_path=report_path,
        render_profile_id=None,
        audiocompose_version=None,
        status=None,
        usable_items=0,
        total_items=0,
        output=None,
        error=message,
    )


def _optional_text(payload: Mapping[str, Any], field: str) -> str | None:
    value = payload.get(field)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _load_report(report_path: Path) -> Mapping[str, Any]:
    if report_path.is_symlink():
        raise ValueError("compose report must not be a symlink")
    try:
        report_stat = report_path.lstat()
    except OSError as exc:
        raise ValueError(f"cannot inspect compose report: {exc}") from exc
    if not stat.S_ISREG(report_stat.st_mode):
        raise ValueError("compose-report.toml must be a regular file")
    try:
        with report_path.open("rb") as stream:
            payload = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot parse compose report: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise TypeError("compose report must be a TOML table")
    return payload


def _report_info(
    kind: BundleKind,
    variant: str,
    report_path: Path,
    payload: Mapping[str, Any],
) -> BundleVariantInfo:
    expected_format = (
        "audiocompose-document-report" if kind == "document" else "audiocompose-book-report"
    )
    version = payload.get("version")
    if (
        payload.get("format") != expected_format
        or isinstance(version, bool)
        or version != 1
        or payload.get("variant") != variant
    ):
        raise ValueError("compose report format, version, or variant does not match")

    profile_id = _optional_text(payload, "render_profile_id")
    compose_version = _optional_text(payload, "audiocompose_version")
    status_value = payload.get("status")
    status = (
        status_value.strip() if isinstance(status_value, str) and status_value.strip() else None
    )
    output_value = payload.get("output")
    output = output_value if isinstance(output_value, str) else None

    if kind == "document":
        geometry = payload.get("output_geometry")
        if payload.get("document_id") != "document-0001" or not isinstance(geometry, Mapping):
            raise ValueError("document compose report identity or output_geometry table is invalid")
        if status in {"written", "reused"}:
            state: VariantState = "ready"
            usable_items = 1
        elif status is not None:
            state = "failed"
            usable_items = 0
        else:
            raise ValueError("document compose report status is missing or invalid")
        return BundleVariantInfo(
            name=variant,
            state=state,
            report_path=report_path,
            render_profile_id=profile_id,
            audiocompose_version=compose_version,
            status=status,
            usable_items=usable_items,
            total_items=1,
            output=output,
        )

    has_item = "item" in payload
    has_items = "items" in payload
    if has_item == has_items:
        raise ValueError("book compose report must contain exactly one item or items array")
    raw_items = payload["item"] if has_item else payload["items"]
    if not isinstance(raw_items, list):
        raise TypeError("book compose report items must be an array")
    if not raw_items:
        raise ValueError("book compose report must contain at least one item")

    usable_items = 0
    failed_items = 0
    for index, raw_item in enumerate(raw_items, 1):
        if not isinstance(raw_item, Mapping):
            raise TypeError(f"book compose report item {index} must be a table")
        item_status = raw_item.get("status")
        if not isinstance(item_status, str) or item_status not in {
            "written",
            "reused",
            "failed",
            "skipped",
            "missing",
        }:
            raise ValueError(f"book compose report item {index} has an invalid status")
        if item_status in {"written", "reused"}:
            usable_items += 1
        elif item_status == "failed":
            failed_items += 1

    if failed_items:
        state = "failed"
    elif usable_items == len(raw_items):
        state = "ready"
    else:
        state = "partial"
    return BundleVariantInfo(
        name=variant,
        state=state,
        report_path=report_path,
        render_profile_id=profile_id,
        audiocompose_version=compose_version,
        status=status,
        usable_items=usable_items,
        total_items=len(raw_items),
        output=output,
    )


def _inspect_variant(
    kind: BundleKind, stage_root: Path, name: str, entry: Path
) -> BundleVariantInfo:
    try:
        safe_variant(name, error_code="audioexport.bundle_variant_invalid")
    except InvalidExportError as exc:
        return _invalid_info(name, None, str(exc))

    report_path = entry / "compose-report.toml"
    if entry.is_symlink():
        return _invalid_info(name, report_path, "variant directory must not be a symlink")
    try:
        entry_stat = entry.lstat()
    except OSError as exc:
        return _invalid_info(name, report_path, f"cannot inspect variant directory: {exc}")
    if not stat.S_ISDIR(entry_stat.st_mode):
        return _invalid_info(name, report_path, "variant entry is not a directory")

    try:
        variant_dir = entry.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return _invalid_info(name, report_path, f"cannot resolve variant directory: {exc}")
    if not variant_dir.is_dir() or not path_is_within(stage_root, variant_dir):
        return _invalid_info(name, report_path, "variant directory escapes the AudioCompose root")

    report_path = variant_dir / "compose-report.toml"
    if report_path.is_symlink():
        return _invalid_info(name, report_path, "compose report must not be a symlink")
    if not report_path.exists():
        return BundleVariantInfo(
            name=name,
            state="missing_report",
            report_path=report_path,
            render_profile_id=None,
            audiocompose_version=None,
            status=None,
            usable_items=0,
            total_items=0,
            output=None,
            error=None,
        )
    try:
        payload = _load_report(report_path)
        return _report_info(kind, name, report_path, payload)
    except (OSError, TypeError, ValueError, InvalidExportError) as exc:
        return _invalid_info(name, report_path, str(exc))


def list_bundle_variants(
    bundle: str | Path = ".",
    *,
    audiocompose_root: str | Path | None = None,
) -> BundleVariantsResult:
    """List safe immediate AudioCompose variant directories without modifying files."""
    identity = load_bundle_identity(bundle)
    stage_root = resolve_stage_root(
        identity.root,
        audiocompose_root,
        stage_name="audiocompose",
        missing_code="audioexport.bundle_variants_compose_root_missing",
        invalid_code="audioexport.bundle_variants_compose_root_invalid",
    )
    try:
        children = sorted(stage_root.iterdir(), key=lambda child: child.name)
    except OSError as exc:
        raise InvalidExportError(
            f"cannot list AudioCompose root {stage_root}: {exc}",
            code="audioexport.bundle_variants_compose_root_invalid",
        ) from exc

    variants: list[BundleVariantInfo] = []
    for child in children:
        try:
            child_stat = child.lstat()
        except OSError as exc:
            variants.append(_invalid_info(child.name, None, f"cannot inspect variant entry: {exc}"))
            continue
        if stat.S_ISDIR(child_stat.st_mode) or stat.S_ISLNK(child_stat.st_mode):
            variants.append(_inspect_variant(identity.kind, stage_root, child.name, child))

    return BundleVariantsResult(
        workspace=identity.root,
        kind=identity.kind,
        audiocompose_root=stage_root,
        variants=tuple(variants),
    )
