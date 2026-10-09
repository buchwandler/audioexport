from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from audioexport import EncodingError, InvalidExportError, encode, pipeline


def _temporary_artifacts(folder: Path) -> list[Path]:
    return [path for path in folder.iterdir() if path.name.endswith((".tmp", ".backup"))]


def test_manifest_commit_failure_restores_last_good_pair(
    wav: Path, tmp_path: Path, have_tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "last-good.mp3"
    original = encode(wav, output)
    audio_bytes = output.read_bytes()
    manifest_bytes = original.manifest_path.read_bytes()
    real_replace = os.replace

    def fail_staged_manifest(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        if Path(destination) == original.manifest_path and Path(source).suffix == ".tmp":
            raise OSError("injected manifest commit failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_staged_manifest)
    with pytest.raises(EncodingError) as error:
        encode(wav, output, bitrate="128k")
    assert error.value.code == "audioexport.manifest_commit_failed"
    assert output.read_bytes() == audio_bytes
    assert original.manifest_path.read_bytes() == manifest_bytes
    assert _temporary_artifacts(tmp_path) == []

    monkeypatch.setattr(os, "replace", real_replace)
    assert encode(wav, output).reused


def test_manifest_staging_failure_does_not_install_new_audio(
    wav: Path, tmp_path: Path, have_tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "new.mp3"
    monkeypatch.setattr(
        pipeline,
        "_write_json_temp",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected disk-full failure")),
    )

    with pytest.raises(EncodingError) as error:
        encode(wav, output)

    assert error.value.code == "audioexport.manifest_write_failed"
    assert not output.exists()
    assert not output.with_name(output.name + ".audioexport.json").exists()
    assert _temporary_artifacts(tmp_path) == []


def test_audio_replace_failure_leaves_old_artifact_unchanged(
    wav: Path, tmp_path: Path, have_tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "old.mp3"
    original = encode(wav, output)
    audio_bytes = output.read_bytes()
    manifest_bytes = original.manifest_path.read_bytes()
    real_replace = os.replace

    def fail_audio_commit(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        if Path(destination) == output and Path(source).suffix == ".mp3":
            raise OSError("injected audio replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_audio_commit)
    with pytest.raises(EncodingError) as error:
        encode(wav, output, bitrate="128k")

    assert error.value.code == "audioexport.output_commit_failed"
    assert output.read_bytes() == audio_bytes
    assert original.manifest_path.read_bytes() == manifest_bytes
    assert _temporary_artifacts(tmp_path) == []


def test_new_output_is_removed_if_manifest_commit_fails(
    wav: Path, tmp_path: Path, have_tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "new.mp3"
    real_replace = os.replace

    def fail_manifest_commit(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        if (
            Path(destination) == output.with_name(output.name + ".audioexport.json")
            and Path(source).suffix == ".tmp"
        ):
            raise OSError("injected sidecar replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_manifest_commit)
    with pytest.raises(EncodingError) as error:
        encode(wav, output)

    assert error.value.code == "audioexport.manifest_commit_failed"
    assert not output.exists()
    assert not output.with_name(output.name + ".audioexport.json").exists()
    assert _temporary_artifacts(tmp_path) == []


def test_symlink_outputs_are_never_followed_or_replaced(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    target = tmp_path / "unrelated.bin"
    target.write_bytes(b"user data")
    output = tmp_path / "linked.mp3"
    try:
        output.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(InvalidExportError, match="symlink output"):
        encode(wav, output, force=True)

    assert output.is_symlink()
    assert target.read_bytes() == b"user data"


def test_same_target_contention_is_rejected_and_lock_is_released(
    wav: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()

    def blocked_encode(*args: Any, **kwargs: Any) -> str:
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release the first writer")
        return "first writer finished"

    monkeypatch.setattr(pipeline, "_encode_unlocked", blocked_encode)
    output = tmp_path / "same.mp3"
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(encode, wav, output)
        assert started.wait(timeout=5)
        with pytest.raises(EncodingError) as error:
            encode(wav, output)
        assert error.value.code == "audioexport.output_busy"
        release.set()
        assert first.result(timeout=5) == "first writer finished"

    assert not pipeline._output_lock_path(output).exists()


def test_incomplete_rollback_preserves_recovery_backup_and_refuses_reuse(
    wav: Path, tmp_path: Path, have_tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "recover.mp3"
    original = encode(wav, output)
    original_bytes = output.read_bytes()
    manifest_bytes = original.manifest_path.read_bytes()
    real_replace = os.replace

    def fail_commit_and_audio_restore(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ) -> None:
        source_path = Path(source)
        if Path(destination) == original.manifest_path and source_path.suffix == ".tmp":
            raise OSError("injected manifest commit failure")
        if Path(destination) == output and source_path.suffix == ".backup":
            raise OSError("injected rollback failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_commit_and_audio_restore)
    with pytest.raises(EncodingError) as error:
        encode(wav, output, bitrate="128k")

    assert error.value.code == "audioexport.commit_recovery_failed"
    assert output.read_bytes() != original_bytes
    assert original.manifest_path.read_bytes() == manifest_bytes
    backups = [path for path in _temporary_artifacts(tmp_path) if path.suffix == ".backup"]
    assert len(backups) == 1
    assert backups[0].read_bytes() == original_bytes
    assert str(backups[0]) in str(error.value)
    with pytest.raises(FileExistsError):
        encode(wav, output)


def test_directory_output_is_rejected_without_modification(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    output = tmp_path / "directory.mp3"
    output.mkdir()

    with pytest.raises(InvalidExportError, match="regular file"):
        encode(wav, output)

    assert output.is_dir()


def test_manifest_symlink_is_rejected_without_following_it(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    output = tmp_path / "sidecar-link.mp3"
    target = tmp_path / "unrelated.json"
    target.write_text("{}", encoding="utf-8")
    sidecar = output.with_name(output.name + ".audioexport.json")
    try:
        sidecar.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(InvalidExportError, match="sidecar"):
        encode(wav, output)

    assert not output.exists()
    assert sidecar.is_symlink()
    assert target.read_text(encoding="utf-8") == "{}"
