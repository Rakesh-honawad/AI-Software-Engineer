"""
RepositoryProfile persistence — JSON backend, same pattern and same
reasoning as storage/graph/json_store.py: simplest correct version
first, swappable for a real DB later without touching callers.

Handles nested dataclasses (FrameworkInfo, CodingStyle, VCSInfo,
RepositoryCapabilities, RepositoryStats, ProfileMetadata) and enums
(ProfileSource, IndexStatus) explicitly, since dataclasses.asdict()
alone doesn't round-trip enums or datetimes cleanly.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .types import (
    CodingStyle,
    FrameworkInfo,
    IndexStatus,
    ProfileMetadata,
    ProfileSource,
    RepositoryCapabilities,
    RepositoryProfile,
    RepositoryStats,
    VCSInfo,
)


def _serialize_profile(profile: RepositoryProfile) -> dict:
    data = asdict(profile)
    # asdict() flattens nested dataclasses to plain dicts, but leaves
    # Enum members and datetimes as-is (it doesn't know how to convert
    # them) — so those need explicit handling here.
    data["index_status"] = profile.index_status.value
    data["architecture_pattern_source"] = profile.architecture_pattern_source.value
    data["domain_vocabulary_source"] = profile.domain_vocabulary_source.value
    data["summary_source"] = profile.summary_source.value
    data["frameworks"] = [
        {**asdict(f), "source": f.source.value} for f in profile.frameworks
    ]
    data["metadata"]["generated_at"] = profile.metadata.generated_at.isoformat()
    return data


def _deserialize_profile(data: dict) -> RepositoryProfile:
    frameworks = [
        FrameworkInfo(
            name=f["name"], category=f["category"], version=f.get("version"),
            confidence=f.get("confidence", 1.0), source=ProfileSource(f["source"]),
        )
        for f in data.get("frameworks", [])
    ]

    vcs_data = data.get("vcs")
    vcs = VCSInfo(**vcs_data) if vcs_data else None

    capabilities_data = data.get("capabilities", {})
    capabilities = RepositoryCapabilities(**capabilities_data) if capabilities_data else RepositoryCapabilities()

    coding_style_data = data.get("coding_style", {})
    coding_style = CodingStyle(**coding_style_data) if coding_style_data else CodingStyle()

    stats_data = data.get("stats", {})
    stats = RepositoryStats(**stats_data) if stats_data else RepositoryStats()

    metadata_data = data.get("metadata", {})
    metadata = ProfileMetadata(
        schema_version=metadata_data.get("schema_version", ""),
        generator_version=metadata_data.get("generator_version", ""),
        generated_at=datetime.fromisoformat(metadata_data["generated_at"]),
    )

    return RepositoryProfile(
        repository_id=data["repository_id"],
        name=data["name"],
        index_status=IndexStatus(data["index_status"]),
        completeness=data.get("completeness", 0.0),
        primary_language=data.get("primary_language", ""),
        languages=data.get("languages", {}),
        frameworks=frameworks,
        architecture_pattern=data.get("architecture_pattern"),
        architecture_pattern_confidence=data.get("architecture_pattern_confidence", 0.0),
        architecture_pattern_source=ProfileSource(data.get("architecture_pattern_source", "AI")),
        architecture_pattern_confirmed_by=data.get("architecture_pattern_confirmed_by"),
        entry_points=data.get("entry_points", []),
        key_service_ids=data.get("key_service_ids", []),
        important_module_ids=data.get("important_module_ids", []),
        database_systems=data.get("database_systems", []),
        capabilities=capabilities,
        dependency_count=data.get("dependency_count", 0),
        key_dependencies=data.get("key_dependencies", []),
        domain_vocabulary=data.get("domain_vocabulary", []),
        domain_vocabulary_confidence=data.get("domain_vocabulary_confidence", 0.0),
        domain_vocabulary_source=ProfileSource(data.get("domain_vocabulary_source", "AI")),
        domain_vocabulary_confirmed_by=data.get("domain_vocabulary_confirmed_by"),
        summary=data.get("summary"),
        summary_confidence=data.get("summary_confidence", 0.0),
        summary_source=ProfileSource(data.get("summary_source", "AI")),
        summary_confirmed_by=data.get("summary_confirmed_by"),
        coding_style=coding_style,
        build_system=data.get("build_system"),
        package_manager=data.get("package_manager"),
        testing_framework=data.get("testing_framework"),
        stats=stats,
        vcs=vcs,
        metadata=metadata,
    )


def save_profile(path: str | Path, profile: RepositoryProfile) -> None:
    data = _serialize_profile(profile)
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_profile(path: str | Path) -> RepositoryProfile:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return _deserialize_profile(data)