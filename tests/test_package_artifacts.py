from __future__ import annotations

import importlib.util
from email.message import Message
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_package_artifacts.py"
    spec = importlib.util.spec_from_file_location("check_package_artifacts", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def package_metadata(*requirements: str) -> Message:
    metadata = Message()
    metadata["Name"] = "audioexport"
    metadata["Version"] = "0.1.2.dev0"
    metadata["Requires-Python"] = ">=3.10"
    for requirement in requirements:
        metadata["Requires-Dist"] = requirement
    return metadata


def test_accepts_intended_runtime_dependencies(checker: ModuleType) -> None:
    metadata = package_metadata(
        "langcodes>=3.3",
        'tomli>=2; python_version < "3.11"',
        "pytest>=8; extra == 'dev'",
    )

    assert checker._check_runtime_metadata(metadata, "0.1.2.dev0") == "0.1.2.dev0"


@pytest.mark.parametrize(
    "requirements, message",
    [
        (
            ("langcodes>=3.3", 'tomli>=2; python_version < "3.11"', "requests>=2"),
            "unexpected runtime dependency",
        ),
        (
            ("langcodes>=3.3", 'tomli>=2; python_version < "3.11"', "numpy>=1"),
            "forbidden runtime dependency",
        ),
        (
            ("langcodes>=3.2", 'tomli>=2; python_version < "3.11"'),
            "langcodes must require version >=3.3",
        ),
        (
            ("langcodes>=3.3; python_version >= '3.11'", 'tomli>=2; python_version < "3.11"'),
            "langcodes must be an unconditional",
        ),
        (("langcodes>=3.3", "tomli>=2"), "tomli must be conditional on Python < 3.11"),
        (("langcodes>=3.3", 'tomli>=1; python_version < "3.11"'), "tomli must require version >=2"),
        (("langcodes>=3.3",), "missing required runtime dependencies"),
    ],
)
def test_rejects_invalid_runtime_dependency_metadata(
    checker: ModuleType, requirements: tuple[str, ...], message: str
) -> None:
    with pytest.raises(SystemExit, match=message):
        checker._check_runtime_metadata(package_metadata(*requirements), None)
