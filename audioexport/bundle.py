"""Unified public orchestration for book and document bundle exports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .book import BookExportResult, export_book
from .bundle_contract import BundleKind, load_bundle_identity
from .document import DocumentExportResult, export_document
from .errors import InvalidExportError
from .formats import normalize_format
from .metadata import AudiobookMetadata


@dataclass(frozen=True, slots=True)
class BundleExportResult:
    """Unified result wrapper around a verified book or document export."""

    kind: BundleKind
    value: BookExportResult | DocumentExportResult

    @property
    def output(self) -> Path:
        """Return the final encoded artifact path."""
        return self.value.output

    def to_dict(self) -> dict[str, Any]:
        """Return the stable top-level CLI/API result envelope."""
        if self.kind == "book":
            if not isinstance(self.value, BookExportResult):
                raise TypeError("book bundle result must contain a BookExportResult")
            result: dict[str, Any] = {
                **self.value.audiobook.to_dict(),
                "export_identity": self.value.audiobook.export_id,
                "workspace": str(self.value.workspace),
                "variant": self.value.variant,
                "selected_chapters": list(self.value.selected_chapter_ids),
                "selected_chapter_ids": list(self.value.selected_chapter_ids),
                "input_compose_report": (
                    str(self.value.input_compose_report)
                    if self.value.input_compose_report is not None
                    else None
                ),
                "output": str(self.value.output),
                "sidecar": str(self.value.sidecar),
                "duration_seconds": self.value.audiobook.duration_ms / 1000,
            }
        else:
            if not isinstance(self.value, DocumentExportResult):
                raise TypeError("document bundle result must contain a DocumentExportResult")
            result = self.value.to_dict()
        return {
            "format": "audioexport-bundle-result",
            "version": 1,
            "kind": self.kind,
            "result": result,
        }


def _option_error(message: str) -> InvalidExportError:
    return InvalidExportError(message, code="audioexport.bundle_option_invalid")


def _metadata_for_kind(
    metadata: AudiobookMetadata | Mapping[str, str] | None, *, kind: BundleKind
) -> AudiobookMetadata | dict[str, str] | None:
    if metadata is None:
        return None
    if isinstance(metadata, AudiobookMetadata):
        if kind != "book":
            raise _option_error("AudiobookMetadata is supported only for book bundles")
        return metadata
    if not isinstance(metadata, Mapping):
        raise _option_error("metadata must be a mapping of string keys to string values")
    values: dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise _option_error("metadata must contain only string keys and values")
        values[key] = value

    target, alias = ("author", "artist") if kind == "book" else ("artist", "author")
    alias_value = values.pop(alias, None)
    target_value = values.get(target)
    if alias_value is not None and target_value is not None and alias_value != target_value:
        raise _option_error(f"metadata {target!r} conflicts with its {alias!r} alias")
    if alias_value is not None:
        values[target] = alias_value
    return values


def export_bundle(
    bundle: str | Path,
    *,
    variant: str,
    chapters: str = "all",
    output: str | Path | None = None,
    format: str | None = None,
    profile: str | Path | None = None,
    bitrate: str | int | None = None,
    sample_rate: int | None = None,
    channels: int | None = None,
    metadata: AudiobookMetadata | Mapping[str, str] | None = None,
    cover: str | Path | None = None,
    timeline: str | Path | None = None,
    audiocompose_root: str | Path | None = None,
    output_root: str | Path | None = None,
    force: bool = False,
    ffmpeg: str | Path | None = None,
    ffprobe: str | Path | None = None,
) -> BundleExportResult:
    """Export one canonical bundle, selecting the specialized path from its manifest."""
    identity = load_bundle_identity(bundle)
    normalized_metadata = _metadata_for_kind(metadata, kind=identity.kind)

    value: BookExportResult | DocumentExportResult
    if identity.kind == "book":
        if format is not None and normalize_format(format) != "m4b":
            raise _option_error("book bundles support only --format m4b")
        if timeline is not None:
            raise _option_error("--timeline is not supported for book bundles")
        value = export_book(
            identity.root,
            variant=variant,
            chapters=chapters,
            output=output,
            profile=profile,
            metadata=normalized_metadata,
            cover=cover,
            bitrate=bitrate,
            sample_rate=sample_rate,
            channels=channels,
            audiocompose_root=audiocompose_root,
            output_root=output_root,
            force=force,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
        return BundleExportResult(kind="book", value=value)

    if chapters != "all":
        raise _option_error("document bundles support only --chapters all")
    if profile is not None:
        raise _option_error("--profile is supported only for book bundles")
    if sample_rate is not None or channels is not None:
        raise _option_error("--sample-rate and --channels are supported only for book bundles")
    if isinstance(normalized_metadata, AudiobookMetadata):
        raise _option_error("document bundle metadata must be a string mapping")
    value = export_document(
        identity.root,
        variant=variant,
        output=output,
        format=format,
        bitrate=bitrate,
        metadata=normalized_metadata,
        cover=cover,
        timeline=timeline,
        audiocompose_root=audiocompose_root,
        output_root=output_root,
        force=force,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
    )
    return BundleExportResult(kind="document", value=value)
