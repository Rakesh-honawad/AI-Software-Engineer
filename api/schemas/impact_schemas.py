from __future__ import annotations

from pydantic import BaseModel


class ImpactAnalysisRequest(BaseModel):
    symbol_name: str
    node_type: str | None = None  # e.g. "Function" - narrows resolve_symbol if ambiguous


class ImpactedNodeResponse(BaseModel):
    node_id: str
    node_type: str
    name: str
    distance: int
    fan_in: int
    is_tested: bool
    severity: str
    risk_score: float


class ImpactAnalysisResponse(BaseModel):
    changed_node_id: str
    changed_node_name: str
    impacted_nodes: list[ImpactedNodeResponse]
    total_affected: int
    high_severity_count: int
    medium_severity_count: int
    low_severity_count: int
    narration: str | None = None  # populated only by the /narrate variant