from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GraphNodeResponse(BaseModel):
    id: str
    type: str
    name: str
    file_path: str | None
    line_range: list[int] | None
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdgeResponse(BaseModel):
    from_id: str
    to_id: str
    type: str
    confidence: float


class GraphResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
