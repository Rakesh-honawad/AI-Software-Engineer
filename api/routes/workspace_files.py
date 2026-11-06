
"""
api/routes/workspace_files.py

Backend for the live editor:
  GET  /repositories/{id}/files     list repository files
  GET  /repositories/{id}/file      read one source file
  POST /repositories/{id}/verify    verify edited files in a throwaway copy
  POST /repositories/{id}/apply     apply verified edits to the real repo
  POST /repositories/{id}/restore   restore the previous contents after Apply

All endpoints require Supabase authentication and enforce repository
ownership. All file paths are validated to stay inside the repository.
"""

from __future__ import annotations

import asyncio
import difflib
import os
import secrets
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
import zipfile

from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from api.dependencies.repository_deps import get_storage_dir_or_404
from auth.dependencies import AuthenticatedUser, get_current_user
from repository.manager.repository_manager import load_record
from sandbox.runner import DEFAULT_TEST_CMD, verify_editor_patch

router = APIRouter(prefix="/repositories", tags=["workspace-files"])

MAX_FILE_BYTES = 300_000
MAX_FILES = 5
BLOCKED_PARTS = {".git"}

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    "dist",
    "build",
    "coverage",
}

MAX_LISTED_FILES = 50000

RESTORE_TOKEN_TTL = 1800  # 30 minutes

# token -> repository/user identity, expiry, and exact file snapshots
_RESTORE_TOKENS: dict[str, dict] = {}


class FileEdit(BaseModel):
    path: str
    content: str


class VerifyRequest(BaseModel):
    files: list[FileEdit]
    test_path: str | None = None


class ApplyRequest(BaseModel):
    files: list[FileEdit]
    confirm: bool = False


class RestoreRequest(BaseModel):
    token: str
    confirm: bool = False


def _cleanup_restore_tokens() -> None:
    now = time.time()

    expired = [
        token
        for token, data in _RESTORE_TOKENS.items()
        if data["expires_at"] <= now
    ]

    for token in expired:
        _RESTORE_TOKENS.pop(token, None)


def _repo_root(repository_id: str, user_id: str) -> Path:
    """Resolve a repository only if it belongs to the authenticated user."""
    storage_dir = get_storage_dir_or_404(repository_id, user_id)
    record = load_record(storage_dir)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No record found for '{repository_id}'.",
        )

    if record.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Repository not found.")

    root = Path(record.local_path).resolve()

    if not root.exists() or not root.is_dir():
        raise HTTPException(
            status_code=404,
            detail="Repository directory not found.",
        )

    return root


EXPORT_IGNORED_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules",
    ".next", ".turbo", ".vercel", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".cache", "dist", "build", "coverage", "target",
}

def _ignore_export_path(relative: Path) -> bool:
    parts = [part.casefold() for part in relative.parts]
    name = parts[-1] if parts else ""

    if any(part in EXPORT_IGNORED_DIRS for part in parts[:-1]):
        return True

    if name == ".env":
        return True

    if name.startswith(".env.") and name not in {
        ".env.example", ".env.sample", ".env.template"
    }:
        return True

    if name.endswith((".pem", ".key", ".p12", ".pfx")):
        return True

    if name in {"id_rsa", "id_ed25519", "id_ecdsa"}:
        return True

    return False


@router.get("/{repository_id}/export.zip")
async def export_repository_zip(
    repository_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    root = _repo_root(repository_id, user.id)

    fd, archive_name = tempfile.mkstemp(
        prefix="knowledge-engine-export-",
        suffix=".zip",
    )
    os.close(fd)
    archive_path = Path(archive_name)

    try:
        with zipfile.ZipFile(
            archive_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for file_path in root.rglob("*"):
                if file_path.is_symlink() or not file_path.is_file():
                    continue

                relative = file_path.relative_to(root)

                if _ignore_export_path(relative):
                    continue

                archive.write(
                    file_path,
                    arcname=relative.as_posix(),
                )

        return FileResponse(
            path=archive_path,
            media_type="application/zip",
            filename=f"{repository_id}-updated.zip",
            background=BackgroundTask(
                lambda: archive_path.unlink(missing_ok=True)
            ),
        )

    except Exception:
        archive_path.unlink(missing_ok=True)
        raise

def _safe_path(root: Path, rel: str) -> Path:
    rel_n = rel.replace("\\", "/").strip()

    if not rel_n or rel_n.startswith("/") or ":" in rel_n:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid path: {rel!r}",
        )

    parts = Path(rel_n).parts

    if ".." in parts or BLOCKED_PARTS.intersection(
        part.lower() for part in parts
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid path: {rel!r}",
        )

    target = (root / rel_n).resolve()

    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Path escapes the repository: {rel!r}",
        ) from exc

    return target


def _read(path: Path) -> str:
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {path.name}",
        )

    if path.stat().st_size > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="File is too large to edit here.",
        )

    data = path.read_bytes()

    if b"\x00" in data:
        raise HTTPException(
            status_code=415,
            detail="Binary files cannot be edited.",
        )

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=415,
            detail="Only UTF-8 text files can be edited.",
        ) from exc


def _match_format(original: str, edited: str) -> str:
    """Restore the file's original BOM and line-ending convention."""
    if original.startswith("\ufeff") and not edited.startswith("\ufeff"):
        edited = "\ufeff" + edited

    if "\r\n" in original:
        edited = edited.replace("\r\n", "\n").replace("\n", "\r\n")

    return edited


def _diff(rel: str, original: str, edited: str) -> str:
    out = []

    for line in difflib.unified_diff(
        original.splitlines(keepends=True),
        edited.splitlines(keepends=True),
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
    ):
        out.append(
            line
            if line.endswith("\n")
            else line + "\n\\ No newline at end of file\n"
        )

    return "".join(out)


def _prepare(
    root: Path,
    files: list[FileEdit],
) -> tuple[list[tuple[str, Path, str, str]], str]:
    if not files or len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Send between 1 and {MAX_FILES} files.",
        )

    changes: list[tuple[str, Path, str, str]] = []
    diffs: list[str] = []
    seen: set[str] = set()

    for file_edit in files:
        target = _safe_path(root, file_edit.path)
        rel = target.relative_to(root).as_posix()

        if rel in seen:
            raise HTTPException(
                status_code=400,
                detail=f"Duplicate file path: {rel}",
            )

        seen.add(rel)

        original = _read(target)
        edited = _match_format(original, file_edit.content)

        if len(edited.encode("utf-8")) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Edited file is too large.",
            )

        if edited == original:
            continue

        changes.append((rel, target, original, edited))
        diffs.append(_diff(rel, original, edited))

    return changes, "".join(diffs)


def _test_cmd(test_path: str | None) -> list[str]:
    if not test_path:
        return DEFAULT_TEST_CMD

    normalized = test_path.replace("\\", "/")

    if (
        normalized.startswith(("-", "/"))
        or ":" in normalized
        or ".." in normalized.split("/")
    ):
        raise HTTPException(
            status_code=400,
            detail="test_path must be a relative path inside the repository.",
        )

    return [*DEFAULT_TEST_CMD, test_path]


def _command_summary(run) -> dict | None:
    if run is None:
        return None

    outcomes = run.outcomes or {}

    return {
        "exit_code": run.exit_code,
        "duration_s": round(run.duration_s, 2),
        "timed_out": run.timed_out,
        "junit_valid": run.junit_valid,
        "collection_errors": run.collection_errors,
        "total": len(outcomes),
        "passed": sum(value == "passed" for value in outcomes.values()),
        "failed": sum(value == "failed" for value in outcomes.values()),
        "errors": sum(value == "error" for value in outcomes.values()),
        "skipped": sum(value == "skipped" for value in outcomes.values()),
        "failed_tests": (run.failed_tests or [])[:50],
        "output": (run.output or "")[-4000:],
    }


def _write_temporary_file(target: Path, content: str) -> Path:
    """Create and fsync a temporary file beside its destination."""
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as temp:
        temp.write(content.encode("utf-8"))
        temp.flush()
        os.fsync(temp.fileno())
        return Path(temp.name)


def _replace_files(
    replacements: list[tuple[Path, str]],
) -> None:
    """
    Replace multiple files using temporary files.

    Each item contains (destination_path, replacement_content).
    If a replacement fails, already-replaced files are rolled back
    on a best-effort basis.
    """
    temporary_files: list[tuple[Path, Path, str]] = []

    try:
        for target, content in replacements:
            original = _read(target)
            temp_path = _write_temporary_file(target, content)
            temporary_files.append((temp_path, target, original))

        replaced: list[tuple[Path, str]] = []

        try:
            for temp_path, target, original in temporary_files:
                os.replace(temp_path, target)
                replaced.append((target, original))

        except OSError:
            for target, original in reversed(replaced):
                target.write_bytes(original.encode("utf-8"))
            raise

    finally:
        for temp_path, _, _ in temporary_files:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


@router.get("/{repository_id}/files")
async def list_repository_files(
    repository_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """List files from the authenticated user's actual repository."""
    root = _repo_root(repository_id, user.id)

    files: list[str] = []

    for current_dir, dirnames, filenames in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_dir)

        dirnames[:] = sorted(
            dirname
            for dirname in dirnames
            if dirname.lower() not in IGNORED_DIRS
            and not (current / dirname).is_symlink()
        )

        for filename in filenames:
            target = current / filename

            if target.is_symlink() or not target.is_file():
                continue

            relative_path = target.relative_to(root).as_posix()

            if len(files) >= MAX_LISTED_FILES:
                return {
                    "files": files,
                    "count": len(files),
                    "truncated": True,
                }

            files.append(relative_path)

    files.sort(key=str.lower)

    return {
        "files": files,
        "count": len(files),
        "truncated": False,
    }


@router.get("/{repository_id}/file")
async def get_file(
    repository_id: str,
    path: str = Query(...),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    root = _repo_root(repository_id, user.id)
    target = _safe_path(root, path)

    return {
        "path": target.relative_to(root).as_posix(),
        "content": _read(target),
    }


@router.post("/{repository_id}/verify")
async def verify_edit(
    repository_id: str,
    request: VerifyRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    root = _repo_root(repository_id, user.id)

    changes, diff = _prepare(root, request.files)

    if not changes:
        raise HTTPException(
            status_code=400,
            detail="No changes to verify.",
        )

    # An explicitly selected test file/directory is Quick Verify.
    # Without one, run the full repository test suite.
    test_cmd = DEFAULT_TEST_CMD

    if request.test_path:
        test_target = _safe_path(root, request.test_path)

        if not test_target.exists() or not (
            test_target.is_file() or test_target.is_dir()
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Selected test path does not exist or is not "
                    "a file/directory."
                ),
            )

        test_cmd = _test_cmd(request.test_path)

    # Import locally to avoid a circular import.
    from api.routes.verification_runner import (
        create_verification_job,
        wait_for_verification_job,
    )

    # Create and dispatch a remote verification job.
    job_id = await asyncio.to_thread(
        create_verification_job,
        str(user.id),
        repository_id,
        "verify_editor_patch",
        {
            "diff": diff,
            "test_cmd": test_cmd,
            "scope": "quick" if request.test_path else "full",
        },
    )

    # Wait for GitHub Actions to finish verification.
    verification = await wait_for_verification_job(
        job_id,
        str(user.id),
        timeout=300,
    )

    if not isinstance(verification, dict):
        raise HTTPException(
            status_code=502,
            detail="Invalid verification result from remote runner.",
        )

    return {
        "diff": diff,
        "patch_applied": verification.get("patch_applied", False),
        "apply_log": verification.get("apply_log", ""),
        "scope": "quick" if request.test_path else "full",
        "complete": verification.get("complete", False),
        "safe_to_apply": verification.get("safe_to_apply", False),
        "verdict": verification.get("verdict", "verification_unavailable"),
        "tests_pass_after": verification.get("tests_pass_after", False),
        "baseline": verification.get("baseline"),
        "patched": verification.get("patched"),
        "baseline_failures": verification.get("baseline_failures", []),
        "still_failing": verification.get("still_failing", []),
        "new_failures": verification.get("new_failures", []),
        "errors": verification.get("errors", []),
    }

@router.post("/{repository_id}/apply")
async def apply_edit(
    repository_id: str,
    request: ApplyRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    if not request.confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to modify the repository.",
        )

    root = _repo_root(repository_id, user.id)
    changes, diff = _prepare(root, request.files)

    if not changes:
        raise HTTPException(
            status_code=400,
            detail="No changes to apply.",
        )

    # Import locally to avoid a circular import.
    from api.routes.verification_runner import (
        create_verification_job,
        wait_for_verification_job,
    )

    # Apply always requires a fresh FULL remote verification.
    job_id = await asyncio.to_thread(
        create_verification_job,
        str(user.id),
        repository_id,
        "verify_editor_patch",
        {
            "diff": diff,
            "test_cmd": DEFAULT_TEST_CMD,
            "scope": "full",
        },
    )

    verification_result = await wait_for_verification_job(
        job_id,
        str(user.id),
        timeout=300,
    )
    if not isinstance(verification_result, dict):
        raise HTTPException(
            status_code=502,
            detail="Invalid remote verification result. Apply blocked.",
        )

    verification = {
        "complete": verification_result.get("complete", False),
        "safe_to_apply": verification_result.get("safe_to_apply", False),
        "tests_pass_after": verification_result.get(
            "tests_pass_after", False
        ),
        "verdict": verification_result.get(
            "verdict", "verification_unavailable"
        ),
        "baseline": verification_result.get("baseline"),
        "patched": verification_result.get("patched"),
        "baseline_failures": verification_result.get(
            "baseline_failures", []
        ),
        "still_failing": verification_result.get("still_failing", []),
        "new_failures": verification_result.get("new_failures", []),
        "errors": verification_result.get("errors", []),
    }

    # Fail closed: only a complete, safe verification can authorize Apply.
    if (
        not verification["complete"]
        or not verification["safe_to_apply"]
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Apply blocked: full remote verification was "
                    "incomplete or found regressions."
                ),
                "verification": verification,
            },
        )

    # Stale-file protection: reject if any real file changed
    # while verification was running.
    for rel, target, original, edited in changes:
        try:
            current = target.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Could not safely recheck repository file: "
                    f"{rel}"
                ),
            ) from exc

        if current != original:
            raise HTTPException(
                status_code=409,
                detail=(
                    "File changed during verification. "
                    f"Apply cancelled: {rel}"
                ),
            )

    try:
        _replace_files([
            (target, edited)
            for _, target, _, edited in changes
        ])
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not safely apply edits: {exc}",
        ) from exc

    _cleanup_restore_tokens()

    restore_token = secrets.token_urlsafe(32)

    _RESTORE_TOKENS[restore_token] = {
        "repository_id": repository_id,
        "user_id": user.id,
        "expires_at": time.time() + RESTORE_TOKEN_TTL,
        "files": [
            {
                "path": rel,
                "original": original,
                "applied": edited,
            }
            for rel, _, original, edited in changes
        ],
    }

    return {
        "applied": [rel for rel, _, _, _ in changes],
        "diff": diff,
        "verification": verification,
        "restore_token": restore_token,
        "note": (
            "Full remote verification completed with no new regressions. "
            "Re-index the repository so the graph reflects the changes."
        ),
    }


@router.post("/{repository_id}/restore")
async def restore_edit(
    repository_id: str,
    request: RestoreRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    if not request.confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to restore the repository files.",
        )

    root = _repo_root(repository_id, user.id)

    _cleanup_restore_tokens()

    token_data = _RESTORE_TOKENS.get(request.token)

    if token_data is None:
        raise HTTPException(
            status_code=404,
            detail="Restore token is invalid, expired, or already used.",
        )

    if (
        token_data["repository_id"] != repository_id
        or token_data["user_id"] != user.id
    ):
        raise HTTPException(
            status_code=404,
            detail="Restore token not found.",
        )

    snapshots = token_data["files"]

    if not snapshots:
        raise HTTPException(
            status_code=400,
            detail="Restore token contains no file snapshots.",
        )

    # Check every file before changing any of them. Never overwrite
    # edits made after the original Apply operation.
    replacements: list[tuple[Path, str]] = []

    for snapshot in snapshots:
        target = _safe_path(root, snapshot["path"])

        try:
            current = _read(target)
        except HTTPException as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Could not safely verify the current file before "
                    f"restore: {snapshot['path']}"
                ),
            ) from exc

        if current != snapshot["applied"]:
            raise HTTPException(
                status_code=409,
                detail=(
                    "File changed after Apply. Restore cancelled to "
                    f"protect newer edits: {snapshot['path']}"
                ),
            )

        replacements.append((target, snapshot["original"]))

    try:
        _replace_files(replacements)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not safely restore repository files: {exc}",
        ) from exc

    # Consume the token only after successful restoration.
    _RESTORE_TOKENS.pop(request.token, None)

    return {
        "restored": [snapshot["path"] for snapshot in snapshots],
        "note": (
            "Original file contents restored. "
            "Re-index the repository so the graph reflects the restored files."
        ),
    }
