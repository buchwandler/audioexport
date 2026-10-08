# Readio -> audioexport: compatibility-first extraction

**Reference inputs**: uploaded Readio Codecrate snapshot (2026-10-08) and `02_readio_modular_migration_roadmap.md`, milestone 06. This is an **implementation guide**, not an applied Readio patch.

## What was extracted/adapted

| Readio source                                                                                    | Independent implementation                                                             | Kept in Readio for now                                                                                               |
| ------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `readio/formats.py` format mappings + capability checks                                          | `audioexport/formats.py`, `audioexport/fftools.py` explicit FFmpeg mapping, `doctor()` | Readio's original public format detection and messages                                                               |
| `readio/wave.py` FFmpeg sink for M4A/Opus                                                        | `audioexport/pipeline.py` file-to-file FFmpeg argv encoding, all codecs                | Readio's streaming PCM sink and live `speak` path                                                                    |
| `readio/stages/export.py` bitrate normalization, export ID, output replacement                   | `audioexport/formats.py` bitrate; `pipeline.py` own export identity and sidecars       | `readio.export-state`, project lock, existing export identity and cache index                                        |
| `readio/stages/audiobook_export.py` metadata escaping, timeline chapters, M4B command and checks | `audioexport/chapters.py`, `audioexport/pipeline.py` FFmetadata/M4B/cover/FFprobe      | Readio's audiobook metadata defaults, project & timeline freshness checks, cover selection, audiobook-state identity |

The new independent package is not a verbatim file move: generic FFmpeg/chapter logic was adapted and decoupled from `Project`, `soundfile`, `numpy`, `audiocompose`, and Readio exception classes. M4B chapter timelines can be read as-is, without importing Readio.

## Current state

**No existing Readio files are changed by this archive**. In particular, no import from `readio` appears in `audioexport/`. There is no dependency from `audioexport` to UtterPlan, AudioCompose, VoiceRender, or Readio. Readio still owns its complete legacy fallback; switching it to this exporter is an intentionally separate step.

## Next PR, in the Readio repository

1. Capture golden baseline for `readio export` (WAV/FLAC/MP3/M4A/OGG/Opus), `readio audiobook export` (M4B metadata/chapters/cover), `readio speak --format`, cache/status/force; verify existing test suites.
2. Publish an installable audioexport release; pin an explicitly tested version range in Readio, initially via an optional `audioexport` extra or mandatory dep only when stabilized.
3. Add a **feature-flagged bridge**, e.g. environment/config `READIO_AUDIOEXPORT=1`. Leave default/legacy execution untouched initially. The bridge's only encoding operation is `audioexport.encode(...)` from Python, **not a subprocess invocation of `audioexport` CLI**.
4. In `readio/stages/export.py`, resolve the existing master WAV, `audio_format`, bitrate, output and overwrite policy using the _existing_ Readio helpers. Delegate the actual encode to `audioexport.encode(master, target, format=audio_format, bitrate=bitrate, force=replace_existing)`. Readio should keep its existing `readio.export-state` and `readio.export-index` state updates, identity, path ownership and lock. Because the new sidecar has a different identity, don't use `audioexport`'s manifest as a drop-in replacement for Readio state.
5. In `readio/stages/audiobook_export.py`, retain `prepare_audiobook_export()` and its freshness checks. Pass `prepared.master`, timeline file, `title`, `author`, `cover`, bitrate and output to the encoder. Preserve existing audiobook error-code translations and return fields, including `chapter_count`.
6. Keep the old encoding path for `speak --format` until stream/buffer parity is explicitly addressed: `audioexport.encode()` consumes an **existing file**, whereas `readio/wave.py` currently also supports **PCM streaming**. An intermediate temporary WAV adapter can work but should be isolated/tested, not silently forced on the live path.
7. Run the existing Readio format, export, audiobook, CLI, API and project-state test suites. On parity, enable the flag progressively, then deprecate the redundant encoding implementation in a later PR. Rollback = feature flag off; never delete last-good artifacts.

## Bridge shape (illustrative, not a drop-in patch)

```python
# Inside the existing Readio export operation, while the project lock is held,
# after its existing target ownership / provenance checks:
from audioexport import encode

result = encode(
    master, target, format=audio_format,
    bitrate=bitrate, force=replace_existing,
)
# Readio keeps storing its OWN export state/manifest and returning its OWN API result.
# The new audioexport sidecar is additional, not a replacement for Readio state.
```

**Important nuance:** For `readio export` with a tracked file that has changed inputs, using a new sidecar for the first time means the target appears untracked to audioexport. While bridging, Readio must explicitly authorize replace by passing `force=True` **only after Readio's own ownership check**; this applies even when its existing `force` flag was false. Never broaden replacement for arbitrary external targets.

For M4B the existing Readio source validates `composition-state` against master/timeline digests before export. AudioExport validates chapter JSON and media, but it deliberately does not know the Readio project state. Those validations must remain in Readio's wrapper.

## Differences to preserve or accept deliberately

- Readio's generic MP3/FLAC/OGG were written using `soundfile`; AudioExport uses FFmpeg for all encoders, so bitstreams and encoder provenance differ (expected). Tests should compare format, basic audio duration/properties, metadata and semantic chapter times, not exact bytes.
- Readio's bitrate CLI historically supports only M4A and Opus for generic `export`; AudioExport also supports MP3, OGG and M4B. Keep old Readio validation on the bridge until changing Readio's public contract deliberately.
- AudioExport's `audioexport.manifest.v1` is independent of Readio `readio.export-state`, and its identity includes source hashes, metadata/cover/chapters and FFmpeg/FFprobe version. Readio must not mistake the new sidecar for the old project index.
- A Readio sample-based timeline is supported, but its original project plan/metadata IDs are intentionally not validated outside Readio.
- Export-only change never triggers voice synthesis or composition. Readio's current project-level freshness must remain the source of truth.

## Acceptance gates before switching Readio by default

- All 7 standalone formats encode in CI and produce correct probed codec.
- M4B chapter order/title/starts/ends, cover, and author/title tags survive.
- Changes to bitrate/title/cover affect export only, and unchanged exports are reusable.
- Readio's `export`, `audiobook export`, and `speak` old signatures, errors, project indexes, and project state survive.
- No circular dependencies; last-good project remains readable/resumable.
