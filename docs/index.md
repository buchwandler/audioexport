# audioexport

`audioexport` is a standalone Python library and command-line interface for
exporting audio through FFmpeg. It accepts any FFmpeg-decodable audio source
and produces reproducible, verified output artifacts with no runtime dependencies on
planning, synthesis, or composition packages.

```{toctree}
:maxdepth: 2
:caption: Documentation

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

Use `export-book` when a composed-book workspace is ready for final M4B assembly. By default, the workspace contains `manifest.json`, a `chapters/` directory, and an AudioCompose report at `audiocompose/<variant>/compose-report.toml`; the report identifies composed WAV outputs and the variant that produced them. As with `export-document`, `--audiocompose-root` and `--output-root` can select custom stage roots. Each root contains a `<variant>/` directory; the compose report and default export are resolved within that selected variant.

```bash
audioexport export-book "My Book.ssmdbook" --variant "$VARIANT" --chapters 5,7,11-13 --audiocompose-root /srv/audio-stages/audiocompose --output-root /srv/audio-exports --title "My Book" --author "An Author" --language en-US
```

The chapter selector accepts `all` (the default), a source number, an inclusive range, or comma-separated numbers/ranges. Selection is emitted in manifest chapter order. Before assembly, AudioExport verifies the selected report entries, SHA-256 digest, and WAV sample rate, channel count, frame count, and duration; failed, skipped, or missing entries cannot be exported. The report supports both `[[item]]` and legacy `[[items]]` arrays.

The default output is `audioexport/<variant>/<workspace-name-without-.ssmdbook>.m4b` inside the workspace; `--output-root` relocates that default to `<output-root>/<variant>/...`. Destination precedence is explicit `--output`, then an audiobook profile's output when defined, then the resolver's default (including `--output-root`). Metadata precedence is manifest values, then audiobook-profile values, then explicit CLI/API metadata; audio settings follow defaults, profile settings, then explicit flags. For M4A/M4B, BCP 47 language tags are normalized to ISO 639-2/T track codes (for example, `en-US` to `eng`).

Python callers can resolve and inspect the selected, verified chapters before exporting:

```python
from pathlib import Path
from audioexport import export_book, resolve_book_export

workspace = Path("My Book.ssmdbook")
variant = "my-variant"
compose_root = Path("/srv/audio-stages/audiocompose")
export_root = Path("/srv/audio-exports")
context = resolve_book_export(
    workspace,
    variant=variant,
    chapters="5,7",
    audiocompose_root=compose_root,
    output_root=export_root,
)
print(context.default_output, [chapter.title for chapter in context.chapters])

result = export_book(
    workspace,
    variant=variant,
    chapters="5,7",
    audiocompose_root=compose_root,
    output_root=export_root,
)
print(result.output, result.input_compose_report, result.selected_chapter_ids)
```

`BookExportContext`, `BookExportResult`, and `ComposedChapter` describe resolved inputs and export results. `BookExportResult.input_compose_report` identifies the exact verified report consumed by the export. This adapter consumes the immediate AudioCompose handoff and does not modify upstream pipeline stages.

## Native `.ssmdoc` export

`export-document` consumes the immediate `audiocompose/<variant>/compose-report.toml` and its verified `document-0001.wav` by default. Like book export, it accepts `--audiocompose-root` and `--output-root` (or the equivalent `audiocompose_root` and `output_root` API arguments) for custom stage roots. It validates the SSMDDoc v1 manifest, exact variant, report status, output path, digest, and WAV geometry, then delegates final output to the generic encoder. Earlier stage directories are not required.

```bash
audioexport export-document story.ssmdoc --variant "$VARIANT" --format mp3 --audiocompose-root /srv/audio-stages/audiocompose --output-root /srv/audio-exports --title "Story"
```

The default output is `audioexport/<variant>/<workspace-name>.<format>` with the standard adjacent AudioExport sidecar; `--output-root` relocates the default to `<output-root>/<variant>/...`. `--output` remains the explicit destination. The caller selects the exact variant and a format or output extension; tags, cover art, and a generic timeline are explicit inputs.

```python
from audioexport import export_document, resolve_document_export

context = resolve_document_export("story.ssmdoc", variant=variant)  # read-only
result = export_document("story.ssmdoc", variant=variant, format="mp3")
```

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
- `export_document()` and `resolve_document_export()` for verified composed-document export; `ComposedDocument`, `DocumentExportContext`, and `DocumentExportResult` describe its inputs and results
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
- [Changelog](changelog.md) — release history
