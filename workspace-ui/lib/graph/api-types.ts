/**
 * Types matching knowledge-engine's REAL API contracts, per:
 *   docs/schema/api_v1.md
 *   docs/schema/impact_analysis_v1.md
 *   docs/schema/graph_schema_v1.md
 *
 * These are intentionally separate from lib/graph/data.ts's UI-facing
 * types (GraphNode/GraphEdge/ImpactData) — same separation-of-concerns
 * reasoning CLAUDE.md uses for api/schemas vs storage internal types:
 * the wire format and the UI's display shape change for different
 * reasons. adapters.ts is the only file that should import from both.
 */

// ---- GET /repositories/{id}/graph ----

/**
 * NOTE: confirmed live against a real /graph response that the wire
 * format is PascalCase ("File", not "file") — docs never showed
 * literal casing. Kept as `string` here rather than a strict union
 * so an unexpected value doesn't fail to typecheck; adapters.ts does
 * the actual case-insensitive normalization to the UI's NodeType.
 */
export type ApiNodeType = string

export interface ApiGraphNode {
  id: string
  type: ApiNodeType
  name: string
  file_path?: string
  // Provenance fields every node carries per CLAUDE.md's rules —
  // not currently used by the UI, kept here for completeness /
  // future use (e.g. showing confidence on inferred Feature nodes).
  source?: "AST" | "Detected" | "AI" | "User"
  confidence?: number
  is_deleted?: boolean
}

/**
 * Edge type carries real semantic meaning in your schema — calls/
 * inherits/implements/reads/writes are "depends on" edges impact
 * analysis traverses; "contains" is structural and deliberately
 * excluded from traversal (impact_analysis_v1.md). The UI should
 * eventually render these differently; right now graph-canvas.tsx
 * treats every edge identically. Kept as `string` (not a strict
 * union) for the same live-casing-mismatch reason as ApiNodeType above.
 */
export type ApiEdgeType = string

export interface ApiGraphEdge {
  from_id: string
  to_id: string
  type: ApiEdgeType
  source?: "AST" | "Detected" | "AI" | "User"
  confidence?: number
}

export interface ApiGraphResponse {
  nodes: ApiGraphNode[]
  edges: ApiGraphEdge[]
}

// ---- POST /repositories/{id}/impact ----
// Structured only, NO narration — analysis/ never imports ai/.

export type ApiSeverity = "high" | "medium" | "low"

export interface ApiImpactedNode {
  node_id: string
  node_type: ApiNodeType
  name: string
  distance: number
  fan_in: number
  is_tested: boolean
  severity: ApiSeverity
  risk_score: number
}

export interface ApiImpactAnalysisResult {
  changed_node_id: string
  changed_node_name: string
  impacted_nodes: ApiImpactedNode[]
  high_severity_count: number
  medium_severity_count: number
  low_severity_count: number
}

// ---- POST /repositories/{id}/impact/narrate ----
// Same structured result, plus narration from the AI router.

export interface ApiImpactNarrateResult extends ApiImpactAnalysisResult {
  narration: string
}

// ---- GET /repositories/{id} ----

export type ApiIndexStatus = "Pending" | "Indexing" | "Ready" | "Stale" | "Failed"

export interface ApiRepositoryStatus {
  repository_id: string
  index_status: ApiIndexStatus
  name?: string
}

// ---- GET /repositories/{id}/dead-code ----

export interface ApiDeadCodeFinding {
  node_id: string
  node_type: string
  name: string
  file_path: string | null
  possible_entry_point: boolean
  fan_in_from_other_dead_code: number
}

export interface ApiDeadCodeResult {
  repository_id: string
  total_candidates: number
  total_reachable: number
  findings: ApiDeadCodeFinding[]
  caveat: string
}

export interface ApiDeadCodeExplainResponse {
  node_id: string
  name: string
  file_path: string | null
  explanation: string
}

// ---- Error shapes the API returns (api_v1.md's translation table) ----

export interface ApiAmbiguousSymbolError {
  detail: {
    message: string
    candidates: { node_id: string; file_path: string; name: string }[]
  }
}