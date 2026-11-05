from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .schema_version import REPOSITORY_PROFILE_SCHEMA_VERSION


class ProfileSource(str, Enum):
    DETECTED = "Detected"
    AI = "AI"
    USER = "User"


class IndexStatus(str, Enum):
    PENDING = "Pending"
    INDEXING = "Indexing"
    READY = "Ready"
    STALE = "Stale"
    FAILED = "Failed"


@dataclass
class FrameworkInfo:
    name: str
    category: str
    version: str | None = None
    confidence: float = 1.0
    source: ProfileSource = ProfileSource.DETECTED

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"FrameworkInfo.confidence must be in [0.0, 1.0], got {self.confidence}"
            )


@dataclass
class CodingStyle:
    indent_style: str | None = None
    indent_size: int | None = None
    quote_style: str | None = None
    line_length: int | None = None
    linter: str | None = None
    formatter: str | None = None
    detected_from: list[str] = field(default_factory=list)


@dataclass
class VCSInfo:
    branch: str
    default_branch: str
    last_indexed_commit: str
    remote_url: str | None = None
    is_dirty: bool = False


@dataclass
class RepositoryCapabilities:
    has_api: bool = False
    has_database: bool = False
    has_tests: bool = False
    has_frontend: bool = False
    has_background_jobs: bool = False
    has_ci: bool = False
    has_docker: bool = False
    has_kubernetes: bool = False


@dataclass
class RepositoryStats:
    files: int = 0
    lines_of_code: int = 0
    functions: int = 0
    classes: int = 0
    services: int = 0
    tests: int = 0
    graph_nodes: int = 0
    graph_edges: int = 0


@dataclass
class ProfileMetadata:
    schema_version: str = REPOSITORY_PROFILE_SCHEMA_VERSION
    generator_version: str = "0.1.0"
    generated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class RepositoryProfile:
    repository_id: str
    name: str
    index_status: IndexStatus = IndexStatus.PENDING
    completeness: float = 0.0

    primary_language: str = ""
    languages: dict[str, float] = field(default_factory=dict)
    frameworks: list[FrameworkInfo] = field(default_factory=list)

    architecture_pattern: str | None = None
    architecture_pattern_confidence: float = 0.0
    architecture_pattern_source: ProfileSource = ProfileSource.AI
    architecture_pattern_confirmed_by: str | None = None

    entry_points: list[str] = field(default_factory=list)
    key_service_ids: list[str] = field(default_factory=list)
    important_module_ids: list[str] = field(default_factory=list)
    database_systems: list[str] = field(default_factory=list)
    capabilities: RepositoryCapabilities = field(default_factory=RepositoryCapabilities)

    dependency_count: int = 0
    key_dependencies: list[str] = field(default_factory=list)

    domain_vocabulary: list[str] = field(default_factory=list)
    domain_vocabulary_confidence: float = 0.0
    domain_vocabulary_source: ProfileSource = ProfileSource.AI
    domain_vocabulary_confirmed_by: str | None = None

    summary: str | None = None
    summary_confidence: float = 0.0
    summary_source: ProfileSource = ProfileSource.AI
    summary_confirmed_by: str | None = None

    coding_style: CodingStyle = field(default_factory=CodingStyle)
    build_system: str | None = None
    package_manager: str | None = None
    testing_framework: str | None = None

    stats: RepositoryStats = field(default_factory=RepositoryStats)
    vcs: VCSInfo | None = None
    metadata: ProfileMetadata = field(default_factory=ProfileMetadata)

    def __post_init__(self) -> None:
        for conf_name in (
            "architecture_pattern_confidence",
            "domain_vocabulary_confidence",
            "summary_confidence",
            "completeness",
        ):
            value = getattr(self, conf_name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{conf_name} must be in [0.0, 1.0], got {value}")

    def confirm_architecture_pattern(self, user_id: str, pattern: str | None = None) -> None:
        if pattern is not None:
            self.architecture_pattern = pattern
        self.architecture_pattern_confidence = 1.0
        self.architecture_pattern_source = ProfileSource.USER
        self.architecture_pattern_confirmed_by = user_id

    def confirm_domain_vocabulary(self, user_id: str, terms: list[str] | None = None) -> None:
        if terms is not None:
            self.domain_vocabulary = terms
        self.domain_vocabulary_confidence = 1.0
        self.domain_vocabulary_source = ProfileSource.USER
        self.domain_vocabulary_confirmed_by = user_id

    def confirm_summary(self, user_id: str, text: str | None = None) -> None:
        if text is not None:
            self.summary = text
        self.summary_confidence = 1.0
        self.summary_source = ProfileSource.USER
        self.summary_confirmed_by = user_id