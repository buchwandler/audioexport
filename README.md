# audioexport

An independent Python library and command-line interface for exporting audio through **FFmpeg**. This is the first extraction step from Readio's output/encoding subsystem, but **audioexport does not import or install Readio, AudioCompose, UtterPlan, or synthesis engines**.

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

The only base Python dependency is `tomli` on Python 3.10. FFmpeg/FFprobe are external executables; AudioExport does not install or import Readio, AudioCompose, UtterPlan, VoiceRender, soundfile, or NumPy.

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

# Explicit chapter starts from chapters.txt or the existing JSON/Readio timeline format.
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

Run it with `audioexport audiobook --profile audiobook.toml`. CLI `--output`, metadata, audio settings, cover, and `--chapters-file` override profile values; the TOML input order remains authoritative. `mode = "tracks"` (the default) creates one chapter per track; `mode = "explicit"` requires `chapters_file`; `mode = "none"` disables chapters. Existing `encode(..., format="m4b", timeline=...)` remains the right choice for Readio's prepared master and project timeline.

If `--output` is not set for `encode`, the output filename defaults to the input stem and chosen format. To avoid destroying the input, encoding input and output to the same real path is always rejected.

Without `--force`, an untracked, symlink, or manually modified output is never overwritten. A previously generated output is updated when its sidecar and checksum still match; re-running identical options returns `reused: true` without re-encoding. `--force` authorizes replacing an unknown or modified regular file, but symlink outputs and sidecars are always rejected. Writes are staged with their manifest; a failed manifest commit restores the last good pair when possible, and an incomplete recovery reports the preserved backup path. A per-target process lock rejects concurrent writers with `audioexport.output_busy`; remove a stale lock only after confirming no writer is active.

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

The public errors are `AudioExportError` and subclasses `InvalidExportError`, `ToolNotFoundError`, `EncodingError`, and `VerificationError`, with a stable machine-readable `.code` value. `encode()` returns an immutable `ExportResult`; `build_audiobook()` returns an `AudiobookResult` with track/chapter counts and verified duration. Both APIs include identity, path, checksum, and reuse state, and work without a Readio project.

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
    # A consumer such as Readio performs its target/ownership/overwrite checks here.
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

A consumer may instead call `resolve_output()` for a single selected spec; use full-profile preflight when the operation requires every configured output validated. Preflight is not an ownership authorization: Readio remains responsible for project locks and freshness, destination selection and collision/ownership checks, overwrite authorization, state/index updates, result types, and partial-batch bookkeeping. Call `encode()` only after those consumer-owned checks pass. `ResolvedOutput`, `resolve_output`, and `preflight_profile` are available from both `audioexport` and `audioexport.api`.

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

For an incremental Readio migration, the same `--timeline` API also reads **Readio's `readio.composition-timeline` JSON** with `sample_rate` and chapter `start_sample`. FFmetadata is constructed with its original sample timebase for precision, with generic millisecond chapter fields available for non-Readio workflows.

## Integrity and versioning

`encode()` and `run_profile()` outputs have a sibling sidecar, for example `output.mp3.audioexport.json`, holding `audioexport.manifest.v1`, a SHA-256 export identity, source/output and consumed-resource digests, tool versions, normalized options, chapter mapping, and the probed output summary. `build_audiobook()` uses the same sidecar path with `audioexport.audiobook-manifest.v1`, recording ordered tracks, consumed resources, normalized settings, resolved chapter structure, tools, and verification. Generic export identity is **input-content + relevant options/resources + tools**, not output location or TOML formatting. The original sources are never modified.

Encoding uses argument-vector subprocess calls (`shell=False`) and stages both audio and manifest before committing. The output lock serializes same-target writers across processes; a competing call receives a typed busy error. If a sidecar commit fails, the previous output/manifest are restored when possible. A recovery failure retains its backup and refuses to treat a mismatched sidecar as current; inspect the reported recovery path before retrying. FFprobe validation checks the single audio stream, codec, duration, requested tags/chapters, and cover codec/disposition.

Version is dynamic through `setuptools-scm`: `pyproject.toml` has no static `version` field, and Git tags determine wheel/sdist metadata, `audioexport.__version__`, and CLI output. The project supports Python 3.10+ and retains the flat `audioexport/` layout. Unversioned archives use a development fallback; a fallback build must never be treated as final `0.1.0`.

For a release, first commit the code and wait for the complete Linux/Windows/macOS, Python 3.10/3.13 CI matrix. Then create and push the intended version tag (for example `v0.1.0`). Manually dispatch `.github/workflows/release.yml` with that existing tag. It reruns the test matrix, checks the tag against wheel/sdist metadata, inspects package contents, installs the wheel outside the checkout, and checks CLI/API version parity. Publishing uses PyPI trusted publishing only after the `pypi` GitHub environment is approved; repository administrators must configure required reviewers for that environment and register the trusted publisher on PyPI. Do not publish from an untagged/fallback build.

## Readio migration

**Readio is deliberately not patched by this project.** Its existing export commands continue using their current code until an opt-in bridge is tested. A future integration must call AudioExport's Python library API; it must never launch the `audioexport` CLI as a subprocess. See [`docs/readio-migration.md`](docs/readio-migration.md) for the compatibility boundary, integration order, and rollback rules. Readio continues owning project locks, path defaults, staleness, indexed state, and orchestration.

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

Tests cover seven real formats in one profile, mixed-format audiobook assembly, 100-track ordering/chapters, audiobook cache invalidation, M4A/M4B chapters and covers, Readio sample timelines, malformed probes, encoder availability, transactional failure recovery, no-clobber behavior, and concurrent writers. CI runs Python 3.10 and 3.13 on Linux, Windows, and macOS, and checks lint, formatting, typing, build metadata, Twine, package contents, and an isolated wheel install outside the checkout. Cross-platform encoder availability still depends on the FFmpeg build installed by the runner.

## Limitations (MVP)

No audio mastering, TTS, audio mixing, streaming PCM API, FFmpeg filter graphs, network service, or GUI. No automatic conversion of Readio project configs, legacy export cache, or Audiocompose timeline semantics other than chapter starts. Encoder output may vary across FFmpeg versions; the cache key includes tool versions. Chapter verification has a small tolerance for FFmpeg container timebase rounding. Standalone encoding does not modify Readio projects.

## License

Apache-2.0. The FFmpeg executable is a separate third-party program distributed under its own terms.
