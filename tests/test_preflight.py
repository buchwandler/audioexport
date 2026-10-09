from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import audioexport.preflight as preflight_module
from audioexport.errors import EncodingError, InvalidExportError, ToolNotFoundError
from audioexport.preflight import preflight_profile as public_preflight
from audioexport.profile import ExportProfile, OutputSpec, load_profile

ALL_ENCODERS = frozenset(
    {"pcm_s16le", "flac", "libmp3lame", "aac", "libvorbis", "vorbis", "libopus"}
)
PROBE_RESULT: dict[str, Any] = {
    "streams": [{"codec_type": "audio", "duration": "2.0"}],
    "format": {"duration": "2.0"},
}


def _tools(
    monkeypatch: pytest.MonkeyPatch,
    *,
    encoders: frozenset[str] = ALL_ENCODERS,
) -> dict[str, int]:
    counts = {"probe": 0, "encoders": 0}

    def executable(name: str, provided: str | Path | None = None) -> str:
        return str(provided) if provided is not None else f"/tools/{name}"

    def probe(source: Path, ffprobe: str) -> dict[str, Any]:
        counts["probe"] += 1
        assert Path(source).is_file()
        assert ffprobe == "/tools/ffprobe"
        return PROBE_RESULT

    def available_encoders(ffmpeg: str) -> frozenset[str]:
        counts["encoders"] += 1
        assert ffmpeg == "/tools/ffmpeg"
        return encoders

    monkeypatch.setattr(preflight_module, "executable", executable)
    monkeypatch.setattr(preflight_module, "probe", probe)
    monkeypatch.setattr(preflight_module, "available_encoders", available_encoders)
    return counts


def _valid_cover(path: Path) -> None:
    path.write_bytes(b"\xff\xd8\xff" + b"cover-data")


def _valid_timeline(path: Path, start_ms: int = 0) -> None:
    path.write_text(
        json.dumps({"chapters": [{"title": "Intro", "start_ms": start_ms}]}),
        encoding="utf-8",
    )


def test_preflight_is_public_from_its_module_and_preserves_order_without_writes(
    tmp_path: Path, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = _tools(monkeypatch)
    cover = tmp_path / "cover.jpg"
    timeline = tmp_path / "chapters.json"
    _valid_cover(cover)
    _valid_timeline(timeline)
    profile = ExportProfile(
        (
            OutputSpec("flac"),
            OutputSpec("mp3"),
            OutputSpec("m4a"),
            OutputSpec("m4b"),
            OutputSpec("wav"),
            OutputSpec("ogg"),
            OutputSpec("opus"),
        ),
        {"title": "Example"},
        cover,
        timeline,
    )
    before = {path: path.read_bytes() for path in (wav, cover, timeline)}
    uncreated_output = tmp_path / "would-be-output"

    resolved = public_preflight(profile, wav)

    assert [item.format for item in resolved] == ["flac", "mp3", "m4a", "m4b", "wav", "ogg", "opus"]
    assert [item.cover is not None for item in resolved] == [
        False,
        True,
        True,
        True,
        False,
        False,
        False,
    ]
    assert [item.timeline is not None for item in resolved] == [
        False,
        False,
        True,
        True,
        False,
        False,
        False,
    ]
    assert counts == {"probe": 1, "encoders": 1}
    assert not uncreated_output.exists()
    assert {path: path.read_bytes() for path in before} == before
    assert not list(tmp_path.glob("*.audioexport.json"))


def test_preflight_does_not_validate_unselected_missing_global_resources(
    tmp_path: Path, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = _tools(monkeypatch)
    profile_path = tmp_path / "flac-only.toml"
    profile_path.write_text(
        'schema = "audioexport.profile.v1"\n'
        'cover = "missing.jpg"\ntimeline = "missing.json"\n'
        '[[outputs]]\nformat = "flac"\n',
        encoding="utf-8",
    )

    profile = load_profile(profile_path)
    resolved = public_preflight(profile, wav)

    assert len(resolved) == 1
    assert resolved[0].cover is None
    assert resolved[0].timeline is None
    assert counts == {"probe": 1, "encoders": 1}


def test_preflight_rejects_duplicate_resolved_filenames_before_tool_calls(
    tmp_path: Path, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = _tools(monkeypatch)
    profile = ExportProfile((OutputSpec("mp3"), OutputSpec("MP3")), {})

    with pytest.raises(InvalidExportError) as error:
        public_preflight(profile, wav)

    assert error.value.code == "audioexport.profile_invalid"
    assert counts == {"probe": 0, "encoders": 0}


def test_preflight_validates_only_requested_encoders_and_queries_once(
    tmp_path: Path, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = _tools(monkeypatch, encoders=frozenset({"aac"}))
    profile = ExportProfile((OutputSpec("m4a"), OutputSpec("m4b")), {})

    resolved = public_preflight(profile, wav)

    assert [item.format for item in resolved] == ["m4a", "m4b"]
    assert counts["encoders"] == 1


def test_preflight_reports_a_missing_requested_encoder(
    tmp_path: Path, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tools(monkeypatch, encoders=frozenset())

    with pytest.raises(EncodingError) as error:
        public_preflight(ExportProfile((OutputSpec("opus"),), {}), wav)

    assert error.value.code == "audioexport.encoder_missing"


@pytest.mark.parametrize("missing", ("ffmpeg", "ffprobe"))
def test_preflight_reports_missing_tools(
    tmp_path: Path, wav: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    def executable(name: str, provided: str | Path | None = None) -> str:
        if name == missing:
            raise ToolNotFoundError(f"missing {name}", code=f"audioexport.{name}_missing")
        return f"/tools/{name}"

    monkeypatch.setattr(preflight_module, "executable", executable)
    profile = ExportProfile((OutputSpec("wav"),), {})

    with pytest.raises(ToolNotFoundError) as error:
        public_preflight(profile, wav)

    assert error.value.code == f"audioexport.{missing}_missing"


def test_preflight_reports_missing_source_and_invalid_duration(
    tmp_path: Path, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(InvalidExportError) as missing:
        public_preflight(ExportProfile((OutputSpec("wav"),), {}), tmp_path / "absent.wav")
    assert missing.value.code == "audioexport.source_missing"

    _tools(monkeypatch)
    monkeypatch.setattr(
        preflight_module,
        "probe",
        lambda source, ffprobe: {"streams": [{"codec_type": "audio"}], "format": {}},
    )
    with pytest.raises(InvalidExportError) as duration:
        public_preflight(ExportProfile((OutputSpec("wav"),), {}), wav)
    assert duration.value.code == "audioexport.duration_invalid"


def test_preflight_validates_selected_cover_and_chapters(
    tmp_path: Path, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tools(monkeypatch)
    invalid_cover = tmp_path / "bad.jpg"
    invalid_cover.write_bytes(b"not an image")
    with pytest.raises(InvalidExportError) as cover_error:
        public_preflight(ExportProfile((OutputSpec("mp3"),), {}, invalid_cover), wav)
    assert cover_error.value.code == "audioexport.cover_invalid"

    missing_timeline = tmp_path / "missing.json"
    with pytest.raises(InvalidExportError) as timeline_error:
        public_preflight(ExportProfile((OutputSpec("m4b"),), {}, timeline=missing_timeline), wav)
    assert timeline_error.value.code == "audioexport.timeline_invalid"

    outside_duration = tmp_path / "outside.json"
    _valid_timeline(outside_duration, 3000)
    with pytest.raises(InvalidExportError) as chapters_error:
        public_preflight(ExportProfile((OutputSpec("m4b"),), {}, timeline=outside_duration), wav)
    assert chapters_error.value.code == "audioexport.chapters_invalid"


def test_preflight_rejects_invalid_metadata_before_tool_work(
    tmp_path: Path, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = _tools(monkeypatch)
    profile = ExportProfile((OutputSpec("wav"),), {"bad key": "value"})

    with pytest.raises(InvalidExportError) as error:
        public_preflight(profile, wav)

    assert error.value.code == "audioexport.metadata_invalid"
    assert counts == {"probe": 0, "encoders": 0}


def test_preflight_validates_each_shared_resource_once(
    tmp_path: Path, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tools(monkeypatch)
    cover = tmp_path / "cover.jpg"
    timeline = tmp_path / "chapters.json"
    _valid_cover(cover)
    _valid_timeline(timeline)
    calls = {"cover": 0, "timeline": 0, "normalize": 0}
    original_cover_kind = preflight_module.cover_kind
    original_load_chapters = preflight_module.load_chapters
    original_normalize_chapters = preflight_module.normalize_chapters

    def check_cover(path: Path) -> str:
        calls["cover"] += 1
        return original_cover_kind(path)

    def read_timeline(path: Path) -> tuple[Any, ...]:
        calls["timeline"] += 1
        return original_load_chapters(path)

    def normalize(chapters: tuple[Any, ...], duration_ms: int) -> tuple[Any, ...]:
        calls["normalize"] += 1
        return original_normalize_chapters(chapters, duration_ms)

    monkeypatch.setattr(preflight_module, "cover_kind", check_cover)
    monkeypatch.setattr(preflight_module, "load_chapters", read_timeline)
    monkeypatch.setattr(preflight_module, "normalize_chapters", normalize)
    profile = ExportProfile(
        (OutputSpec("mp3"), OutputSpec("m4a"), OutputSpec("m4b")),
        {},
        cover,
        timeline,
    )

    public_preflight(profile, wav)

    assert calls == {"cover": 1, "timeline": 1, "normalize": 1}
