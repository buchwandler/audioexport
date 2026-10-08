"""Subprocess access to FFmpeg and FFprobe. No shell invocation."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .errors import EncodingError, ToolNotFoundError, VerificationError
from .formats import FORMATS


def executable(name: str, provided: str | Path | None = None) -> str:
    if provided is None:
        found = shutil.which(name)
    else:
        found = shutil.which(str(provided))
    if not found:
        raise ToolNotFoundError(
            f"{name} executable not found: {provided or name}",
            code=f"audioexport.{name}_missing",
        )
    return found


def run(
    args: list[str], *, error_type: type[EncodingError | VerificationError] = EncodingError
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise error_type(f"failed to run {args[0]}: {exc}", code="audioexport.tool_failed") from exc
    if result.returncode:
        tail = "\n".join(result.stderr.splitlines()[-6:]).strip()
        raise error_type(
            f"{Path(args[0]).name} exited {result.returncode}: {tail}",
            code="audioexport.tool_failed",
        )
    return result


def version(exe: str) -> str:
    return run([exe, "-version"]).stdout.splitlines()[0].strip()


def probe(path: Path | str, ffprobe: str | Path | None = None) -> dict[str, Any]:
    exe = executable("ffprobe", ffprobe)
    text = run(
        [
            exe,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            "-of",
            "json",
            str(path),
        ],
        error_type=VerificationError,
    ).stdout
    try:
        payload: dict[str, Any] = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise VerificationError(
            "ffprobe returned invalid JSON", code="audioexport.probe_invalid"
        ) from exc
    streams = payload.get("streams", [])
    if not isinstance(streams, list) or not any(
        row.get("codec_type") == "audio" for row in streams
    ):
        raise VerificationError("input has no audio stream", code="audioexport.no_audio")
    return payload


def doctor(
    *, ffmpeg: str | Path | None = None, ffprobe: str | Path | None = None
) -> dict[str, Any]:
    tools: dict[str, Any] = {}
    for name, override in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        try:
            exe = executable(name, override)
            tools[name] = {"available": True, "path": exe, "version": version(exe)}
        except (ToolNotFoundError, EncodingError) as exc:
            tools[name] = {"available": False, "error": str(exc)}
    ff = tools["ffmpeg"]
    supported: dict[str, Any] = {}
    if ff["available"]:
        try:
            encoders = run([ff["path"], "-hide_banner", "-encoders"]).stdout
            for name, spec in FORMATS.items():
                supported[name] = {
                    "encoder": spec.codec,
                    "available": any(
                        line.split()[-1:] and line.split()[1:2] == [spec.codec]
                        for line in encoders.splitlines()
                        if len(line.split()) >= 2
                    ),
                }
        except EncodingError:
            supported = {
                name: {"encoder": spec.codec, "available": None} for name, spec in FORMATS.items()
            }
    return {
        "tools": tools,
        "formats": supported,
        "ready": all(x["available"] for x in tools.values()),
    }
