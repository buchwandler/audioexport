"""Stable public exceptions suitable for API consumers."""

from __future__ import annotations


class AudioExportError(Exception):
    def __init__(self, message: str, *, code: str = "audioexport.error") -> None:
        super().__init__(message)
        self.code = code


class InvalidExportError(AudioExportError, ValueError):
    pass


class ToolNotFoundError(AudioExportError):
    pass


class EncodingError(AudioExportError):
    pass


class VerificationError(AudioExportError):
    pass
