---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.1.8
kind: fixed
summary:
  Fixed M4B exports to use the audiobook-specific container brand and reject
  outputs with an incorrect brand
status: accepted
audience: null
scopes: []
source_refs:
  - git:eb983a8b4057f7832b82e477aeffdd2d8fc9cb73
paths:
  - audioexport/audiobook.py
  - audioexport/formats.py
  - audioexport/pipeline.py
  - tests/test_audiobook_real.py
  - tests/test_book.py
  - tests/test_core.py
issues: []
prs: []
sources:
  - git:eb983a8b4057f7832b82e477aeffdd2d8fc9cb73
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---
