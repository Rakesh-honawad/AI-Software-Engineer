
"""
Owner-scoped repository registry.

A repository is identified by (owner_id, repository_id).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

DEFAULT_REGISTRY_PATH = Path(os.getenv("KNOWLEDGE_ENGINE_REGISTRY_PATH", str(Path.home() / ".miniai" / "repository_registry.json")))


def _load(registry_path: Path) -> dict[str, str]:
    if not registry_path.exists():
        return {}
    return json.loads(registry_path.read_text(encoding="utf-8"))


def _save(registry_path: Path, data: dict[str, str]) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


def _registry_key(owner_id: str, repository_id: str) -> str:
    identity = f"{owner_id}:{repository_id}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def register(
    repository_id: str,
    storage_dir: str | Path,
    owner_id: str = "",
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
) -> None:
    path = Path(registry_path)
    data = _load(path)
    data[_registry_key(owner_id, repository_id)] = str(storage_dir)
    _save(path, data)


def lookup(
    repository_id: str,
    owner_id: str = "",
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
) -> str | None:
    return _load(Path(registry_path)).get(
        _registry_key(owner_id, repository_id)
    )


def list_all(
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
) -> dict[str, str]:
    return _load(Path(registry_path))
