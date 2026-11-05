import type { NodeType } from "./data"

export interface Point {
  x: number
  y: number
}

/** Smooth cubic bezier between two node centers, curved along the dominant axis. */
export function edgePath(from: Point, to: Point): string {
  const dx = to.x - from.x
  const dy = to.y - from.y
  const horizontal = Math.abs(dx) > Math.abs(dy)
  if (horizontal) {
    const cx = dx * 0.5
    return `M ${from.x} ${from.y} C ${from.x + cx} ${from.y}, ${to.x - cx} ${to.y}, ${to.x} ${to.y}`
  }
  const cy = dy * 0.5
  return `M ${from.x} ${from.y} C ${from.x} ${from.y + cy}, ${to.x} ${to.y - cy}, ${to.x} ${to.y}`
}

export const NODE_SIZE: Record<NodeType, number> = {
  file: 15,
  class: 13,
  function: 11,
  module: 18,
  service: 16,
  external_dependency: 12,
  feature: 17,
}

/**
 * Returns an SVG path for a node's distinct silhouette:
 *  - file: rounded square (a "document")
 *  - class: hexagon (a structured container)
 *  - function: horizontal pill/capsule (a "call")
 *  - module: octagon (a larger grouping/container)
 *  - service: diamond (a process/API boundary)
 *  - external_dependency: outward-pointing triangle (something outside the repo)
 *  - feature: rounded shield (a bundled, cross-cutting concern)
 * All centered on (0,0) so the node group can be translated freely.
 */
export function nodeShapePath(type: NodeType, r: number): string {
  if (type === "function") {
    const w = r * 1.9
    const h = r * 1.15
    const rad = h / 2
    return `M ${-w + rad} ${-h}
            L ${w - rad} ${-h}
            A ${rad} ${rad} 0 0 1 ${w - rad} ${h}
            L ${-w + rad} ${h}
            A ${rad} ${rad} 0 0 1 ${-w + rad} ${-h} Z`
  }
  if (type === "class") {
    // pointy-top hexagon
    const pts: Point[] = []
    for (let i = 0; i < 6; i++) {
      const a = (Math.PI / 3) * i - Math.PI / 2
      pts.push({ x: r * Math.cos(a), y: r * Math.sin(a) })
    }
    return "M " + pts.map((p) => `${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(" L ") + " Z"
  }
  if (type === "module") {
    // flat-top octagon — reads as a bigger, sturdier container than class's hexagon
    const pts: Point[] = []
    for (let i = 0; i < 8; i++) {
      const a = (Math.PI / 4) * i - Math.PI / 8
      pts.push({ x: r * Math.cos(a), y: r * Math.sin(a) })
    }
    return "M " + pts.map((p) => `${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(" L ") + " Z"
  }
  if (type === "service") {
    // diamond — a boundary/gateway shape, distinct from every other silhouette
    return `M 0 ${-r} L ${r} 0 L 0 ${r} L ${-r} 0 Z`
  }
  if (type === "external_dependency") {
    // outward-pointing triangle — reads as "leaving the repo"
    const h = r * 1.15
    return `M 0 ${-h} L ${r} ${h * 0.7} L ${-r} ${h * 0.7} Z`
  }
  if (type === "feature") {
    // rounded shield — flat top, pointed bottom, for a bundled/cross-cutting concept
    const w = r
    const topY = -r
    const midY = r * 0.15
    const botY = r * 1.05
    return `M ${-w} ${topY + r * 0.3}
            Q ${-w} ${topY} ${-w * 0.5} ${topY}
            L ${w * 0.5} ${topY}
            Q ${w} ${topY} ${w} ${topY + r * 0.3}
            L ${w} ${midY}
            Q ${w} ${botY * 0.6} 0 ${botY}
            Q ${-w} ${botY * 0.6} ${-w} ${midY} Z`
  }
  // file: rounded square
  const s = r
  const rad = r * 0.34
  return `M ${-s + rad} ${-s}
          L ${s - rad} ${-s}
          Q ${s} ${-s} ${s} ${-s + rad}
          L ${s} ${s - rad}
          Q ${s} ${s} ${s - rad} ${s}
          L ${-s + rad} ${s}
          Q ${-s} ${s} ${-s} ${s - rad}
          L ${-s} ${-s + rad}
          Q ${-s} ${-s} ${-s + rad} ${-s} Z`
}

export function clamp(v: number, min: number, max: number) {
  return Math.min(Math.max(v, min), max)
}

export function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t
}