"""Stable library API import surface (also available from `audioexport`)."""

from __future__ import annotations

from .audiobook import AudiobookResult, AudiobookTrack, build_audiobook
from .audiobook_profile import AudiobookAudioOptions, AudiobookProfile, load_audiobook_profile
from .chapters import Chapter, load_chapters
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
from .profile import ExportProfile, OutputSpec, load_profile

__all__ = [
    "FORMATS",
    "AudioExportError",
    "AudiobookAudioOptions",
    "AudiobookMetadata",
    "AudiobookProfile",
    "AudiobookResult",
    "AudiobookTrack",
    "Chapter",
    "EncodingError",
    "ExportProfile",
    "ExportResult",
    "InvalidExportError",
    "OutputSpec",
    "ToolNotFoundError",
    "VerificationError",
    "build_audiobook",
    "doctor",
    "encode",
    "load_audiobook_profile",
    "load_chapters",
    "load_profile",
    "normalize_bitrate",
    "probe",
    "run_profile",
]
