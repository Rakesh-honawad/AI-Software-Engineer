"""
Impact analysis — the domain-specific layer on top of the pure graph
traversal. This is where "how risky is this" gets decided; the
traversal module only knows "what's reachable."

HARD RULE: this module never imports from ai/ and never calls a
model. It produces ImpactAnalysisResult — structured data only.
narration/ is the (separate, not-yet-built) module that turns this
into plain English.

SCORING (documented so the weights aren't a black box):
  risk_score = distance_component + fan_in_component + untested_penalty
  - distance_component: DISTANCE_WEIGHT / distance — closer dependents
    score higher; a distance-2 node is worth half a distance-1 node.
  - fan_in_component: FAN_IN_WEIGHT * min(fan_in, FAN_IN_CAP) / FAN_IN_CAP
    — capped so one extremely-connected node doesn't dominate the scale.
  - untested_penalty: added in full if no Test node has a `tests` edge
    to this node, zero if it does — coverage meaningfully lowers risk.
  Result is clamped to [0.0, 1.0] and bucketed at HIGH_THRESHOLD /
  MEDIUM_THRESHOLD. These are starting weights, not tuned against real
  data yet — expect to revisit once this runs against a real repo.
"""

from __future__ import annotations

from graph.schema.edge_types import EdgeType, GraphEdge
from graph.schema.node_types import GraphNode

from contracts.analysis.impact_result import ImpactAnalysisResult, ImpactedNode, Severity
from graph.algorithms.impact.traversal import DEPENDS_ON_EDGE_TYPES, reverse_impact_traversal

DISTANCE_WEIGHT = 0.4
FAN_IN_WEIGHT = 0.3
FAN_IN_CAP = 10
UNTESTED_PENALTY = 0.3

HIGH_THRESHOLD = 0.66
MEDIUM_THRESHOLD = 0.33


def _compute_fan_in(node_id: str, edges: list[GraphEdge]) -> int:
    return sum(1 for e in edges if e.to_id == node_id and e.type in DEPENDS_ON_EDGE_TYPES)


def _is_tested(node_id: str, edges: list[GraphEdge]) -> bool:
    return any(e.to_id == node_id and e.type == EdgeType.TESTS for e in edges)


def _severity_bucket(score: float) -> Severity:
    if score >= HIGH_THRESHOLD:
        return Severity.HIGH
    if score >= MEDIUM_THRESHOLD:
        return Severity.MEDIUM
    return Severity.LOW


def analyze_impact(
    changed_node_id: str,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> ImpactAnalysisResult:
    """The entry point: 'what breaks if I change this node?'

    Raises ValueError if changed_node_id isn't in `nodes` — callers
    should look the node up (e.g. by name via query/) before calling
    this, so a typo surfaces immediately rather than returning an
    empty, misleadingly-successful result.
    """
    node_by_id = {n.id: n for n in nodes}
    changed_node = node_by_id.get(changed_node_id)
    if changed_node is None:
        raise ValueError(f"changed_node_id not found in graph: {changed_node_id}")

    distances = reverse_impact_traversal(changed_node_id, edges)

    impacted_nodes: list[ImpactedNode] = []
    for node_id, distance in distances.items():
        node = node_by_id.get(node_id)
        if node is None or node.is_deleted:
            continue

        fan_in = _compute_fan_in(node_id, edges)
        tested = _is_tested(node_id, edges)

        distance_component = DISTANCE_WEIGHT / distance
        fan_in_component = FAN_IN_WEIGHT * min(fan_in, FAN_IN_CAP) / FAN_IN_CAP
        untested_penalty = 0.0 if tested else UNTESTED_PENALTY
        risk_score = min(1.0, distance_component + fan_in_component + untested_penalty)

        impacted_nodes.append(ImpactedNode(
            node_id=node_id,
            node_type=node.type,
            name=node.name,
            distance=distance,
            fan_in=fan_in,
            is_tested=tested,
            severity=_severity_bucket(risk_score),
            risk_score=round(risk_score, 3),
        ))

    impacted_nodes.sort(key=lambda n: n.risk_score, reverse=True)

    return ImpactAnalysisResult(
        changed_node_id=changed_node_id,
        changed_node_name=changed_node.name,
        impacted_nodes=impacted_nodes,
        total_affected=len(impacted_nodes),
        high_severity_count=sum(1 for n in impacted_nodes if n.severity == Severity.HIGH),
        medium_severity_count=sum(1 for n in impacted_nodes if n.severity == Severity.MEDIUM),
        low_severity_count=sum(1 for n in impacted_nodes if n.severity == Severity.LOW),
    )