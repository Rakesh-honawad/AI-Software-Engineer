"""
Node type registry for the knowledge graph.

RULE FOR ADDING A NEW NODE TYPE (do not skip this check):
A concept becomes a node ONLY if either is true:
  1. Other nodes need to reference it via an edge, or
  2. It needs its own independent lifecycle, severity score, or query.
Otherwise it belongs as a property on an existing node, not a new type.
This is what keeps the graph from exploding into hundreds of micro-types.

Phase 2 (not yet implemented, do not add to NodeType until needed):
  Package, Event, UIComponent, MessageQueue, BackgroundJob,
  Scheduler, Workflow, Microservice, WorkspaceBoundary

RESERVED PROPERTY KEYS (not enforced yet, but reuse these names rather
than inventing synonyms — keeps `properties` dicts consistent across
parsers and node types):
  language        e.g. "python", "typescript"
  visibility      "public" | "private" | "protected"
  signature       function/method signature string
  hash            content hash, used to detect unchanged nodes on reparse
  path            file-system path, when distinct from file_path below
  branch          Repository node: current branch
  default_branch  Repository node: default branch name
  framework       Repository node: detected framework(s)
  build_system    Repository node: e.g. "pip", "npm", "maven"
  entry_points    Repository node: list of entry file paths
  last_indexed_commit  Repository node: commit hash of last full index
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    REPOSITORY = "Repository"              # the repo itself: commit, branch, indexing status
    MODULE = "Module"                      # logical grouping above file level (package/namespace)
    FILE = "File"                          # physical unit — git diffs by file
    CLASS = "Class"
    INTERFACE = "Interface"                # contracts (TS interfaces, Java interfaces, ABCs)
    FUNCTION = "Function"                  # atomic unit for impact analysis
    SERVICE = "Service"                    # business logic layer (matches backend/app/services/)
    REPOSITORY_LAYER = "RepositoryLayer"   # DDD data-access repository (matches backend/app/repositories/)
    ENDPOINT = "Endpoint"                  # API route — often what a breaking change actually breaks
    ENTITY = "Entity"                      # domain/data model — DB-agnostic (SQL, Mongo, Redis, etc.)
    CONFIGURATION = "Configuration"        # .env, settings.py, application.yml — silent breakage source
    TEST = "Test"                          # feeds the risk score directly (is this covered?)
    EXTERNAL_DEPENDENCY = "ExternalDependency"  # third-party packages — version bumps are a risk source
    FEATURE = "Feature"                    # cross-cutting logical grouping (AI-suggested, user-confirmed)


class NodeSource(str, Enum):
    """Mirrors EdgeSource — nodes need provenance too, not just edges.
    Example: a Function node comes from AST; a Feature node may come from AI."""
    AST = "AST"
    AI = "AI"
    USER = "User"


@dataclass
class GraphNode:
    """
    Generic node contract. All node types share this shape; type-specific
    data lives in `properties` rather than as dataclass subclasses, so the
    builder/query/analysis layers don't need to branch on Python types.
    See RESERVED PROPERTY KEYS above before inventing a new properties key.
    """

    id: str                        # stable unique id, e.g. "function:app/services/user.py:create_user"
    type: NodeType
    name: str                      # human-readable label (function name, file path, feature name...)
    properties: dict[str, Any] = field(default_factory=dict)

    # Populated by the builder, not set by hand:
    file_path: str | None = None   # originating file, if applicable
    line_range: tuple[int, int] | None = None

    # Provenance and lifecycle:
    source: NodeSource = NodeSource.AST
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    is_deleted: bool = False        # incremental indexing marks nodes deleted rather than removing them,
                                     # so graph history stays queryable