---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0001
release_version: v0.1.0
kind: added
summary:
  Added standalone FFmpeg export API and CLI for seven formats with metadata,
  chapters, cover art, and manifests
status: accepted
audience: null
scopes: []
source_refs:
  - git:5e1e3c93adea067337b8c0367749c212eb54c974
paths:
  - .codecrate.toml
  - .github/workflows/tests.yml
  - .gitignore
  - .ledger/ledger.toml
  - .ledger/releaseledger/.ledger-project.toml
  - .ledger/releaseledger/config.toml
  - .ledger/releaseledger/data/.ledger-project.toml
  - .ledger/taskledger/.ledger-project.toml
  - .ledger/taskledger/config.toml
  - .pre-commit-config.yaml
  - README.md
  - audioexport/__init__.py
  - audioexport/__main__.py
  - audioexport/api.py
  - audioexport/chapters.py
  - audioexport/cli.py
  - audioexport/errors.py
  - audioexport/fftools.py
  - audioexport/formats.py
  - audioexport/pipeline.py
  - audioexport/profile.py
  - audioexport/py.typed
  - docs/readio-migration.md
  - examples/chapters.json
  - examples/create_demo_wav.py
  - examples/export.toml
  - pyproject.toml
  - tests/conftest.py
  - tests/test_api_import.py
  - tests/test_cli.py
  - tests/test_core.py
  - tests/test_cover_and_errors.py
issues: []
prs: []
sources:
  - git:5e1e3c93adea067337b8c0367749c212eb54c974
contributors:
  - "@holgern"
breaking: false
internal: false
order: 1
---
