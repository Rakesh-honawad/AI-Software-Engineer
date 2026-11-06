import subprocess
from pathlib import Path

import sandbox.docker_runner as docker_runner
import sandbox.runner as runner


def completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=args,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_pytest_args_accepts_supported_commands():
    assert docker_runner._pytest_args(["pytest", "-q"]) == ["-q"]
    assert docker_runner._pytest_args(["python", "-m", "pytest", "-q"]) == ["-q"]
    assert docker_runner._pytest_args(["py", "-3", "-m", "pytest", "-q"]) == ["-q"]


def test_pytest_args_rejects_arbitrary_commands():
    for command in (
        [],
        ["python", "script.py"],
        ["cmd", "/c", "whoami"],
        ["powershell", "-Command", "whoami"],
    ):
        try:
            docker_runner._pytest_args(command)
            assert False, f"Command should have been rejected: {command}"
        except ValueError:
            pass


def test_pytest_args_removes_caller_junit_paths():
    result = docker_runner._pytest_args([
        "pytest",
        "-q",
        "--junitxml",
        "C:/outside/report.xml",
        "--junit-xml=C:/outside/other.xml",
    ])

    assert result == ["-q"]


def test_docker_environment_excludes_application_secrets(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return completed(args, stdout="Docker OK")

    monkeypatch.setattr(docker_runner.subprocess, "run", fake_run)
    monkeypatch.setenv("PATH", "test-path")
    monkeypatch.setenv("GROQ_API_KEY", "secret-value")
    monkeypatch.setenv("GOOGLE_API_KEY", "another-secret")

    docker_runner._docker(["info"])

    assert captured["shell"] is False
    assert captured["env"]["PATH"] == "test-path"
    assert "GROQ_API_KEY" not in captured["env"]
    assert "GOOGLE_API_KEY" not in captured["env"]


def test_successful_execution_uses_isolated_container(monkeypatch, tmp_path):
    calls = []

    def fake_docker(args, timeout=docker_runner.DOCKER_CHECK_TIMEOUT):
        args = list(args)
        calls.append(args)

        if args[:2] == ["info", "--format"]:
            return completed(args, stdout="27.0")

        if args[:3] == ["image", "inspect", "--format"]:
            return completed(args, stdout="sha256:test")

        if args[:2] == ["inspect", "--format"]:
            return completed(args, stdout="{}")

        if args[0] == "create":
            return completed(args, stdout="container123")

        if args[0] in {"start", "cp", "kill"}:
            return completed(args)

        if args[:2] == ["exec", "--user"] and "pytest" in args:
            return completed(args, stdout="6 passed in 0.02s")

        return completed(args)

    monkeypatch.setattr(docker_runner, "_docker", fake_docker)
    monkeypatch.setattr(
        runner,
        "_read_junit",
        lambda path: ({"test_example": "passed"}, True, False),
    )

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "test_example.py").write_text(
        "def test_example(): assert True\n",
        encoding="utf-8",
    )

    result = docker_runner.run_pytest_in_docker(
        ["pytest", "-q"],
        workspace,
        30,
        tmp_path / "report.xml",
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.outcomes == {"test_example": "passed"}
    assert result.junit_valid is True
    assert result.collection_errors is False

    create_call = next(call for call in calls if call[0] == "create")

    assert "--network" in create_call
    assert create_call[create_call.index("--network") + 1] == "bridge"

    disconnect_call = [
        "network", "disconnect", "bridge", "container123"
    ]
    inspect_call = [
        "inspect", "--format",
        "{{json .NetworkSettings.Networks}}", "container123"
    ]

    assert disconnect_call in calls
    assert inspect_call in calls
    assert "--read-only" in create_call
    assert "--memory" in create_call
    assert "--pids-limit" in create_call
    assert "--cap-drop" in create_call
    assert "ALL" in create_call
    assert "--security-opt" in create_call
    assert "no-new-privileges:true" in create_call

    pytest_call = next(
        call for call in calls
        if call[0] == "exec" and "pytest" in call
    )
    assert pytest_call[pytest_call.index("--user") + 1] == "10001:10001"

    assert any(
        call[:3] == ["rm", "-f", "-v"]
        for call in calls
    )


def test_invalid_command_never_calls_docker(monkeypatch, tmp_path):
    def unexpected_docker_call(*args, **kwargs):
        raise AssertionError("Docker must not be called for an invalid command")

    monkeypatch.setattr(docker_runner, "_docker", unexpected_docker_call)

    result = docker_runner.run_pytest_in_docker(
        ["python", "malicious_script.py"],
        tmp_path,
        30,
        tmp_path / "report.xml",
    )

    assert result.exit_code is None
    assert "-m pytest" in result.output.lower()


def test_missing_workspace_never_calls_docker(monkeypatch, tmp_path):
    def unexpected_docker_call(*args, **kwargs):
        raise AssertionError("Docker must not be called for a missing workspace")

    monkeypatch.setattr(docker_runner, "_docker", unexpected_docker_call)

    result = docker_runner.run_pytest_in_docker(
        ["pytest", "-q"],
        tmp_path / "missing",
        30,
        tmp_path / "report.xml",
    )

    assert result.exit_code is None
    assert "workspace not found" in result.output.lower()


def _prepare_docker_failure_test(monkeypatch, tmp_path, failure_stage):
    calls = []

    def fake_docker(args, timeout=docker_runner.DOCKER_CHECK_TIMEOUT):
        args = list(args)
        calls.append(args)

        if args[:2] == ["info", "--format"]:
            return completed(args, stdout="28.5.1")

        if args[:3] == ["image", "inspect", "--format"]:
            return completed(args, stdout="sha256:test")

        if args[:2] == ["inspect", "--format"]:
            return completed(args, stdout="{}")

        if args[0] == "create":
            if failure_stage == "create":
                return completed(args, returncode=1, stderr="simulated create failure")
            return completed(args, stdout="container123")

        if args[0] == "start":
            if failure_stage == "start":
                return completed(args, returncode=1, stderr="simulated start failure")
            return completed(args)

        if args[0] == "cp":
            if failure_stage == "copy":
                return completed(args, returncode=1, stderr="simulated copy failure")
            return completed(args)

        if args[:3] == ["exec", "--user", "0:0"]:
            if failure_stage == "permissions":
                return completed(args, returncode=1, stderr="simulated permission failure")
            if failure_stage == "execution_exception":
                raise OSError("simulated Docker execution error")
            return completed(args)

        if args[0] == "exec" and "pytest" in args:
            return completed(args, stdout="1 passed")

        return completed(args)

    monkeypatch.setattr(docker_runner, "_docker", fake_docker)

    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "test_example.py").write_text(
        "def test_example(): assert True\n",
        encoding="utf-8",
    )

    result = docker_runner.run_pytest_in_docker(
        ["pytest", "-q"],
        workspace,
        30,
        tmp_path / "report.xml",
    )

    return result, calls


def test_container_creation_failure_still_attempts_cleanup(monkeypatch, tmp_path):
    result, calls = _prepare_docker_failure_test(monkeypatch, tmp_path, "create")

    assert result.exit_code is None
    assert "could not create isolated test container" in result.output.lower()
    assert "simulated create failure" in result.output.lower()

    create_call = next(call for call in calls if call[0] == "create")
    expected_name = create_call[create_call.index("--name") + 1]

    cleanup_call = next(call for call in calls if call[:3] == ["rm", "-f", "-v"])
    assert cleanup_call[-1] == expected_name
    assert not any(call[0] == "start" for call in calls)


def test_container_start_failure_still_attempts_cleanup(monkeypatch, tmp_path):
    result, calls = _prepare_docker_failure_test(monkeypatch, tmp_path, "start")

    assert result.exit_code is None
    assert "could not start isolated test container" in result.output.lower()
    assert "simulated start failure" in result.output.lower()
    assert any(call[:3] == ["rm", "-f", "-v"] and call[-1] == "container123" for call in calls)
    assert not any(call[0] == "cp" for call in calls)


def test_repository_copy_failure_still_attempts_cleanup(monkeypatch, tmp_path):
    result, calls = _prepare_docker_failure_test(monkeypatch, tmp_path, "copy")

    assert result.exit_code is None
    assert "could not copy repository" in result.output.lower()
    assert "simulated copy failure" in result.output.lower()
    assert any(call[:3] == ["rm", "-f", "-v"] and call[-1] == "container123" for call in calls)
    assert not any(call[0] == "exec" for call in calls)


def test_workspace_permission_failure_still_attempts_cleanup(monkeypatch, tmp_path):
    result, calls = _prepare_docker_failure_test(monkeypatch, tmp_path, "permissions")

    assert result.exit_code is None
    assert "could not prepare isolated workspace" in result.output.lower()
    assert "simulated permission failure" in result.output.lower()
    assert any(call[:3] == ["rm", "-f", "-v"] and call[-1] == "container123" for call in calls)
    assert not any(call[0] == "exec" and "pytest" in call for call in calls)


def test_docker_execution_exception_still_attempts_cleanup(monkeypatch, tmp_path):
    result, calls = _prepare_docker_failure_test(
        monkeypatch, tmp_path, "execution_exception"
    )

    assert result.exit_code is None
    assert "docker test execution failed" in result.output.lower()
    assert "simulated docker execution error" in result.output.lower()
    assert any(call[:3] == ["rm", "-f", "-v"] and call[-1] == "container123" for call in calls)
    assert not any(call[0] == "exec" and "pytest" in call for call in calls)
