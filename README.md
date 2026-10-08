# audioexport

An independent Python library and command-line interface for exporting audio through **FFmpeg**. This is the first extraction step from Readio's output/encoding subsystem, but **audioexport does not import or install Readio, AudioCompose, UtterPlan, or synthesis engines**.

Supported formats: WAV (PCM16), FLAC, MP3, M4A (AAC), M4B (AAC audiobook), OGG (Vorbis), and Opus. Handles tagged metadata, embedded JPEG/PNG cover art for MP3/M4A/M4B, JSON chapter timelines for M4A/M4B, FFprobe verification, artifact identities and safe reuse.

## Install

Requirements: **Python 3.10+** and **FFmpeg + FFprobe** executables accessible on `PATH`. The FFmpeg build needs the encoders you use (`libmp3lame`, `aac`, `flac`, `libvorbis`, `libopus`, etc.).

```bash
# clone this repository or unzip the MVP, then from its root:
python -m pip install -e .
audioexport --version
audioexport doctor
```

The sole Python runtime dependency is `tomli` on Python 3.10. The encoders and probe tools are external, not downloaded by the package.

## CLI quickstart

```bash
# Create a self-contained test WAV (or use your own audio file):
python examples/create_demo_wav.py
# Any FFmpeg-decodable audio source is accepted.
audioexport encode master.wav --format mp3 -o output.mp3 --bitrate 192k
audioexport encode master.wav --format flac -o output.flac
audioexport encode master.wav --format m4a -o output.m4a --title "My recording" --artist "An author"
audioexport encode master.wav --format ogg -o output.ogg
audioexport encode master.wav --format opus -o output.opus

audioexport inspect output.mp3 --json
audioexport doctor --json

# With M4B chapters, cover art, and metadata:
audioexport encode master.wav --format m4b -o mybook.m4b \
  --title "My Book" --artist "An Author" \
  --timeline chapters.json --cover cover.jpg --bitrate 192k

# Export multiple formats at once using an editable profile:
audioexport run master.wav --profile examples/export.toml --out-dir exported/ --json
```

If `--output` is not set for `encode`, the output filename defaults to the input stem and chosen format. To avoid destroying the input, encoding input and output to the same real path is always rejected.

`--force` replaces an existing output; without it, the exporter only rewrites files it previously generated **and whose checksum still matches the sidecar**. Re-running identical options returns `reused: true` and doesn't re-encode.

OGG defaults to Vorbis quality-based encoding (`-qscale:a 4`) to avoid low-rate/mono bitrate incompatibilities. You can explicitly select `--bitrate` when your FFmpeg build/input supports that rate. WAV/FLAC don't accept bitrate.

## Python API

```python
from pathlib import Path
from audioexport import Chapter, encode, run_profile, doctor, probe

result = encode(
    Path("master.wav"), Path("exported/book.m4b"),
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

print(doctor()["ready"])
print(probe("exported/book.m4b")["chapters"])
```

You may import the same public functions from `audioexport.api` as well as from `audioexport`.

The public errors are `AudioExportError` and subclasses `InvalidExportError`, `ToolNotFoundError`, `EncodingError`, and `VerificationError`, with a stable machine-readable `.code` value. `encode()` returns an immutable `ExportResult` containing the identity, path, checksum, and reuse state. Both `encode` and `run_profile` work without a Readio project.

## Export profile

See [`examples/export.toml`](examples/export.toml). The v1 contract is deliberately small:

```toml
schema = "audioexport.profile.v1"
# cover = "cover.jpg"
# timeline = "chapters.json"

[metadata]
title = "My audiobook"
artist = "An author"

[[outputs]]
format = "mp3"
bitrate = "192k"

[[outputs]]
format = "flac"
filename = "archival.flac"

[[outputs]]
format = "m4b"
bitrate = "192k"
```

`cover` and `timeline` paths are resolved relative to the profile file; `out_dir` is the requested destination. Output `filename` must be a simple filename (no `../` or nested paths) and match the format extension. Values not understood by schema v1 are rejected instead of silently ignored.

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

Each completed output has a sibling sidecar, for example `output.mp3.audioexport.json`, holding `audioexport.manifest.v1`, a SHA-256 export identity, the source/output/cover/timeline digests, FFmpeg/FFprobe versions, normalized options, chapter mapping, and probed output summary. The identity is **input-content + options + tools**, not output location. A profile change reruns only affected outputs; the original WAV is never modified.

Encoding uses argument-vector subprocess calls (`shell=False`) and a temporary output in the destination directory. The FFprobe validation checks codec, audio stream properties, duration, requested chapters, and attached cover art, before atomically installing the output. Manifest writes are also atomic. For an MVP this is **not a transactional concurrency lock**; don't run two writers to the exact same target simultaneously.

Version is **dynamic**, supplied by `setuptools-scm` from Git tags (same approach as Readio), with no committed hard-coded package version and **no `src/` directory**:

```bash
git init
git add .
git commit -m "Initial audioexport MVP"
git tag v0.1.0
python -m pip install -e .
audioexport --version
```

After unzipping _without_ Git metadata, the development build falls back to `0.1.0.dev0`. Once versioned in Git, tags such as `v0.1.0` and `v0.2.0` determine wheel versions. `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_AUDIOEXPORT` can also supply a build version when needed. Publish only after taking ownership of package name, license, and release process.

## Readio migration

**Readio is deliberately not patched by this archive.** Its `readio export`, `readio audiobook export`, and `readio speak --format` continue using the existing code until an opt-in bridge is tested. See [`docs/readio-migration.md`](docs/readio-migration.md) for the current source map, compatibility boundary, integration order, and rollback rules. The new library handles encoding; Readio continues owning project locks, path defaults, staleness, indexed state, and orchestration.

## Tests

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python -m build
```

Tests cover format mappings, chapter validation, export reuse, overwrite safety, real FFmpeg transcoding of seven formats, CLI, profiles, and M4B chapter metadata. Real codec tests skip when FFmpeg/FFprobe aren't available. CI runs Linux, macOS and Windows where platform FFmpeg packages can differ.

## Limitations (MVP)

No audio mastering, TTS, audio mixing, streaming PCM API, FFmpeg filter graphs, network service, or GUI. No automatic conversion of Readio project configs, legacy export cache, or Audiocompose timeline semantics other than chapter starts. Encoder output may vary across FFmpeg versions; the cache key includes tool versions. Chapter verification has a small tolerance for FFmpeg container timebase rounding. Standalone encoding does not modify Readio projects.

## License

Apache-2.0. The FFmpeg executable is a separate third-party program distributed under its own terms.
