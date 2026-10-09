from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from audioexport.errors import InvalidExportError
from audioexport.formats import FORMATS
from audioexport.profile import ExportProfile, OutputSpec, load_profile, resolve_output

FORMATS_IN_ORDER = ("wav", "flac", "mp3", "m4a", "m4b", "ogg", "opus")
COVER_FORMATS = {"mp3", "m4a", "m4b"}
CHAPTER_FORMATS = {"m4a", "m4b"}


def _error_code(error: pytest.ExceptionInfo[InvalidExportError], code: str) -> None:
    assert error.value.code == code


def test_automatic_resolution_matrix_and_effective_bitrates(tmp_path: Path) -> None:
    cover = tmp_path / "not-created.jpg"
    timeline = tmp_path / "not-created.json"
    profile = ExportProfile((), {}, cover, timeline)
    expected_bitrates = {
        "wav": None,
        "flac": None,
        "mp3": "192k",
        "m4a": "192k",
        "m4b": "192k",
        "ogg": None,
        "opus": "96k",
    }

    for fmt in FORMATS_IN_ORDER:
        resolved = resolve_output(profile, OutputSpec(fmt), "book")
        assert resolved.format == fmt
        assert resolved.filename == f"book{FORMATS[fmt].extension}"
        assert resolved.bitrate == expected_bitrates[fmt]
        assert (resolved.cover == cover) is (fmt in COVER_FORMATS)
        assert (resolved.timeline == timeline) is (fmt in CHAPTER_FORMATS)


def test_profile_loader_parses_optional_flags_and_keeps_relative_resources(tmp_path: Path) -> None:
    profile_path = tmp_path / "profiles" / "export.toml"
    profile_path.parent.mkdir()
    profile_path.write_text(
        'schema = "audioexport.profile.v1"\n'
        'cover = "missing-cover.jpg"\ntimeline = "missing-chapters.json"\n'
        '[[outputs]]\nformat = "mp3"\n'
        '[[outputs]]\nformat = "m4b"\nuse_cover = true\nuse_chapters = false\n',
        encoding="utf-8",
    )

    profile = load_profile(profile_path)
    assert profile.outputs[0].use_cover is None
    assert profile.outputs[0].use_chapters is None
    assert profile.outputs[1].use_cover is True
    assert profile.outputs[1].use_chapters is False
    assert profile.cover == (profile_path.parent / "missing-cover.jpg").resolve()
    assert profile.timeline == (profile_path.parent / "missing-chapters.json").resolve()
    assert resolve_output(profile, profile.outputs[1], "book").timeline is None


def test_v1_profile_without_new_fields_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "old.toml"
    path.write_text(
        'schema = "audioexport.profile.v1"\n'
        '[[outputs]]\nformat = "mp3"\nfilename = "custom.mp3"\nbitrate = "128k"\n',
        encoding="utf-8",
    )

    profile = load_profile(path)
    assert profile.outputs == (OutputSpec("mp3", "custom.mp3", "128k"),)
    assert resolve_output(profile, profile.outputs[0], "unused").filename == "custom.mp3"


@pytest.mark.parametrize("flag", ("use_cover", "use_chapters"))
def test_profile_loader_rejects_non_boolean_flags(tmp_path: Path, flag: str) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(
        f'schema = "audioexport.profile.v1"\n[[outputs]]\nformat = "m4b"\n{flag} = "yes"\n',
        encoding="utf-8",
    )

    with pytest.raises(InvalidExportError) as error:
        load_profile(path)
    _error_code(error, "audioexport.profile_invalid")


def test_unknown_output_fields_remain_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unknown.toml"
    path.write_text(
        'schema = "audioexport.profile.v1"\n[[outputs]]\nformat = "wav"\nother = true\n',
        encoding="utf-8",
    )

    with pytest.raises(InvalidExportError) as error:
        load_profile(path)
    _error_code(error, "audioexport.profile_invalid")


def test_explicit_selectors_can_suppress_resources_and_require_them(tmp_path: Path) -> None:
    profile = ExportProfile((), {}, tmp_path / "cover.jpg", tmp_path / "chapters.json")
    no_resources = resolve_output(
        profile, OutputSpec("m4b", use_cover=False, use_chapters=False), "book"
    )
    assert no_resources.cover is None
    assert no_resources.timeline is None

    missing = ExportProfile((), {})
    with pytest.raises(InvalidExportError) as cover_error:
        resolve_output(missing, OutputSpec("mp3", use_cover=True), "book")
    _error_code(cover_error, "audioexport.cover_required")
    with pytest.raises(InvalidExportError) as chapter_error:
        resolve_output(missing, OutputSpec("m4a", use_chapters=True), "book")
    _error_code(chapter_error, "audioexport.chapters_required")


def test_explicit_selectors_reject_unsupported_formats() -> None:
    profile = ExportProfile((), {}, Path("cover.jpg"), Path("chapters.json"))
    with pytest.raises(InvalidExportError) as cover_error:
        resolve_output(profile, OutputSpec("flac", use_cover=True), "book")
    _error_code(cover_error, "audioexport.cover_unsupported")
    with pytest.raises(InvalidExportError) as chapter_error:
        resolve_output(profile, OutputSpec("mp3", use_chapters=True), "book")
    _error_code(chapter_error, "audioexport.chapters_unsupported")


def test_explicit_filename_is_literal_and_bitrate_is_normalized() -> None:
    result = resolve_output(
        ExportProfile((), {}), OutputSpec("MP3", "{stem}.mp3", "128k"), "ignored"
    )
    assert result.format == "mp3"
    assert result.filename == "{stem}.mp3"
    assert result.bitrate == "128k"


@pytest.mark.parametrize("filename", ("../book.mp3", r"..\\book.mp3", "book.wav"))
def test_resolver_rejects_unsafe_or_mismatched_explicit_filename(filename: str) -> None:
    with pytest.raises(InvalidExportError) as error:
        resolve_output(ExportProfile((), {}), OutputSpec("mp3", filename), "book")
    _error_code(error, "audioexport.profile_invalid")


@pytest.mark.parametrize("source_stem", ("", "../book", r"folder\\book", "."))
def test_resolver_rejects_unsafe_source_stem(source_stem: str) -> None:
    with pytest.raises(InvalidExportError) as error:
        resolve_output(ExportProfile((), {}), OutputSpec("mp3"), source_stem)
    _error_code(error, "audioexport.profile_invalid")


def test_resolver_validates_manually_constructed_selector_types() -> None:
    with pytest.raises(InvalidExportError) as error:
        resolve_output(ExportProfile((), {}), OutputSpec("mp3", use_cover=cast(bool, 1)), "book")
    _error_code(error, "audioexport.profile_invalid")


def test_explicit_filename_loader_rejects_duplicates_and_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.toml"
    path.write_text(
        'schema = "audioexport.profile.v1"\n'
        '[[outputs]]\nformat = "mp3"\nfilename = "book.mp3"\n'
        '[[outputs]]\nformat = "mp3"\nfilename = "BOOK.mp3"\n',
        encoding="utf-8",
    )
    with pytest.raises(InvalidExportError) as error:
        load_profile(path)
    _error_code(error, "audioexport.profile_invalid")
