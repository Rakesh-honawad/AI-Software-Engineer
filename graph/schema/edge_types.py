"""
Edge type registry for the knowledge graph.

Every edge carries confidence + source + created_by. This is what makes
the graph explainable instead of a black box: a `calls` edge from AST
parsing is confidence=1.0/source=AST, but a `belongs_to_feature` edge
proposed by the AI starts at confidence<1.0/source=AI until a developer
confirms it, at which point it's rewritten to confidence=1.0/source=User.

Phase 2 (not yet implemented, do not add to EdgeType until needed):
  emits, subscribes, renders
  ("uses" is intentionally folded into depends_on rather than added
  as a separate edge — a third near-synonym edge creates ambiguity
  without adding real distinguishing power.)
"""

from dataclasses import dataclass
from enum import Enum


class EdgeType(str, Enum):
    CONTAINS = "contains"                    # structural containment (Module->File, File->Function)
    IMPORTS = "imports"                       # file/module-level dependency
    CALLS = "calls"                           # THE core edge for impact-analysis traversal
    INHERITS = "inherits"                     # class inheritance chain
    IMPLEMENTS = "implements"                 # Class -> Interface
    EXPOSES = "exposes"                       # Function -> Endpoint (which function backs a route)
    READS = "reads"                           # Function -> Entity (data dependency, read)
    WRITES = "writes"                         # Function -> Entity (data dependency, write)
    TESTS = "tests"                           # Test -> Function/Class (feeds risk score)
    DEPENDS_ON = "depends_on"                 # Function/File -> ExternalDependency
    BELONGS_TO_FEATURE = "belongs_to_feature" # Function/File -> Feature
    CONFIGURES = "configures"                 # Configuration -> Service/Function
    REFERENCES = "references"                 # type/name reference without a call


class EdgeSource(str, Enum):
    AST = "AST"     # deterministically extracted from parsing — always confidence 1.0
    AI = "AI"       # proposed by the AI layer — starts below 1.0 until confirmed
    USER = "User"   # manually created or confirmed by a developer — always confidence 1.0


@dataclass
class GraphEdge:
    from_id: str          # source GraphNode.id
    to_id: str             # target GraphNode.id
    type: EdgeType
    confidence: float = 1.0
    source: EdgeSource = EdgeSource.AST
    created_by: str | None = None   # user id, if source=User; model name, if source=AI

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"GraphEdge.confidence must be between 0.0 and 1.0, got {self.confidence}"
            )

    def confirm(self, user_id: str) -> "GraphEdge":
        """Called when a developer approves an AI-suggested edge."""
        self.confidence = 1.0
        self.source = EdgeSource.USER
        self.created_by = user_id
        return self