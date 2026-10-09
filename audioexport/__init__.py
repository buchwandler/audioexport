"""Standalone audio export; no import of Readio or AudioCompose."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

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
from .preflight import preflight_profile
from .profile import ExportProfile, OutputSpec, ResolvedOutput, load_profile, resolve_output

try:
    __version__ = version("audioexport")
except PackageNotFoundError:
    __version__ = "0+uninstalled"

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
    "ResolvedOutput",
    "ToolNotFoundError",
    "VerificationError",
    "__version__",
    "build_audiobook",
    "doctor",
    "encode",
    "load_audiobook_profile",
    "load_chapters",
    "load_profile",
    "normalize_bitrate",
    "preflight_profile",
    "probe",
    "resolve_output",
    "run_profile",
]
