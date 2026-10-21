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

## SSMD bundle export

Book and single-document workspaces share the canonical editable root `<name>.ssmd-bundle/`. The authoritative manifest `format` (`ssmdconvert.book` or `ssmdconvert.document`) selects the implementation; both schemas remain at version 1. Commands accept only the bundle root directory, not internal component paths or ZIP files.

```bash
audiocompose bundle compose "My Book.ssmd-bundle" --variant "$VARIANT"
audioexport bundle export "My Book.ssmd-bundle" --variant "$VARIANT" \
  --chapters 5,7,11-13 --audiocompose-root /srv/audio-stages/audiocompose \
  --output-root /srv/audio-exports --title "My Book" --author "An Author"

audioexport bundle export story.ssmd-bundle --variant "$VARIANT" \
  --output story.m4a --title "Story" --artist "An Author"
```

AudioExport verifies the selected compose report, output paths, WAV digests, and audio geometry before export. Books use verified M4B assembly, support chapter selection and audiobook profiles, and accept only `--format m4b`. Documents support only `--chapters all`; `--format` or the output filename extension selects the final format, and `--timeline` supplies an optional generic chapter timeline. `--author` and `--artist` are aliases for the appropriate metadata field. Options unsupported for the detected kind fail explicitly.

Default output stems strip `.ssmd-bundle`: book output is `audioexport/<variant>/<name>.m4b`, and document output is `audioexport/<variant>/<name>.<format>`. `--output-root` relocates these defaults and `--output` selects an explicit path. Book metadata precedence remains manifest, audiobook profile, then explicit API/CLI values. BCP 47 language tags such as `en-US` normalize to ISO 639-2/T track codes such as `eng` for M4A/M4B.

JSON output has one versioned envelope: `format: "audioexport-bundle-result"`, `version: 1`, `kind`, and kind-specific `result`. Human output begins with `AudioExport bundle export` and includes kind, workspace, variant, output, and the selected chapter count or composed document item.

Python callers use the same facade:

```python
from audioexport import export_bundle

book_result = export_bundle("My Book.ssmd-bundle", variant=variant, chapters="5,7")
document_result = export_bundle("story.ssmd-bundle", variant=variant, format="mp3")
print(book_result.kind, book_result.output)
print(document_result.kind, document_result.output)
```

`BundleExportResult.value` holds the specialized export result. The facade is available from both `audioexport` and `audioexport.api`.

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
- `export_bundle()` for verified book or document bundle export; `BundleExportResult` exposes the detected kind, specialized result, and output path
- `BundleIdentity` and the `audioexport.bundle_contract` loader for safe canonical-root and manifest-kind validation
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
