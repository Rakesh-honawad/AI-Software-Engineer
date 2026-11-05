from __future__ import annotations

from pydantic import BaseModel


class DeadCodeExplainRequest(BaseModel):
    node_id: str


class DeadCodeExplainResponse(BaseModel):
    node_id: str
    name: str
    file_path: str | None
    explanation: str