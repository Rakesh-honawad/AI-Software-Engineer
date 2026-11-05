"""
API schemas for repository endpoints.

Deliberately separate from storage/repository_profile/types.py's
RepositoryProfile dataclass — this was an explicit earlier decision:
internal storage shape and external API contract change for different
reasons and on different schedules. A field can be renamed internally
without breaking API consumers, and vice versa, only because these
are two different classes, not one shared one.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class IndexRepositoryRequest(BaseModel):
    """Either provide local_path directly, or provide the three
    github_* fields to fetch from GitHub first. Exactly one path
    should be used — the route handler validates this, not pydantic,
    since "exactly one of two groups of fields" isn't a clean pydantic
    validator on its own.
    """

    repository_id: str
    local_path: str | None = None
    github_owner: str | None = None
    github_repo: str | None = None
    github_token: str | None = Field(default=None, repr=False)  # never echoed in responses


class RepositoryStatusResponse(BaseModel):
    repository_id: str
    name: str
    index_status: str
    last_indexed_commit: str
    graph_hash: str
    updated_at: str
    # Populated only when index_status == "Failed" — set by
    # run_indexing()'s exception handler in repository_manager.py.
    # Added alongside the move to background indexing: since a
    # pipeline failure can no longer become a synchronous HTTP error
    # (the response already went out as 202 before the failure
    # happened), the caller needs this to know WHY polling shows
    # "Failed" instead of just THAT it failed.
    error_message: str = ""


class FrameworkResponse(BaseModel):
    name: str
    category: str
    version: str | None


class RepositoryProfileResponse(BaseModel):
    repository_id: str
    name: str
    index_status: str
    completeness: float
    primary_language: str
    languages: dict[str, float]
    frameworks: list[FrameworkResponse]
    architecture_pattern: str | None
    entry_points: list[str]
    key_service_ids: list[str]
    important_module_ids: list[str]
    database_systems: list[str]
    dependency_count: int
    key_dependencies: list[str]
    build_system: str | None
    package_manager: str | None