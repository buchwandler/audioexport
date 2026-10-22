"""Stable library API import surface (also available from `audioexport`)."""

from __future__ import annotations

from .audiobook import AudiobookResult, AudiobookTrack, build_audiobook
from .audiobook_profile import AudiobookAudioOptions, AudiobookProfile, load_audiobook_profile
from .book import (
    BookExportContext,
    BookExportResult,
    ComposedChapter,
    export_book,
    resolve_book_export,
)
from .bundle import BundleExportBatchResult, BundleExportResult, export_bundle, export_bundle_all
from .chapters import Chapter, load_chapters
from .document import (
    ComposedDocument,
    DocumentExportContext,
    DocumentExportResult,
    export_document,
    resolve_document_export,
)
from .errors import (
    AudioExportError,
    EncodingError,
    InvalidExportError,
    ToolNotFoundError,
    VerificationError,
)
from .fftools import doctor, probe
from .formats import FORMATS, normalize_bitrate
from .metadata import AudiobookMetadata
from .pipeline import ExportResult, encode, run_profile
from .preflight import preflight_profile
from .profile import ExportProfile, OutputSpec, ResolvedOutput, load_profile, resolve_output
from .variants import BundleVariantInfo, BundleVariantsResult, list_bundle_variants

__all__ = [
    "FORMATS",
    "AudioExportError",
    "AudiobookAudioOptions",
    "AudiobookMetadata",
    "AudiobookProfile",
    "AudiobookResult",
    "AudiobookTrack",
    "BookExportContext",
    "BookExportResult",
    "BundleExportBatchResult",
    "BundleExportResult",
    "BundleVariantInfo",
    "BundleVariantsResult",
    "Chapter",
    "ComposedChapter",
    "ComposedDocument",
    "DocumentExportContext",
    "DocumentExportResult",
    "EncodingError",
    "ExportProfile",
    "ExportResult",
    "InvalidExportError",
    "OutputSpec",
    "ResolvedOutput",
    "ToolNotFoundError",
    "VerificationError",
    "build_audiobook",
    "doctor",
    "encode",
    "export_book",
    "export_bundle",
    "export_bundle_all",
    "export_document",
    "list_bundle_variants",
    "load_audiobook_profile",
    "load_chapters",
    "load_profile",
    "normalize_bitrate",
    "preflight_profile",
    "probe",
    "resolve_book_export",
    "resolve_document_export",
    "resolve_output",
    "run_profile",
]
