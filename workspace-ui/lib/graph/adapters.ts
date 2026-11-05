import type { ApiGraphEdge, ApiGraphNode, ApiGraphResponse, ApiImpactAnalysisResult, ApiImpactNarrateResult } from "./api-types"
import type { Graph, GraphEdge, GraphNode, ImpactData, NodeType } from "./data"

/**
 * Maps the real backend's node type to the UI's NodeType.
 * CONFIRMED LIVE (2026-07-18 against repo:miniai-backend): the wire
 * format is PascalCase ("File", not "file") — the docs never showed
 * literal casing, so this was originally guessed wrong as lowercase.
 * Normalizing case-insensitively here rather than trusting exact
 * casing, so any other casing surprise (e.g. "EXTERNAL_DEPENDENCY")
 * degrades to the fallback below instead of breaking node rendering
 * (undefined size/color/shape) silently.
 */
function toDisplayNodeType(apiType: string): NodeType {
  const normalized = apiType.toLowerCase().replace(/[_\s]/g, "")
  const known: Record<string, NodeType> = {
    file: "file",
    function: "function",
    method: "function",
    class: "class",
    module: "module",
    service: "service",
    externaldependency: "external_dependency",
    feature: "feature",
  }
  return known[normalized] ?? "class"
}

/**
 * Best-effort "module" label derived client-side from file_path,
 * since the real GraphNode has no `module` field (that's a Profile-
 * level / Tier-1 concept, not a graph node property per
 * repository_profile_schema_v1.md). Takes the first path segment
 * after a common src/app root as a readable grouping label.
 */
function deriveModuleLabel(filePath: string | undefined): string {
  if (!filePath) return "—"
  const parts = filePath.split("/").filter(Boolean)
  const rootIdx = parts.findIndex((p) => p === "src" || p === "app" || p === "lib")
  const seg = rootIdx >= 0 ? parts[rootIdx + 1] : parts[0]
  return seg ?? "—"
}

/**
 * Maps the real /graph response into the UI's GraphNode/GraphEdge
 * shape. fan_in and is_tested are NOT available here — the real
 * schema only fills those in as part of an ImpactedNode, computed
 * during impact analysis (impact_analysis_v1.md), not stored on the
 * raw node. They're left undefined; components must handle that
 * (see node-inspector.tsx's fallback rendering) rather than assuming
 * a default of 0/false, which would silently lie about test coverage.
 */
export function adaptGraph(api: ApiGraphResponse): Graph {
  const nodes: GraphNode[] = api.nodes
    .filter((n) => !n.is_deleted)
    .map((n) => ({
      id: n.id,
      name: n.name,
      type: toDisplayNodeType(n.type),
      file_path: n.file_path ?? "",
      module: deriveModuleLabel(n.file_path),
      fan_in: undefined,
      is_tested: undefined,
    }))

  // "contains" edges are structural, not "depends on" — impact
  // analysis excludes them from traversal (impact_analysis_v1.md).
  // The canvas still draws them, but visually distinct (thin, dashed,
  // no pulse) rather than dropping them entirely — see graph-canvas.tsx.
  // Normalize casing here (confirmed nodes come back PascalCase live;
  // assume edges may too) so every downstream comparison — graph-canvas.tsx's
  // `edge.type === "contains"` check included — can rely on lowercase.
  const edges: GraphEdge[] = api.edges.map((e) => ({
    from_id: e.from_id,
    to_id: e.to_id,
    type: e.type?.toLowerCase(),
  }))

  return { nodes, edges }
}

/**
 * Maps a structured-only /impact response (no narration) to the
 * UI's ImpactData. narration is left as an empty string — the
 * caller (engineer-workspace.tsx) is responsible for a SEPARATE
 * request to /impact/narrate and merging it in via
 * mergeNarration() below, matching the real API's two-endpoint
 * split (analysis/ never imports ai/).
 */
export function adaptImpact(api: ApiImpactAnalysisResult): ImpactData {
  return {
    changed_node_id: api.changed_node_id,
    total_affected: api.impacted_nodes.length,
    high_severity_count: api.high_severity_count,
    medium_severity_count: api.medium_severity_count,
    low_severity_count: api.low_severity_count,
    narration: "",
    impacted_nodes: api.impacted_nodes.map((n) => ({
      node_id: n.node_id,
      severity: n.severity,
      distance: n.distance,
      fan_in: n.fan_in,
      is_tested: n.is_tested,
    })),
  }
}

/** Same as adaptImpact, but for the /impact/narrate response which includes narration inline. */
export function adaptImpactWithNarration(api: ApiImpactNarrateResult): ImpactData {
  return { ...adaptImpact(api), narration: api.narration }
}

/** Merges a narration string (fetched separately or arriving later) into existing ImpactData without a full re-map. */
export function mergeNarration(data: ImpactData, narration: string): ImpactData {
  return { ...data, narration }
}