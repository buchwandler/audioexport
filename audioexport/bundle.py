"""Unified public orchestration for book and document bundle exports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audiobook_profile import load_audiobook_profile
from .book import BookExportResult, export_book
from .bundle_contract import BundleKind, load_bundle_identity
from .document import DocumentExportResult, export_document
from .errors import InvalidExportError
from .formats import normalize_format
from .metadata import AudiobookMetadata
from .variants import BundleVariantsResult, list_bundle_variants


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


@dataclass(frozen=True, slots=True)
class BundleExportBatchResult:
    """Results from exporting every discovered variant of one bundle."""

    kind: BundleKind
    workspace: Path
    results: tuple[BundleExportResult, ...]

    @property
    def outputs(self) -> tuple[Path, ...]:
        return tuple(result.output for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "audioexport-bundle-batch-result",
            "version": 1,
            "kind": self.kind,
            "workspace": str(self.workspace),
            "count": len(self.results),
            "results": [result.to_dict()["result"] for result in self.results],
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


def _validate_options_for_kind(
    *,
    kind: BundleKind,
    chapters: str,
    output: str | Path | None,
    format: str | None,
    profile: str | Path | None,
    sample_rate: int | None,
    channels: int | None,
    timeline: str | Path | None,
    metadata: AudiobookMetadata | Mapping[str, str] | None,
    batch: bool,
) -> AudiobookMetadata | dict[str, str] | None:
    """Validate shared kind restrictions before dispatch or batch iteration."""
    if batch and output is not None:
        raise _option_error(
            "--output requires --variant; use --output-root when exporting all variants"
        )

    normalized_metadata = _metadata_for_kind(metadata, kind=kind)
    if kind == "book":
        if format is not None and normalize_format(format) != "m4b":
            raise _option_error("book bundles support only --format m4b")
        if timeline is not None:
            raise _option_error("--timeline is not supported for book bundles")
        return normalized_metadata

    if chapters != "all":
        raise _option_error("document bundles support only --chapters all")
    if profile is not None:
        raise _option_error("--profile is supported only for book bundles")
    if sample_rate is not None or channels is not None:
        raise _option_error("--sample-rate and --channels are supported only for book bundles")
    if batch:
        if format is None:
            raise InvalidExportError(
                "document export requires a format when exporting all variants",
                code="audioexport.document_format_required",
            )
        normalize_format(format)
    return normalized_metadata


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
    normalized_metadata = _validate_options_for_kind(
        kind=identity.kind,
        chapters=chapters,
        output=output,
        format=format,
        profile=profile,
        sample_rate=sample_rate,
        channels=channels,
        timeline=timeline,
        metadata=metadata,
        batch=False,
    )

    value: BookExportResult | DocumentExportResult
    if identity.kind == "book":
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


def export_bundle_all(
    bundle: str | Path,
    *,
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
) -> BundleExportBatchResult:
    """Export every discovered AudioCompose variant in deterministic name order."""
    identity = load_bundle_identity(bundle)
    _validate_options_for_kind(
        kind=identity.kind,
        chapters=chapters,
        output=output,
        format=format,
        profile=profile,
        sample_rate=sample_rate,
        channels=channels,
        timeline=timeline,
        metadata=metadata,
        batch=True,
    )

    profile_output_name: str | None = None
    if identity.kind == "book" and profile is not None:
        profile_output_name = load_audiobook_profile(profile).output.name
    discovered: BundleVariantsResult = list_bundle_variants(
        identity.root,
        audiocompose_root=audiocompose_root,
    )
    if not discovered.variants:
        raise InvalidExportError(
            "no AudioCompose variants were found",
            code="audioexport.bundle_no_variants",
        )

    results: list[BundleExportResult] = []
    for item in sorted(discovered.variants, key=lambda variant_info: variant_info.name):
        batch_output = output
        if profile_output_name is not None:
            base_output = (
                Path(output_root).expanduser()
                if output_root is not None
                else identity.root / "audioexport"
            )
            if not base_output.is_absolute():
                base_output = Path.cwd() / base_output
            batch_output = base_output.absolute() / item.name / profile_output_name
        results.append(
            export_bundle(
                identity.root,
                variant=item.name,
                chapters=chapters,
                output=batch_output,
                format=format,
                profile=profile,
                bitrate=bitrate,
                sample_rate=sample_rate,
                channels=channels,
                metadata=metadata,
                cover=cover,
                timeline=timeline,
                audiocompose_root=audiocompose_root,
                output_root=output_root,
                force=force,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
            )
        )
    return BundleExportBatchResult(
        kind=identity.kind,
        workspace=identity.root,
        results=tuple(results),
    )
