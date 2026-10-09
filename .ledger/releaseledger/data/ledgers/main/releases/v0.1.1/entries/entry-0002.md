---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0002
release_version: v0.1.1
kind: added
summary:
  Added read-only profile preflight and output resolution APIs that validate
  selected resources and encoders before export
status: accepted
audience: null
scopes: []
source_refs:
  - git:31a1e4e74bd6a1721ecba0b19eb59b401415e40a
paths:
  - README.md
  - audioexport/__init__.py
  - audioexport/api.py
  - audioexport/audiobook.py
  - audioexport/fftools.py
  - audioexport/pipeline.py
  - audioexport/preflight.py
  - audioexport/profile.py
  - audioexport/validation.py
  - docs/Makefile
  - docs/conf.py
  - docs/make.bat
  - docs/make.py
  - docs/readio-migration.md
  - docs/requirements.txt
  - scripts/check_package_artifacts.py
  - tests/test_api_import.py
  - tests/test_preflight.py
  - tests/test_probe_and_doctor.py
  - tests/test_profile_resolution.py
  - tests/test_profiles_multiformat.py
issues: []
prs: []
sources:
  - git:31a1e4e74bd6a1721ecba0b19eb59b401415e40a
contributors:
  - "@holgern"
breaking: false
internal: false
order: 2
---
