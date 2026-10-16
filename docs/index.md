# audioexport

`audioexport` is a standalone Python library and command-line interface for
exporting audio through FFmpeg. It accepts any FFmpeg-decodable audio source
and produces reproducible, verified output artifacts without importing Readio,
AudioCompose, UtterPlan, or a synthesis engine.

```{toctree}
:maxdepth: 2
:caption: Documentation

readio-migration
changelog
```

## Requirements

- Python 3.10 or newer
- FFmpeg and FFprobe available on `PATH`
- An FFmpeg build with the encoders required by the selected formats

Install the package and the documentation toolchain with:

```bash
python -m pip install -e .
python -m pip install -r docs/requirements.txt
```

Check the local FFmpeg installation before exporting:

```bash
audioexport doctor --json
```

The diagnostic reports tool versions and encoder availability. A missing
optional encoder only prevents the affected format; it does not make unrelated
formats unavailable.

## Command-line quickstart

Create or provide an audio source, then encode it to one format:

```bash
python examples/create_demo_wav.py
audioexport encode demo.wav --format mp3 -o output.mp3 --bitrate 192k
audioexport inspect output.mp3 --json
```

Metadata, cover art, and chapters can be supplied for formats that support
them:

```bash
audioexport encode demo.wav --format m4b -o audiobook.m4b \
  --title "My Book" --artist "An Author" \
  --timeline examples/chapters.json --cover cover.jpg --bitrate 192k
```

The supported output formats are WAV, FLAC, MP3, M4A, M4B, OGG, and Opus.
WAV and FLAC do not accept a bitrate. M4A and M4B support embedded chapter
markers; MP3, M4A, and M4B support cover art.

## Multi-format profiles

Use an `audioexport.profile.v1` TOML file when the same source should produce
several outputs:

```bash
audioexport run demo.wav \
  --profile examples/export.toml \
  --out-dir exported/ \
  --json
```

`run` validates every profile output, resource, encoder, and filename before
writing the first artifact. Profile resources are resolved relative to the
profile file. `preflight_profile()` exposes the same read-only validation for
Python callers.

## Audiobook assembly

`encode` converts one prepared source. To combine ordered tracks or a
non-recursive directory of tracks into one M4B audiobook, use `audiobook`:

```bash
audioexport audiobook book-tracks/ -o book.m4b \
  --title "My Book" --author "Author Name" --cover cover.jpg
```

Each track becomes a chapter by default. Use `--chapters-file` for explicit
chapter boundaries, or provide an `audioexport.audiobook.v1` profile for a
repeatable assembly. The audiobook workflow normalizes tracks, concatenates
them, writes metadata and chapters, and verifies the final artifact.

## Native `.ssmdbook` export

Use `export-book` when a composed-book workspace is ready for final M4B assembly. A standard workspace contains `manifest.json`, a `chapters/` directory, and an AudioCompose report at `audiocompose/<variant>/compose-report.toml`; the report identifies composed WAV outputs and the variant that produced them.

```bash
audioexport export-book "My Book.ssmdbook" \
  --variant "$VARIANT" \
  --chapters 5,7,11-13 \
  --output exports/book.m4b \
  --title "My Book" --author "An Author" --language en-US
```

The chapter selector accepts `all` (the default), a source number, an inclusive range, or comma-separated numbers/ranges. Selection is emitted in manifest chapter order. Before assembly, AudioExport verifies the selected report entries, SHA-256 digest, and WAV sample rate, channel count, frame count, and duration; failed, skipped, or missing entries cannot be exported. The report supports both `[[item]]` and legacy `[[items]]` arrays.

The default output is `audioexport/<variant>/<workspace-name-without-.ssmdbook>.m4b` inside the workspace. `--output` overrides it. Metadata precedence is manifest values, then audiobook-profile values, then explicit CLI/API metadata; audio settings follow defaults, profile settings, then explicit flags. Profile output is used when supplied unless `--output` is explicit. For M4A/M4B, BCP 47 language tags are normalized to ISO 639-2/T track codes (for example, `en-US` to `eng`).

Python callers can resolve and inspect the selected, verified chapters before exporting:

```python
from pathlib import Path
from audioexport import export_book, resolve_book_export

workspace = Path("My Book.ssmdbook")
variant = "my-variant"
context = resolve_book_export(workspace, variant=variant, chapters="5,7")
print(context.default_output, [chapter.title for chapter in context.chapters])

result = export_book(
    workspace,
    variant=variant,
    chapters="5,7",
    output=Path("exports/book.m4b"),
)
print(result.output, result.selected_chapter_ids)
```

`BookExportContext`, `BookExportResult`, and `ComposedChapter` describe resolved inputs and export results. This adapter does not run AudioCompose or modify Readio project state.

## Python API

The public API is available from both `audioexport` and `audioexport.api`:

```python
from pathlib import Path

from audioexport import Chapter, encode

result = encode(
    Path("master.wav"),
    Path("exports/book.m4b"),
    format="m4b",
    bitrate="192k",
    metadata={"title": "My Book", "artist": "An Author"},
    chapters=[
        Chapter("Introduction", start_ms=0),
        Chapter("Chapter One", start_ms=60_000),
    ],
)
print(result.path, result.reused, result.output_sha256)
```

Important public entry points include:

- `encode()` for one prepared source
- `run_profile()` for multiple configured outputs
- `build_audiobook()` for ordered-track assembly
- `export_book()` and `resolve_book_export()` for verified composed-book export; `BookExportContext`, `BookExportResult`, and `ComposedChapter` describe its public results
- `preflight_profile()` for read-only profile validation
- `doctor()` and `probe()` for tool and artifact diagnostics
- `load_profile()` and `load_audiobook_profile()` for TOML profiles

Exports return an immutable result with the output path, codec, export identity,
checksum, manifest path, reuse state, and chapter count. Public failures use
`AudioExportError` subclasses with stable machine-readable error codes.

## Artifact safety and reuse

Each generated audio file has an adjacent
`.audioexport.json` manifest. The manifest records the inputs and settings used
to calculate the export identity. Repeating an unchanged export reuses the
existing artifact instead of encoding again.

By default, an unknown, modified, or symlink output is not overwritten. Use
`--force` only when replacing a regular file is explicitly authorized. Writes
are staged with the manifest, committed atomically where possible, and guarded
by a per-target process lock.

## Further information

- [`README.md`](https://github.com/buchwandler/audioexport/blob/main/README.md) —
  complete usage reference and examples
- [Readio migration notes](readio-migration.md) — compatibility boundaries and
  the planned integration shape
- [Changelog](changelog.md) — release history
