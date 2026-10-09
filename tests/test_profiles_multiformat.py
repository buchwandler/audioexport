from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from audioexport import InvalidExportError, encode, probe, run_profile
from audioexport.profile import load_profile, resolve_output

FORMATS = ("mp3", "flac", "m4a", "m4b", "ogg", "opus", "wav")


def _write_cover(path: Path, color: str = "red") -> None:
    result = subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=64x64:d=0.1",
            "-frames:v",
            "1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _timeline(path: Path, title: str = "Intro") -> None:
    path.write_text(json.dumps({"chapters": [{"title": title, "start_ms": 0}]}), encoding="utf-8")


def test_one_profile_exports_all_formats_with_capability_defaults(
    tmp_path: Path, wav: Path, have_tools: None
) -> None:
    _write_cover(tmp_path / "cover.jpg")
    _timeline(tmp_path / "chapters.json")
    profile = tmp_path / "all.toml"
    outputs = []
    for fmt in FORMATS:
        options = ""
        if fmt == "mp3":
            options = "use_cover = true\nuse_chapters = false\n"
        elif fmt == "flac":
            options = "use_cover = false\nuse_chapters = false\n"
        elif fmt == "m4b":
            options = "use_cover = true\nuse_chapters = true\n"
        outputs.append(f"[[outputs]]\nformat = {fmt!r}\n{options}")
    profile.write_text(
        'schema = "audioexport.profile.v1"\n'
        'cover = "cover.jpg"\n'
        'timeline = "chapters.json"\n'
        '[metadata]\ntitle = "Mixed exports"\n' + "\n".join(outputs),
        encoding="utf-8",
    )

    results = run_profile(wav, profile, tmp_path / "out")

    assert [result.format for result in results] == list(FORMATS)
    assert all(result.path.is_file() and result.path.stat().st_size > 0 for result in results)
    assert [result.chapter_count for result in results] == [0, 0, 1, 1, 0, 0, 0]
    for result in results:
        info = probe(result.path)
        has_cover = any(
            stream.get("codec_type") == "video"
            and stream.get("disposition", {}).get("attached_pic") == 1
            for stream in info["streams"]
        )
        assert has_cover is (result.format in {"mp3", "m4a", "m4b"})
        assert len(info.get("chapters", [])) == (1 if result.format in {"m4a", "m4b"} else 0)
        if result.format in {"m4a", "m4b"}:
            assert info["format"].get("tags", {}).get("title") == "Mixed exports"
            assert info["chapters"][0].get("tags", {}).get("title") == "Intro"


def test_profile_preflight_rejects_late_capability_error_before_writing(
    tmp_path: Path, wav: Path
) -> None:
    _timeline(tmp_path / "chapters.json")
    profile = tmp_path / "bad.toml"
    profile.write_text(
        'schema = "audioexport.profile.v1"\n'
        'timeline = "chapters.json"\n'
        '[[outputs]]\nformat = "wav"\n'
        '[[outputs]]\nformat = "mp3"\nuse_chapters = true\n',
        encoding="utf-8",
    )
    out = tmp_path / "out"

    with pytest.raises(InvalidExportError, match="chapters are unsupported"):
        run_profile(wav, profile, out)

    assert not out.exists()


def test_profile_preflight_detects_default_and_explicit_name_collision(
    tmp_path: Path, wav: Path
) -> None:
    profile = tmp_path / "collision.toml"
    profile.write_text(
        'schema = "audioexport.profile.v1"\n'
        '[[outputs]]\nformat = "mp3"\n'
        '[[outputs]]\nformat = "mp3"\nfilename = "SPEECH.mp3"\n',
        encoding="utf-8",
    )
    out = tmp_path / "out"

    with pytest.raises(InvalidExportError, match="duplicate profile output filenames"):
        run_profile(wav, profile, out)

    assert not out.exists()


def test_profile_rejects_non_boolean_selectors_but_defers_missing_resources(
    tmp_path: Path,
) -> None:
    non_boolean = tmp_path / "bad-selector.toml"
    non_boolean.write_text(
        'schema = "audioexport.profile.v1"\n[[outputs]]\nformat = "mp3"\nuse_cover = "yes"\n',
        encoding="utf-8",
    )
    with pytest.raises(InvalidExportError, match="use_cover must be a boolean"):
        load_profile(non_boolean)

    missing = tmp_path / "missing.toml"
    missing.write_text(
        'schema = "audioexport.profile.v1"\ncover = "absent.jpg"\n[[outputs]]\nformat = "mp3"\n',
        encoding="utf-8",
    )
    profile = load_profile(missing)
    assert profile.cover == (tmp_path / "absent.jpg").resolve()
    assert resolve_output(profile, profile.outputs[0], "book").cover == profile.cover


def test_profile_cache_identity_tracks_only_consumed_resources(
    tmp_path: Path, wav: Path, have_tools: None
) -> None:
    cover = tmp_path / "cover.jpg"
    timeline = tmp_path / "chapters.json"
    _write_cover(cover)
    _timeline(timeline)
    profile = tmp_path / "cache.toml"
    formats = ("wav", "flac", "mp3", "m4a", "m4b", "ogg", "opus")

    def set_profile(*, bitrate: str = "128k", title: str = "Book") -> None:
        outputs = []
        for fmt in formats:
            options = f'bitrate = "{bitrate}"\n' if fmt == "mp3" else ""
            outputs.append(f"[[outputs]]\nformat = {fmt!r}\n{options}")
        profile.write_text(
            'schema = "audioexport.profile.v1"\n'
            'cover = "cover.jpg"\n'
            'timeline = "chapters.json"\n'
            f'[metadata]\ntitle = "{title}"\n' + "".join(outputs),
            encoding="utf-8",
        )

    out = tmp_path / "out"
    set_profile()
    initial = run_profile(wav, profile, out)
    repeated = run_profile(wav, profile, out)
    assert [result.format for result in initial] == list(formats)
    assert [result.reused for result in repeated] == [True] * len(formats)
    assert [result.export_id for result in repeated] == [result.export_id for result in initial]

    _timeline(timeline, "Changed chapter")
    timeline_changed = run_profile(wav, profile, out)
    assert [result.reused for result in timeline_changed] == [
        True,
        True,
        True,
        False,
        False,
        True,
        True,
    ]

    set_profile(bitrate="192k")
    bitrate_changed = run_profile(wav, profile, out)
    assert [result.reused for result in bitrate_changed] == [
        True,
        True,
        False,
        True,
        True,
        True,
        True,
    ]

    _write_cover(cover, "blue")
    cover_changed = run_profile(wav, profile, out)
    assert [result.reused for result in cover_changed] == [
        True,
        True,
        False,
        False,
        False,
        True,
        True,
    ]

    set_profile(bitrate="192k", title="Updated Book")
    metadata_changed = run_profile(wav, profile, out)
    assert [result.reused for result in metadata_changed] == [False] * len(formats)


def test_direct_encode_keeps_unsupported_timeline_strict(tmp_path: Path, wav: Path) -> None:
    timeline = tmp_path / "chapters.json"
    _timeline(timeline)
    with pytest.raises(InvalidExportError, match="chapter marks require M4B or M4A"):
        encode(wav, tmp_path / "direct.mp3", timeline=timeline)
    assert not (tmp_path / "direct.mp3").exists()


def test_output_resolution_uses_automatic_defaults_and_explicit_suppression(
    tmp_path: Path,
) -> None:
    (tmp_path / "cover.jpg").write_bytes(b"cover")
    _timeline(tmp_path / "chapters.json")
    profile_path = tmp_path / "selectors.toml"
    profile_path.write_text(
        'schema = "audioexport.profile.v1"\n'
        'cover = "cover.jpg"\ntimeline = "chapters.json"\n'
        '[[outputs]]\nformat = "flac"\nuse_cover = false\nuse_chapters = false\n'
        '[[outputs]]\nformat = "m4b"\n',
        encoding="utf-8",
    )
    loaded = load_profile(profile_path)

    flac = resolve_output(loaded, loaded.outputs[0], "speech")
    m4b = resolve_output(loaded, loaded.outputs[1], "speech")
    assert flac.filename == "speech.flac"
    assert flac.cover is None and flac.timeline is None
    assert m4b.cover == loaded.cover and m4b.timeline == loaded.timeline
