"""
GitHub connector — the one connector this MVP needs (GitLab/Bitbucket
explicitly deferred as a prior decision: GitHub alone is enough for
MVP). Uses the GitHub REST API for metadata (default branch,
public/private) and plain `git` via subprocess for the actual clone —
same "subprocess over a library" choice made in tier1_generator's VCS
detection, for the same reason: zero extra dependencies.

RE-INDEXING: fetch() checks whether target_dir already has a clone. If
so, it fetches + hard-resets to the remote default branch rather than
re-cloning from scratch — this is what makes calling index_repository()
again on an already-indexed repo cheap.

SCOPE: shallow clone (--depth 1) by default — fast, and all Tier
1/2/graph-building needs is the current state of the code, not full
history. Full git history (blueprint's "Git history analysis" future
idea) needs a full clone — that's a parameter here, not a redesign,
if/when that feature gets built.
"""

from __future__ import annotations

import base64
import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .base_connector import ConnectorFetchResult


class GitHubRepoNotFoundError(Exception):
    """Repo doesn't exist, or exists but is private and no (or an
    insufficient) token was provided — GitHub's API returns 404 for
    both cases, deliberately not distinguishing them for security
    reasons, so this error can't either."""


class GitHubRateLimitError(Exception):
    """GitHub API rate limit hit. Unauthenticated requests get 60/hour;
    an authenticated token gets 5000/hour — if this fires often,
    that's a signal a token should be configured, not just retried."""


class GitCloneError(Exception):
    """The `git` subprocess itself failed (network issue, disk space,
    corrupted partial clone, etc.) — carries git's own stderr."""


# Files these extensions almost never contribute anything to the
# tree-sitter-based graph builder (which only parses Python/TS/TSX/
# JS/JSX) but reliably break large clones on constrained connections
# -- confirmed live, a 14MB .pt file was enough to fail even a 300s
# clone timeout. Excluded from checkout via sparse-checkout so their
# *content* is never fetched at all, not just deferred to a later
# step (a size-based --filter=blob:limit alone does NOT solve this,
# since git still fetches large blobs during checkout regardless).
DEFAULT_EXCLUDED_PATTERNS = [
    "*.pt", "*.pth", "*.onnx", "*.h5", "*.pkl",
    "*.mp4", "*.mov", "*.avi", "*.mkv",
    "*.csv", "*.parquet", "*.tsv",
    "*.zip", "*.tar", "*.tar.gz", "*.7z",
    "*.mp3", "*.wav",
]


@dataclass
class GitHubConnector:
    owner: str
    repo: str
    token: str | None = None
    shallow: bool = True
    timeout_seconds: int = 30
    excluded_patterns: list[str] = None  # None -> DEFAULT_EXCLUDED_PATTERNS used

    def _api_url(self) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repo}"

    def _clone_url(self) -> str:
        # Deliberately NOT embedding the token in this URL — see
        # _auth_args(). A token in the URL gets written permanently
        # into .git/config by `git clone`/`git remote`, which is a
        # real credential leak for anyone who later reads that file.
        return f"https://github.com/{self.owner}/{self.repo}.git"

    def _auth_args(self) -> list[str]:
        """Passes the token as a transient extra HTTP header for this
        git invocation only — never written to .git/config, unlike a
        token embedded in the remote URL.

        Uses HTTP Basic auth (x-access-token as username, token as
        password) rather than Bearer -- GitHub's git-over-HTTPS smart
        protocol accepts Basic for both classic PATs and GitHub App
        installation tokens, but rejects installation tokens sent via
        Bearer with "invalid credentials" (confirmed live: Bearer
        worked fine for the REST API calls elsewhere in this project,
        but git's HTTP transport is a separate code path with a
        stricter/different requirement). Basic auth is the documented,
        universal approach for both token types, so this fix doesn't
        regress the PAT case.
        """
        if not self.token:
            return []
        basic_credentials = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
        return ["-c", f"http.extraheader=AUTHORIZATION: basic {basic_credentials}"]

    def get_repo_metadata(self) -> dict:
        """Calls the GitHub API for default_branch, private/public, etc.
        Raises GitHubRepoNotFoundError (404) or GitHubRateLimitError
        (403 with rate-limit headers) rather than a raw HTTP error.
        """
        req = urllib.request.Request(self._api_url())
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "miniai-knowledge-engine")  # GitHub's API
        # rejects requests with no User-Agent header at all — this bit
        # a real test run against a live repo before being caught here.
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise GitHubRepoNotFoundError(
                    f"{self.owner}/{self.repo} not found, or private and inaccessible "
                    f"with the provided token."
                ) from e
            if e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0":
                raise GitHubRateLimitError(
                    f"GitHub API rate limit hit fetching {self.owner}/{self.repo}. "
                    f"{'Configure a token to raise the limit.' if not self.token else 'Wait for the rate limit window to reset.'}"
                ) from e
            raise

    def _run_git(self, args: list[str], cwd: Path | None = None, timeout: int = 120) -> None:
        try:
            result = subprocess.run(
                ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            # Don't let the raw exception (which includes the full argv,
            # auth header and all) bubble up -- scrub it to a safe message.
            raise GitCloneError(
                f"git command timed out after {timeout}s (repo may be large "
                f"or the network slow -- consider retrying)"
            ) from None

        if result.returncode != 0:
            # Also scrub here: args may contain the auth header if this
            # was an authenticated call.
            safe_args = [a if "AUTHORIZATION" not in a else "[REDACTED]" for a in args]
            raise GitCloneError(f"git {' '.join(safe_args)} failed: {result.stderr.strip()}")
    def _get_head_commit(self, repo_dir: Path) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_dir,
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"

    def fetch(self, target_dir: str | Path, branch: str | None = None) -> ConnectorFetchResult:
        """
        branch: which branch to clone/checkout. Defaults to the repo's
        actual default branch (from GitHub's API) if not given. If a
        specific branch is requested, it's used for the clone/checkout
        directly -- git itself will fail loudly (GitCloneError) if that
        branch doesn't exist, rather than silently falling back to the
        default the way this used to behave before branch was wired
        through at all.
        """
        target_dir = Path(target_dir)
        metadata = self.get_repo_metadata()
        repo_default_branch = metadata["default_branch"]
        target_branch = branch or repo_default_branch
        is_private = metadata["private"]

        patterns = self.excluded_patterns if self.excluded_patterns is not None else DEFAULT_EXCLUDED_PATTERNS

        if (target_dir / ".git").exists():
            # Already cloned — update rather than re-clone from scratch.
            self._run_git(self._auth_args() + ["fetch", "origin", target_branch], cwd=target_dir, timeout=300)
            self._run_git(["reset", "--hard", f"origin/{target_branch}"], cwd=target_dir)
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            clone_args = self._auth_args() + ["clone", "--branch", target_branch, "--no-checkout"]
            if self.shallow:
                clone_args += ["--depth", "1"]
            clone_args += [self._clone_url(), str(target_dir)]
            self._run_git(clone_args, timeout=300)

            self._run_git(["sparse-checkout", "init", "--cone"], cwd=target_dir)
            sparse_patterns = ["/*"] + [f"!{p}" for p in patterns]
            self._run_git(["sparse-checkout", "set", "--no-cone", *sparse_patterns], cwd=target_dir)
            self._run_git(["checkout", target_branch], cwd=target_dir, timeout=300)

        commit = self._get_head_commit(target_dir)

        return ConnectorFetchResult(
            local_path=str(target_dir),
            default_branch=repo_default_branch,
            commit=commit,
            is_private=is_private,
            remote_url=metadata["html_url"],
        )