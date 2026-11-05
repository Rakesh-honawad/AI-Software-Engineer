"""Isolated GitHub branch workspaces and safe branch publishing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import exc

from api.dependencies.repository_deps import get_storage_dir_or_404
from auth.dependencies import AuthenticatedUser, get_current_user
from github_integration import store
from github_integration.client import (
    create_branch_ref,
    create_installation_token,
    get_branch_commit_sha,
    GitHubAPIError
)
from github_integration.routes import _verified_installation
from ingestion.connectors.github import GitHubConnector, GitCloneError
from repository.manager.repository_manager import load_record
from api.routes.workspace_files import (
    FileEdit,
    MAX_FILE_BYTES,
    MAX_FILES,
    IGNORED_DIRS,
    _prepare,
    _read,
    _safe_path,
    _replace_files,
)

router = APIRouter(prefix="/repositories", tags=["github-branches"])

BRANCH_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")


class CreateBranchRequest(BaseModel):
    branch_name: str | None = Field(default=None, max_length=120)


class BranchEditRequest(BaseModel):
    branch: str = Field(min_length=1, max_length=120)
    files: list[FileEdit]
    confirm: bool = False


class BranchPushRequest(BaseModel):
    branch: str = Field(min_length=1, max_length=120)
    confirm: bool = False


def _repository_context(repository_id: str, user_id: str):
    storage_dir = Path(get_storage_dir_or_404(repository_id, user_id)).resolve()
    record = load_record(storage_dir)

    if record is None or record.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Repository not found.")

    if (
        record.source_type != "github_app"
        or not record.github_owner
        or not record.github_repo
        or not record.github_repository_id
        or not record.github_installation_record_id
        or not record.github_default_branch
    ):
        raise HTTPException(
            status_code=409,
            detail="Branch publishing is available only for GitHub App-connected repositories.",
        )

    try:
        owned = store.get_installation(
            user_id,
            record.github_installation_record_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not verify GitHub installation ownership.",
        ) from exc

    if owned is None:
        raise HTTPException(
            status_code=403,
            detail="The GitHub installation is no longer connected to this account.",
        )

    _verified_installation(record.github_installation_record_id)
    return storage_dir, record


def _validate_branch_name(branch: str) -> str:
    if (
        not BRANCH_NAME_PATTERN.fullmatch(branch)
        or ".." in branch
        or "//" in branch
        or branch.endswith(("/", "."))
        or branch.endswith(".lock")
        or "@{" in branch
        or any(part.startswith(".") for part in branch.split("/"))
    ):
        raise HTTPException(status_code=400, detail="Invalid Git branch name.")

    check = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if check.returncode != 0:
        raise HTTPException(status_code=400, detail="Invalid Git branch name.")

    return branch


def _branch_directory(storage_dir: Path, branch: str) -> Path:
    identity = hashlib.sha256(branch.encode("utf-8")).hexdigest()[:24]
    return storage_dir / "_branches" / identity


def _branch_context(repository_id: str, user_id: str, branch: str):
    branch = _validate_branch_name(branch)
    storage_dir, record = _repository_context(repository_id, user_id)
    branch_dir = _branch_directory(storage_dir, branch)
    metadata_path = branch_dir.parent / (branch_dir.name + ".json")

    if not metadata_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="This branch workspace was not created in this repository.",
        )

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail="Could not read branch workspace metadata.",
        ) from exc

    if (
        metadata.get("owner_id") != user_id
        or metadata.get("repository_id") != repository_id
        or metadata.get("branch") != branch
        or not (branch_dir / ".git").is_dir()
    ):
        raise HTTPException(status_code=404, detail="Branch workspace not found.")

    return storage_dir, record, branch_dir, metadata, metadata_path


def _write_metadata(path: Path, metadata: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    os.replace(temporary, path)


@router.post("/{repository_id}/github/branches", status_code=201)
def create_github_branch(
    repository_id: str,
    request: CreateBranchRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Create an isolated workspace and publish its branch reference."""
    storage_dir, record = _repository_context(repository_id, user.id)

    branch = request.branch_name or (
        "ai-fix/"
        + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        + "-"
        + secrets.token_hex(3)
    )
    branch = _validate_branch_name(branch)
    branch_dir = _branch_directory(storage_dir, branch)

    if branch_dir.exists():
        raise HTTPException(
            status_code=409,
            detail="A local workspace for this branch already exists.",
        )

    installation_id = record.github_installation_record_id
    repository_id_number = record.github_repository_id

    try:
        token = create_installation_token(
            installation_id,
            repository_ids=[repository_id_number],
        )
    except Exception as exc:
        if isinstance(exc, GitHubAPIError):
            raise HTTPException(
                status_code=502,
                detail=str(exc),
        ) from exc

        raise HTTPException(
            status_code=502,
            detail=f"Could not create the GitHub branch workspace: {type(exc).__name__}",
        ) from exc

    connector = GitHubConnector(
        owner=record.github_owner,
        repo=record.github_repo,
        token=token,
    )

    try:
        # Clone the latest default branch into an isolated workspace.
        connector.fetch(
            branch_dir,
            branch=record.github_default_branch,
        )

        # Use the exact commit actually checked out by the clone.
        base_sha = connector._get_head_commit(branch_dir)

        if not base_sha or base_sha == "unknown":
            raise RuntimeError("Could not determine the cloned base commit.")

        # Create the local branch before publishing its remote reference.
        connector._run_git(
            ["switch", "-c", branch],
            cwd=branch_dir,
        )

        # Publish the branch at the exact commit used by this workspace.
        create_branch_ref(
            record.github_owner,
            record.github_repo,
            branch,
            base_sha,
            token,
        )

    except Exception as exc:
        # The remote reference has not been deliberately deleted here.
        # Clean up only the newly created local workspace.
        shutil.rmtree(branch_dir, ignore_errors=True)

        status = getattr(exc, "status_code", None)

        if status == 422:
            raise HTTPException(
                status_code=409,
                detail="That branch already exists on GitHub. Choose another branch name.",
            ) from exc

        if isinstance(exc, GitCloneError):
            raise HTTPException(
                status_code=502,
                detail="Could not clone or prepare the isolated branch workspace.",
            ) from exc

        raise HTTPException(
            status_code=502,
            detail="Could not create the GitHub branch workspace. Check GitHub and retry.",
        ) from exc

    finally:
        token = None

    metadata = {
        "repository_id": repository_id,
        "owner_id": user.id,
        "branch": branch,
        "base_branch": record.github_default_branch,
        "base_sha": base_sha,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    metadata_path = branch_dir.parent / (branch_dir.name + ".json")
    _write_metadata(metadata_path, metadata)

    store.audit_event(
        user.id,
        "github_branch_created",
        installation_id=installation_id,
        details=f"repository_id={repository_id};branch={branch}",
    )

    return {
        "branch": branch,
        "base_branch": record.github_default_branch,
        "base_sha": base_sha,
        "workspace_ready": True,
    }

@router.get("/{repository_id}/github/branch/files")
def list_branch_files(
    repository_id: str,
    branch: str = Query(...),
    user: AuthenticatedUser = Depends(get_current_user),
):
    _, _, root, _, _ = _branch_context(repository_id, user.id, branch)
    files = []

    for current_dir, dirnames, filenames in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_dir)
        dirnames[:] = sorted(
            name for name in dirnames
            if name.lower() not in IGNORED_DIRS
            and not (current / name).is_symlink()
        )

        for filename in filenames:
            target = current / filename
            if target.is_symlink() or not target.is_file():
                continue
            if target.name == "branch_record.json":
                continue
            files.append(target.relative_to(root).as_posix())

    files.sort(key=str.lower)
    return {"files": files, "count": len(files), "truncated": False}


@router.get("/{repository_id}/github/branch/file")
def get_branch_file(
    repository_id: str,
    branch: str = Query(...),
    path: str = Query(...),
    user: AuthenticatedUser = Depends(get_current_user),
):
    _, _, root, _, _ = _branch_context(repository_id, user.id, branch)
    target = _safe_path(root, path)
    return {
        "path": target.relative_to(root).as_posix(),
        "content": _read(target),
    }


@router.post("/{repository_id}/github/branch/apply")
def apply_branch_edits(
    repository_id: str,
    request: BranchEditRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    if not request.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to save branch edits.")

    _, _, root, _, _ = _branch_context(repository_id, user.id, request.branch)

    changes, diff = _prepare(root, request.files)
    if not changes:
        raise HTTPException(status_code=400, detail="No changes to save.")

    # Branch edits are isolated and do not require a local full-test runner.
    # GitHub Actions and branch protection remain responsible for CI policy.
    for rel, target, original, _ in changes:
        try:
            current = target.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Could not recheck branch file: {rel}",
            ) from exc
        if current != original:
            raise HTTPException(
                status_code=409,
                detail=f"Branch file changed while saving: {rel}",
            )

    try:
        _replace_files([(target, edited) for _, target, _, edited in changes])
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Could not safely save branch edits.",
        ) from exc

    return {
        "saved": [rel for rel, _, _, _ in changes],
        "diff": diff,
        "verification": "not_run",
        "note": "Saved to the isolated branch workspace. Local tests are optional; GitHub Actions runs after push.",
    }


@router.post("/{repository_id}/github/branch/push")
def push_github_branch(
    repository_id: str,
    request: BranchPushRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    if not request.confirm:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required before pushing.")

    _, record, branch_dir, metadata, metadata_path = _branch_context(
        repository_id,
        user.id,
        request.branch,
    )

    if request.branch == record.github_default_branch:
        raise HTTPException(
            status_code=403,
            detail="Pushing directly to the repository default branch is not allowed.",
        )
    
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=branch_dir,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if status.returncode != 0:
        raise HTTPException(status_code=500, detail="Could not inspect branch changes.")

    status_lines = status.stdout.splitlines()

    # Reject untracked and newly added files.
    new_files = [
        line for line in status_lines
        if line.startswith("??") or "A" in line[:2]
    ]

    if new_files:
        raise HTTPException(
            status_code=400,
            detail=(
                "New files are not supported by this branch push workflow yet. "
                "Only modifications and deletions of tracked files can be pushed."
            ),
        )

    head_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=branch_dir,
        capture_output=True,
        text=True,
        timeout=10,
    )

    if head_result.returncode != 0:
        raise HTTPException(status_code=500, detail="Could not inspect the local branch commit.")

    current_sha = head_result.stdout.strip()
    known_sha = metadata.get("last_pushed_sha") or metadata.get("base_sha")

    if not status_lines and current_sha == known_sha:
        raise HTTPException(status_code=400, detail="There are no changes to push.")

    installation_id = record.github_installation_record_id

    try:
        token = create_installation_token(
            installation_id,
            repository_ids=[record.github_repository_id],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not create a repository-scoped GitHub token.",
        ) from exc

    connector = GitHubConnector(
        owner=record.github_owner,
        repo=record.github_repo,
        token=token,
    )

    try:
        # Stage only the isolated branch workspace. No default-branch push
        # and no force-push are ever performed.
        if status_lines:
            connector._run_git(
                ["add", "-u", "--", "."],
                cwd=branch_dir,
            )

            staged = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=branch_dir,
                capture_output=True,
                text=True,
                timeout=20,
            )

            if staged.returncode not in (0, 1):
                raise GitCloneError("Could not inspect staged branch changes.")

            if staged.returncode == 1:
                connector._run_git(
                    [
                        "-c", "user.name=Knowledge Engine",
                        "-c", "user.email=knowledge-engine@users.noreply.github.com",
                        "commit", "-m", "Apply Knowledge Engine workspace edits",
                    ],
                    cwd=branch_dir,
                )
        connector._run_git(
            connector._auth_args()
            + ["push", "origin", f"HEAD:refs/heads/{request.branch}"],
            cwd=branch_dir,
            timeout=300,
        )
        pushed_sha = connector._get_head_commit(branch_dir)
    except GitCloneError as exc:
        message = str(exc)
        if "non-fast-forward" in message.lower() or "[rejected]" in message.lower():
            raise HTTPException(
                status_code=409,
                detail="Push rejected because the remote branch changed or GitHub branch protection blocked the update. No force-push was attempted.",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail="GitHub rejected the branch push. Check repository permissions and branch protection.",
        ) from exc
    finally:
        token = None

    metadata["last_pushed_sha"] = pushed_sha
    metadata["last_pushed_at"] = datetime.now(timezone.utc).isoformat()
    _write_metadata(metadata_path, metadata)

    store.audit_event(
        user.id,
        "github_branch_pushed",
        installation_id=installation_id,
        details=f"repository_id={repository_id};branch={request.branch};sha={pushed_sha}",
    )

    return {
        "pushed": True,
        "branch": request.branch,
        "commit_sha": pushed_sha,
        "branch_url": f"https://github.com/{record.github_owner}/{record.github_repo}/tree/{request.branch}",
        "note": "Branch pushed. GitHub Actions and repository branch protection determine whether checks pass and whether a pull request can be merged.",
    }



