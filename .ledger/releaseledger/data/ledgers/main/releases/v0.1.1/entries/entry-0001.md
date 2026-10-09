---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.1.1
kind: added
summary:
  Added ordered multi-track audiobook assembly with chapters, metadata, cover
  art, verification, and reusable manifests
status: accepted
audience: null
scopes: []
source_refs:
  - git:3d51c13bb8342d90bd9477ddcca79818ab88b949
paths:
  - README.md
  - audioexport/__init__.py
  - audioexport/api.py
  - audioexport/audiobook.py
  - audioexport/audiobook_profile.py
  - audioexport/chapters.py
  - audioexport/cli.py
  - audioexport/fftools.py
  - audioexport/inputs.py
  - audioexport/metadata.py
  - tests/test_api_import.py
  - tests/test_audiobook_atomicity.py
  - tests/test_audiobook_cache.py
  - tests/test_audiobook_inputs.py
  - tests/test_audiobook_metadata.py
  - tests/test_audiobook_profile.py
  - tests/test_audiobook_real.py
  - tests/test_audiobook_scale.py
  - tests/test_chapters_txt.py
  - tests/test_cli.py
  - tests/test_probe_and_doctor.py
issues: []
prs: []
sources:
  - git:3d51c13bb8342d90bd9477ddcca79818ab88b949
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---
