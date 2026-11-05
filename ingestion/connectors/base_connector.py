"""
Base connector contract. A connector's only job is: given some remote
source, produce a local path on disk that repository_manager.index_repository()
can run against exactly like any other local folder. Nothing downstream
(Tier 1, graph builder, Tier 2) needs to know or care that a repo came
from GitHub instead of an existing local clone — that's the whole
point of this boundary.

GitLab/Bitbucket (explicitly deferred earlier — "GitHub alone is enough
for MVP") would each be a new file here implementing the same
ConnectorFetchResult shape, not a change to anything that calls a
connector.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class ConnectorFetchResult:
    local_path: str
    default_branch: str
    commit: str
    is_private: bool
    remote_url: str


class RepoConnector(Protocol):
    def fetch(self, target_dir: str | Path) -> ConnectorFetchResult:
        """Clone (first time) or update (subsequent calls) the remote
        repo into target_dir, and return where it landed plus the
        metadata index_repository() needs."""
        ...