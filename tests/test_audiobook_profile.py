from __future__ import annotations

from pathlib import Path

import pytest

from audioexport.audiobook_profile import load_audiobook_profile, resolve_profile_overrides
from audioexport.errors import InvalidExportError


def _base_profile() -> str:
    return (
        'schema = "audioexport.audiobook.v1"\n'
        'output = "My Book.m4b"\n'
        'inputs = ["10.mp3", "02 chapter.flac"]\n'
        '[audio]\nbitrate = "96k"\nsample_rate = 44100\nchannels = 2\n'
        '[metadata]\ntitle = "Book"\nauthor = "Author"\n'
        '[metadata.extra]\ngrouping = "Series"\n'
        '[chapters]\nmode = "tracks"\ntitle_source = "tag"\n'
    )


def test_profile_resolves_ordered_resources_relative_to_toml(tmp_path: Path) -> None:
    for name in ("10.mp3", "02 chapter.flac", "cover.jpg", "chapters.txt"):
        (tmp_path / name).write_text("resource", encoding="utf-8")
    profile_path = tmp_path / "audiobook.toml"
    profile_path.write_text(
        _base_profile()
        .replace("[audio]", 'cover = "cover.jpg"\nchapters_file = "chapters.txt"\n[audio]')
        .replace('mode = "tracks"', 'mode = "explicit"'),
        encoding="utf-8",
    )

    profile = load_audiobook_profile(profile_path)
    assert profile.output == (tmp_path / "My Book.m4b").resolve()
    assert [path.name for path in profile.inputs] == ["10.mp3", "02 chapter.flac"]
    assert profile.cover == (tmp_path / "cover.jpg").resolve()
    assert profile.chapters_file == (tmp_path / "chapters.txt").resolve()
    assert profile.audio.bitrate == "96k"
    assert profile.metadata.author == "Author"
    assert profile.metadata.extra == {"grouping": "Series"}
    assert profile.chapter_mode == "explicit"


def test_profile_rejects_unknown_fields_missing_resources_and_directories(tmp_path: Path) -> None:
    (tmp_path / "book.mp3").touch()
    for name in ("10.mp3", "02 chapter.flac"):
        (tmp_path / name).touch()
    profile_path = tmp_path / "bad.toml"
    profile_path.write_text(_base_profile().replace('"10.mp3"', '"missing.mp3"'), encoding="utf-8")
    with pytest.raises(InvalidExportError) as error:
        load_audiobook_profile(profile_path)
    assert error.value.code == "audioexport.audiobook_profile_invalid"

    profile_path.write_text(
        _base_profile().replace("[audio]", "unexpected = true\n[audio]"), encoding="utf-8"
    )
    with pytest.raises(InvalidExportError, match="unknown audiobook profile fields"):
        load_audiobook_profile(profile_path)

    (tmp_path / "subdir").mkdir()
    profile_path.write_text(_base_profile().replace('"10.mp3"', '"subdir"'), encoding="utf-8")
    with pytest.raises(InvalidExportError, match="existing file"):
        load_audiobook_profile(profile_path)


def test_profile_rejects_globs_traversal_and_unknown_nested_fields(tmp_path: Path) -> None:
    (tmp_path / "book.mp3").touch()
    for name in ("10.mp3", "02 chapter.flac"):
        (tmp_path / name).touch()
    profile_path = tmp_path / "bad.toml"
    profile_path.write_text(_base_profile().replace('"10.mp3"', '"*.mp3"'), encoding="utf-8")
    with pytest.raises(InvalidExportError, match="glob or template"):
        load_audiobook_profile(profile_path)

    profile_path.write_text(
        _base_profile().replace('"10.mp3"', '"../outside.mp3"'), encoding="utf-8"
    )
    with pytest.raises(InvalidExportError, match="parent directories"):
        load_audiobook_profile(profile_path)

    profile_path.write_text(
        _base_profile().replace('title_source = "tag"', 'title_source = "tag"\nextra = true'),
        encoding="utf-8",
    )
    with pytest.raises(InvalidExportError, match="unknown fields"):
        load_audiobook_profile(profile_path)


def test_cli_overrides_take_precedence_without_changing_input_order(tmp_path: Path) -> None:
    for name in ("10.mp3", "02 chapter.flac", "override.jpg", "override.txt"):
        (tmp_path / name).touch()
    profile_path = tmp_path / "audiobook.toml"
    profile_path.write_text(_base_profile(), encoding="utf-8")
    profile = load_audiobook_profile(profile_path)

    overridden = resolve_profile_overrides(
        profile,
        output=tmp_path / "CLI.m4b",
        metadata={"title": "CLI title", "author": "CLI author", "custom": "extra"},
        cover=tmp_path / "override.jpg",
        chapters_file=tmp_path / "override.txt",
        bitrate="128k",
        sample_rate=48000,
        channels=1,
        use_filenames_as_chapters=True,
    )
    assert overridden.output == (tmp_path / "CLI.m4b").absolute()
    assert overridden.inputs == profile.inputs
    assert overridden.metadata.title == "CLI title"
    assert overridden.metadata.author == "CLI author"
    assert overridden.metadata.extra == {"grouping": "Series", "custom": "extra"}
    assert overridden.cover == (tmp_path / "override.jpg").resolve()
    assert overridden.chapters_file == (tmp_path / "override.txt").resolve()
    assert overridden.chapter_mode == "explicit"
    assert overridden.title_source == "filename"
    assert overridden.audio.bitrate == "128k"
    assert overridden.audio.sample_rate == 48000
    assert overridden.audio.channels == 1
