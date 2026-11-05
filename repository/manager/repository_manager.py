
"""
Repository Manager.

Coordinates repository indexing and persists repository state.
Repositories are owned by authenticated users.
"""

from __future__ import annotations

import json
import traceback
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from graph.builder.repo_builder import build_repo_graph
from storage.graph.json_store import save_graph
from storage.repository_profile.tier1_generator import generate_tier1_profile
from storage.repository_profile.tier2_backfill import backfill_tier2_profile
from storage.repository_profile.profile_store import save_profile
from storage.repository_profile.types import IndexStatus

from . import registry


class RepositoryLockedError(Exception):
    """Raised when an indexing run is already in progress."""


@dataclass
class RepositoryRecord:
    repository_id: str
    name: str
    local_path: str
    storage_dir: str
    owner_id: str = ""
    graph_path: str = ""
    profile_path: str = ""
    index_status: IndexStatus = IndexStatus.PENDING
    last_indexed_commit: str = ""
    graph_hash: str = ""
    error_message: str = ""
    source_type: str = "local"
    github_owner: str = ""
    github_repo: str = ""
    github_repository_id: int | None = None
    github_default_branch: str = ""
    github_remote_url: str = ""
    github_installation_record_id: int | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


def _record_path(storage_dir: Path) -> Path:
    return storage_dir / "repository_record.json"


def _lock_path(storage_dir: Path) -> Path:
    return storage_dir / ".indexing.lock"


def _write_lock(storage_dir: Path) -> None:
    lock_path = _lock_path(storage_dir)

    if lock_path.exists():
        raise RepositoryLockedError(
            f"An indexing run is already in progress for {storage_dir}. "
            "If this is a stale lock from a crashed process, remove it manually."
        )

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(datetime.utcnow().isoformat(), encoding="utf-8")


def _release_lock(storage_dir: Path) -> None:
    _lock_path(storage_dir).unlink(missing_ok=True)


@contextmanager
def _acquire_lock(storage_dir: Path):
    _write_lock(storage_dir)
    try:
        yield
    finally:
        _release_lock(storage_dir)


def _save_record(storage_dir: Path, record: RepositoryRecord) -> None:
    storage_dir.mkdir(parents=True, exist_ok=True)

    data = asdict(record)
    data["index_status"] = record.index_status.value
    data["created_at"] = record.created_at.isoformat()
    data["updated_at"] = record.updated_at.isoformat()

    _record_path(storage_dir).write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


def load_record(storage_dir: str | Path) -> RepositoryRecord | None:
    path = _record_path(Path(storage_dir))

    if not path.exists():
        return None

    data = json.loads(path.read_text(encoding="utf-8"))

    return RepositoryRecord(
        repository_id=data["repository_id"],
        name=data["name"],
        local_path=data["local_path"],
        storage_dir=data["storage_dir"],
        owner_id=data.get("owner_id", ""),
        graph_path=data.get("graph_path", ""),
        profile_path=data.get("profile_path", ""),
        index_status=IndexStatus(data["index_status"]),
        last_indexed_commit=data.get("last_indexed_commit", ""),
        graph_hash=data.get("graph_hash", ""),
        error_message=data.get("error_message", ""),
        source_type=data.get("source_type", "local"),
        github_owner=data.get("github_owner", ""),
        github_repo=data.get("github_repo", ""),
        github_repository_id=data.get("github_repository_id"),
        github_default_branch=data.get("github_default_branch", ""),
        github_remote_url=data.get("github_remote_url", ""),
        github_installation_record_id=data.get("github_installation_record_id"),
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
    )


def begin_indexing(
    repo_path: str | Path,
    repository_id: str,
    storage_dir: str | Path,
    owner_id: str = "",
    repository_name: str | None = None,
    source_type: str | None = None,
    github_owner: str | None = None,
    github_repo: str | None = None,
    github_repository_id: int | None = None,
    github_default_branch: str | None = None,
    github_remote_url: str | None = None,
    github_installation_record_id: int | None = None,
) -> RepositoryRecord:
    """
    Prepare an indexing run synchronously.

    The repository is registered under its owner before background
    indexing starts.
    """
    repo_path = Path(repo_path).resolve()
    storage_dir = Path(storage_dir).resolve()
    storage_dir.mkdir(parents=True, exist_ok=True)

    existing = load_record(storage_dir)

    if existing is not None and existing.owner_id != owner_id:
        raise PermissionError(
            "This repository belongs to another account or is an "
            "unowned legacy repository. Re-index it under your account."
        )

    _write_lock(storage_dir)

    try:
        record = existing or RepositoryRecord(
            repository_id=repository_id,
            name=repo_path.name,
            local_path=str(repo_path),
            storage_dir=str(storage_dir),
            owner_id=owner_id,
        )

        if repository_name:
            record.name = repository_name

        if source_type is not None:
            record.source_type = source_type
        if github_owner is not None:
            record.github_owner = github_owner
        if github_repo is not None:
            record.github_repo = github_repo
        if github_repository_id is not None:
            record.github_repository_id = github_repository_id
        if github_default_branch is not None:
            record.github_default_branch = github_default_branch
        if github_remote_url is not None:
            record.github_remote_url = github_remote_url
        if github_installation_record_id is not None:
            record.github_installation_record_id = github_installation_record_id

        record.index_status = IndexStatus.INDEXING
        record.error_message = ""
        record.updated_at = datetime.utcnow()

        _save_record(storage_dir, record)
        registry.register(repository_id, storage_dir, owner_id)

        return record

    except Exception:
        _release_lock(storage_dir)
        raise


def run_indexing(
    repo_path: str | Path,
    repository_id: str,
    storage_dir: str | Path,
) -> None:
    """
    Run the indexing pipeline.

    Intended for FastAPI BackgroundTasks after begin_indexing().
    """
    repo_path = Path(repo_path).resolve()
    storage_dir = Path(storage_dir).resolve()

    record = load_record(storage_dir)

    if record is None:
        raise RuntimeError(
            f"No repository record found in {storage_dir}. "
            "begin_indexing() must run first."
        )

    try:
        profile = generate_tier1_profile(repo_path, repository_id)
        repo_graph = build_repo_graph(repo_path)

        graph_path = storage_dir / "graph.json"
        commit = (
            profile.vcs.last_indexed_commit
            if profile.vcs
            else "unknown"
        )

        graph_metadata = save_graph(
            graph_path,
            repo_graph.nodes,
            repo_graph.edges,
            repository_id=repository_id,
            repository_commit=commit,
        )

        backfill_tier2_profile(
            profile,
            repo_graph.nodes,
            repo_graph.edges,
        )

        profile_path = storage_dir / "profile.json"
        save_profile(profile_path, profile)

        record.graph_path = str(graph_path)
        record.profile_path = str(profile_path)
        record.last_indexed_commit = commit
        record.graph_hash = graph_metadata.graph_hash
        record.index_status = profile.index_status
        record.error_message = ""
        record.updated_at = datetime.utcnow()

        _save_record(storage_dir, record)

    except Exception as exc:
        traceback.print_exc()
        print(f"ERROR: {exc}")

        record.index_status = IndexStatus.FAILED
        record.error_message = str(exc)
        record.updated_at = datetime.utcnow()

        _save_record(storage_dir, record)
        raise

    finally:
        _release_lock(storage_dir)


def index_repository(
    repo_path: str | Path,
    repository_id: str,
    storage_dir: str | Path,
    owner_id: str = "",
) -> RepositoryRecord:
    """Synchronous convenience wrapper for scripts and tests."""
    begin_indexing(
        repo_path,
        repository_id,
        storage_dir,
        owner_id,
    )

    run_indexing(repo_path, repository_id, storage_dir)

    result = load_record(storage_dir)

    if result is None:
        raise RuntimeError("Repository record disappeared after indexing.")

    return result
