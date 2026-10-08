"""Standalone audio export; no import of Readio or AudioCompose."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

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

try:
    __version__ = version("audioexport")
except PackageNotFoundError:
    __version__ = "0+uninstalled"

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
    "__version__",
    "doctor",
    "encode",
    "load_chapters",
    "load_profile",
    "normalize_bitrate",
    "probe",
    "run_profile",
]
