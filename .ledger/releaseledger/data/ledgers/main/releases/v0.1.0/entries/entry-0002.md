---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0002
release_version: v0.1.0
kind: changed
summary:
  Changed exports with multi-format profiles, diagnostics, transactional recovery,
  writer locking, and release checks
status: accepted
audience: null
scopes: []
source_refs:
  - git:a61e986f7d8f5534ed12269f39160226c3f03773
paths:
  - .github/workflows/release.yml
  - .github/workflows/tests.yml
  - MANIFEST.in
  - README.md
  - audioexport/fftools.py
  - audioexport/pipeline.py
  - audioexport/profile.py
  - examples/chapters.json
  - examples/export.toml
  - pyproject.toml
  - scripts/check_package_artifacts.py
  - tests/test_atomicity.py
  - tests/test_chapter_precision.py
  - tests/test_probe_and_doctor.py
  - tests/test_profiles_multiformat.py
issues: []
prs: []
sources:
  - git:a61e986f7d8f5534ed12269f39160226c3f03773
contributors:
  - "@holgern"
breaking: false
internal: false
order: 2
---
