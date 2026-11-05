"""Detect Python project dependency manifests and import layouts."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

REQUIREMENTS_CANDIDATES = (
    "requirements-dev.txt",
    "requirements-test.txt",
    "test-requirements.txt",
    "requirements.txt",
)

PROJECT_MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg")
PYTEST_CONFIGS = ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")

_LOCAL_PROJECT_RE = re.compile(
    r"^(?:-e|--editable)(?:\s+|=)(?:\.)?(?:\[.*\])?$",
    re.IGNORECASE,
)


@dataclass
class DependencyPlan:
    language: str
    supported: bool
    requirements_file: str | None
    project_manifest: str | None
    install_project: bool
    source_paths: list[str]
    pytest_config: str | None
    detected_manifests: list[str]
    notes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _requirements_install_local_project(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return False

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        line = line.split(" #", 1)[0].strip()

        if _LOCAL_PROJECT_RE.match(line):
            return True

        if line.startswith((".", "./")) and "[" in line:
            return True

    return False


def detect_python_project(repository_root: str | Path) -> DependencyPlan:
    root = Path(repository_root).resolve()

    if not root.is_dir():
        raise ValueError(f"Repository directory does not exist: {root}")

    manifests = [
        name
        for name in (*PROJECT_MANIFESTS, *REQUIREMENTS_CANDIDATES, "tox.ini")
        if (root / name).is_file()
    ]

    project_manifest = next(
        (name for name in PROJECT_MANIFESTS if (root / name).is_file()),
        None,
    )
    requirements_file = next(
        (name for name in REQUIREMENTS_CANDIDATES if (root / name).is_file()),
        None,
    )
    pytest_config = next(
        (name for name in PYTEST_CONFIGS if (root / name).is_file()),
        None,
    )

    source_paths = ["."]

    for source_dir in ("src", "lib"):
        if (root / source_dir).is_dir():
            source_paths.insert(0, source_dir)

    notes: list[str] = []

    installs_local_project = (
        _requirements_install_local_project(root / requirements_file)
        if requirements_file
        else False
    )

    install_project = bool(project_manifest and not installs_local_project)

    if installs_local_project:
        notes.append(
            f"{requirements_file} appears to install the local project; "
            "avoid installing it a second time."
        )
    elif install_project:
        notes.append(
            "Project metadata exists, but the requirements file does not "
            "appear to install the local project."
        )

    if (root / "tox.ini").is_file():
        notes.append(
            "tox.ini detected. Its commands will not be executed."
        )

    if not project_manifest and not requirements_file:
        notes.append("No supported Python dependency manifest was found.")

    return DependencyPlan(
        language="python",
        supported=bool(project_manifest or requirements_file),
        requirements_file=requirements_file,
        project_manifest=project_manifest,
        install_project=install_project,
        source_paths=source_paths,
        pytest_config=pytest_config,
        detected_manifests=manifests,
        notes=notes,
    )


def build_install_commands(plan: DependencyPlan) -> list[list[str]]:
    """Build pip commands for a disposable repository preparation container.

    Commands must only be executed inside the isolated preparation container,
    never directly on the host.
    """
    if not plan.supported:
        return []

    commands: list[list[str]] = []

    if plan.requirements_file:
        commands.append([
            "python",
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            f"/workspace/{plan.requirements_file}",
        ])

    if plan.install_project and plan.project_manifest:
        commands.append([
            "python",
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-e",
            "/workspace",
        ])

    return commands


def build_pythonpath(plan: DependencyPlan) -> str:
    """Return normalized import paths for the repository source layout."""
    paths = []
    for source_path in plan.source_paths:
        normalized = Path(source_path).as_posix().rstrip("/")
        if normalized == ".":
            normalized = ""
        paths.append(f"/workspace/{normalized}".rstrip("/"))

    return ":".join(paths)