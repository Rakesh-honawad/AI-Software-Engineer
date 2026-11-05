export type NodeType = "function" | "class" | "file" | "service" | "module" | "external_dependency" | "feature"

export interface GraphNode {
  id: string
  name: string
  type: NodeType
  file_path: string
  /** Best-effort, derived client-side from file_path — not a real backend field. See adapters.ts. */
  module: string
  /**
   * Only known once impact analysis has run for this node (it's an
   * ImpactedNode field in the real API, not a raw graph node field).
   * undefined = "we don't know yet", NOT "zero dependents" — render
   * accordingly rather than defaulting to 0.
   */
  fan_in?: number
  /** Same caveat as fan_in — undefined means unknown, not "untested". */
  is_tested?: boolean
}

export interface GraphEdge {
  from_id: string
  to_id: string
  /** Real edge type from the schema (calls/inherits/etc). Optional so dev-mock data without types still typechecks. */
  type?: string
}

export type Severity = "high" | "medium" | "low"

export interface ImpactedNode {
  node_id: string
  severity: Severity
  distance: number
  fan_in: number
  is_tested: boolean
}

export interface ImpactData {
  changed_node_id: string
  total_affected: number
  high_severity_count: number
  medium_severity_count: number
  low_severity_count: number
  narration: string
  impacted_nodes: ImpactedNode[]
}

export interface Graph {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

/* Deterministic PRNG so the generated repo is stable between renders. */
function mulberry32(seed: number) {
  return function () {
    seed |= 0
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const MODULES = [
  { name: "auth", dir: "src/auth" },
  { name: "api", dir: "src/api" },
  { name: "db", dir: "src/db" },
  { name: "payments", dir: "src/payments" },
  { name: "ui", dir: "src/ui" },
  { name: "core", dir: "src/core" },
  { name: "utils", dir: "src/lib" },
  { name: "queue", dir: "src/queue" },
  { name: "search", dir: "src/search" },
  { name: "notifications", dir: "src/notify" },
]

const FUNCTION_NAMES = [
  "resolve",
  "validate",
  "serialize",
  "hydrate",
  "dispatch",
  "normalize",
  "encode",
  "parse",
  "connect",
  "commit",
  "handle",
  "transform",
  "compute",
  "reconcile",
  "flush",
]

const CLASS_NAMES = [
  "Session",
  "Client",
  "Store",
  "Manager",
  "Registry",
  "Adapter",
  "Gateway",
  "Scheduler",
  "Cache",
  "Pool",
  "Builder",
  "Resolver",
]

const FILE_NAMES = [
  "index",
  "handler",
  "service",
  "model",
  "router",
  "middleware",
  "config",
  "client",
  "worker",
  "schema",
]

/**
 * DEV/DEMO MOCK ONLY. Builds a synthetic-but-plausible repository
 * dependency graph so the UI is explorable without a live backend.
 * `scale` roughly controls node count — the default (~150 nodes)
 * reads cleanly; pass a larger value to stress-test 500+ nodes.
 * Real data comes from services/api-client.ts + lib/graph/adapters.ts
 * instead — see engineer-workspace.tsx's `useRepositoryGraph` for the
 * live-vs-mock switch.
 */
export function generateGraph(scale = 1): Graph {
  const rand = mulberry32(1337)
  const nodes: GraphNode[] = []
  const edges: GraphEdge[] = []
  const filesByModule: Record<string, string[]> = {}

  const filesPerModule = Math.round(4 * scale)
  const symbolsPerFile = 3

  MODULES.forEach((mod, mi) => {
    filesByModule[mod.name] = []
    for (let f = 0; f < filesPerModule; f++) {
      const fileName = FILE_NAMES[(f + mi) % FILE_NAMES.length]
      const filePath = `${mod.dir}/${fileName}${f > FILE_NAMES.length - 1 ? f : ""}.ts`
      const fileId = `${mod.name}:file:${f}`
      nodes.push({
        id: fileId,
        name: `${fileName}.ts`,
        type: "file",
        file_path: filePath,
        module: mod.name,
        fan_in: 0,
        is_tested: rand() > 0.4,
      })
      filesByModule[mod.name].push(fileId)

      for (let s = 0; s < symbolsPerFile; s++) {
        const isClass = rand() > 0.62
        const symId = `${mod.name}:sym:${f}:${s}`
        const base = isClass
          ? CLASS_NAMES[Math.floor(rand() * CLASS_NAMES.length)]
          : FUNCTION_NAMES[Math.floor(rand() * FUNCTION_NAMES.length)]
        const name = isClass ? `${base}${mod.name[0].toUpperCase()}${mod.name.slice(1)}` : `${base}${base.length % 3 === 0 ? "Item" : ""}`
        nodes.push({
          id: symId,
          name: isClass ? base + mod.name.charAt(0).toUpperCase() + mod.name.slice(1, 3) : `${base}()`,
          type: isClass ? "class" : "function",
          file_path: filePath,
          module: mod.name,
          fan_in: 0,
          is_tested: rand() > 0.45,
        })
        // symbol belongs to its file
        edges.push({ from_id: fileId, to_id: symId })
      }
    }
  })

  // Cross-module + intra-module dependency edges between symbols.
  const symbolNodes = nodes.filter((n) => n.type !== "file")
  symbolNodes.forEach((n) => {
    const deps = Math.floor(rand() * 3)
    for (let d = 0; d < deps; d++) {
      const target = symbolNodes[Math.floor(rand() * symbolNodes.length)]
      if (target.id !== n.id) {
        edges.push({ from_id: n.id, to_id: target.id })
      }
    }
  })

  // Compute fan-in (how many things depend on each node).
  const fanIn: Record<string, number> = {}
  edges.forEach((e) => {
    fanIn[e.to_id] = (fanIn[e.to_id] || 0) + 1
  })
  nodes.forEach((n) => {
    n.fan_in = fanIn[n.id] || 0
  })

  return { nodes, edges }
}

/**
 * Computes the blast radius of changing `changedNodeId`: every node
 * that (transitively) depends on it, ranked by severity. Severity is
 * driven by distance from the change, fan-in, and test coverage —
 * the same signals the inspector surfaces.
 */
export function computeImpact(graph: Graph, changedNodeId: string): ImpactData {
  const { nodes, edges } = graph
  const nodeById = new Map(nodes.map((n) => [n.id, n]))

  // reverse adjacency: who depends on X
  const dependents = new Map<string, string[]>()
  edges.forEach((e) => {
    if (!dependents.has(e.to_id)) dependents.set(e.to_id, [])
    dependents.get(e.to_id)!.push(e.from_id)
  })

  const distance = new Map<string, number>()
  distance.set(changedNodeId, 0)
  const queue = [changedNodeId]
  while (queue.length) {
    const cur = queue.shift()!
    const d = distance.get(cur)!
    for (const dep of dependents.get(cur) || []) {
      if (!distance.has(dep)) {
        distance.set(dep, d + 1)
        queue.push(dep)
      }
    }
  }

  const impacted: ImpactedNode[] = []
  for (const [id, dist] of distance) {
    if (id === changedNodeId) continue
    const node = nodeById.get(id)
    if (!node) continue
    const fanIn = node.fan_in ?? 0
    const isTested = node.is_tested ?? false
    const severity = severityFor(dist, fanIn, isTested)
    impacted.push({
      node_id: id,
      severity,
      distance: dist,
      fan_in: fanIn,
      is_tested: isTested,
    })
  }

  impacted.sort((a, b) => a.distance - b.distance || b.fan_in - a.fan_in)

  const high = impacted.filter((n) => n.severity === "high").length
  const medium = impacted.filter((n) => n.severity === "medium").length
  const low = impacted.filter((n) => n.severity === "low").length
  const changed = nodeById.get(changedNodeId)

  return {
    changed_node_id: changedNodeId,
    total_affected: impacted.length,
    high_severity_count: high,
    medium_severity_count: medium,
    low_severity_count: low,
    narration: buildNarration(changed, impacted.length, high, medium, low),
    impacted_nodes: impacted,
  }
}

function severityFor(distance: number, fanIn: number, isTested: boolean): Severity {
  let score = 0
  if (distance <= 1) score += 3
  else if (distance === 2) score += 2
  else score += 1
  if (fanIn >= 4) score += 2
  else if (fanIn >= 2) score += 1
  if (!isTested) score += 1
  if (score >= 5) return "high"
  if (score >= 3) return "medium"
  return "low"
}

function buildNarration(
  node: GraphNode | undefined,
  total: number,
  high: number,
  medium: number,
  low: number,
): string {
  if (!node) return "Select a node to analyze its impact."
  if (total === 0) {
    return `Changing ${node.name} looks safe — nothing else in the indexed graph depends on it directly.`
  }
  const risk = high > 0 ? "notable" : medium > 0 ? "moderate" : "low"
  return `Changing ${node.name} touches ${total} downstream ${
    total === 1 ? "symbol" : "symbols"
  } with ${risk} risk. ${high} high, ${medium} medium, and ${low} low-severity dependents would need review${
    high > 0 ? " — start with the high-severity, untested paths." : "."
  }`
}