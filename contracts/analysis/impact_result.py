"""
Impact analysis result contract.

Lives in contracts/, not analysis/, because it's a shared shape:
narration/ (ai/) reads it to generate plain-English explanations,
workspace-ui reads it to color the graph, api/ serializes it to the
frontend. None of those should import analysis/impact/'s internals to
get this shape.

This is pure data — no method here calls AI, computes anything, or
has side effects. Computation lives in analysis/impact/impact_analysis.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from graph.schema.node_types import NodeType


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class ImpactedNode:
    node_id: str
    node_type: NodeType
    name: str
    distance: int              # hops from the changed node (1 = direct dependent)
    fan_in: int                 # how many things directly depend on THIS node
    is_tested: bool
    severity: Severity
    risk_score: float          # 0.0-1.0, the raw score `severity` was bucketed from

    def __post_init__(self) -> None:
        if not 0.0 <= self.risk_score <= 1.0:
            raise ValueError(f"ImpactedNode.risk_score must be in [0.0, 1.0], got {self.risk_score}")
        if self.distance < 1:
            raise ValueError(f"ImpactedNode.distance must be >= 1, got {self.distance}")


@dataclass
class ImpactAnalysisResult:
    changed_node_id: str
    changed_node_name: str
    impacted_nodes: list[ImpactedNode] = field(default_factory=list)
    total_affected: int = 0
    high_severity_count: int = 0
    medium_severity_count: int = 0
    low_severity_count: int = 0