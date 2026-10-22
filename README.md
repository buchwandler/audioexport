# audioexport

AudioExport is a standalone Python library and CLI for verified FFmpeg audio export.

Supported formats: WAV (PCM16), FLAC, MP3, M4A (AAC), M4B (AAC audiobook), OGG (Vorbis), and Opus. Handles tagged metadata, embedded JPEG/PNG cover art for MP3/M4A/M4B, JSON chapter timelines for M4A/M4B, standalone ordered multi-track audiobook assembly, FFprobe verification, artifact identities and safe reuse.

## Install

Requirements: **Python 3.10+** and external **FFmpeg + FFprobe** executables available on `PATH`. Install a build containing the encoders you need: `pcm_s16le`, `flac`, `libmp3lame`, `aac`, `libvorbis` (or native `vorbis`), and/or `libopus`.

Platform notes:

- Ubuntu/Debian: `sudo apt-get install ffmpeg`.
- macOS: `brew install ffmpeg`.
- Windows: install a current FFmpeg build and add its `bin` directory to `PATH`.

```bash
python -m pip install audioexport
python -m audioexport --version
# Or install from a checkout:
python -m pip install -e .
audioexport doctor --json
```

`doctor` reports package/tool versions and encoder availability per format. Its existing `ready` key means FFmpeg and FFprobe are present; `all_formats_ready` and each `formats` entry report encoder capability and a reason when unavailable. The additive `features` entries report `m4b_encode`, `m4b_audiobook`, and `m4b_stream_copy` availability; missing optional capabilities do not make `ready` false. `run_profile()` checks the encoders selected by the profile before writing outputs.

Base Python dependencies are `langcodes>=3.3` on all supported versions and `tomli>=2` on Python 3.10. FFmpeg/FFprobe are external executables; AudioExport has no runtime dependency on synthesis, planning, or composition packages.

## CLI quickstart

```bash
# Creates demo.wav in the current directory (or use your own audio file):
python examples/create_demo_wav.py
# Any FFmpeg-decodable audio source is accepted.
audioexport encode demo.wav --format mp3 -o output.mp3 --bitrate 192k
audioexport encode demo.wav --format flac -o output.flac
audioexport encode demo.wav --format m4a -o output.m4a --title "My recording" --artist "An author"
audioexport encode demo.wav --format ogg -o output.ogg
audioexport encode demo.wav --format opus -o output.opus

audioexport inspect output.mp3 --json
audioexport doctor --json

# For M4B chapters; use your own cover.jpg or omit --cover:
audioexport encode demo.wav --format m4b -o mybook.m4b \
  --title "My Book" --artist "An Author" \
  --timeline examples/chapters.json --cover cover.jpg --bitrate 192k

# Export multiple formats at once using an editable profile:
audioexport run demo.wav --profile examples/export.toml --out-dir exported/ --json
```

## Standalone audiobook assembly

`encode` converts one already-prepared audio source to a format; it does not merge a collection of source tracks. Use the separate `audiobook` workflow when starting from ordered tracks or a directory:

```bash
# Direct directory input: immediate audio files only, natural filename ordering.
audioexport audiobook book-tracks/ -o book.m4b \
  --title "My Book" --author "Author Name" --album "My Book" --cover cover.jpg

# Or provide an explicit order; source title tags become chapter titles when present.
audioexport audiobook 01-intro.mp3 02-chapter-one.flac 03-chapter-two.wav \
  -o book.m4b --bitrate 96k --language eng

# Explicit chapter starts from chapters.txt or a generic JSON timeline.
audioexport audiobook book-tracks/ -o book.m4b --chapters-file chapters.txt
```

Each source track becomes one chapter by default. Chapter starts use normalized segment durations; titles prefer an explicit `AudiobookTrack` title, then the source title tag, then the filename stem. Tracks are serially normalized to common AAC settings, concatenated with stream copy, and final-muxed without a second audio encode. Sample rate and channel count default from the first track; only mono/stereo targets are supported. The audiobook command defaults to the FFmpeg-compatible `media_type=2` tag. `--author` (or its `--artist` alias) writes the player-compatible `artist` tag; `--album-artist`, `--writer`, and `--long-description` map to `album_artist`, `composer`, and `synopsis`. `--metadata KEY=VALUE` adds or overrides an FFmpeg tag.

For one directory input, the CLI discovers `cover.jpg`, then `cover.jpeg`, then `cover.png`, plus `chapters.txt`, when explicit resources were not supplied. Discovered names appear in command output and the audiobook manifest. Use `--no-auto-sidecars` to disable this behavior. The library API does not scan sibling files unless called with `discover_sidecars=True`.

Python callers can use the same assembly workflow without invoking the CLI:

```python
from audioexport import AudiobookMetadata, AudiobookTrack, build_audiobook

result = build_audiobook(
    [
        AudiobookTrack("01-intro.mp3", title="Introduction"),
        "02-chapter-one.flac",
    ],
    "book.m4b",
    metadata=AudiobookMetadata(title="My Book", author="Author Name"),
    cover="cover.jpg",
)
print(result.track_count, result.chapter_count, result.reused)
```

A separate `audioexport.audiobook.v1` profile describes ordered inputs and explicit resources. All relative paths resolve from the profile file; no glob or implicit directory expansion is used in TOML. `chapters_file` is a top-level resource path, while `[chapters]` selects its mode:

```toml
schema = "audioexport.audiobook.v1"
output = "My Book.m4b"
inputs = ["01 Intro.mp3", "02 Chapter One.flac"]
cover = "cover.jpg"
chapters_file = "chapters.txt"

[audio]
bitrate = "96k"
sample_rate = 44100
channels = 2

[metadata]
title = "My Book"
author = "Author Name"
album = "My Book"
genre = "Audiobook"
language = "eng"

[chapters]
mode = "explicit"       # tracks | explicit | none
# title_source = "tag"  # tag (falls back to filename) | filename
```

Run it with `audioexport audiobook --profile audiobook.toml`. CLI `--output`, metadata, audio settings, cover, and `--chapters-file` override profile values; the TOML input order remains authoritative. `mode = "tracks"` (the default) creates one chapter per track; `mode = "explicit"` requires `chapters_file`; `mode = "none"` disables chapters. Existing `encode(..., format="m4b", timeline=...)` remains the right choice for one prepared source with caller-supplied chapters.

## SSMD bundle export

Book and single-document workspaces share one canonical editable directory root, `<name>.ssmd-bundle/`. The manifest selects the export path: `format = "ssmdconvert.book"` uses verified audiobook assembly, while `format = "ssmdconvert.document"` uses verified one-document encoding; both manifest schemas remain at version 1. AudioExport accepts only the bundle root, not an internal chapter directory, the document source file, or a ZIP archive.

Use the same command for either kind:

```bash
audiocompose bundle compose "My Book.ssmd-bundle" --variant "$VARIANT"
audioexport bundle export "My Book.ssmd-bundle" --variant "$VARIANT" \
  --chapters 5,7,11-13 --audiocompose-root /srv/audio-stages/audiocompose \
  --output-root /srv/audio-exports --title "My Book" --author "An Author" \
  --language en-US

audioexport bundle export story.ssmd-bundle --variant "$VARIANT" \
  --output story.m4a --title "Story" --artist "An Author"

# Inspect the AudioCompose export-stage inventory (BUNDLE defaults to .).
audioexport bundle variants

# Export every discovered variant in deterministic name order.
audioexport bundle export story.ssmd-bundle --format mp3
# A batch can relocate its variant-separated outputs.
audioexport bundle export story.ssmd-bundle --format mp3 --output-root /srv/audio-exports
```

`audioexport bundle variants [BUNDLE]` is a read-only AudioExport-stage inventory: it inspects immediate variant directories and compose reports only, does not hash WAVs or create output directories, and reports malformed candidates without hiding other variants. Use `--json` for the `audioexport-bundle-variants` envelope or `--audiocompose-root PATH` to select another stage root. Full render/compose pipeline status remains AudioCompose's concern.

AudioExport verifies selected reports, report-relative output paths, WAV checksums, and audio geometry before encoding. Current AudioCompose document reports store geometry in `[output_geometry]`; AudioExport also accepts legacy flat reports, but rejects contradictory mixed layouts. Books use verified M4B assembly, support chapter selection and audiobook profiles, and accept only `--format m4b` (M4B is the default). Documents support only `--chapters all`; explicit single-variant export accepts `--format` or infers the format from `--output`, while all-variant document export requires `--format`. `--timeline` supplies an optional generic document chapter timeline.

Single-variant default names strip only `.ssmd-bundle`: books use `audioexport/<variant>/<name>.m4b`, and documents use `audioexport/<variant>/<name>.<format>`. When `--variant` is omitted, export processes every immediate AudioCompose variant in deterministic name order and keeps outputs separated as `audioexport/<variant>/<name>.<format>` (books use `.m4b`). `--output-root` relocates single-variant defaults or, in batch mode, the whole variant-separated tree. A single `--output FILE` requires an explicit `--variant`; it is rejected in batch mode before encoding. Batch export is fail-fast and does not remove outputs already completed before a later variant fails. `--author` and `--artist` are aliases for the appropriate metadata field; options unsupported for the detected bundle kind fail explicitly.

The explicit single-variant JSON result remains `audioexport-bundle-result` with version 1 and a `result` object. Human single-variant output begins with `AudioExport bundle export` and reports kind, workspace, variant, output, and the selected chapter count or composed document item. All-variant export uses `audioexport-bundle-batch-result`; human output lists each variant and its output. Book metadata precedence is manifest, then audiobook profile, then explicit CLI/API values. BCP 47 language tags such as `en-US` normalize to ISO 639-2/T track codes such as `eng` for M4A/M4B.

Python callers can inspect and export variants through the same public API:

```python
from audioexport import export_bundle, export_bundle_all, list_bundle_variants

inventory = list_bundle_variants("story.ssmd-bundle")
single = export_bundle("story.ssmd-bundle", variant=variant, format="mp3")
batch = export_bundle_all("story.ssmd-bundle", format="mp3")
print(inventory.to_dict(), single.output, batch.outputs)
```

`BundleExportResult.value` contains the specialized book or document result, and its `output` property gives the artifact path. It remains the result type for explicit single-variant export. `BundleExportBatchResult` contains the ordered per-variant results and outputs. These APIs and `BundleVariantInfo`/`BundleVariantsResult` are available from both `audioexport` and `audioexport.api`.
OGG defaults to Vorbis quality-based encoding (`-qscale:a 4`) to avoid low-rate/mono bitrate incompatibilities. `--bitrate` accepts values such as `192k`, `192000`, or `1M`; whitespace is trimmed. A bare integer is interpreted as **bits per second**, so use `192k` (or `192000`), not `192`, for a nominal 192 kb/s rate. WAV/FLAC don't accept bitrate. WAV output is always PCM16 conversion, not a byte-for-byte passthrough; lossy codecs and even PCM output can vary with FFmpeg versions, which are included in the export identity.

## Python API

```python
import audioexport
from pathlib import Path
from audioexport import Chapter, encode, run_profile, doctor, probe

result = encode(
    Path("master.wav"),
    Path("exported/book.m4b"),
    format="m4b", bitrate="192k",
    metadata={"title": "My Book", "artist": "Author Name"},
    cover=Path("cover.jpg"),
    chapters=[
        Chapter("Introduction", start_ms=0),
        Chapter("Chapter One", start_ms=60_000),
    ],
)
print(result.to_dict())

for other in run_profile("master.wav", "examples/export.toml", "exported"):
    print(other.path, other.reused)

print(audioexport.__version__)
diagnostics = doctor()
print(diagnostics["ready"], diagnostics["all_formats_ready"])
print(probe("exported/book.m4b")["chapters"])
```

You may import the same public functions from `audioexport.api` as well as from `audioexport`.

The public errors are `AudioExportError` and subclasses `InvalidExportError`, `ToolNotFoundError`, `EncodingError`, and `VerificationError`, with a stable machine-readable `.code` value. `encode()` returns an immutable `ExportResult`; `build_audiobook()` returns an `AudiobookResult`; `export_bundle()` provides one manifest-dispatched entry point for verified book and document bundles and returns `BundleExportResult`; `export_bundle_all()` batches all variants, and `list_bundle_variants()` provides read-only inventory. The batch and inventory result types are available from both `audioexport` and `audioexport.api`.

## Export profile

[`examples/export.toml`](examples/export.toml) is a complete seven-format example. The `audioexport.profile.v1` schema keeps profile-wide `cover`, `timeline`, and `[metadata]`, while each `[[outputs]]` row may add `use_cover` and `use_chapters`:

```toml
schema = "audioexport.profile.v1"
timeline = "chapters.json"  # resolved relative to this TOML
# cover = "cover.jpg"        # optional; uncomment and provide the file

[metadata]
title = "My audiobook"
artist = "An author"
album = "My audiobook"

[[outputs]]
format = "mp3"
bitrate = "192k"
use_cover = false
use_chapters = false

[[outputs]]
format = "flac"
use_cover = false
use_chapters = false

[[outputs]]
format = "m4a"
bitrate = "192k"
use_chapters = true

[[outputs]]
format = "m4b"
bitrate = "192k"
use_chapters = true

[[outputs]]
format = "ogg"

[[outputs]]
format = "opus"
bitrate = "96k"

[[outputs]]
format = "wav"
```

When a selector is omitted (`None`), configured global artwork is attached automatically only to MP3/M4A/M4B, and chapters only to M4A/M4B; unsupported formats skip those global resources. `true` requires the resource and a capable format; `false` suppresses it. For example, to enable artwork, set a valid top-level `cover` and `use_cover = true` on the MP3/M4A/M4B rows. Direct `encode()` calls remain strict: unsupported cover/chapter arguments are errors.

`run_profile()` validates every row, resource, capability, and concrete filename collision before the first encode, then returns results in TOML order. Resource paths are relative to the profile file. Explicit `filename` values are literal simple filenames with the matching extension; no template expansion or nested paths are supported. If omitted, each filename uses the input stem. Profile-wide metadata is inherited by every output; there is no per-output metadata override.

## Profile resolution and read-only preflight API

The `audioexport.profile.v1` `use_cover` and `use_chapters` fields select profile-wide resources for each output. An omitted selector (`None`) uses a configured resource only when the format supports it; `false` suppresses it; `true` requires both a configured resource and a supporting format. Automatic selection is:

| Formats              | Cover by default   | Chapters by default |
| -------------------- | ------------------ | ------------------- |
| WAV, FLAC, OGG, Opus | No                 | No                  |
| MP3                  | Yes, if configured | No                  |
| M4A, M4B             | Yes, if configured | Yes, if configured  |

`ResolvedOutput` is an immutable description containing normalized format, literal filename, effective normalized bitrate, and selected cover/timeline paths. Explicit filenames are simple filenames with a matching extension and are never interpolated; only an omitted filename uses the supplied source stem. `resolve_output(profile, spec, source_stem)` is a pure operation on an already-loaded profile and does not probe media or read the TOML again.

Use `preflight_profile()` when the complete profile must be proven ready before the first output write. It returns the resolved outputs in TOML order, checks the source, metadata, selected resources and chapters, FFmpeg/FFprobe, and only the requested encoders. It does not accept an output directory and creates no outputs, directories, manifests, or temporary files. An unused global resource (for example, a missing cover on a FLAC-only profile) is not validated.

```python
from pathlib import Path
from audioexport import encode, load_profile, preflight_profile

source = Path("master.wav")
profile = load_profile("export.toml")  # Load once.
resolved_outputs = preflight_profile(profile, source)

for resolved in resolved_outputs:
    target = Path("exports") / resolved.filename
    # Applications should perform their own target and overwrite checks here.
    result = encode(
        source,
        target,
        format=resolved.format,
        bitrate=resolved.bitrate,
        metadata=profile.metadata,
        cover=resolved.cover,
        timeline=resolved.timeline,
    )
```

A consumer may instead call `resolve_output()` for a single selected spec; use full-profile preflight when the operation requires every configured output validated. Preflight is not an ownership authorization: applications remain responsible for their own locks, target selection, overwrite authorization, and state updates. Call `encode()` only after those consumer-owned checks pass. `ResolvedOutput`, `resolve_output`, and `preflight_profile` are available from both `audioexport` and `audioexport.api`.

## Chapters/timeline

Use an independent JSON file such as [`examples/chapters.json`](examples/chapters.json):

```json
{
  "chapters": [
    { "title": "Intro", "start_ms": 0 },
    { "title": "Part One", "start_ms": 60000 }
  ]
}
```

`end_ms` is optional; without it, chapter ends are the next chapter start or the audio duration. Chapter offsets must be strictly increasing and within the audio file. FFmpeg metadata escaping is handled internally. Embedded chapter markers are currently supported for **M4A and M4B**.

Exact-sample timelines may include a top-level `sample_rate` and chapter `start_sample` values. FFmetadata preserves their original sample timebase and coordinates using `TIMEBASE=1/<sample_rate>`.

## Integrity and versioning

`encode()` and `run_profile()` outputs have a sibling sidecar, for example `output.mp3.audioexport.json`, holding `audioexport.manifest.v1`, a SHA-256 export identity, source/output and consumed-resource digests, tool versions, normalized options, chapter mapping, and the probed output summary. `build_audiobook()` uses the same sidecar path with `audioexport.audiobook-manifest.v1`, recording ordered tracks, consumed resources, normalized settings, resolved chapter structure, tools, and verification. Generic export identity is **input-content + relevant options/resources + tools**, not output location or TOML formatting. The original sources are never modified.

Encoding uses argument-vector subprocess calls (`shell=False`) and stages both audio and manifest before committing. The output lock serializes same-target writers across processes; a competing call receives a typed busy error. If a sidecar commit fails, the previous output/manifest are restored when possible. A recovery failure retains its backup and refuses to treat a mismatched sidecar as current; inspect the reported recovery path before retrying. FFprobe validation checks the single audio stream, codec, duration, requested tags/chapters, and cover codec/disposition.

Version is dynamic through `setuptools-scm`: `pyproject.toml` has no static `version` field, and Git tags determine wheel/sdist metadata, `audioexport.__version__`, and CLI output. The project supports Python 3.10+ and retains the flat `audioexport/` layout. Unversioned archives use a development fallback; a fallback build must never be treated as final `0.1.2`.

For a release, first commit the code and wait for the complete Linux/Windows/macOS, Python 3.10–3.14 CI matrix, including its Sphinx warning-as-error documentation build. Then create and push the intended version tag (for example `v0.1.2`). Manually dispatch the GitHub Actions workflow named **Release** with that existing tag. It reruns the full supported-Python test matrix, checks the tag against wheel/sdist metadata, inspects package contents, installs the wheel outside the checkout, and checks CLI/API version parity. Publishing uses the configured `PYPI_API_TOKEN` secret after approval in the `pypi` GitHub environment; repository administrators must ensure the token and environment approval rules are configured. Do not publish from an untagged/fallback build.

## Tests

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
ruff check .
ruff format --check .
mypy audioexport
python -m compileall -q audioexport
python -m build
python -m twine check dist/*
python scripts/check_package_artifacts.py
audioexport doctor --json
```

Tests cover seven real formats in one profile, mixed-format audiobook assembly, 100-track ordering/chapters, audiobook cache invalidation, M4A/M4B chapters and covers, exact-sample chapter timelines, malformed probes, encoder availability, transactional failure recovery, no-clobber behavior, and concurrent writers. Normal CI runs Python 3.10–3.14 on Linux, Windows, and macOS, and checks tests, lint, formatting, typing, build metadata, Twine, package contents, and isolated wheel installation. CI also builds the Sphinx documentation on Linux/Python 3.13 with warnings treated as errors. The manually dispatched **Release** workflow retests the full supported Python range before packaging and API-token publishing. Cross-platform encoder availability depends on the FFmpeg build installed by each runner.

## Limitations (MVP)

No audio mastering, TTS, audio mixing, streaming PCM API, FFmpeg filter graphs, network service, or GUI. Encoder output may vary across FFmpeg versions; the export identity includes tool versions. Chapter verification tolerates small FFmpeg container timebase rounding.

## License

Apache-2.0. The FFmpeg executable is a separate third-party program distributed under its own terms.
