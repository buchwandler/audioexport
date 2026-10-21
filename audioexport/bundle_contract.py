"""Canonical SSMD bundle identity and safe manifest loading."""

from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .errors import InvalidExportError

BundleKind = Literal["book", "document"]
_SUPPORTED_FORMATS: dict[str, BundleKind] = {
    "ssmdconvert.book": "book",
    "ssmdconvert.document": "document",
}
_BUNDLE_SUFFIX = ".ssmd-bundle"


@dataclass(frozen=True, slots=True)
class BundleIdentity:
    """The verified canonical root and kind of one SSMD bundle."""

    root: Path
    manifest_path: Path
    kind: BundleKind


def _invalid(message: str) -> InvalidExportError:
    return InvalidExportError(message, code="audioexport.bundle_invalid")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def load_bundle_identity(bundle: str | Path) -> BundleIdentity:
    """Load and validate a canonical bundle root without inspecting stage outputs."""
    if not isinstance(bundle, (str, Path)):
        raise _invalid("bundle must be a path to a canonical .ssmd-bundle directory")

    candidate = Path(bundle).expanduser()
    if candidate.is_symlink():
        raise _invalid(f"bundle root must not be a symlink: {candidate}")
    try:
        root = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _invalid(f"bundle root does not exist or cannot be resolved: {candidate}") from exc

    if not root.is_dir() or not root.name.casefold().endswith(_BUNDLE_SUFFIX):
        raise _invalid(
            f"bundle input must be the canonical .ssmd-bundle directory root: {candidate}"
        )

    manifest_path = root / "manifest.json"
    try:
        manifest_stat = manifest_path.lstat()
    except OSError as exc:
        raise _invalid(f"bundle manifest.json is missing or unreadable: {manifest_path}") from exc
    if not stat.S_ISREG(manifest_stat.st_mode):
        raise _invalid(f"manifest.json must be a regular non-symlink file: {manifest_path}")

    try:
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise _invalid(f"cannot safely read bundle manifest.json: {exc}") from exc
    if not isinstance(manifest, Mapping):
        raise _invalid("bundle manifest.json must contain a JSON object")

    bundle_format = manifest.get("format")
    kind = _SUPPORTED_FORMATS.get(bundle_format) if isinstance(bundle_format, str) else None
    if kind is None:
        supported = ", ".join(_SUPPORTED_FORMATS)
        raise _invalid(
            f"unsupported bundle format {bundle_format!r}; supported formats are {supported}"
        )
    schema_version = manifest.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise _invalid(
            f"bundle format {bundle_format!r} requires schema_version 1; got {schema_version!r}"
        )

    return BundleIdentity(root=root, manifest_path=manifest_path, kind=kind)


def bundle_stem(root: Path, *, fallback: str) -> str:
    """Return the filename stem for a canonical bundle root."""
    name = root.name
    if name.casefold().endswith(_BUNDLE_SUFFIX):
        name = name[: -len(_BUNDLE_SUFFIX)]
    return name or fallback
