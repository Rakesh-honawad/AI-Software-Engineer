"""
Repository Profile â€” Tier 2 backfill.

Fills every Tier 2 field defined in types.py: key_service_ids,
important_module_ids, and the graph-derived RepositoryStats counts
(functions, classes, services, tests, graph_nodes, graph_edges). Tier 1
fields (language, frameworks, VCS, capabilities, ...) are untouched
here â€” this pass only runs AFTER generate_tier1_profile() and AFTER
the graph builder has produced nodes/edges for the repository.

DESIGN NOTE â€” dependency-free ranking:
"Important module" and "key service" ranking uses plain in/out-degree
counts computed directly from the edge list, not a graph library
(NetworkX/Kuzu) centrality algorithm. This is intentional for now: it's
the same ranking signal (how connected is this node) with zero new
dependencies, and it's easy to verify by hand. If a real graph store
lands and richer centrality (betweenness, PageRank) turns out to rank
noticeably better in practice, swap the ranking function only â€” the
rest of this module (counting, index_status transition) is unaffected.

DESIGN NOTE â€” this takes nodes/edges as plain lists, not a graph store
handle. The graph builder and graph/storage/ don't exist yet. This
keeps Tier 2 backfill testable and correct today against any list of
GraphNode/GraphEdge that matches the locked schema, and requires zero
changes when a real store replaces "a list in memory."
"""

from __future__ import annotations

from collections import Counter

from graph.schema.edge_types import GraphEdge
from graph.schema.node_types import GraphNode, NodeType

from .types import IndexStatus, RepositoryProfile, RepositoryStats

# How many ids to keep for each ranked list. Kept as module constants
# (not magic numbers inline) so they're easy to find and tune later.
DEFAULT_TOP_N_SERVICES = 10
DEFAULT_TOP_N_MODULES = 10


def _compute_degree_counts(nodes: list[GraphNode], edges: list[GraphEdge]) -> dict[str, int]:
    """Returns {node_id: total_degree} where total_degree = in-degree +
    out-degree. Only counts edges where BOTH endpoints are live (not
    is_deleted) nodes in this graph snapshot, so soft-deleted nodes
    from incremental reindexing don't skew rankings.
    """
    live_ids = {n.id for n in nodes if not n.is_deleted}
    degree: dict[str, int] = Counter()
    for edge in edges:
        if edge.from_id in live_ids and edge.to_id in live_ids:
            degree[edge.from_id] += 1
            degree[edge.to_id] += 1
    return degree


def _rank_node_ids_by_degree(
    nodes: list[GraphNode],
    degree: dict[str, int],
    node_type: NodeType,
    top_n: int,
) -> list[str]:
    candidates = [n for n in nodes if n.type == node_type and not n.is_deleted]
    candidates.sort(key=lambda n: degree.get(n.id, 0), reverse=True)
    return [n.id for n in candidates[:top_n]]


def _compute_graph_stats(nodes: list[GraphNode], edges: list[GraphEdge]) -> RepositoryStats:
    live_nodes = [n for n in nodes if not n.is_deleted]
    live_ids = {n.id for n in live_nodes}
    live_edges = [e for e in edges if e.from_id in live_ids and e.to_id in live_ids]
    counts = Counter(n.type for n in live_nodes)
    return RepositoryStats(
        functions=counts.get(NodeType.FUNCTION, 0),
        classes=counts.get(NodeType.CLASS, 0),
        services=counts.get(NodeType.SERVICE, 0),
        tests=counts.get(NodeType.TEST, 0),
        graph_nodes=len(live_nodes),
        graph_edges=len(live_edges),
    )


def backfill_tier2_profile(
    profile: RepositoryProfile,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    top_n_services: int = DEFAULT_TOP_N_SERVICES,
    top_n_modules: int = DEFAULT_TOP_N_MODULES,
) -> RepositoryProfile:
    """Mutates and returns `profile` with every Tier 2 field filled from
    the given graph snapshot. `profile` should already have Tier 1
    fields filled by generate_tier1_profile() â€” this does not re-run
    or overwrite any Tier 1 field.

    VCS metadata is optional. Git-backed repositories retain commit information. Non-Git repositories can still receive graph-derived statistics and rankings.
    """
    degree = _compute_degree_counts(nodes, edges)

    profile.key_service_ids = _rank_node_ids_by_degree(
        nodes, degree, NodeType.SERVICE, top_n_services
    )
    profile.important_module_ids = _rank_node_ids_by_degree(
        nodes, degree, NodeType.MODULE, top_n_modules
    )

    # Preserve Tier 1 stats (files, lines_of_code) already on profile.stats,
    # only overwrite the Tier 2 (graph-derived) fields.
    graph_stats = _compute_graph_stats(nodes, edges)
    profile.stats.functions = graph_stats.functions
    profile.stats.classes = graph_stats.classes
    profile.stats.services = graph_stats.services
    profile.stats.tests = graph_stats.tests
    profile.stats.graph_nodes = graph_stats.graph_nodes
    profile.stats.graph_edges = graph_stats.graph_edges

    profile.completeness = _compute_combined_completeness(profile)

    # Only advance INDEXING -> READY. Never override STALE/FAILED, and
    # never regress READY back to INDEXING here â€” reindexing owns that
    # transition, not backfill.
    if profile.index_status == IndexStatus.INDEXING:
        profile.index_status = IndexStatus.READY

    return profile


# Tier 2 fields checked for the completeness calculation, mirroring
# _TIER1_COMPLETENESS_CHECKS in tier1_generator.py. Inferred fields
# (architecture_pattern, domain_vocabulary, summary) are deliberately
# excluded from both tiers' completeness â€” they're optional enrichment,
# not required for a profile to be usable.
_TIER2_COMPLETENESS_CHECKS = ("key_service_ids", "important_module_ids")


def _compute_combined_completeness(profile: RepositoryProfile) -> float:
    from .tier1_generator import _TIER1_COMPLETENESS_CHECKS

    all_checks = _TIER1_COMPLETENESS_CHECKS + _TIER2_COMPLETENESS_CHECKS
    filled = 0
    for field_name in all_checks:
        value = getattr(profile, field_name)
        if value not in (None, "", [], {}, 0):
            filled += 1
    return round(filled / len(all_checks), 3)




