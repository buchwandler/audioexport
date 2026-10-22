from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[1])
    return subprocess.run(
        [sys.executable, "-m", "audioexport", *args],
        text=True,
        capture_output=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def test_version() -> None:
    result = _run("--version")
    assert result.returncode == 0, result.stderr
    assert "audioexport " in result.stdout


def test_unified_bundle_parser_and_removed_flat_commands() -> None:
    help_result = _run("bundle", "export", "--help")
    assert help_result.returncode == 0, help_result.stderr
    for option in ("BUNDLE", "--variant", "--chapters", "--format", "--timeline", "--profile"):
        assert option in help_result.stdout

    for removed in ("export" + chr(45) + "book", "export" + chr(45) + "document"):
        result = _run(removed)
        assert result.returncode != 0
        assert "invalid choice" in result.stderr


def test_cli_encode_inspect(wav: Path, have_tools: None) -> None:
    output = wav.with_name("cli.mp3")
    completed = _run(
        "encode",
        str(wav),
        "--format",
        "mp3",
        "-o",
        str(output),
        "--metadata",
        "title=cli",
        "--json",
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["reused"] is False
    inspect = _run("inspect", str(output), "--json")
    assert inspect.returncode == 0, inspect.stderr
    assert json.loads(inspect.stdout)["audioexport"]["schema"] == "audioexport.manifest.v1"
    repeat = _run(
        "encode",
        str(wav),
        "--format",
        "mp3",
        "-o",
        str(output),
        "--metadata",
        "title=cli",
        "--json",
    )
    assert json.loads(repeat.stdout)["reused"] is True


def test_doctor(have_tools: None) -> None:
    result = _run("doctor", "--json")
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["ready"]
    assert parsed["formats"]["mp3"]["encoder"] == "libmp3lame"


def test_cli_audiobook_auto_discovers_directory_sidecars(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    shutil.copy2(wav, tracks / "01.wav")
    (tracks / "chapters.txt").write_text("00:00:00.000 Sidecar chapter\n", encoding="utf-8")
    cover = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:d=0.1",
            "-frames:v",
            "1",
            str(tracks / "cover.jpg"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert cover.returncode == 0, cover.stderr
    (tracks / "cover.jpeg").write_bytes(b"lower precedence")
    (tracks / "cover.png").write_bytes(b"lower precedence")

    output = tmp_path / "cli-book.m4b"
    completed = _run(
        "audiobook",
        str(tracks),
        "-o",
        str(output),
        "--title",
        "CLI Book",
        "--author",
        "CLI Author",
        "--json",
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert set(result["discovered_resources"]) == {"cover.jpg", "chapters.txt"}
    info = json.loads(_run("inspect", str(output), "--json").stdout)
    assert info["chapters"][0]["tags"]["title"] == "Sidecar chapter"
    assert any(
        stream.get("codec_type") == "video"
        and stream.get("disposition", {}).get("attached_pic") == 1
        for stream in info["streams"]
    )
    assert set(result["discovered_resources"]).issubset(
        json.loads(output.with_name(output.name + ".audioexport.json").read_text())["resources"][
            "auto_discovered"
        ]
    )

    no_auto_output = tmp_path / "no-auto.m4b"
    no_auto = _run(
        "audiobook",
        str(tracks),
        "-o",
        str(no_auto_output),
        "--no-auto-sidecars",
        "--json",
    )
    assert no_auto.returncode == 0, no_auto.stderr
    assert json.loads(no_auto.stdout)["discovered_resources"] == []
    no_auto_info = json.loads(_run("inspect", str(no_auto_output), "--json").stdout)
    assert not any(
        stream.get("disposition", {}).get("attached_pic") == 1 for stream in no_auto_info["streams"]
    )
    assert no_auto_info["chapters"][0]["tags"]["title"] == "01"


def test_cli_audiobook_profile_overrides_metadata_and_output(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    profile = tmp_path / "audiobook.toml"
    profile.write_text(
        'schema = "audioexport.audiobook.v1"\n'
        'output = "profile-book.m4b"\n'
        'inputs = ["speech.wav"]\n'
        '[metadata]\ntitle = "Profile title"\nauthor = "Profile author"\n'
        '[chapters]\nmode = "none"\n',
        encoding="utf-8",
    )
    output = tmp_path / "override.m4b"
    completed = _run(
        "audiobook",
        "--profile",
        str(profile),
        "--output",
        str(output),
        "--author",
        "CLI author",
        "--json",
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["path"] == str(output)
    assert result["chapter_count"] == 0
    info = json.loads(_run("inspect", str(output), "--json").stdout)
    tags = {key.lower(): value for key, value in info["format"].get("tags", {}).items()}
    assert tags["title"] == "Profile title"
    assert tags["artist"] == "CLI author"


def _book_fixture(tmp_path: Path, wav: Path) -> tuple[Path, str]:
    variant = "narration2--3b5f647f8f57"
    workspace = tmp_path / "Platform Decay.ssmd-bundle"
    chapters_dir = workspace / "chapters"
    chapters_dir.mkdir(parents=True)
    variant_dir = workspace / "audiocompose" / variant
    variant_dir.mkdir(parents=True)
    chapters = [
        {"id": "chapter-five", "source_number": 5, "title": "Chapter Five"},
        {"id": "chapter-two", "source_number": 2, "title": "Chapter Two"},
    ]
    (workspace / "manifest.json").write_text(
        json.dumps(
            {
                "format": "ssmdconvert.book",
                "schema_version": 1,
                "metadata": {"title": "Platform Decay", "authors": ["Martha Wells"]},
                "chapters": chapters,
            }
        ),
        encoding="utf-8",
    )
    items = []
    for chapter in reversed(chapters):
        filename = f"chapter-{chapter['source_number']:04}.wav"
        artifact = variant_dir / filename
        shutil.copy2(wav, artifact)
        items.append(
            {
                "status": "written",
                "id": chapter["id"],
                "source_number": chapter["source_number"],
                "render_plan_id": f"plan-{chapter['source_number']}",
                "manifest_id": "fixture-manifest",
                "output": filename,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "sample_rate": 24000,
                "channels": 1,
                "frames": 48000,
                "duration_seconds": 2.0,
                "output_policy_id": "wav-policy",
            }
        )
    report = [
        'format = "audiocompose-book-report"',
        "version = 1",
        f"variant = {json.dumps(variant)}",
        "",
    ]
    for item in items:
        report.append("[[items]]")
        report.extend(f"{key} = {json.dumps(value)}" for key, value in item.items())
        report.append("")
    (variant_dir / "compose-report.toml").write_text("\n".join(report), encoding="utf-8")
    return workspace, variant


def test_cli_export_book_end_to_end_json_human_reuse_and_force(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    workspace, variant = _book_fixture(tmp_path, wav)
    arguments = (
        "bundle",
        "export",
        str(workspace),
        "--variant",
        variant,
        "--title",
        "CLI title",
        "--author",
        "CLI author",
        "--bitrate",
        "96k",
        "--sample-rate",
        "24000",
        "--channels",
        "1",
    )
    first = _run(*arguments, "--json")
    assert first.returncode == 0, first.stderr
    envelope = json.loads(first.stdout)
    assert envelope["format"] == "audioexport-bundle-result"
    assert envelope["kind"] == "book"
    result = envelope["result"]
    output = workspace / "audioexport" / variant / "Platform Decay.m4b"
    assert result["workspace"] == str(workspace)
    assert result["variant"] == variant
    assert result["selected_chapters"] == ["chapter-five", "chapter-two"]
    assert result["input_compose_report"] == str(
        workspace / "audiocompose" / variant / "compose-report.toml"
    )
    assert result["output"] == str(output)
    assert result["export_identity"]
    assert result["duration_ms"] > 0
    assert result["codec"] == "aac"
    assert result["reused"] is False
    assert Path(result["sidecar"]).is_file()

    inspected = json.loads(_run("inspect", str(output), "--json").stdout)
    assert inspected["chapters"][0]["tags"]["title"] == "Chapter Five"
    assert inspected["chapters"][1]["tags"]["title"] == "Chapter Two"
    audio = next(stream for stream in inspected["streams"] if stream["codec_type"] == "audio")
    assert audio["codec_name"] == "aac"
    assert audio["sample_rate"] == "24000"
    assert audio["channels"] == 1
    tags = {key.lower(): value for key, value in inspected["format"].get("tags", {}).items()}
    assert tags["title"] == "CLI title"
    assert tags["artist"] == "CLI author"

    repeated = _run(*arguments, "--json")
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)["result"]["reused"] is True

    human = _run(*arguments)
    assert human.returncode == 0, human.stderr
    for expected in (
        "AudioExport bundle export",
        "kind: book",
        "workspace:",
        "variant:",
        "selected chapters: 2",
        "input compose report:",
        "output:",
        "duration:",
        "codec:",
        "reused: yes",
    ):
        assert expected in human.stdout

    forced = _run(*arguments, "--force", "--json")
    assert forced.returncode == 0, forced.stderr
    assert json.loads(forced.stdout)["result"]["reused"] is False


def test_cli_export_book_forwards_custom_roots_and_reports_provenance(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    workspace, variant = _book_fixture(tmp_path, wav)
    custom_compose = tmp_path / "external-compose"
    shutil.copytree(workspace / "audiocompose" / variant, custom_compose / variant)
    shutil.rmtree(workspace / "audiocompose")
    custom_output = tmp_path / "external-export"
    compose_report = custom_compose / variant / "compose-report.toml"
    arguments = (
        "bundle",
        "export",
        str(workspace),
        "--variant",
        variant,
        "--audiocompose-root",
        str(custom_compose),
        "--output-root",
        str(custom_output),
    )

    completed = _run(*arguments, "--json")
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)["result"]
    assert result["input_compose_report"] == str(compose_report)
    assert result["output"] == str(custom_output / variant / "Platform Decay.m4b")

    human = _run(*arguments)
    assert human.returncode == 0, human.stderr
    assert f"input compose report: {compose_report}" in human.stdout


def _document_fixture(tmp_path: Path, wav: Path) -> tuple[Path, str]:
    variant = "printer-cast--28f616b6fe3b"
    workspace = tmp_path / "story.ssmd-bundle"
    workspace.mkdir()
    (workspace / "manifest.json").write_text(
        json.dumps(
            {
                "format": "ssmdconvert.document",
                "schema_version": 1,
                "document": {"id": "document-0001", "path": "document.ssmd.md"},
            }
        ),
        encoding="utf-8",
    )
    (workspace / "document.ssmd.md").write_text("# Story\n", encoding="utf-8")
    variant_dir = workspace / "audiocompose" / variant
    variant_dir.mkdir(parents=True)
    artifact = variant_dir / "document-0001.wav"
    shutil.copy2(wav, artifact)
    report = {
        "format": "audiocompose-document-report",
        "version": 1,
        "document_id": "document-0001",
        "variant": variant,
        "render_profile_id": "profile-id",
        "source_plan_id": "source-plan-id",
        "render_plan_id": "render-plan-id",
        "render_manifest_id": "render-manifest-id",
        "render_manifest_sha256": "render-manifest-sha",
        "render_diagnostics_sha256": "render-diagnostics-sha",
        "pause_policy_id": "pause-policy-id",
        "pause_policy_provenance": "renderprofile",
        "output_policy_id": "output-policy-id",
        "audiojob_id": "audiojob-id",
        "audiojob_schema_version": 2,
        "audiocompose_version": "0.3.1",
        "status": "written",
        "output": artifact.name,
        "output_geometry": {
            "sha256": f"sha256:{hashlib.sha256(artifact.read_bytes()).hexdigest()}",
            "sample_rate": 24000,
            "channels": 1,
            "frames": 48000,
            "duration_seconds": 2.0,
        },
    }
    report_lines: list[str] = []
    for key, value in report.items():
        if isinstance(value, dict):
            report_lines.extend(("", f"[{key}]"))
            report_lines.extend(
                f"{nested_key} = {json.dumps(nested_value)}"
                for nested_key, nested_value in value.items()
            )
        else:
            report_lines.append(f"{key} = {json.dumps(value)}")
    (variant_dir / "compose-report.toml").write_text(
        "\n".join(report_lines) + "\n", encoding="utf-8"
    )
    return workspace, variant


def _clone_compose_variant(workspace: Path, source_variant: str, variant: str) -> Path:
    source = workspace / "audiocompose" / source_variant
    target = workspace / "audiocompose" / variant
    shutil.copytree(source, target)
    report_path = target / "compose-report.toml"
    report_text = report_path.read_text(encoding="utf-8")
    old_variant = f'variant = "{source_variant}"'
    new_variant = f'variant = "{variant}"'
    assert old_variant in report_text
    report_path.write_text(report_text.replace(old_variant, new_variant, 1), encoding="utf-8")
    return target


def test_cli_export_document_json_human_reuse_and_metadata(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    workspace, variant = _document_fixture(tmp_path, wav)
    arguments = (
        "bundle",
        "export",
        str(workspace),
        "--variant",
        variant,
        "--format",
        "mp3",
        "--metadata",
        "title=metadata title",
        "--title",
        "CLI title",
        "--artist",
        "CLI artist",
        "--album",
        "CLI album",
    )
    first = _run(*arguments, "--json")
    assert first.returncode == 0, first.stderr
    envelope = json.loads(first.stdout)
    assert envelope["format"] == "audioexport-bundle-result"
    assert envelope["kind"] == "document"
    result = envelope["result"]
    output = workspace / "audioexport" / variant / "story.mp3"
    assert result["workspace"] == str(workspace)
    assert result["variant"] == variant
    assert result["document_id"] == "document-0001"
    assert result["input_compose_report"].endswith("compose-report.toml")
    assert result["source"].endswith("document-0001.wav")
    assert result["output"] == str(output)
    assert Path(result["sidecar"]).is_file()
    assert result["export_identity"].startswith("sha256:")
    assert result["output_sha256"]
    assert result["reused"] is False

    inspected = json.loads(_run("inspect", str(output), "--json").stdout)
    tags = {key.lower(): value for key, value in inspected["format"].get("tags", {}).items()}
    assert tags["title"] == "CLI title"
    assert tags["artist"] == "CLI artist"
    assert tags["album"] == "CLI album"

    repeated = _run(*arguments, "--json")
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)["result"]["reused"] is True

    human = _run(*arguments)
    assert human.returncode == 0, human.stderr
    assert human.stdout.startswith("AudioExport bundle export")
    for field in (
        "AudioExport bundle export",
        "kind: document",
        "workspace:",
        "variant:",
        "document: document-0001",
        "input compose report:",
        "source:",
        "output:",
        "duration:",
        "codec:",
        "reused: yes",
    ):
        assert field in human.stdout
    inferred_output = tmp_path / "inferred.opus"
    inferred = _run(
        "bundle",
        "export",
        str(workspace),
        "--variant",
        variant,
        "--output",
        str(inferred_output),
        "--json",
    )
    assert inferred.returncode == 0, inferred.stderr
    inferred_payload = json.loads(inferred.stdout)["result"]
    assert inferred_payload["format"] == "opus"
    assert inferred_payload["output"] == str(inferred_output)


def test_cli_export_document_errors_are_json_and_stable(tmp_path: Path) -> None:
    result = _run(
        "bundle",
        "export",
        str(tmp_path / "bad.ssmd-bundle"),
        "--variant",
        "v1",
        "--format",
        "mp3",
        "--json",
    )

    assert result.returncode == 2
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "audioexport.bundle_invalid"
    assert payload["error"]["message"]


def test_cli_export_document_requires_format_or_output(wav: Path, tmp_path: Path) -> None:
    workspace, variant = _document_fixture(tmp_path, wav)
    result = _run("bundle", "export", str(workspace), "--variant", variant, "--json")

    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "audioexport.document_format_required"


def test_cli_bundle_variants_defaults_bundle_and_has_json_and_human_output(
    wav: Path, tmp_path: Path
) -> None:
    workspace, variant = _document_fixture(tmp_path, wav)

    default = _run("bundle", "variants", "--json", cwd=workspace)
    assert default.returncode == 0, default.stderr
    payload = json.loads(default.stdout)
    assert payload["format"] == "audioexport-bundle-variants"
    assert payload["version"] == 1
    assert payload["kind"] == "document"
    assert payload["variants"][0]["name"] == variant
    assert payload["variants"][0]["state"] == "ready"
    assert not (workspace / "audioexport").exists()

    explicit = _run("bundle", "variants", ".", "--json", cwd=workspace)
    assert explicit.returncode == 0, explicit.stderr
    assert json.loads(explicit.stdout) == payload

    human = _run("bundle", "variants", cwd=workspace)
    assert human.returncode == 0, human.stderr
    assert "AudioExport bundle variants" in human.stdout
    assert variant in human.stdout
    assert "ready" in human.stdout

    external_root = tmp_path / "external-compose"
    shutil.copytree(workspace / "audiocompose", external_root)
    custom = _run(
        "bundle",
        "variants",
        str(workspace),
        "--audiocompose-root",
        str(external_root),
        "--json",
    )
    assert custom.returncode == 0, custom.stderr
    assert json.loads(custom.stdout)["audiocompose_root"] == str(external_root)


def test_cli_batch_document_export_separates_outputs_and_reuses(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    workspace, first_variant = _document_fixture(tmp_path, wav)
    second_variant = "alpha--aaaaaaaaaaaa"
    _clone_compose_variant(workspace, first_variant, second_variant)
    output_root = tmp_path / "all-exports"
    arguments = (
        "bundle",
        "export",
        str(workspace),
        "--format",
        "mp3",
        "--output-root",
        str(output_root),
    )

    first = _run(*arguments, "--json")
    assert first.returncode == 0, first.stderr
    envelope = json.loads(first.stdout)
    assert envelope["format"] == "audioexport-bundle-batch-result"
    assert envelope["version"] == 1
    assert envelope["count"] == 2
    results = envelope["results"]
    assert [result["variant"] for result in results] == sorted([first_variant, second_variant])
    for result in results:
        output = output_root / result["variant"] / "story.mp3"
        assert result["output"] == str(output)
        assert Path(result["sidecar"]).is_file()

    human = _run(*arguments)
    assert human.returncode == 0, human.stderr
    assert "variants: 2" in human.stdout
    assert first_variant in human.stdout
    assert second_variant in human.stdout

    repeated = _run(*arguments, "--json")
    assert repeated.returncode == 0, repeated.stderr
    assert all(result["reused"] for result in json.loads(repeated.stdout)["results"])


def test_cli_batch_output_option_and_empty_variant_root_fail_before_encoding(
    wav: Path, tmp_path: Path
) -> None:
    workspace, variant = _document_fixture(tmp_path, wav)
    output_error = _run(
        "bundle",
        "export",
        str(workspace),
        "--format",
        "mp3",
        "--output",
        str(tmp_path / "one.mp3"),
        "--json",
    )
    assert output_error.returncode == 2
    assert json.loads(output_error.stdout)["error"]["code"] == "audioexport.bundle_option_invalid"
    assert not (workspace / "audioexport").exists()

    shutil.rmtree(workspace / "audiocompose" / variant)
    no_variants = _run("bundle", "export", str(workspace), "--format", "mp3", "--json")
    assert no_variants.returncode == 2
    assert json.loads(no_variants.stdout)["error"]["code"] == "audioexport.bundle_no_variants"
    assert not (workspace / "audioexport").exists()


def test_cli_batch_keeps_prior_outputs_and_reuses_them_after_report_repair(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    workspace, original_variant = _document_fixture(tmp_path, wav)
    first_variant = "a-first--aaaaaaaaaaaa"
    broken_variant = "z-broken--zzzzzzzzzzzz"
    _clone_compose_variant(workspace, original_variant, first_variant)
    broken_dir = _clone_compose_variant(workspace, original_variant, broken_variant)
    report_path = broken_dir / "compose-report.toml"
    report_path.write_text("format = [\n", encoding="utf-8")
    command = ("bundle", "export", str(workspace), "--format", "mp3", "--json")

    failed = _run(*command)
    assert failed.returncode == 2
    assert (
        json.loads(failed.stdout)["error"]["code"] == "audioexport.document_compose_report_invalid"
    )
    first_output = workspace / "audioexport" / first_variant / "story.mp3"
    original_output = workspace / "audioexport" / original_variant / "story.mp3"
    assert first_output.is_file()
    assert original_output.is_file()
    assert not (workspace / "audioexport" / broken_variant / "story.mp3").exists()

    source_report = (
        workspace / "audiocompose" / original_variant / "compose-report.toml"
    ).read_text(encoding="utf-8")
    report_path.write_text(
        source_report.replace(
            f'variant = "{original_variant}"', f'variant = "{broken_variant}"', 1
        ),
        encoding="utf-8",
    )
    repaired = _run(*command)
    assert repaired.returncode == 0, repaired.stderr
    results = json.loads(repaired.stdout)["results"]
    assert results[0]["variant"] == first_variant
    assert results[0]["reused"] is True
    assert results[1]["variant"] == original_variant
    assert results[1]["reused"] is True
    assert results[2]["variant"] == broken_variant


def test_cli_batch_book_defaults_to_m4b_and_supports_profile_chapters_and_geometry(
    wav: Path, tmp_path: Path, have_tools: None
) -> None:
    workspace, first_variant = _book_fixture(tmp_path, wav)
    second_variant = "fast--bbbbbbbbbbbb"
    _clone_compose_variant(workspace, first_variant, second_variant)
    profile = tmp_path / "book-profile.toml"
    profile.write_text(
        'schema = "audioexport.audiobook.v1"\n'
        'output = "profile-book.m4b"\n'
        f"inputs = [{json.dumps(str(wav))}]\n"
        '[audio]\nbitrate = "96k"\nsample_rate = 44100\nchannels = 2\n'
        '[metadata]\ntitle = "Profile title"\nauthor = "Profile author"\n',
        encoding="utf-8",
    )

    result = _run(
        "bundle",
        "export",
        str(workspace),
        "--profile",
        str(profile),
        "--chapters",
        "2",
        "--sample-rate",
        "24000",
        "--channels",
        "1",
        "--json",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["count"] == 2
    assert [item["variant"] for item in payload["results"]] == sorted(
        [first_variant, second_variant]
    )
    for item in payload["results"]:
        assert item["output"] == str(
            workspace / "audioexport" / item["variant"] / "profile-book.m4b"
        )
        assert item["selected_chapters"] == ["chapter-two"]
        assert item["codec"] == "aac"


def test_cli_batch_book_rejects_mp3_and_timeline_before_output(wav: Path, tmp_path: Path) -> None:
    workspace, _ = _book_fixture(tmp_path, wav)
    mp3 = _run("bundle", "export", str(workspace), "--format", "mp3", "--json")
    assert mp3.returncode == 2
    assert json.loads(mp3.stdout)["error"]["code"] == "audioexport.bundle_option_invalid"
    assert not (workspace / "audioexport").exists()

    timeline = _run(
        "bundle",
        "export",
        str(workspace),
        "--timeline",
        str(tmp_path / "timeline.json"),
        "--json",
    )
    assert timeline.returncode == 2
    assert json.loads(timeline.stdout)["error"]["code"] == "audioexport.bundle_option_invalid"
    assert not (workspace / "audioexport").exists()
