"""Explicit FFmpeg encoder/container policy, not Readio-dependent."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import InvalidExportError


@dataclass(frozen=True, slots=True)
class FormatSpec:
    extension: str
    codec: str
    muxer: str
    default_bitrate: str | None = None
    extra_args: tuple[str, ...] = ()
    fallback_codecs: tuple[str, ...] = ()


FORMATS: dict[str, FormatSpec] = {
    "wav": FormatSpec(".wav", "pcm_s16le", "wav"),
    "flac": FormatSpec(".flac", "flac", "flac"),
    "mp3": FormatSpec(".mp3", "libmp3lame", "mp3", "192k", ("-id3v2_version", "3")),
    "m4a": FormatSpec(".m4a", "aac", "ipod", "192k", ("-movflags", "+faststart")),
    "m4b": FormatSpec(".m4b", "aac", "ipod", "192k", ("-movflags", "+faststart")),
    # Some packaged FFmpeg builds omit the optional libvorbis encoder but
    # retain FFmpeg's native Vorbis encoder.
    "ogg": FormatSpec(".ogg", "libvorbis", "ogg", fallback_codecs=("vorbis",)),
    "opus": FormatSpec(".opus", "libopus", "opus", "96k"),
}

EXPECTED_CODECS = {
    "wav": "pcm_s16le",
    "flac": "flac",
    "mp3": "mp3",
    "m4a": "aac",
    "m4b": "aac",
    "ogg": "vorbis",
    "opus": "opus",
}


def normalize_format(name: str) -> str:
    name = name.lower().strip().removeprefix(".")
    if name not in FORMATS:
        raise InvalidExportError(
            f"unsupported format {name!r}; expected {', '.join(FORMATS)}",
            code="audioexport.format_unsupported",
        )
    return name


def encoder_options(codec: str) -> tuple[str, ...]:
    """Return FFmpeg options required by a selected encoder."""
    if codec == "vorbis":
        # FFmpeg's native Vorbis encoder is experimental and only accepts stereo.
        return ("-strict", "-2", "-ac", "2")
    return ()


def normalize_bitrate(value: str | int | None, fmt: str) -> str | None:
    fmt = normalize_format(fmt)
    if value is None:
        return FORMATS[fmt].default_bitrate
    if FORMATS[fmt].default_bitrate is None and fmt != "ogg":
        raise InvalidExportError(
            f"bitrate is not supported for {fmt}", code="audioexport.bitrate_unsupported"
        )
    # Adapted from Readio's normalize_bitrate, with a strict integer bits/s form.
    text = str(value).strip()
    match = re.fullmatch(r"(\d+)([kKmM]?)", text)
    if match is None or int(match[1]) <= 0:
        raise InvalidExportError(f"invalid bitrate {value!r}", code="audioexport.bitrate_invalid")
    quantity, unit = int(match[1]), match[2].lower()
    kbps = (
        quantity * 1000
        if unit == "m"
        else quantity
        if unit == "k"
        else max(1, round(quantity / 1000))
    )
    return f"{kbps}k"
