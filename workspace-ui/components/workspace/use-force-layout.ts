import { useMemo } from "react"
import type { GraphNode, GraphEdge } from "@/lib/graph/data"
import type { Point } from "@/lib/graph/geometry"

export interface LayoutResult {
  positions: Record<string, Point>
  bounds: { minX: number; minY: number; maxX: number; maxY: number }
}

/**
 * Force-directed layout with grid-bucketed repulsion so it stays fast
 * for large graphs (500+ nodes). Positions are a pure derivation of
 * (nodes, edges), so we memoize rather than sync via an effect.
 *
 * The canvas footprint grows with node count so labels get room to
 * breathe instead of piling up — key for readable large projects.
 */
export function useForceLayout(nodes: GraphNode[], edges: GraphEdge[]): LayoutResult {
  return useMemo(() => {
    const n = nodes.length
    if (n === 0) {
      return { positions: {}, bounds: { minX: 0, minY: 0, maxX: 1000, maxY: 700 } }
    }

    // Scale the world with node count. Kept compact so the initial
    // fit-to-view lands at a readable zoom instead of a dot field.
    const span = Math.max(760, Math.sqrt(n) * 78)
    const width = span
    const height = span * 0.78
    const iterations = n > 300 ? 140 : n > 150 ? 220 : 320

    const pos: Record<string, Point> = {}
    const vel: Record<string, Point> = {}
    const index = new Map<string, number>()

    nodes.forEach((node, i) => {
      index.set(node.id, i)
      const angle = (i / n) * 2 * Math.PI * 4
      const radius = (i / n) * Math.min(width, height) * 0.45
      pos[node.id] = {
        x: width / 2 + radius * Math.cos(angle),
        y: height / 2 + radius * Math.sin(angle),
      }
      vel[node.id] = { x: 0, y: 0 }
    })

        // Repulsion/spacing must scale with node count -- fixed constants
    // caused large graphs (2000+ nodes) to collapse into a dense center
    // blob, since edge-spring attraction grows with graph size but a
    // fixed repulsion doesn't keep pace. Scaling factor is sqrt(n) so
    // it grows sublinearly (avoids making small graphs too sparse while
    // still meaningfully spreading out large ones).
    const scaleFactor = Math.max(1, Math.sqrt(n / 200))
    const REPULSION = 4200 * scaleFactor
    const ATTRACTION = 0.011
    const CENTER_PULL = 0.01
    const DAMPING = 0.86
    const MIN_DIST = 54 * Math.max(1, scaleFactor * 0.6)
    const cellSize = 120 * Math.max(1, scaleFactor * 0.5)

    const ids = nodes.map((node) => node.id)

    for (let iter = 0; iter < iterations; iter++) {
      // Bucket nodes into a spatial grid; only repel within a 3x3 cell window.
      const grid = new Map<string, string[]>()
      for (const id of ids) {
        const cx = Math.floor(pos[id].x / cellSize)
        const cy = Math.floor(pos[id].y / cellSize)
        const key = `${cx},${cy}`
        if (!grid.has(key)) grid.set(key, [])
        grid.get(key)!.push(id)
      }

      for (const id of ids) {
        const cx = Math.floor(pos[id].x / cellSize)
        const cy = Math.floor(pos[id].y / cellSize)
        for (let gx = cx - 1; gx <= cx + 1; gx++) {
          for (let gy = cy - 1; gy <= cy + 1; gy++) {
            const bucket = grid.get(`${gx},${gy}`)
            if (!bucket) continue
            for (const other of bucket) {
              if (other === id) continue
              let dx = pos[id].x - pos[other].x
              let dy = pos[id].y - pos[other].y
              let distSq = dx * dx + dy * dy
              if (distSq < 0.01) {
                dx = (Math.random() - 0.5) * 2
                dy = (Math.random() - 0.5) * 2
                distSq = 1
              }
              if (distSq > cellSize * cellSize * 4) continue
              const dist = Math.sqrt(distSq)
              const force = REPULSION / distSq
              vel[id].x += (dx / dist) * force
              vel[id].y += (dy / dist) * force
            }
          }
        }
      }

      // Attraction along edges (springs).
      for (const e of edges) {
        const a = pos[e.from_id]
        const b = pos[e.to_id]
        if (!a || !b) continue
        const dx = b.x - a.x
        const dy = b.y - a.y
        vel[e.from_id].x += dx * ATTRACTION
        vel[e.from_id].y += dy * ATTRACTION
        vel[e.to_id].x -= dx * ATTRACTION
        vel[e.to_id].y -= dy * ATTRACTION
      }

      // Gentle pull toward center + integrate.
      for (const id of ids) {
        vel[id].x += (width / 2 - pos[id].x) * CENTER_PULL
        vel[id].y += (height / 2 - pos[id].y) * CENTER_PULL
        vel[id].x *= DAMPING
        vel[id].y *= DAMPING
        pos[id].x += vel[id].x
        pos[id].y += vel[id].y
      }
    }

        // Radial clamp — without this, isolated/low-degree nodes (which
    // only feel REPULSION pushing them away, with nothing but the
    // weak CENTER_PULL reeling them back) can drift arbitrarily far
    // from the main cluster on larger graphs. Since `bounds` below is
    // just min/max over every node's position, a single such outlier
    // stretches the whole world size — collapsing the real, connected
    // cluster into a tiny corner both on the main canvas and in the
    // minimap (its viewport rect becomes sub-pixel at that scale).
    // Clamping keeps every node within a fixed multiple of the
    // intended layout radius, so a handful of stray nodes can't blow
    // up the bounding box for everyone else.
    const maxRadius = Math.min(width, height) * 0.85
    const cx = width / 2
    const cy = height / 2
    for (const id of ids) {
      const dx = pos[id].x - cx
      const dy = pos[id].y - cy
      const dist = Math.sqrt(dx * dx + dy * dy)
      if (dist > maxRadius) {
        const k = maxRadius / dist
        pos[id].x = cx + dx * k
        pos[id].y = cy + dy * k
      }
    }

    // Minimum-distance relaxation pass so labels never overlap neighbors.
    for (let pass = 0; pass < 4; pass++) {
      for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
          const a = pos[ids[i]]
          const b = pos[ids[j]]
          const dx = b.x - a.x
          const dy = b.y - a.y
          const d = Math.sqrt(dx * dx + dy * dy) || 1
          if (d < MIN_DIST) {
            const push = (MIN_DIST - d) / 2
            const ux = (dx / d) * push
            const uy = (dy / d) * push
            a.x -= ux
            a.y -= uy
            b.x += ux
            b.y += uy
          }
        }
      }
      if (n > 350) break // keep the relaxation cheap on huge graphs
    }

    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity
    for (const id of ids) {
      minX = Math.min(minX, pos[id].x)
      minY = Math.min(minY, pos[id].y)
      maxX = Math.max(maxX, pos[id].x)
      maxY = Math.max(maxY, pos[id].y)
    }

    return { positions: pos, bounds: { minX, minY, maxX, maxY } }
  }, [nodes, edges])
}
