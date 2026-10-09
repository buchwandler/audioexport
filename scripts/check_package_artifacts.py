from __future__ import annotations

import argparse
import email
import os
import subprocess
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename

REQUIRED_MODULES = {
    "__init__.py",
    "__main__.py",
    "_version.py",
    "api.py",
    "chapters.py",
    "cli.py",
    "errors.py",
    "fftools.py",
    "formats.py",
    "pipeline.py",
    "profile.py",
    "preflight.py",
    "validation.py",
}
FORBIDDEN_RUNTIME_DEPENDENCIES = {
    "audiocompose",
    "numpy",
    "readio",
    "soundfile",
    "utterplan",
    "voicerender",
}


def _fail(message: str) -> None:
    raise SystemExit(f"package artifact check failed: {message}")


def _metadata(raw: str) -> email.message.Message:
    return email.message_from_string(raw)


def _check_runtime_metadata(metadata: email.message.Message, expected_version: str | None) -> str:
    name = canonicalize_name(metadata.get("Name", ""))
    version = metadata.get("Version", "")
    if name != "audioexport":
        _fail(f"unexpected distribution name {name!r}")
    if expected_version is not None and version != expected_version:
        _fail(f"expected version {expected_version}, found {version}")
    if metadata.get("Requires-Python") != ">=3.10":
        _fail(f"unexpected Requires-Python: {metadata.get('Requires-Python')!r}")
    for raw_requirement in metadata.get_all("Requires-Dist", []):
        requirement = Requirement(raw_requirement)
        marker = requirement.marker
        if marker is not None and "extra" in str(marker) and not marker.evaluate({"extra": ""}):
            continue
        normalized = canonicalize_name(requirement.name)
        if normalized in FORBIDDEN_RUNTIME_DEPENDENCIES:
            _fail(f"forbidden runtime dependency: {raw_requirement}")
        if normalized != "tomli":
            _fail(f"unexpected runtime dependency: {raw_requirement}")
        if (
            marker is None
            or not marker.evaluate({"python_version": "3.10", "extra": ""})
            or marker.evaluate({"python_version": "3.11", "extra": ""})
        ):
            _fail(f"tomli must be conditional on Python < 3.11: {raw_requirement}")
    return version


def _check_wheel(path: Path, expected_version: str | None) -> str:
    name, parsed_version, _, _ = parse_wheel_filename(path.name)
    if canonicalize_name(name) != "audioexport":
        _fail(f"unexpected wheel name: {path.name}")
    with zipfile.ZipFile(path) as wheel:
        files = set(wheel.namelist())
        for module in REQUIRED_MODULES:
            if f"audioexport/{module}" not in files:
                _fail(f"wheel is missing audioexport/{module}")
        if "audioexport/py.typed" not in files:
            _fail("wheel is missing audioexport/py.typed")
        if any(path.startswith(("tests/", "src/audioexport/")) for path in files):
            _fail("wheel contains tests or an accidental src/ package")
        metadata_paths = [item for item in files if item.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            _fail("wheel must contain exactly one METADATA")
        metadata = _metadata(wheel.read(metadata_paths[0]).decode("utf-8"))
        wheel_version = _check_runtime_metadata(metadata, expected_version)
        if str(parsed_version) != wheel_version:
            _fail("wheel filename and METADATA versions differ")
        if not metadata.get_payload().strip():
            _fail("wheel metadata is missing its README description")
        if not any(item.endswith(("/LICENSE", "/licenses/LICENSE")) for item in files):
            _fail("wheel is missing the license file")
    return wheel_version


def _check_sdist(path: Path, expected_version: str | None) -> str:
    name, parsed_version = parse_sdist_filename(path.name)
    if canonicalize_name(name) != "audioexport":
        _fail(f"unexpected sdist name: {path.name}")
    with tarfile.open(path, "r:gz") as archive:
        members = {item.name for item in archive.getmembers() if item.isfile()}
        module_paths = {f"audioexport/{module}" for module in REQUIRED_MODULES}
        for module_path in module_paths:
            if not any(item.endswith("/" + module_path) for item in members):
                _fail(f"sdist is missing {module_path}")
        if not any(item.endswith("/audioexport/py.typed") for item in members):
            _fail("sdist is missing audioexport/py.typed")
        if not any(item.endswith("/README.md") for item in members):
            _fail("sdist is missing README.md")
        if not any(item.endswith("/LICENSE") for item in members):
            _fail("sdist is missing LICENSE")
        if any("/src/audioexport/" in item for item in members):
            _fail("sdist contains an accidental src/ package")
        if any(
            any(
                marker in item
                for marker in (
                    "/.ledger/",
                    "/.github/",
                    "/tests/",
                    "/scripts/",
                    "/.gitignore",
                    "/.codecrate.toml",
                    "/.pre-commit-config.yaml",
                )
            )
            for item in members
        ):
            _fail("sdist contains private ledger, workflow, or test-only files")
        metadata_paths = [item for item in members if item.endswith("/PKG-INFO")]
        if len(metadata_paths) != 1:
            _fail("sdist must contain exactly one PKG-INFO")
        stream = archive.extractfile(metadata_paths[0])
        if stream is None:
            _fail("could not read sdist PKG-INFO")
        metadata = _metadata(stream.read().decode("utf-8"))
        sdist_version = _check_runtime_metadata(metadata, expected_version)
        if str(parsed_version) != sdist_version:
            _fail("sdist filename and PKG-INFO versions differ")
    return sdist_version


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def _smoke_install(wheel: Path, version: str, source_root: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="audioexport-wheel-smoke-") as temporary:
        temp_root = Path(temporary).resolve()
        if temp_root.is_relative_to(source_root):
            _fail("wheel smoke directory is inside the source checkout")
        environment = temp_root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        install = _run([str(python), "-m", "pip", "install", str(wheel)], cwd=temp_root)
        if install.returncode:
            _fail(f"clean wheel install failed:\n{install.stdout}\n{install.stderr}")
        smoke = (
            "import importlib.metadata as metadata; "
            "import importlib.resources as resources; "
            "from pathlib import Path; import sys; import audioexport; "
            "from audioexport import ResolvedOutput, resolve_output, preflight_profile, "
            "encode, run_profile, load_profile, probe, doctor; "
            "from audioexport.api import ResolvedOutput as ApiResolvedOutput, "
            "resolve_output as api_resolve_output, "
            "preflight_profile as api_preflight_profile; "
            "assert ResolvedOutput is ApiResolvedOutput and "
            "resolve_output is api_resolve_output and "
            "preflight_profile is api_preflight_profile; "
            f"assert audioexport.__version__ == metadata.version('audioexport') == {version!r}; "
            "assert resources.files('audioexport').joinpath('py.typed').is_file(); "
            f"assert not Path(audioexport.__file__).resolve().is_relative_to(Path({str(source_root)!r})); "
            "assert not any(name in sys.modules for name in "
            "('readio', 'audiocompose', 'utterplan', 'voicerender', 'soundfile', 'numpy')); "
            "assert all(callable(item) for item in (ResolvedOutput, resolve_output, "
            "preflight_profile, encode, run_profile, load_profile, probe, doctor))"
        )
        result = _run([str(python), "-c", smoke], cwd=temp_root)
        if result.returncode:
            _fail(f"outside-checkout API smoke failed:\n{result.stdout}\n{result.stderr}")
        cli = scripts / ("audioexport.exe" if os.name == "nt" else "audioexport")
        for command in ([str(cli), "--version"], [str(python), "-m", "audioexport", "--version"]):
            result = _run(list(command), cwd=temp_root)
            if result.returncode or not result.stdout.strip().endswith(version):
                _fail(f"version command failed or mismatched: {result.stdout}{result.stderr}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument("--expected-version")
    args = parser.parse_args()
    dist_dir = args.dist_dir.resolve()
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        _fail(f"expected exactly one wheel and sdist; found {len(wheels)} and {len(sdists)}")
    expected = args.expected_version
    wheel_version = _check_wheel(wheels[0], expected)
    sdist_version = _check_sdist(sdists[0], expected)
    if wheel_version != sdist_version:
        _fail(f"wheel/sdist version mismatch: {wheel_version} != {sdist_version}")
    source_root = Path(__file__).resolve().parents[1]
    _smoke_install(wheels[0], wheel_version, source_root)
    print(f"verified audioexport {wheel_version}: wheel, sdist, and outside-checkout install")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
