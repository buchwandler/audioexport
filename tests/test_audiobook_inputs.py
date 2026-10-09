from __future__ import annotations

from pathlib import Path

import pytest

from audioexport.errors import InvalidExportError
from audioexport.inputs import discover_audio_inputs, natural_sort_key, source_title


def test_directory_inputs_are_naturally_sorted_and_sidecars_ignored(tmp_path: Path) -> None:
    for name in ("10.mp3", "2.mp3", "1.mp3", "chapters.txt", "cover.jpg", "notes.txt"):
        (tmp_path / name).write_bytes(b"data")

    assert [path.name for path in discover_audio_inputs(tmp_path)] == ["1.mp3", "2.mp3", "10.mp3"]


def test_multiple_inputs_preserve_order_and_reject_resolved_duplicates(tmp_path: Path) -> None:
    first = tmp_path / "first track.wav"
    second = tmp_path / "第二.mp3"
    first.touch()
    second.touch()

    assert discover_audio_inputs([second, first]) == (second.resolve(), first.resolve())
    with pytest.raises(InvalidExportError) as error:
        discover_audio_inputs([first, first.resolve()])
    assert error.value.code == "audioexport.audiobook_duplicate_input"


def test_empty_directory_and_unsupported_explicit_input_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidExportError, match="contains no supported"):
        discover_audio_inputs(tmp_path)

    sidecar = tmp_path / "chapters.txt"
    sidecar.touch()
    with pytest.raises(InvalidExportError) as error:
        discover_audio_inputs(sidecar)
    assert error.value.code == "audioexport.audiobook_input_invalid"


def test_missing_and_empty_input_list_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidExportError) as error:
        discover_audio_inputs([])
    assert error.value.code == "audioexport.audiobook_no_inputs"

    with pytest.raises(InvalidExportError):
        discover_audio_inputs(tmp_path / "missing.wav")


def test_natural_key_is_deterministic() -> None:
    names = ["chapter 10.mp3", "chapter 2.mp3", "Chapter 1.mp3"]
    assert sorted(names, key=natural_sort_key) == [
        "Chapter 1.mp3",
        "chapter 2.mp3",
        "chapter 10.mp3",
    ]


def test_source_title_prefers_audio_tag_then_format_tag() -> None:
    assert (
        source_title(
            {
                "streams": [{"codec_type": "audio", "tags": {"TITLE": "  Track title "}}],
                "format": {"tags": {"title": "Container title"}},
            }
        )
        == "Track title"
    )
    assert source_title({"streams": [], "format": {"tags": {"title": "Fallback"}}}) == "Fallback"
    assert source_title({"streams": [], "format": {}}) is None
