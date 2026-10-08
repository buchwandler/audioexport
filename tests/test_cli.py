from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[1])
    return subprocess.run(
        [sys.executable, "-m", "audioexport", *args],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_version() -> None:
    result = _run("--version")
    assert result.returncode == 0, result.stderr
    assert "audioexport " in result.stdout


def test_cli_encode_inspect(wav: Path, have_tools: None) -> None:
    output = wav.with_name("cli.mp3")
    completed = _run(
        "encode",
        str(wav),
        "--format",
        "mp3",
        "-o",
        str(output),
        "--metadata",
        "title=cli",
        "--json",
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["reused"] is False
    inspect = _run("inspect", str(output), "--json")
    assert inspect.returncode == 0, inspect.stderr
    assert json.loads(inspect.stdout)["audioexport"]["schema"] == "audioexport.manifest.v1"
    repeat = _run(
        "encode",
        str(wav),
        "--format",
        "mp3",
        "-o",
        str(output),
        "--metadata",
        "title=cli",
        "--json",
    )
    assert json.loads(repeat.stdout)["reused"] is True


def test_doctor(have_tools: None) -> None:
    result = _run("doctor", "--json")
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["ready"]
    assert parsed["formats"]["mp3"]["encoder"] == "libmp3lame"
