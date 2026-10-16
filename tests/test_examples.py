from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from audioexport.audiobook_profile import load_audiobook_profile
from audioexport.book import resolve_book_export
from audioexport.chapters import load_chapters_txt

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def test_audiobook_example_profile_and_chapters_are_valid(tmp_path: Path) -> None:
    example_dir = tmp_path / "examples"
    example_dir.mkdir()
    profile_path = example_dir / "audiobook.toml"
    shutil.copyfile(EXAMPLES / "audiobook.toml", profile_path)
    shutil.copyfile(EXAMPLES / "chapters.txt", example_dir / "chapters.txt")
    (example_dir / "demo.wav").write_bytes(b"profile parser only checks the referenced file")

    profile = load_audiobook_profile(profile_path)
    assert profile.chapters_file is not None
    chapters = load_chapters_txt(profile.chapters_file)

    assert profile.inputs == ((example_dir / "demo.wav").resolve(),)
    assert profile.chapter_mode == "explicit"
    assert [(chapter.title, chapter.start_ms) for chapter in chapters] == [("Opening", 0)]


def test_ssmdbook_generator_creates_a_resolvable_verified_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "AudioExport Demo.ssmdbook"
    script = EXAMPLES / "create_ssmdbook_demo.py"
    subprocess.run([sys.executable, str(script), str(workspace)], check=True, capture_output=True)

    context = resolve_book_export(workspace, variant="demo-variant")

    assert context.workspace == workspace.resolve()
    assert len(context.chapters) == 1
    chapter = context.chapters[0]
    assert chapter.id == "opening"
    assert chapter.title == "Opening"
    assert chapter.sample_rate == 8_000
    assert chapter.frames == 8_000
    assert chapter.duration_seconds == 1.0
    assert context.default_output.name == "AudioExport Demo.m4b"
