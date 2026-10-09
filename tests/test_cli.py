from __future__ import annotations

import json
import os
import shutil
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


def test_cli_audiobook_auto_discovers_directory_sidecars(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    shutil.copy2(wav, tracks / "01.wav")
    (tracks / "chapters.txt").write_text("00:00:00.000 Sidecar chapter\n", encoding="utf-8")
    cover = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:d=0.1",
            "-frames:v",
            "1",
            str(tracks / "cover.jpg"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert cover.returncode == 0, cover.stderr
    (tracks / "cover.jpeg").write_bytes(b"lower precedence")
    (tracks / "cover.png").write_bytes(b"lower precedence")

    output = tmp_path / "cli-book.m4b"
    completed = _run(
        "audiobook",
        str(tracks),
        "-o",
        str(output),
        "--title",
        "CLI Book",
        "--author",
        "CLI Author",
        "--json",
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert set(result["discovered_resources"]) == {"cover.jpg", "chapters.txt"}
    info = json.loads(_run("inspect", str(output), "--json").stdout)
    assert info["chapters"][0]["tags"]["title"] == "Sidecar chapter"
    assert any(
        stream.get("codec_type") == "video"
        and stream.get("disposition", {}).get("attached_pic") == 1
        for stream in info["streams"]
    )
    assert set(result["discovered_resources"]).issubset(
        json.loads(output.with_name(output.name + ".audioexport.json").read_text())["resources"][
            "auto_discovered"
        ]
    )

    no_auto_output = tmp_path / "no-auto.m4b"
    no_auto = _run(
        "audiobook",
        str(tracks),
        "-o",
        str(no_auto_output),
        "--no-auto-sidecars",
        "--json",
    )
    assert no_auto.returncode == 0, no_auto.stderr
    assert json.loads(no_auto.stdout)["discovered_resources"] == []
    no_auto_info = json.loads(_run("inspect", str(no_auto_output), "--json").stdout)
    assert not any(
        stream.get("disposition", {}).get("attached_pic") == 1 for stream in no_auto_info["streams"]
    )
    assert no_auto_info["chapters"][0]["tags"]["title"] == "01"


def test_cli_audiobook_profile_overrides_metadata_and_output(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    profile = tmp_path / "audiobook.toml"
    profile.write_text(
        'schema = "audioexport.audiobook.v1"\n'
        'output = "profile-book.m4b"\n'
        'inputs = ["speech.wav"]\n'
        '[metadata]\ntitle = "Profile title"\nauthor = "Profile author"\n'
        '[chapters]\nmode = "none"\n',
        encoding="utf-8",
    )
    output = tmp_path / "override.m4b"
    completed = _run(
        "audiobook",
        "--profile",
        str(profile),
        "--output",
        str(output),
        "--author",
        "CLI author",
        "--json",
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["path"] == str(output)
    assert result["chapter_count"] == 0
    info = json.loads(_run("inspect", str(output), "--json").stdout)
    tags = {key.lower(): value for key, value in info["format"].get("tags", {}).items()}
    assert tags["title"] == "Profile title"
    assert tags["artist"] == "CLI author"
