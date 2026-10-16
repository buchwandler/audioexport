---
schema_version: 2
object_type: release_entry
versioning:
  schema_version: 1
  revision: 1
entry_id: entry-0004
release_version: v0.1.2
kind: quality
summary: Improved package artifact checks and release documentation for v0.1.2
status: accepted
audience: null
scopes: []
source_refs: []
paths:
  - scripts/check_package_artifacts.py
  - tests/test_package_artifacts.py
  - tests/test_examples.py
  - examples/audiobook.toml
  - examples/chapters.txt
  - examples/create_ssmdbook_demo.py
  - README.md
  - docs/index.md
  - docs/conf.py
  - docs/changelog.md
  - .github/workflows/tests.yml
  - .github/workflows/python-release.yml
  - .github/workflows/pre-commit.yml
  - pyproject.toml
issues: []
prs: []
sources:
  - tl:task-0005
contributors: []
breaking: false
internal: false
order: 4
---
