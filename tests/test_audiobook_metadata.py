from __future__ import annotations

import pytest

from audioexport.errors import InvalidExportError
from audioexport.metadata import AudiobookMetadata, audiobook_tags


def test_canonical_audiobook_fields_map_to_ffmpeg_tags() -> None:
    tags = audiobook_tags(
        AudiobookMetadata(
            title="The Book",
            author="A. Author",
            album_artist="The Publisher",
            writer="W. Writer",
            long_description="Long text",
        )
    )
    assert tags["media_type"] == "2"
    assert tags["title"] == "The Book"
    assert tags["artist"] == "A. Author"
    assert tags["album_artist"] == "The Publisher"
    assert tags["composer"] == "W. Writer"
    assert tags["synopsis"] == "Long text"


def test_extra_metadata_applies_after_default_and_canonical_mapping() -> None:
    tags = audiobook_tags(
        AudiobookMetadata(
            author="Author", extra={"artist": "Override", "media_type": "1", "custom": "v"}
        )
    )
    assert tags["artist"] == "Override"
    assert tags["media_type"] == "1"
    assert tags["custom"] == "v"


def test_mapping_accepts_canonical_and_extra_tags() -> None:
    assert audiobook_tags({"author": "Author", "title": "Title", "publisher": "Press"}) == {
        "artist": "Author",
        "media_type": "2",
        "publisher": "Press",
        "title": "Title",
    }


@pytest.mark.parametrize(
    "metadata",
    [
        {"bad-key": "value"},
        {"title": 4},
        {"title": None},
        {"media_type": ["2"]},
    ],
)
def test_invalid_metadata_is_rejected(metadata: object) -> None:
    with pytest.raises(InvalidExportError) as error:
        audiobook_tags(metadata)  # type: ignore[arg-type]
    assert error.value.code == "audioexport.audiobook_metadata_invalid"


def test_typed_model_rejects_non_string_values() -> None:
    with pytest.raises(InvalidExportError):
        AudiobookMetadata(author=12)  # type: ignore[arg-type]
