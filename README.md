# audioexport

An independent Python library and command-line interface for exporting audio through **FFmpeg**. This is the first extraction step from Readio's output/encoding subsystem, but **audioexport does not import or install Readio, AudioCompose, UtterPlan, or synthesis engines**.

Supported formats: WAV (PCM16), FLAC, MP3, M4A (AAC), M4B (AAC audiobook), OGG (Vorbis), and Opus. Handles tagged metadata, embedded JPEG/PNG cover art for MP3/M4A/M4B, JSON chapter timelines for M4A/M4B, FFprobe verification, artifact identities and safe reuse.

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

`doctor` reports package/tool versions and encoder availability per format. Its existing `ready` key means FFmpeg and FFprobe are present; `all_formats_ready` and each `formats` entry report encoder capability and a reason when unavailable. `run_profile()` checks the encoders selected by the profile before writing outputs.

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

The public errors are `AudioExportError` and subclasses `InvalidExportError`, `ToolNotFoundError`, `EncodingError`, and `VerificationError`, with a stable machine-readable `.code` value. `encode()` returns an immutable `ExportResult` containing the identity, path, checksum, and reuse state. Both `encode` and `run_profile` work without a Readio project.

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

Each completed output has a sibling sidecar, for example `output.mp3.audioexport.json`, holding `audioexport.manifest.v1`, a SHA-256 export identity, the source/output and consumed-resource digests, FFmpeg/FFprobe versions, normalized options, chapter mapping, and probed output summary. The identity is **input-content + relevant options/resources + tools**, not output location or TOML formatting. A bitrate change affects only that output; a timeline change affects only chapter-consuming outputs; changing shared metadata affects all outputs that inherit it. The original source is never modified.

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

Tests cover seven real formats in one profile, selective cache invalidation, M4A/M4B chapters and covers, Readio sample timelines, malformed probes, encoder availability, transactional failure recovery, no-clobber behavior, and concurrent writers. CI runs Python 3.10 and 3.13 on Linux, Windows, and macOS, and checks lint, formatting, typing, build metadata, Twine, package contents, and an isolated wheel install outside the checkout. Cross-platform encoder availability still depends on the FFmpeg build installed by the runner.

## Limitations (MVP)

No audio mastering, TTS, audio mixing, streaming PCM API, FFmpeg filter graphs, network service, or GUI. No automatic conversion of Readio project configs, legacy export cache, or Audiocompose timeline semantics other than chapter starts. Encoder output may vary across FFmpeg versions; the cache key includes tool versions. Chapter verification has a small tolerance for FFmpeg container timebase rounding. Standalone encoding does not modify Readio projects.

## License

Apache-2.0. The FFmpeg executable is a separate third-party program distributed under its own terms.
