from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path
from typing import Any

import pytest

from audioexport.book import resolve_book_export
from audioexport.errors import InvalidExportError, VerificationError

VARIANT = "narration2--3b5f647f8f57"


def _write_wav(
    path: Path, *, sample_rate: int = 8000, channels: int = 1, frames: int = 800
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(b"\0\0" * frames * channels)


def _report_text(
    items: list[dict[str, Any]],
    *,
    format_name: str = "audiocompose-book-report",
    version: int = 1,
    variant: str = VARIANT,
    item_table: str = "item",
) -> str:
    lines = [
        f"format = {json.dumps(format_name)}",
        f"version = {version}",
        f"variant = {json.dumps(variant)}",
        "",
    ]
    for item in items:
        lines.append(f"[[{item_table}]]")
        for key, value in item.items():
            lines.append(f"{key} = {json.dumps(value)}")
        lines.append("")
    return "\n".join(lines)


def _workspace(tmp_path: Path) -> tuple[Path, list[dict[str, Any]]]:
    workspace = tmp_path / "Platform Decay - Martha Wells.ssmdbook"
    (workspace / "chapters").mkdir(parents=True)
    variant_dir = workspace / "audiocompose" / VARIANT
    variant_dir.mkdir(parents=True)
    manifest_chapters = [
        {"id": "chapter-five", "source_number": 5, "title": "Five"},
        {"id": "chapter-two", "source_number": 2, "title": "Two"},
        {"id": "chapter-seven", "source_number": 7, "title": "Seven"},
    ]
    (workspace / "manifest.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "title": "Platform Decay",
                    "authors": ["Martha Wells"],
                    "publisher": "Example Press",
                    "language": "en",
                },
                "chapters": manifest_chapters,
            }
        ),
        encoding="utf-8",
    )
    # Deliberately create files/report items in an order different from the manifest.
    items: list[dict[str, Any]] = []
    for chapter in (manifest_chapters[1], manifest_chapters[2], manifest_chapters[0]):
        filename = f"chapter-{chapter['source_number']:04}.wav"
        path = variant_dir / filename
        _write_wav(path)
        items.append(
            {
                "status": "written",
                "id": chapter["id"],
                "source_number": chapter["source_number"],
                "render_plan_id": f"plan-{chapter['source_number']}",
                "manifest_id": "book-manifest-1",
                "output": filename,
                "sha256": f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
                "sample_rate": 8000,
                "channels": 1,
                "frames": 800,
                "duration_seconds": 0.1,
                "output_policy_id": "wav-policy-1",
            }
        )
    (variant_dir / "compose-report.toml").write_text(_report_text(items), encoding="utf-8")
    return workspace, items


def _write_report(workspace: Path, items: list[dict[str, Any]], **kwargs: Any) -> None:
    (workspace / "audiocompose" / VARIANT / "compose-report.toml").write_text(
        _report_text(items, **kwargs), encoding="utf-8"
    )


def test_accepts_legacy_plural_items_table(tmp_path: Path) -> None:
    workspace, items = _workspace(tmp_path)
    _write_report(workspace, items, item_table="items")

    result = resolve_book_export(workspace, variant=VARIANT)

    assert [chapter.id for chapter in result.chapters] == [
        "chapter-five",
        "chapter-two",
        "chapter-seven",
    ]


def test_rejects_ambiguous_compose_item_tables(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)
    report_path = workspace / "audiocompose" / VARIANT / "compose-report.toml"
    report_path.write_text(
        report_path.read_text(encoding="utf-8") + '\n[[items]]\nid = "chapter-five"\n',
        encoding="utf-8",
    )

    with pytest.raises(InvalidExportError, match="exactly one item or items array"):
        resolve_book_export(workspace, variant=VARIANT)


def test_resolves_workspace_and_preserves_manifest_order(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)

    result = resolve_book_export(workspace / "chapters", variant=VARIANT)

    assert result.workspace == workspace
    assert [chapter.id for chapter in result.chapters] == [
        "chapter-five",
        "chapter-two",
        "chapter-seven",
    ]
    assert [chapter.title for chapter in result.chapters] == ["Five", "Two", "Seven"]
    assert result.default_output == (
        workspace / "audioexport" / VARIANT / "Platform Decay - Martha Wells.m4b"
    )
    assert all(chapter.path.parent.name == VARIANT for chapter in result.chapters)
    assert result.chapters[0].duration_seconds == pytest.approx(0.1)


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("all", [5, 2, 7]),
        ("5", [5]),
        ("2-7", [5, 2, 7]),
        ("5,7,2-2", [5, 2, 7]),
    ],
)
def test_source_number_selection_returns_manifest_order(
    tmp_path: Path, selector: str, expected: list[int]
) -> None:
    workspace, _ = _workspace(tmp_path)

    result = resolve_book_export(workspace, variant=VARIANT, chapters=selector)

    assert [chapter.source_number for chapter in result.chapters] == expected


def test_unknown_source_number_is_rejected(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)
    with pytest.raises(InvalidExportError, match="does not match a source number"):
        resolve_book_export(workspace, variant=VARIANT, chapters="99")


@pytest.mark.parametrize(
    "variant", ["../other", "/tmp/variant", "foo/bar", r"foo\\bar", "C:variant"]
)
def test_rejects_unsafe_variants(tmp_path: Path, variant: str) -> None:
    workspace, _ = _workspace(tmp_path)
    with pytest.raises(InvalidExportError, match="safe single directory component"):
        resolve_book_export(workspace, variant=variant)


def test_unknown_variant_and_missing_compose_report_are_rejected(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)
    with pytest.raises(InvalidExportError, match="unknown AudioCompose variant"):
        resolve_book_export(workspace, variant="unknown")
    (workspace / "audiocompose" / VARIANT / "compose-report.toml").unlink()
    with pytest.raises(InvalidExportError, match="regular non-symlink file"):
        resolve_book_export(workspace, variant=VARIANT)


@pytest.mark.parametrize(
    ("format_name", "version", "reported_variant"),
    [
        ("other", 1, VARIANT),
        ("audiocompose-book-report", 2, VARIANT),
        ("audiocompose-book-report", 1, "other"),
    ],
)
def test_rejects_report_format_version_and_variant_mismatch(
    tmp_path: Path, format_name: str, version: int, reported_variant: str
) -> None:
    workspace, items = _workspace(tmp_path)
    _write_report(
        workspace, items, format_name=format_name, version=version, variant=reported_variant
    )

    with pytest.raises(InvalidExportError, match="format, version, or variant"):
        resolve_book_export(workspace, variant=VARIANT)


def test_rejects_duplicate_compose_item_ids_and_source_numbers(tmp_path: Path) -> None:
    workspace, items = _workspace(tmp_path)
    duplicated_id = [*items, {**items[0], "source_number": 99}]
    _write_report(workspace, duplicated_id)
    with pytest.raises(InvalidExportError, match="duplicate compose report item id"):
        resolve_book_export(workspace, variant=VARIANT)

    duplicated_number = [*items, {**items[0], "id": "another-id"}]
    _write_report(workspace, duplicated_number)
    with pytest.raises(InvalidExportError, match="duplicate compose report source number"):
        resolve_book_export(workspace, variant=VARIANT)


def test_rejects_missing_selected_compose_item(tmp_path: Path) -> None:
    workspace, items = _workspace(tmp_path)
    _write_report(workspace, [item for item in items if item["id"] != "chapter-five"])

    with pytest.raises(InvalidExportError, match="missing selected chapter id chapter-five"):
        resolve_book_export(workspace, variant=VARIANT, chapters="5")


def test_rejects_failed_and_skipped_selected_items(tmp_path: Path) -> None:
    workspace, items = _workspace(tmp_path)
    failed = [
        {**item, "status": "failed"} if item["source_number"] == 5 else item for item in items
    ]
    _write_report(workspace, failed)
    with pytest.raises(InvalidExportError, match="unusable compose status 'failed'"):
        resolve_book_export(workspace, variant=VARIANT, chapters="5")

    skipped = [
        {**item, "status": "skipped"} if item["source_number"] == 5 else item for item in items
    ]
    _write_report(workspace, skipped)
    with pytest.raises(InvalidExportError, match="unusable compose status 'skipped'"):
        resolve_book_export(workspace, variant=VARIANT, chapters="5")


def test_rejects_traversal_and_symlink_report_outputs(tmp_path: Path) -> None:
    workspace, items = _workspace(tmp_path)
    traversal = [
        {**item, "output": "../chapter.wav"} if item["source_number"] == 5 else item
        for item in items
    ]
    _write_report(workspace, traversal)
    with pytest.raises(VerificationError, match="traversal components"):
        resolve_book_export(workspace, variant=VARIANT, chapters="5")

    variant_dir = workspace / "audiocompose" / VARIANT
    outside = tmp_path / "outside.wav"
    _write_wav(outside)
    link = variant_dir / "linked.wav"
    link.symlink_to(outside)
    symlinked = [
        {**item, "output": "linked.wav"} if item["source_number"] == 5 else item for item in items
    ]
    _write_report(workspace, symlinked)
    with pytest.raises(VerificationError, match="symlinks"):
        resolve_book_export(workspace, variant=VARIANT, chapters="5")


def test_rejects_hash_and_wav_geometry_mismatches(tmp_path: Path) -> None:
    workspace, items = _workspace(tmp_path)
    bad_hash = [
        {**item, "sha256": "0" * 64} if item["source_number"] == 5 else item for item in items
    ]
    _write_report(workspace, bad_hash)
    with pytest.raises(VerificationError, match="checksum"):
        resolve_book_export(workspace, variant=VARIANT, chapters="5")

    wrong_geometry = [
        {**item, "sample_rate": 16000} if item["source_number"] == 5 else item for item in items
    ]
    _write_report(workspace, wrong_geometry)
    with pytest.raises(VerificationError, match="geometry"):
        resolve_book_export(workspace, variant=VARIANT, chapters="5")


def test_rejects_duplicate_manifest_ids(tmp_path: Path) -> None:
    workspace, _ = _workspace(tmp_path)
    manifest_path = workspace / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["chapters"][1]["id"] = manifest["chapters"][0]["id"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(InvalidExportError, match="duplicate manifest chapter id"):
        resolve_book_export(workspace, variant=VARIANT)


def test_rejects_non_wav_composed_artifact(tmp_path: Path) -> None:
    workspace, items = _workspace(tmp_path)
    artifact = workspace / "audiocompose" / VARIANT / "chapter-0005.wav"
    artifact.write_bytes(b"not a WAV")
    updated = [
        {**item, "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
        if item["source_number"] == 5
        else item
        for item in items
    ]
    _write_report(workspace, updated)

    with pytest.raises(VerificationError, match="not a readable WAV"):
        resolve_book_export(workspace, variant=VARIANT, chapters="5")


def test_public_export_book_applies_profile_and_explicit_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import audioexport.book as book_module
    from audioexport import AudiobookMetadata, BookExportResult, export_book
    from audioexport.audiobook import AudiobookResult

    workspace, _ = _workspace(tmp_path)
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    (profile_root / "input.wav").touch()
    profile_path = profile_root / "audiobook.toml"
    profile_path.write_text(
        'schema = "audioexport.audiobook.v1"\n'
        'output = "profile-output.m4b"\n'
        'inputs = ["input.wav"]\n'
        '[audio]\nbitrate = "96k"\nsample_rate = 44100\nchannels = 2\n'
        '[metadata]\ntitle = "Profile title"\nauthor = "Profile author"\n',
        encoding="utf-8",
    )
    explicit_cover = tmp_path / "explicit.jpg"
    explicit_cover.write_bytes(b"cover")
    captured: dict[str, Any] = {}

    def fake_build(inputs: Any, output: Any, **kwargs: Any) -> AudiobookResult:
        captured["inputs"] = tuple(inputs)
        captured["output"] = Path(output)
        captured["kwargs"] = kwargs
        destination = Path(output).absolute()
        return AudiobookResult(
            destination,
            "identity",
            "a" * 64,
            destination.with_name(destination.name + ".audioexport.json"),
            False,
            len(captured["inputs"]),
            len(captured["inputs"]),
            300,
            "aac",
        )

    monkeypatch.setattr(book_module, "build_audiobook", fake_build)
    explicit_output = tmp_path / "explicit.m4b"
    result = export_book(
        workspace,
        variant=VARIANT,
        profile=profile_path,
        output=explicit_output,
        metadata={"title": "CLI title", "writer": "CLI writer"},
        cover=explicit_cover,
        bitrate="128k",
        sample_rate=48000,
        channels=1,
    )

    assert isinstance(result, BookExportResult)
    assert result.output == explicit_output
    assert result.sidecar == explicit_output.with_name("explicit.m4b.audioexport.json")
    assert result.selected_chapter_ids == ("chapter-five", "chapter-two", "chapter-seven")
    assert [track.title for track in captured["inputs"]] == ["Five", "Two", "Seven"]
    assert captured["output"] == explicit_output
    passed = captured["kwargs"]
    assert passed["metadata"] == AudiobookMetadata(
        title="CLI title",
        author="Profile author",
        writer="CLI writer",
        publisher="Example Press",
        language="en",
    )
    assert passed["cover"] == explicit_cover
    assert (passed["bitrate"], passed["sample_rate"], passed["channels"]) == ("128k", 48000, 1)

    profile_result = export_book(workspace, variant=VARIANT, profile=profile_path, force=True)
    assert profile_result.output == profile_root / "profile-output.m4b"
    profile_kwargs = captured["kwargs"]
    assert profile_kwargs["metadata"] == AudiobookMetadata(
        title="Profile title",
        author="Profile author",
        publisher="Example Press",
        language="en",
    )
    assert (
        profile_kwargs["bitrate"],
        profile_kwargs["sample_rate"],
        profile_kwargs["channels"],
    ) == (
        "96k",
        44100,
        2,
    )
    assert profile_kwargs["force"] is True


def test_export_book_uses_manifest_defaults_and_deterministic_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import audioexport.book as book_module
    from audioexport import AudiobookMetadata, export_book
    from audioexport.audiobook import AudiobookResult

    workspace, _ = _workspace(tmp_path)
    captured: dict[str, Any] = {}

    def fake_build(inputs: Any, output: Any, **kwargs: Any) -> AudiobookResult:
        captured["output"] = Path(output)
        captured["kwargs"] = kwargs
        destination = Path(output).absolute()
        return AudiobookResult(
            destination,
            "identity",
            "b" * 64,
            destination.with_name(destination.name + ".audioexport.json"),
            True,
            len(tuple(inputs)),
            len(tuple(inputs)),
            300,
            "aac",
        )

    monkeypatch.setattr(book_module, "build_audiobook", fake_build)
    result = export_book(workspace, variant=VARIANT)

    assert (
        captured["output"]
        == workspace / "audioexport" / VARIANT / "Platform Decay - Martha Wells.m4b"
    )
    assert captured["kwargs"]["metadata"] == AudiobookMetadata(
        title="Platform Decay", author="Martha Wells", publisher="Example Press", language="en"
    )
    assert result.audiobook.reused is True
