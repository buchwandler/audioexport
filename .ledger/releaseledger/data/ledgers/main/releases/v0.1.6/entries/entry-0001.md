---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.1.6
kind: fixed
summary:
  Fixed M4A/M4B export failures when FFmpeg omits requested publisher or encoded_by
  tags
status: accepted
audience: null
scopes: []
source_refs:
  - tl:task-0010
paths:
  - audioexport/pipeline.py
  - tests/test_core.py
  - tests/test_audiobook_real.py
  - tests/test_book.py
  - docs/changelog.md
issues: []
prs: []
sources: []
contributors: []
breaking: false
internal: false
order: 1
---

Missing publisher and encoded_by tags are treated as best-effort only for M4A/M4B; conflicting emitted values and all other requested metadata remain strictly verified. Requested metadata remains in the export sidecar.
