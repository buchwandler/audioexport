from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from audioexport import EncodingError, InvalidExportError, audiobook, pipeline
from audioexport.audiobook import build_audiobook


def _temporary_artifacts(folder: Path) -> list[Path]:
    return [
        path
        for path in folder.iterdir()
        if path.name.endswith((".tmp", ".backup"))
        or (path.name.startswith(".") and path.suffix == ".m4b")
    ]


def test_segment_failure_preserves_last_good_pair_and_cleans_workdir(
    wav: Path, tmp_path: Path, have_tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "safe.m4b"
    original = build_audiobook([wav], output, bitrate="96k")
    audio_bytes = output.read_bytes()
    manifest_bytes = original.manifest_path.read_bytes()

    def fail_segment(*args: Any, **kwargs: Any) -> int:
        raise EncodingError("injected segment failure", code="audioexport.audiobook_segment_failed")

    monkeypatch.setattr(audiobook, "_normalize_segment", fail_segment)
    with pytest.raises(EncodingError) as error:
        build_audiobook([wav], output, bitrate="128k")

    assert error.value.code == "audioexport.audiobook_segment_failed"
    assert output.read_bytes() == audio_bytes
    assert original.manifest_path.read_bytes() == manifest_bytes
    assert not list(tmp_path.glob(".safe.audioexport-*"))
    assert not [path for path in _temporary_artifacts(tmp_path) if path.name.startswith(".safe.")]


def test_concat_failure_preserves_last_good_pair(
    wav: Path, tmp_path: Path, have_tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "concat-safe.m4b"
    original = build_audiobook([wav], output, bitrate="96k")
    audio_bytes = output.read_bytes()
    manifest_bytes = original.manifest_path.read_bytes()

    def fail_concat(*args: Any, **kwargs: Any) -> int:
        raise EncodingError("injected concat failure", code="audioexport.audiobook_concat_failed")

    monkeypatch.setattr(audiobook, "_concat_segments", fail_concat)
    with pytest.raises(EncodingError) as error:
        build_audiobook([wav], output, bitrate="128k")

    assert error.value.code == "audioexport.audiobook_concat_failed"
    assert output.read_bytes() == audio_bytes
    assert original.manifest_path.read_bytes() == manifest_bytes
    assert not list(tmp_path.glob(".concat-safe.audioexport-*"))


def test_final_mux_failure_preserves_last_good_pair(
    wav: Path, tmp_path: Path, have_tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "mux-safe.m4b"
    original = build_audiobook([wav], output, bitrate="96k")
    audio_bytes = output.read_bytes()
    manifest_bytes = original.manifest_path.read_bytes()
    real_run = audiobook.run

    def fail_final_mux(args: list[str], **kwargs: Any) -> Any:
        if str(args[-1]).endswith(".m4b"):
            raise EncodingError("injected mux failure", code="audioexport.tool_failed")
        return real_run(args, **kwargs)

    monkeypatch.setattr(audiobook, "run", fail_final_mux)
    with pytest.raises(EncodingError) as error:
        build_audiobook([wav], output, bitrate="128k")

    assert error.value.code == "audioexport.audiobook_mux_failed"
    assert output.read_bytes() == audio_bytes
    assert original.manifest_path.read_bytes() == manifest_bytes
    assert _temporary_artifacts(tmp_path) == []


def test_manifest_commit_failure_restores_last_good_audiobook_pair(
    wav: Path, tmp_path: Path, have_tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "rollback.m4b"
    original = build_audiobook([wav], output, bitrate="96k")
    audio_bytes = output.read_bytes()
    manifest_bytes = original.manifest_path.read_bytes()
    real_replace = os.replace

    def fail_manifest_commit(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        if Path(destination) == original.manifest_path and Path(source).suffix == ".tmp":
            raise OSError("injected manifest commit failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_manifest_commit)
    with pytest.raises(EncodingError) as error:
        build_audiobook([wav], output, bitrate="128k")

    assert error.value.code == "audioexport.manifest_commit_failed"
    assert output.read_bytes() == audio_bytes
    assert original.manifest_path.read_bytes() == manifest_bytes
    assert _temporary_artifacts(tmp_path) == []


def test_output_and_manifest_symlinks_are_refused(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    target = tmp_path / "user-data.bin"
    target.write_bytes(b"keep")
    output = tmp_path / "output.m4b"
    try:
        output.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(InvalidExportError, match="symlink output"):
        build_audiobook([wav], output, force=True)
    assert output.is_symlink()
    assert target.read_bytes() == b"keep"

    output.unlink()
    sidecar = output.with_name(output.name + ".audioexport.json")
    sidecar.symlink_to(target)
    with pytest.raises(InvalidExportError, match="sidecar"):
        build_audiobook([wav], output)
    assert sidecar.is_symlink()
    assert target.read_bytes() == b"keep"


def test_same_target_concurrent_writers_are_rejected(
    wav: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()

    def blocked_build(*args: Any, **kwargs: Any) -> str:
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release the first audiobook writer")
        return "first writer finished"

    monkeypatch.setattr(audiobook, "_build_unlocked", blocked_build)
    output = tmp_path / "contended.m4b"
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(build_audiobook, [wav], output)
        assert started.wait(timeout=5)
        with pytest.raises(EncodingError) as error:
            build_audiobook([wav], output)
        assert error.value.code == "audioexport.output_busy"
        release.set()
        assert first.result(timeout=5) == "first writer finished"

    assert not pipeline._output_lock_path(output).exists()
