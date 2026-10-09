from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from audioexport import EncodingError, VerificationError, fftools


def _mock_probe(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    monkeypatch.setattr(fftools, "executable", lambda name, provided=None: "ffprobe-test")
    monkeypatch.setattr(
        fftools,
        "run",
        lambda args, error_type=fftools.EncodingError: subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(payload), stderr=""
        ),
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"streams": [None], "format": {}, "chapters": []},
        {"streams": [], "format": [], "chapters": []},
        {"streams": [{"codec_type": "audio", "channels": "many"}], "format": {}},
        {"streams": [{"codec_type": "audio", "tags": "invalid"}], "format": {}},
        {"streams": [{"codec_type": "audio"}], "format": {"duration": "N/A"}},
        {
            "streams": [{"codec_type": "audio"}],
            "format": {},
            "chapters": [{"tags": []}],
        },
        {
            "streams": [{"codec_type": "audio", "sample_rate": 24000}],
            "format": {},
            "chapters": [{"start_time": "not-a-number"}],
        },
    ],
)
def test_probe_rejects_malformed_structures_with_stable_error(
    monkeypatch: pytest.MonkeyPatch, payload: Any
) -> None:
    _mock_probe(monkeypatch, payload)
    with pytest.raises(VerificationError) as error:
        fftools.probe("ignored.wav")
    assert error.value.code in {"audioexport.probe_invalid", "audioexport.no_audio"}


def test_probe_accepts_valid_structured_audio_result(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "streams": [
            {
                "codec_type": "audio",
                "codec_name": "pcm_s16le",
                "channels": 1,
                "sample_rate": "24000",
                "duration": "1.25",
            }
        ],
        "format": {"duration": "1.25", "tags": {"title": "Sample"}},
        "chapters": [{"start_time": "0.0", "end_time": "1.25", "tags": {"title": "Intro"}}],
    }
    _mock_probe(monkeypatch, payload)

    assert fftools.probe("ignored.wav") == payload


def test_doctor_reports_missing_encoder_and_selected_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fftools, "executable", lambda name, provided=None: name)
    monkeypatch.setattr(fftools, "version", lambda exe: f"{exe} test version")

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        encoders = """Encoders:
 A..... pcm_s16le
 A..... flac
 A..... libmp3lame
 A..... aac
 A..... libvorbis
"""
        return subprocess.CompletedProcess(args, 0, stdout=encoders, stderr="")

    monkeypatch.setattr(fftools, "run", fake_run)
    result = fftools.doctor(requested_formats=("mp3", "opus"))

    assert result["ready"] is True
    assert result["all_formats_ready"] is False
    assert result["requested_formats_ready"] is False
    assert result["formats"]["opus"]["available"] is False
    assert "libopus" in result["formats"]["opus"]["reason"]
    assert result["formats"]["mp3"]["available"] is True
    assert Path(result["tools"]["ffmpeg"]["path"]).name == "ffmpeg"
    assert result["package"]["name"] == "audioexport"
    assert result["package"]["version"]


def test_doctor_uses_native_vorbis_when_libvorbis_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fftools, "executable", lambda name, provided=None: name)
    monkeypatch.setattr(fftools, "version", lambda exe: f"{exe} test version")

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout="Encoders:\n A..... vorbis\n", stderr="")

    monkeypatch.setattr(fftools, "run", fake_run)
    result = fftools.doctor(requested_formats=("ogg",))

    assert result["requested_formats_ready"] is True
    assert result["formats"]["ogg"]["available"] is True
    assert result["formats"]["ogg"]["encoder"] == "vorbis"


def test_profile_encoder_failure_is_preflighted_before_output(
    tmp_path: Path, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from audioexport import pipeline

    profile = tmp_path / "needs-opus.toml"
    profile.write_text(
        'schema = "audioexport.profile.v1"\n'
        '[[outputs]]\nformat = "wav"\n'
        '[[outputs]]\nformat = "opus"\n',
        encoding="utf-8",
    )
    formats = {
        name: {"available": name != "opus", "reason": "libopus is missing"}
        for name in ("wav", "flac", "mp3", "m4a", "m4b", "ogg", "opus")
    }
    monkeypatch.setattr(
        pipeline,
        "fftool_doctor",
        lambda **kwargs: {
            "tools": {
                "ffmpeg": {"available": True},
                "ffprobe": {"available": True},
            },
            "formats": formats,
            "requested_formats_ready": False,
        },
    )
    out = tmp_path / "out"

    with pytest.raises(EncodingError) as error:
        pipeline.run_profile(wav, profile, out)

    assert error.value.code == "audioexport.encoder_unavailable"
    assert not out.exists()
