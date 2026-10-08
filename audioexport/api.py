"""Stable library API import surface (also available from `audioexport`)."""

from __future__ import annotations

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
from .pipeline import ExportResult, encode, run_profile
from .profile import ExportProfile, OutputSpec, load_profile

__all__ = [
    "FORMATS",
    "AudioExportError",
    "Chapter",
    "EncodingError",
    "ExportProfile",
    "ExportResult",
    "InvalidExportError",
    "OutputSpec",
    "ToolNotFoundError",
    "VerificationError",
    "doctor",
    "encode",
    "load_chapters",
    "load_profile",
    "normalize_bitrate",
    "probe",
    "run_profile",
]
