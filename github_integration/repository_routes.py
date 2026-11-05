"""GitHub App-backed repository indexing."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from api.routes.repositories import _user_storage_dir, _validate_repository_id
from api.schemas.repository_schemas import RepositoryStatusResponse
from auth.dependencies import AuthenticatedUser, get_current_user
from github_integration import store
from github_integration.client import create_installation_token, list_installation_repositories
from github_integration.routes import _verified_installation
from ingestion.connectors.github import (
    GitHubConnector,
    GitHubRepoNotFoundError,
    GitHubRateLimitError,
    GitCloneError,
)
from repository.manager.repository_manager import (
    RepositoryLockedError,
    begin_indexing,
    run_indexing,
)

router = APIRouter(prefix="/repositories", tags=["repositories"])


class GitHubAppIndexRequest(BaseModel):
    repository_id: str = Field(min_length=1, max_length=256)
    installation_id: int = Field(gt=0)
    github_repository_id: int = Field(gt=0)


@router.post(
    "/index/github-app",
    response_model=RepositoryStatusResponse,
    status_code=202,
)
def index_github_app_repository(
    request: GitHubAppIndexRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
) -> RepositoryStatusResponse:
    """Index a repository accessible through this user's GitHub App installation."""

    _validate_repository_id(request.repository_id)

    # 1. Verify this installation belongs to the authenticated user.
    try:
        owned_installation = store.get_installation(
            user.id,
            request.installation_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not verify GitHub installation ownership.",
        ) from exc

    if owned_installation is None:
        raise HTTPException(
            status_code=404,
            detail="GitHub installation not found for this user.",
        )

    # 2. Verify the installation still belongs to our configured GitHub App.
    _verified_installation(request.installation_id)

    # 3. Resolve the numeric repository ID from this installation.
    try:
        available_repositories = list_installation_repositories(
            request.installation_id
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not retrieve repositories from this GitHub installation.",
        ) from exc

    selected = next(
        (
            repo for repo in available_repositories
            if repo.get("id") == request.github_repository_id
        ),
        None,
    )

    if selected is None:
        raise HTTPException(
            status_code=403,
            detail="The selected repository is not accessible through this installation.",
        )

    owner = selected.get("owner_login")
    repo_name = selected.get("name")
    default_branch = selected.get("default_branch")

    if not owner or not repo_name or not default_branch:
        raise HTTPException(
            status_code=502,
            detail="GitHub returned incomplete repository metadata.",
        )

    storage_dir = _user_storage_dir(request.repository_id, user.id)
    clone_dir = storage_dir / "_clone"

    # 4. Mint a short-lived token restricted to this exact repository.
    try:
        token = create_installation_token(
            request.installation_id,
            repository_ids=[request.github_repository_id],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not create a repository-scoped GitHub token.",
        ) from exc

    # 5. Fetch through the existing connector. The token is passed in memory
    #    and the connector does not write it into the Git remote URL.
    connector = GitHubConnector(
        owner=owner,
        repo=repo_name,
        token=token,
    )

    try:
        fetch_result = connector.fetch(clone_dir)
    except GitHubRepoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GitHubRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except GitCloneError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not fetch the selected GitHub repository.",
        ) from exc
    finally:
        # Do not retain the installation token in route state or persistent storage.
        token = None

    # 6. Register and start the existing indexing pipeline.
    try:
        record = begin_indexing(
            repo_path=fetch_result.local_path,
            repository_id=request.repository_id,
            storage_dir=storage_dir,
            owner_id=user.id,
            repository_name=repo_name,
            source_type="github_app",
            github_owner=owner,
            github_repo=repo_name,
            github_repository_id=request.github_repository_id,
            github_default_branch=default_branch,
            github_remote_url=fetch_result.remote_url,
            github_installation_record_id=request.installation_id,
        )
    except RepositoryLockedError as exc:
        raise HTTPException(
            status_code=409,
            detail="An indexing run is already in progress for this repository.",
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=404,
            detail="Repository not found for this account.",
        ) from exc

    background_tasks.add_task(
        run_indexing,
        fetch_result.local_path,
        request.repository_id,
        storage_dir,
    )

    store.audit_event(
        user.id,
        "github_app_repository_index_started",
        installation_id=request.installation_id,
        details=(
            f"github_repository_id={request.github_repository_id};"
            f"repository_id={request.repository_id}"
        ),
    )

    return RepositoryStatusResponse(
        repository_id=record.repository_id,
        name=record.name,
        index_status=record.index_status.value,
        last_indexed_commit=record.last_indexed_commit,
        graph_hash=record.graph_hash,
        updated_at=record.updated_at.isoformat(),
    )
