from __future__ import annotations

from pydantic import BaseModel


class DeadCodeFindingResponse(BaseModel):
    node_id: str
    node_type: str
    name: str
    file_path: str | None
    possible_entry_point: bool
    fan_in_from_other_dead_code: int


class DeadCodeAnalysisResponse(BaseModel):
    repository_id: str
    total_candidates: int
    total_reachable: int
    findings: list[DeadCodeFindingResponse]
    caveat: str