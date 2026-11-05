from __future__ import annotations

import hashlib
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
import json
import os

from pathlib import Path, PurePosixPath

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from starlette.datastructures import UploadFile as StarletteUploadFile
from api.dependencies.repository_deps import (
    get_storage_dir_or_404,
    load_graph_for_repo,
    load_profile_for_repo,
)
from api.schemas.graph_schemas import (
    GraphEdgeResponse,
    GraphNodeResponse,
    GraphResponse,
)
from api.schemas.repository_schemas import (
    FrameworkResponse,
    IndexRepositoryRequest,
    RepositoryProfileResponse,
    RepositoryStatusResponse,
)
from auth.dependencies import AuthenticatedUser, get_current_user
from ingestion.connectors.github import (
    GitHubConnector,
    GitHubRepoNotFoundError,
    GitHubRateLimitError,
    GitCloneError,
)
from repository.manager.repository_manager import (
    RepositoryLockedError,
    begin_indexing,
    load_record,
    run_indexing,
)

router = APIRouter(prefix="/repositories", tags=["repositories"])

STORAGE_ROOT = Path(os.getenv("KNOWLEDGE_ENGINE_STORAGE_ROOT", str(Path(tempfile.gettempdir()) / "knowledge_engine_repositories")))

_UNSAFE_PATH_CHARS = re.compile(r'[<>:"/\\|?*]')

# Upload safety limits.
MAX_FOLDER_FILES = 5000
MAX_FOLDER_FILE_BYTES = 100 * 1024 * 1024
MAX_FOLDER_TOTAL_BYTES = 300 * 1024 * 1024

MAX_ZIP_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_ZIP_EXTRACTED_BYTES = 500 * 1024 * 1024
MAX_ZIP_ENTRIES = 20000
MAX_ZIP_ENTRY_BYTES = 100 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 200

IGNORED_UPLOAD_DIRECTORIES = {
    "node_modules",
    ".git",
    ".next",
    ".turbo",
    ".vercel",
    "dist",
    "build",
    "coverage",
    ".cache",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "target",
}


def _should_ignore_upload_path(path: PurePosixPath) -> bool:
    parts = path.parts
    filename = parts[-1].casefold() if parts else ""

    if any(
        part.casefold() in IGNORED_UPLOAD_DIRECTORIES
        for part in parts[:-1]
    ):
        return True

    if filename == ".env":
        return True

    if filename.startswith(".env.") and filename not in {
        ".env.example",
        ".env.sample",
        ".env.template",
    }:
        return True

    if filename.endswith((".pem", ".key", ".p12", ".pfx")):
        return True

    if filename in {"id_rsa", "id_ed25519", "id_ecdsa"}:
        return True

    return False

def _safe_folder_name(repository_id: str) -> str:
    safe = _UNSAFE_PATH_CHARS.sub("_", repository_id)
    safe = safe.strip(" .")

    if not safe:
        safe = "repository"

    digest = hashlib.sha256(
        repository_id.encode("utf-8")
    ).hexdigest()[:12]

    return f"{safe[:80]}-{digest}"


def _user_storage_dir(
    repository_id: str,
    user_id: str,
) -> Path:
    owner_key = hashlib.sha256(
        user_id.encode("utf-8")
    ).hexdigest()

    return (
        STORAGE_ROOT
        / owner_key
        / _safe_folder_name(repository_id)
    )


def _validate_repository_id(repository_id: str) -> None:
    if (
        not repository_id
        or not repository_id.strip()
        or len(repository_id) > 256
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid repository_id.",
        )


def _safe_relative_path(raw_path: str) -> Path:
    """
    Validate a browser-relative or ZIP-relative file path.

    Paths must remain inside the generated source directory.
    Windows-specific unsafe filename characters are rejected too.
    """
    if not raw_path or "\x00" in raw_path:
        raise HTTPException(
            status_code=400,
            detail="An uploaded file has an invalid path.",
        )

    if "\\" in raw_path or raw_path.startswith("/"):
        raise HTTPException(
            status_code=400,
            detail="Absolute paths and backslashes are not allowed.",
        )

    parts = raw_path.split("/")

    if any(part in ("", ".", "..") for part in parts):
        raise HTTPException(
            status_code=400,
            detail="An uploaded file contains an unsafe path.",
        )

    for part in parts:
        if (
            _UNSAFE_PATH_CHARS.search(part)
            or part.endswith((" ", "."))
        ):
            raise HTTPException(
                status_code=400,
                detail="An uploaded file contains an invalid filename.",
            )

    if len(parts[0]) >= 2 and parts[0][1] == ":":
        raise HTTPException(
            status_code=400,
            detail="Drive-qualified paths are not allowed.",
        )

    return Path(*parts)


def _ensure_inside_root(root: Path, target: Path) -> None:
    root_resolved = root.resolve()
    target_resolved = target.resolve()

    try:
        target_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="An uploaded file resolves outside its repository.",
        ) from exc


def _create_source_dir(storage_dir: Path) -> Path:
    source_dir = (
        storage_dir
        / "_sources"
        / uuid.uuid4().hex
    )

    source_dir.mkdir(parents=True, exist_ok=False)

    return source_dir


def _start_uploaded_indexing(
    repository_id: str,
    user_id: str,
    source_dir: Path,
    background_tasks: BackgroundTasks,
) -> RepositoryStatusResponse:
    storage_dir = _user_storage_dir(repository_id, user_id)

    try:
        record = begin_indexing(
            repo_path=source_dir,
            repository_id=repository_id,
            storage_dir=storage_dir,
            owner_id=user_id,
        )

    except RepositoryLockedError as exc:
        raise HTTPException(
            status_code=409,
            detail="An indexing run is already in progress for this repository.",
        ) from exc

    except PermissionError as exc:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        ) from exc

    background_tasks.add_task(
        run_indexing,
        source_dir,
        repository_id,
        storage_dir,
    )

    return RepositoryStatusResponse(
        repository_id=record.repository_id,
        name=record.name,
        index_status=record.index_status.value,
        last_indexed_commit=record.last_indexed_commit,
        graph_hash=record.graph_hash,
        updated_at=record.updated_at.isoformat(),
    )


async def _save_upload(
    upload: UploadFile,
    destination: Path,
    max_bytes: int,
    total_so_far: int = 0,
    total_limit: int | None = None,
) -> tuple[int, int]:
    """
    Stream an upload to disk without reading the entire file into RAM.
    Returns (file_size, updated_total_size).
    """
    file_size = 0
    total_size = total_so_far

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        with destination.open("wb") as output:
            while True:
                chunk = await upload.read(1024 * 1024)

                if not chunk:
                    break

                file_size += len(chunk)
                total_size += len(chunk)

                if file_size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="An uploaded file exceeds the allowed size.",
                    )

                if (
                    total_limit is not None
                    and total_size > total_limit
                ):
                    raise HTTPException(
                        status_code=413,
                        detail="The total upload exceeds the allowed size.",
                    )

                output.write(chunk)

    finally:
        await upload.close()

    return file_size, total_size


# ---------------------------------------------------------
# Existing GitHub / backend-local-path indexing endpoint
# ---------------------------------------------------------

@router.post(
    "/index",
    response_model=RepositoryStatusResponse,
    status_code=202,
)
def index_repository_route(
    request: IndexRepositoryRequest,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
) -> RepositoryStatusResponse:

    _validate_repository_id(request.repository_id)

    has_local = request.local_path is not None
    has_github = bool(
        request.github_owner and request.github_repo
    )

    if has_local == has_github:
        raise HTTPException(
            status_code=400,
            detail=(
                "Provide exactly one of: local_path, "
                "or (github_owner + github_repo)."
            ),
        )

    storage_dir = _user_storage_dir(
        request.repository_id,
        user.id,
    )

    if has_github:
        connector = GitHubConnector(
            owner=request.github_owner,
            repo=request.github_repo,
            token=request.github_token,
        )

        clone_dir = storage_dir / "_clone"

        try:
            fetch_result = connector.fetch(clone_dir)

        except GitHubRepoNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            ) from exc

        except GitHubRateLimitError as exc:
            raise HTTPException(
                status_code=429,
                detail=str(exc),
            ) from exc

        except GitCloneError as exc:
            raise HTTPException(
                status_code=502,
                detail=str(exc),
            ) from exc

        repo_path = fetch_result.local_path

    else:
        repo_path = Path(request.local_path).expanduser()

        if not repo_path.exists():
            raise HTTPException(
                status_code=400,
                detail="The supplied local_path does not exist.",
            )

        if not repo_path.is_dir():
            raise HTTPException(
                status_code=400,
                detail="local_path must point to a directory.",
            )

        repo_path = repo_path.resolve()

    try:
        record = begin_indexing(
            repo_path=repo_path,
            repository_id=request.repository_id,
            storage_dir=storage_dir,
            owner_id=user.id,
        )

    except RepositoryLockedError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except PermissionError as exc:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        ) from exc

    background_tasks.add_task(
        run_indexing,
        repo_path,
        request.repository_id,
        storage_dir,
    )

    return RepositoryStatusResponse(
        repository_id=record.repository_id,
        name=record.name,
        index_status=record.index_status.value,
        last_indexed_commit=record.last_indexed_commit,
        graph_hash=record.graph_hash,
        updated_at=record.updated_at.isoformat(),
    )


# ---------------------------------------------------------
# Browser folder upload
# ---------------------------------------------------------

@router.post(
    "/upload-folder",
    response_model=RepositoryStatusResponse,
    status_code=202,
)
async def upload_folder_route(
    request: Request,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
) -> RepositoryStatusResponse:

    try:
        form = await request.form(
            max_files=MAX_FOLDER_FILES,
            max_fields=10,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid folder upload form.",
        ) from exc

    repository_id = form.get("repository_id")
    relative_paths_json = form.get("relative_paths_json")
    files = form.getlist("files")

    if not isinstance(repository_id, str) or not repository_id.strip():
        raise HTTPException(
            status_code=400,
            detail="Repository ID is required.",
        )

    _validate_repository_id(repository_id)

    if not isinstance(relative_paths_json, str):
        raise HTTPException(
            status_code=400,
            detail="Relative file paths are required.",
        )

    try:
        relative_paths = json.loads(relative_paths_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid relative paths manifest.",
        ) from exc

    if not isinstance(relative_paths, list) or not all(
        isinstance(path, str) for path in relative_paths
    ):
        raise HTTPException(
            status_code=400,
            detail="Relative paths manifest must be a list of strings.",
        )

    if not files:
        raise HTTPException(
            status_code=400,
            detail="Select a folder containing files.",
        )

    if len(files) > MAX_FOLDER_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"A maximum of {MAX_FOLDER_FILES} files is allowed.",
        )

    if len(files) != len(relative_paths):
        raise HTTPException(
            status_code=400,
            detail="Uploaded files and relative paths do not match.",
        )

    if not all(isinstance(upload, StarletteUploadFile) for upload in files):
        raise HTTPException(
            status_code=400,
            detail="Invalid uploaded file parts.",
        )

    storage_dir = _user_storage_dir(
        repository_id,
        user.id,
    )

    source_dir = _create_source_dir(storage_dir)

    total_size = 0
    seen_paths: set[str] = set()

    try:
        for upload, raw_path in zip(files, relative_paths):
            relative_path = _safe_relative_path(raw_path)
            if _should_ignore_upload_path(relative_path):
                continue
            normalized = relative_path.as_posix().casefold()

            if normalized in seen_paths:
                raise HTTPException(
                    status_code=400,
                    detail="The folder contains duplicate file paths.",
                )

            seen_paths.add(normalized)

            destination = source_dir / relative_path

            _ensure_inside_root(
                source_dir,
                destination,
            )

            _, total_size = await _save_upload(
                upload=upload,
                destination=destination,
                max_bytes=MAX_FOLDER_FILE_BYTES,
                total_so_far=total_size,
                total_limit=MAX_FOLDER_TOTAL_BYTES,
            )

        return _start_uploaded_indexing(
            repository_id,
            user.id,
            source_dir,
            background_tasks,
        )

    except Exception:
        shutil.rmtree(
            source_dir,
            ignore_errors=True,
        )
        raise

    finally:
        for upload in files:
            await upload.close()
# ---------------------------------------------------------
# ZIP upload
# ---------------------------------------------------------

@router.post(
    "/upload-zip",
    response_model=RepositoryStatusResponse,
    status_code=202,
)
async def upload_zip_route(
    background_tasks: BackgroundTasks,
    repository_id: str = Form(...),
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(get_current_user),
) -> RepositoryStatusResponse:

    _validate_repository_id(repository_id)

    storage_dir = _user_storage_dir(
        repository_id,
        user.id,
    )

    source_dir = _create_source_dir(storage_dir)

    upload_dir = (
        storage_dir
        / "_uploads"
        / uuid.uuid4().hex
    )

    archive_path = upload_dir / "repository.zip"

    try:
        await _save_upload(
            upload=file,
            destination=archive_path,
            max_bytes=MAX_ZIP_UPLOAD_BYTES,
        )

        try:
            archive = zipfile.ZipFile(archive_path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise HTTPException(
                status_code=400,
                detail="The uploaded file is not a valid ZIP archive.",
            ) from exc

        with archive:
            entries = archive.infolist()

            if len(entries) > MAX_ZIP_ENTRIES:
                raise HTTPException(
                    status_code=413,
                    detail="The ZIP archive contains too many entries.",
                )

            total_extracted = 0
            seen_paths: set[str] = set()

            for entry in entries:
                if entry.is_dir():
                    continue

                relative_path = _safe_relative_path(entry.filename)
                if _should_ignore_upload_path(relative_path):
                    continue
                normalized = relative_path.as_posix().casefold()


                if normalized in seen_paths:
                    raise HTTPException(
                        status_code=400,
                        detail="The ZIP archive contains duplicate file paths.",
                    )

                seen_paths.add(normalized)

                # Reject symbolic links and special filesystem entries.
                unix_mode = entry.external_attr >> 16
                file_type = stat.S_IFMT(unix_mode)

                if file_type not in (
                    0,
                    stat.S_IFREG,
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="The ZIP archive contains an unsupported file type.",
                    )

                if entry.file_size > MAX_ZIP_ENTRY_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="A file inside the ZIP exceeds the allowed size.",
                    )

                total_extracted += entry.file_size

                if total_extracted > MAX_ZIP_EXTRACTED_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="The extracted ZIP exceeds the allowed size.",
                    )

                if (
                    entry.file_size > 0
                    and entry.compress_size == 0
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="The ZIP archive has an invalid compression ratio.",
                    )

                if (
                    entry.compress_size > 0
                    and entry.file_size / entry.compress_size
                    > MAX_ZIP_COMPRESSION_RATIO
                ):
                    raise HTTPException(
                        status_code=413,
                        detail="The ZIP archive has an excessive compression ratio.",
                    )

                destination = source_dir / relative_path

                _ensure_inside_root(
                    source_dir,
                    destination,
                )

                destination.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                # Stream extraction and enforce actual decompressed size.
                extracted_file_size = 0

                with archive.open(entry, "r") as source:
                    with destination.open("wb") as output:
                        while True:
                            chunk = source.read(1024 * 1024)

                            if not chunk:
                                break

                            extracted_file_size += len(chunk)

                            if (
                                extracted_file_size
                                > MAX_ZIP_ENTRY_BYTES
                            ):
                                raise HTTPException(
                                    status_code=413,
                                    detail="An extracted file exceeds the allowed size.",
                                )

                            output.write(chunk)

                if extracted_file_size != entry.file_size:
                    raise HTTPException(
                        status_code=400,
                        detail="The ZIP archive contains an inconsistent file size.",
                    )

        return _start_uploaded_indexing(
            repository_id,
            user.id,
            source_dir,
            background_tasks,
        )

    except Exception:
        shutil.rmtree(
            source_dir,
            ignore_errors=True,
        )
        raise

    finally:
        await file.close()
        shutil.rmtree(
            upload_dir,
            ignore_errors=True,
        )


# ---------------------------------------------------------
# Existing repository status
# ---------------------------------------------------------

@router.get(
    "/{repository_id}",
    response_model=RepositoryStatusResponse,
)
def get_repository_status(
    repository_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> RepositoryStatusResponse:

    storage_dir = get_storage_dir_or_404(
        repository_id,
        user.id,
    )

    record = load_record(storage_dir)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        )

    return RepositoryStatusResponse(
        repository_id=record.repository_id,
        name=record.name,
        index_status=record.index_status.value,
        last_indexed_commit=record.last_indexed_commit,
        graph_hash=record.graph_hash,
        updated_at=record.updated_at.isoformat(),
        error_message=record.error_message,
    )


# ---------------------------------------------------------
# Existing graph endpoint
# ---------------------------------------------------------

@router.get(
    "/{repository_id}/graph",
    response_model=GraphResponse,
)
def get_repository_graph(
    repository_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> GraphResponse:

    nodes, edges = load_graph_for_repo(
        repository_id,
        user.id,
    )

    return GraphResponse(
        nodes=[
            GraphNodeResponse(
                id=node.id,
                type=node.type.value,
                name=node.name,
                file_path=node.file_path,
                line_range=(
                    list(node.line_range)
                    if node.line_range
                    else None
                ),
                properties=node.properties,
            )
            for node in nodes
        ],
        edges=[
            GraphEdgeResponse(
                from_id=edge.from_id,
                to_id=edge.to_id,
                type=edge.type.value,
                confidence=edge.confidence,
            )
            for edge in edges
        ],
    )


# ---------------------------------------------------------
# Existing repository profile endpoint
# ---------------------------------------------------------

@router.get(
    "/{repository_id}/profile",
    response_model=RepositoryProfileResponse,
)
def get_repository_profile(
    repository_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> RepositoryProfileResponse:

    profile = load_profile_for_repo(
        repository_id,
        user.id,
    )

    return RepositoryProfileResponse(
        repository_id=profile.repository_id,
        name=profile.name,
        index_status=profile.index_status.value,
        completeness=profile.completeness,
        primary_language=profile.primary_language,
        languages=profile.languages,
        frameworks=[
            FrameworkResponse(
                name=framework.name,
                category=framework.category,
                version=framework.version,
            )
            for framework in profile.frameworks
        ],
        architecture_pattern=profile.architecture_pattern,
        entry_points=profile.entry_points,
        key_service_ids=profile.key_service_ids,
        important_module_ids=profile.important_module_ids,
        database_systems=profile.database_systems,
        dependency_count=profile.dependency_count,
        key_dependencies=profile.key_dependencies,
        build_system=profile.build_system,
        package_manager=profile.package_manager,
    )