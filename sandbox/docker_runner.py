"""Run repository pytest commands in a disposable, network-disabled Docker container.

Only pytest commands are accepted. The repository is copied into an anonymous
Docker volume; no host directory or Docker socket is mounted into the container.
The container is always removed with its anonymous volume after execution.
"""
from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Sequence

import json

from .dependency_resolver import (
    detect_python_project,
    build_install_commands,
    build_pythonpath,
)

IMAGE = "knowledge-engine-test:py313-pytest92"
CONTAINER_MEMORY = "640m"
CONTAINER_CPUS = "1.0"
CONTAINER_PIDS = "64"
DOCKER_CHECK_TIMEOUT = 12


class DockerRunnerUnavailable(RuntimeError):
    """Docker or the required prebuilt test image is unavailable."""


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _docker(args: Sequence[str], timeout: int = DOCKER_CHECK_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Invoke the Docker CLI without a shell or inherited application secrets."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "WINDIR": os.environ.get("WINDIR", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
        "USERPROFILE": os.environ.get("USERPROFILE", ""),
        "APPDATA": os.environ.get("APPDATA", ""),
        "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
        "DOCKER_HOST": os.environ.get("DOCKER_HOST", ""),
        "DOCKER_CONTEXT": os.environ.get("DOCKER_CONTEXT", ""),
        "DOCKER_CONFIG": os.environ.get("DOCKER_CONFIG", ""),
    }
    env = {key: value for key, value in env.items() if value}
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        shell=False,
        check=False,
    )


def _pytest_args(command: Sequence[str]) -> list[str]:
    """Normalize known Python/pytest launchers to arguments for container Python."""
    parts = [str(part) for part in command]
    if not parts:
        raise ValueError("empty test command")

    executable = parts[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if executable in {"pytest", "pytest.exe"}:
        args = parts[1:]
    elif executable in {"python", "python.exe", "python3", "python3.exe"}:
        if len(parts) < 3 or parts[1:3] != ["-m", "pytest"]:
            raise ValueError("only Python commands using '-m pytest' are supported")
        args = parts[3:]
    elif executable in {"py", "py.exe"}:
        tail = parts[1:]
        if tail and tail[0].startswith("-") and tail[0] not in {"-m"}:
            tail = tail[1:]  # e.g. Windows py -3 -m pytest
        if len(tail) < 2 or tail[:2] != ["-m", "pytest"]:
            raise ValueError("only Python launcher commands using '-m pytest' are supported")
        args = tail[2:]
    else:
        raise ValueError("only pytest test commands are supported; arbitrary commands are refused")

    # The runner owns the JUnit destination and cache setting. Do not allow a
    # caller-supplied report path to redirect output outside the container.
    safe_args: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--junitxml" or arg == "--junit-xml":
            skip_next = True
            continue
        if arg.startswith("--junitxml=") or arg.startswith("--junit-xml="):
            continue
        safe_args.append(arg)
    return safe_args


def _check_docker() -> None:
    try:
        info = _docker(["info", "--format", "{{.ServerVersion}}"])
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise DockerRunnerUnavailable(f"Docker is unavailable or did not respond: {exc}") from exc
    if info.returncode != 0 or not info.stdout.strip():
        detail = (info.stderr or info.stdout or "Docker daemon is not responding").strip()
        raise DockerRunnerUnavailable(f"Docker is unavailable: {detail[:500]}")

    try:
        image = _docker(["image", "inspect", "--format", "{{.Id}}", IMAGE])
    except subprocess.TimeoutExpired as exc:
        raise DockerRunnerUnavailable("Timed out checking the isolated test image") from exc
    if image.returncode != 0 or not image.stdout.strip():
        raise DockerRunnerUnavailable(
            f"Required image '{IMAGE}' is missing. Build it with: "
            "docker build -t knowledge-engine-test:py313-pytest92 -f sandbox/Dockerfile.test ."
        )


def _copy_report(container_id: str, host_xml: Path) -> str:
    host_xml.parent.mkdir(parents=True, exist_ok=True)
    host_xml.unlink(missing_ok=True)
    try:
        copied = _docker(["cp", f"{container_id}:/workspace/pytest-results.xml", str(host_xml)])
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return f"Could not retrieve JUnit XML: {exc}"
    if copied.returncode != 0:
        detail = (copied.stderr or copied.stdout or "JUnit report was not produced").strip()
        return f"JUnit report unavailable: {detail[:400]}"
    return ""


def run_pytest_in_docker(
    command: Sequence[str],
    workdir: Path,
    timeout: int,
    junit_path: Path,
):
    """Run pytest in Docker and return the existing sandbox.runner.CommandResult.

    The import is intentionally local: runner._run_tests imports this function
    lazily, avoiding a module-import cycle while preserving the public API.
    """
    from .runner import CommandResult, _failed_ids, _read_junit, _trim
    started = time.monotonic()
    original_command = [str(part) for part in command]

    if not workdir.is_dir():
        return CommandResult(
            original_command,
            None,
            f"Test workspace not found: {workdir}",
            time.monotonic() - started,
        )

    try:
        dependency_plan = detect_python_project(workdir)
        install_commands = build_install_commands(dependency_plan)
        pythonpath = build_pythonpath(dependency_plan)
    except (OSError, ValueError) as exc:
        return CommandResult(
            original_command,
            None,
            f"Could not inspect repository dependencies: {exc}",
            time.monotonic() - started,
        )
    try:
        pytest_args = _pytest_args(original_command)
        _check_docker()
    except (ValueError, DockerRunnerUnavailable) as exc:
        return CommandResult(original_command, None, str(exc), time.monotonic() - started)

    container_name = f"ke-test-{uuid.uuid4().hex[:12]}"
    container_id: str | None = None
    output = ""
    exit_code: int | None = None
    timed_out = False
    report_note = ""
    junit_path = Path(junit_path)
    junit_path.unlink(missing_ok=True)

    create_args = [
        "create",
        "--name", container_name,
        "--network", "bridge",
        "--read-only",
        "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=256m,mode=1777",
        "--mount", "type=volume,dst=/workspace",
        "--workdir", "/workspace",
        "--user", "10001:10001",
        "--memory", CONTAINER_MEMORY,
        "--memory-swap", CONTAINER_MEMORY,
        "--cpus", CONTAINER_CPUS,
        "--pids-limit", CONTAINER_PIDS,
        "--cap-drop", "ALL",
        "--cap-add", "FOWNER",
        "--security-opt", "no-new-privileges:true",
        "--init",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--env", f"PYTHONPATH={pythonpath}",
        "--env", "PIP_NO_CACHE_DIR=1",
        "--env", "HOME=/tmp",
        "--env", "TMPDIR=/tmp",
        "--entrypoint", "python",
        IMAGE,
        "-c", "import time; time.sleep(3600)",
    ]

    try:
        created = _docker(create_args)
        if created.returncode != 0 or not created.stdout.strip():
            detail = (created.stderr or created.stdout or "docker create failed").strip()
            output = f"Could not create isolated test container: {detail[:1000]}"
            return CommandResult(original_command, None, _trim(output), time.monotonic() - started)
        container_id = created.stdout.strip().splitlines()[-1]

        started_container = _docker(["start", container_id])
        if started_container.returncode != 0:
            detail = (started_container.stderr or started_container.stdout or "docker start failed").strip()
            output = f"Could not start isolated test container: {detail[:1000]}"
            return CommandResult(original_command, None, _trim(output), time.monotonic() - started)

        # Copy contents, not the parent directory, into the anonymous volume.
        source = str(workdir) + os.sep + "."
        copied = _docker(["cp", source, f"{container_id}:/workspace/"])
        if copied.returncode != 0:
            detail = (copied.stderr or copied.stdout or "docker cp failed").strip()
            output = f"Could not copy repository into isolated container: {detail[:1000]}"
            return CommandResult(original_command, None, _trim(output), time.monotonic() - started)

        # docker cp creates root-owned files. Make the disposable workspace
        # writable before executing anything, then run pytest strictly as UID 10001.
        permissions = _docker([
            "exec", "--user", "0:0", container_id,
            "python", "-c",
            "import os; from pathlib import Path; root=Path('/workspace'); "
            "[(os.chmod(p, 0o777 if p.is_dir() else 0o666)) "
            "for p in [root, *root.rglob('*')] if not p.is_symlink()]",
        ])
        if permissions.returncode != 0:
            detail = (permissions.stderr or permissions.stdout or "workspace permission setup failed").strip()
            output = f"Could not prepare isolated workspace: {detail[:1000]}"
            return CommandResult(original_command, None, _trim(output), time.monotonic() - started)

                # Create a disposable environment. Keep the image's pytest available
        # through --system-site-packages.
        venv_result = _docker([
            "exec", "--user", "10001:10001",
            "--workdir", "/workspace",
            container_id,
            "python", "-m", "venv",
            "--system-site-packages", "/tmp/venv",
        ], timeout=30)

        if venv_result.returncode != 0:
            detail = (venv_result.stderr or venv_result.stdout or "").strip()
            output = f"Could not create dependency environment: {detail[:1500]}"
            return CommandResult(
                original_command, 2, _trim(output),
                time.monotonic() - started,
            )

        # Install repository dependencies while the container has network access.
        for install_command in install_commands:
            container_command = [
                "/tmp/venv/bin/python" if part == "python" else part
                for part in install_command
            ]

            try:
                installed = _docker([
                    "exec", "--user", "10001:10001",
                    "--workdir", "/workspace",
                    container_id,
                    *container_command,
                ], timeout=max(60, min(int(timeout), 300)))
            except subprocess.TimeoutExpired:
                output = (
                    "Dependency preparation timed out. "
                    "The repository's test dependencies could not be installed."
                )
                return CommandResult(
                    original_command, 2, output,
                    time.monotonic() - started,
                )

            install_output = (installed.stdout or "") + (installed.stderr or "")
            if installed.returncode != 0:
                output = (
                    "Dependency preparation failed. Pytest was not started.\n\n"
                    + install_output[-12000:]
                )
                return CommandResult(
                    original_command, 2, _trim(output),
                    time.monotonic() - started,
                )

        # Disconnect before executing any repository tests. Fail closed:
        # pytest must never run if network isolation cannot be confirmed.
        disconnected = _docker([
            "network", "disconnect", "bridge", container_id,
        ])

        if disconnected.returncode != 0:
            detail = (disconnected.stderr or disconnected.stdout or "").strip()
            output = f"Could not disable container network: {detail[:1000]}"
            return CommandResult(
                original_command, None, _trim(output),
                time.monotonic() - started,
            )

        network_check = _docker([
            "inspect",
            "--format",
            "{{json .NetworkSettings.Networks}}",
            container_id,
        ])

        try:
            remaining_networks = json.loads(network_check.stdout)
        except (json.JSONDecodeError, TypeError):
            remaining_networks = None

        if network_check.returncode != 0 or remaining_networks != {}:
            output = (
                "Network isolation could not be verified. "
                "Pytest was not started."
            )
            return CommandResult(
                original_command, None, output,
                time.monotonic() - started,
            )    

        test_command = [
            "exec", "--user", "10001:10001", container_id,
            "/tmp/venv/bin/python", "-m", "pytest", *pytest_args,
            "--junitxml=/workspace/pytest-results.xml", "-p", "no:cacheprovider",
        ]
        try:
            tested = _docker(test_command, timeout=max(1, int(timeout)))
            exit_code = tested.returncode
            output = (tested.stdout or "") + (tested.stderr or "")
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            output = _text(exc.stdout) + _text(exc.stderr) + "\n[TIMED OUT; container stopped]"
            try:
                _docker(["kill", container_id], timeout=8)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        report_note = _copy_report(container_id, junit_path)
        outcomes, junit_valid, collection_errors = _read_junit(junit_path)
        if report_note:
            output = (output + "\n" + report_note).strip()
        result = CommandResult(
            original_command,
            None if timed_out else exit_code,
            _trim(output),
            time.monotonic() - started,
            timed_out=timed_out,
            failed_tests=_failed_ids(output),
            outcomes=outcomes,
        )
        return result
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        output = f"Docker test execution failed: {exc}"
        return CommandResult(original_command, None, _trim(output), time.monotonic() - started)
    finally:
        # -v removes the anonymous /workspace volume as well as the container.
        try:
            _docker(["rm", "-f", "-v", container_id or container_name], timeout=15)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
