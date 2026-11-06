import hmac
import os
from uuid import UUID

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
import json
import urllib.request
import tempfile
import zipfile
from pathlib import Path
import asyncio
import time
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from api.routes.workspace_files import _repo_root, _ignore_export_path
from github_integration.client import create_installation_token


router = APIRouter(prefix="/api/v1/runner", tags=["verification-runner"])


def _connect():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(database_url, cursor_factory=RealDictCursor)


def _authenticate(token: str | None):
    expected = os.getenv("RUNNER_AUTH_TOKEN", "")
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid runner credentials")



def create_verification_job(
    user_id: str,
    repository_id: str,
    operation: str,
    payload: dict,
) -> str:
    if operation not in {"baseline", "verify_patch", "verify_editor_patch"}:
        raise ValueError("Unsupported verification operation")

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO public.verification_jobs
                   (user_id, repository_id, operation, payload)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (user_id, repository_id, operation, Json(payload)),
            )
            job_id = str(cur.fetchone()["id"])

    try:
        dispatch_runner_workflow(job_id)
    except Exception as exc:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE public.verification_jobs
                    SET status = 'failed',
                        error = %s,
                        updated_at = NOW()
                    WHERE id = %s AND status = 'queued'
                    """,
                    (str(exc)[:2000], job_id),
                )
        raise HTTPException(
            status_code=502,
            detail=f"Could not start verification: {exc}",
        ) from exc

    return job_id


def get_verification_job(job_id: str, user_id: str) -> dict | None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, status, result, error
                FROM public.verification_jobs
                WHERE id = %s AND user_id = %s
                """,
                (str(job_id), str(user_id)),
            )
            job = cur.fetchone()

    return dict(job) if job else None

async def wait_for_verification_job(
    job_id: str,
    user_id: str,
    timeout: int = 300,
) -> dict:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        job = await asyncio.to_thread(
            get_verification_job,
            job_id,
            user_id,
        )

        if not job:
            raise HTTPException(
                status_code=404,
                detail="Verification job not found",
            )

        if job["status"] == "completed":
            return job.get("result") or {}

        if job["status"] in {"failed", "expired"}:
            raise HTTPException(
                status_code=502,
                detail=job.get("error") or "Remote verification failed",
            )

        await asyncio.sleep(2)

    raise HTTPException(
        status_code=504,
        detail="Remote verification timed out. Apply is blocked.",
    )

@router.post("/jobs/{job_id}/claim")
def claim_job(
    job_id: UUID,
    x_runner_token: str | None = Header(default=None),
):
    _authenticate(x_runner_token)

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.verification_jobs
                SET status = 'claimed',
                    attempts = attempts + 1,
                    lease_expires_at = NOW() + INTERVAL '35 minutes',
                    updated_at = NOW()
                WHERE id = %s
                  AND (
                      status = 'queued'
                      OR (status IN ('claimed', 'running')
                          AND lease_expires_at < NOW())
                  )
                RETURNING id, repository_id, operation, payload
                """,
                (str(job_id),),
            )
            job = cur.fetchone()

    if not job:
        raise HTTPException(status_code=409, detail="Job unavailable")

    return dict(job)


class JobResult(BaseModel):
    result: dict
    error: str | None = None


@router.post("/jobs/{job_id}/complete")
def complete_job(
    job_id: UUID,
    body: JobResult,
    x_runner_token: str | None = Header(default=None),
):
    _authenticate(x_runner_token)

    status = "failed" if body.error else "completed"

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.verification_jobs
                SET status = %s,
                    result = %s,
                    error = %s,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE id = %s
                  AND status IN ('claimed', 'running')
                RETURNING id
                """,
                (status, Json(body.result), body.error, str(job_id)),
            )
            updated = cur.fetchone()

    if not updated:
        raise HTTPException(status_code=409, detail="Job cannot be completed")

    return {"status": status, "job_id": str(job_id)}

def dispatch_runner_workflow(job_id: str) -> None:
    installation_id = int(os.environ["RUNNER_GITHUB_INSTALLATION_ID"])
    token = create_installation_token(installation_id)

    url = (
        "https://api.github.com/repos/"
        "Rakesh-honawad/knowledge-engine-runner/"
        "actions/workflows/verify.yml/dispatches"
    )

    request = urllib.request.Request(
        url,
        data=json.dumps({
            "ref": "main",
            "inputs": {"job_id": job_id},
        }).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 204:
                raise RuntimeError("GitHub workflow dispatch failed")
    except Exception as exc:
        raise RuntimeError(f"Could not start verification workflow: {exc}") from exc

@router.get("/jobs/{job_id}/source.zip")
def download_job_source(
    job_id: UUID,
    x_runner_token: str | None = Header(default=None),
):
    _authenticate(x_runner_token)

    # Atomically move the claimed job to running.
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.verification_jobs
                SET status = 'running',
                    lease_expires_at = NOW() + INTERVAL '35 minutes',
                    updated_at = NOW()
                WHERE id = %s
                  AND status = 'claimed'
                  AND lease_expires_at > NOW()
                RETURNING user_id, repository_id
                """,
                (str(job_id),),
            )
            job = cur.fetchone()

    if not job:
        raise HTTPException(
            status_code=409,
            detail="Job is not claimed or its lease has expired.",
        )

    # Resolve only the repository owned by this job's user.
    root = _repo_root(
        job["repository_id"],
        str(job["user_id"]),
    )

    fd, archive_name = tempfile.mkstemp(
        prefix="verification-source-",
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
            filename="verification-source.zip",
            background=BackgroundTask(
                lambda: archive_path.unlink(missing_ok=True)
            ),
        )

    except Exception:
        archive_path.unlink(missing_ok=True)
        raise        