"use client"

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react"
import { Minus, Plus, Maximize2, LocateFixed } from "lucide-react"
import type { GraphEdge, GraphNode, ImpactData, Severity } from "@/lib/graph/data"
import type { LayoutResult } from "./use-force-layout"
import { clamp, edgePath, lerp, NODE_SIZE, nodeShapePath, type Point } from "@/lib/graph/geometry"

export interface GraphCanvasHandle {
  zoomIn: () => void
  zoomOut: () => void
  fit: () => void
  focusNode: (id: string) => void
  resetLayout: () => void
}

interface GraphCanvasProps {
  nodes: GraphNode[]
  edges: GraphEdge[]
  layout: LayoutResult
  impactData: ImpactData | null
  /**
   * Node ids flagged by GET /repositories/{id}/dead-code. Mutually
   * exclusive with impactData visually — both are "highlight mode"
   * overlays on the same canvas, so if impact mode is active this is
   * ignored rather than fighting it for the same node's stroke/fill.
   */
  deadNodeIds: Set<string> | null
  selectedNodeId: string | null
  searchQuery: string
  onSelectNode: (id: string) => void
  onOpenMenu: (node: GraphNode, screen: Point) => void
  onCloseMenu: () => void
}

const SEVERITY_VAR: Record<Severity, string> = {
  high: "var(--severity-high)",
  medium: "var(--severity-medium)",
  low: "var(--severity-low)",
}

const TYPE_STROKE: Record<GraphNode["type"], string> = {
  function: "var(--type-function)",
  class: "var(--type-class)",
  file: "var(--type-file)",
  module: "var(--type-module)",
  service: "var(--type-service)",
  external_dependency: "var(--type-external)",
  feature: "var(--type-feature)",
}

const PADDING = 70
const MIN_K = 0.12
const MAX_K = 2.6

export const GraphCanvas = forwardRef<GraphCanvasHandle, GraphCanvasProps>(function GraphCanvas(
  { nodes, edges, layout, impactData, deadNodeIds, selectedNodeId, searchQuery, onSelectNode, onOpenMenu, onCloseMenu },
  ref,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const gRef = useRef<SVGGElement>(null)
  const zoomLabelRef = useRef<HTMLSpanElement>(null)
  const miniRectRef = useRef<SVGRectElement>(null)

  const [size, setSize] = useState({ w: 1200, h: 800 })
  const sizeRef = useRef(size)
  const [, forceRender] = useState(0)

  const miniWrapRef = useRef<HTMLDivElement>(null)
  // Actual rendered CSS width of the minimap, not the old hardcoded
  // 178 — CSS media queries shrink .em-minimap at different
  // breakpoints, so a fixed constant drifted out of sync with the
  // real element, causing both wrong visual sizing and wrong
  // pointer-to-world math in onMiniPointerDown (which reads the REAL
  // rendered size via getBoundingClientRect but was dividing by a
  // scale computed against the fake 178 constant).
  const [miniWidth, setMiniWidth] = useState(178)

  // Live, mutable node positions (base = force layout; user drags mutate this).
  const livePos = useRef<Record<string, Point>>({})
  const vp = useRef({ x: 0, y: 0, k: 1 })
  const animRef = useRef(0)
  const inertiaRef = useRef(0)

  // Element registries for imperative drag updates.
  const nodeElRef = useRef(new Map<string, SVGGElement>())
  const edgeMainRef = useRef(new Map<number, SVGPathElement>())
  const edgeFlowRef = useRef(new Map<number, SVGPathElement>())

  const panState = useRef<any>(null)
  const nodeDrag = useRef<any>(null)

  const ambientFlow = edges.length <= 220

  // adjacency: node id -> indices of edges touching it
  const adjacency = useMemo(() => {
    const m = new Map<string, number[]>()
    edges.forEach((e, i) => {
      if (!m.has(e.from_id)) m.set(e.from_id, [])
      if (!m.has(e.to_id)) m.set(e.to_id, [])
      m.get(e.from_id)!.push(i)
      m.get(e.to_id)!.push(i)
    })
    return m
  }, [edges])

  const paddedBounds = useMemo(() => {
    const b = layout.bounds
    return {
      minX: b.minX - PADDING,
      minY: b.minY - PADDING,
      maxX: b.maxX + PADDING,
      maxY: b.maxY + PADDING,
    }
  }, [layout.bounds])

  // Initialize / reset live positions whenever the layout changes.
  useLayoutEffect(() => {
    const next: Record<string, Point> = {}
    for (const id in layout.positions) next[id] = { ...layout.positions[id] }
    livePos.current = next
    forceRender((n) => n + 1)
  }, [layout])

  /// Minimap projection
  const mini = useMemo(() => {
    const worldW = paddedBounds.maxX - paddedBounds.minX || 1
    const worldH = paddedBounds.maxY - paddedBounds.minY || 1
    const mmW = miniWidth
    const mmH = clamp(mmW * (worldH / worldW), 80, 150)
    const scale = Math.min(mmW / worldW, mmH / worldH)
    return { worldW, worldH, mmW, mmH, scale }
  }, [paddedBounds, miniWidth])

  const applyTransform = useCallback(() => {
    const { x, y, k } = vp.current
    gRef.current?.setAttribute("transform", `translate(${x} ${y}) scale(${k})`)
    if (zoomLabelRef.current) zoomLabelRef.current.textContent = `${Math.round(k * 100)}%`
    // minimap viewport rect
    const rect = miniRectRef.current
    if (rect) {
      const s = sizeRef.current
      const left = (0 - x) / k
      const top = (0 - y) / k
      const w = s.w / k
      const h = s.h / k
      const mx = (left - paddedBounds.minX) * mini.scale
      const my = (top - paddedBounds.minY) * mini.scale
      rect.setAttribute("x", String(mx))
      rect.setAttribute("y", String(my))
      rect.setAttribute("width", String(Math.max(6, w * mini.scale)))
      rect.setAttribute("height", String(Math.max(6, h * mini.scale)))
    }
  }, [mini.scale, paddedBounds.minX, paddedBounds.minY])

  const animateTo = useCallback(
    (target: { x: number; y: number; k: number }, dur: number) => {
      cancelAnimationFrame(animRef.current)
      cancelAnimationFrame(inertiaRef.current)
      if (dur <= 0) {
        vp.current = { ...target }
        applyTransform()
        return
      }
      const start = { ...vp.current }
      const t0 = performance.now()
      const step = (now: number) => {
        const p = clamp((now - t0) / dur, 0, 1)
        const e = 1 - Math.pow(1 - p, 3)
        vp.current = {
          x: lerp(start.x, target.x, e),
          y: lerp(start.y, target.y, e),
          k: lerp(start.k, target.k, e),
        }
        applyTransform()
        if (p < 1) animRef.current = requestAnimationFrame(step)
      }
      animRef.current = requestAnimationFrame(step)
    },
    [applyTransform],
  )

  const fit = useCallback(
    (animated = true) => {
      const s = sizeRef.current
      const bw = paddedBounds.maxX - paddedBounds.minX
      const bh = paddedBounds.maxY - paddedBounds.minY
      if (bw <= 0 || bh <= 0) return
      // Fit, but never below a readable floor — small/medium graphs
      // should comfortably fill the viewport rather than shrink to dots.
      const k = clamp(Math.min(s.w / bw, s.h / bh) * 1.02, 0.55, MAX_K)
      const cx = (paddedBounds.minX + paddedBounds.maxX) / 2
      const cy = (paddedBounds.minY + paddedBounds.maxY) / 2
      animateTo({ k, x: s.w / 2 - cx * k, y: s.h / 2 - cy * k }, animated ? 480 : 0)
    },
    [animateTo, paddedBounds],
  )

  const zoomAt = useCallback(
    (sx: number, sy: number, factor: number, dur = 130) => {
      const k0 = vp.current.k
      const k = clamp(k0 * factor, MIN_K, MAX_K)
      const wx = (sx - vp.current.x) / k0
      const wy = (sy - vp.current.y) / k0
      animateTo({ k, x: sx - wx * k, y: sy - wy * k }, dur)
    },
    [animateTo],
  )

  const focusNode = useCallback(
    (id: string) => {
      const p = livePos.current[id]
      if (!p) return
      const s = sizeRef.current
      const k = clamp(Math.max(vp.current.k, 1.1), MIN_K, MAX_K)
      animateTo({ k, x: s.w / 2 - p.x * k, y: s.h / 2 - p.y * k }, 520)
    },
    [animateTo],
  )

  useImperativeHandle(
    ref,
    () => ({
      zoomIn: () => zoomAt(sizeRef.current.w / 2, sizeRef.current.h / 2, 1.35, 200),
      zoomOut: () => zoomAt(sizeRef.current.w / 2, sizeRef.current.h / 2, 0.74, 200),
      fit: () => fit(true),
      focusNode,
      resetLayout: () => {
        const next: Record<string, Point> = {}
        for (const id in layout.positions) next[id] = { ...layout.positions[id] }
        livePos.current = next
        forceRender((n) => n + 1)
        fit(true)
      },
    }),
    [zoomAt, fit, focusNode, layout.positions],
  )

  // Minimap resize observer — keeps mini.mmW in sync with the
  // element's actual rendered width instead of a stale hardcoded
  // constant.
  useLayoutEffect(() => {
    const el = miniWrapRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      const w = entries[0].contentRect.width
      if (w > 0) setMiniWidth(Math.round(w))
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Resize observer -> keep viewBox in sync, fit on first measure.
  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    let firstFit = false
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0].contentRect
      const next = { w: Math.max(1, cr.width), h: Math.max(1, cr.height) }
      sizeRef.current = next
      setSize(next)
      if (!firstFit) {
        firstFit = true
        requestAnimationFrame(() => fit(false))
      } else {
        applyTransform()
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [fit, applyTransform])

  // Re-fit when a brand-new layout arrives (new graph / reset).
  useLayoutEffect(() => {
    requestAnimationFrame(() => {
      applyTransform()
    })
  }, [applyTransform])

  useEffect(() => {
    return () => {
      cancelAnimationFrame(animRef.current)
      cancelAnimationFrame(inertiaRef.current)
    }
  }, [])

  const screenToWorld = useCallback((clientX: number, clientY: number) => {
    const rect = svgRef.current!.getBoundingClientRect()
    const sx = clientX - rect.left
    const sy = clientY - rect.top
    return { x: (sx - vp.current.x) / vp.current.k, y: (sy - vp.current.y) / vp.current.k }
  }, [])

  const updateEdgeDom = useCallback((idx: number) => {
    const e = edges[idx]
    const from = livePos.current[e.from_id]
    const to = livePos.current[e.to_id]
    if (!from || !to) return
    const d = edgePath(from, to)
    edgeMainRef.current.get(idx)?.setAttribute("d", d)
    edgeFlowRef.current.get(idx)?.setAttribute("d", d)
  }, [edges])

  /* ---------- background pan + inertia ---------- */

  const onSvgPointerDown = useCallback((e: ReactPointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return
    cancelAnimationFrame(animRef.current)
    cancelAnimationFrame(inertiaRef.current)
    svgRef.current!.setPointerCapture(e.pointerId)
    svgRef.current!.classList.add("is-panning")
    const now = performance.now()
    panState.current = {
      ox: vp.current.x,
      oy: vp.current.y,
      startX: e.clientX,
      startY: e.clientY,
      lastX: e.clientX,
      lastY: e.clientY,
      lastT: now,
      vx: 0,
      vy: 0,
      moved: false,
    }
  }, [])

  const onSvgPointerMove = useCallback(
    (e: ReactPointerEvent<SVGSVGElement>) => {
      const p = panState.current
      if (!p) return
      const dx = e.clientX - p.startX
      const dy = e.clientY - p.startY
      vp.current.x = p.ox + dx
      vp.current.y = p.oy + dy
      applyTransform()
      const now = performance.now()
      const dt = now - p.lastT || 16
      p.vx = (e.clientX - p.lastX) / dt
      p.vy = (e.clientY - p.lastY) / dt
      p.lastX = e.clientX
      p.lastY = e.clientY
      p.lastT = now
      if (Math.abs(dx) + Math.abs(dy) > 4) p.moved = true
    },
    [applyTransform],
  )

  const onSvgPointerUp = useCallback(
    (e: ReactPointerEvent<SVGSVGElement>) => {
      const p = panState.current
      if (!p) return
      panState.current = null
      svgRef.current!.classList.remove("is-panning")
      try {
        svgRef.current!.releasePointerCapture(e.pointerId)
      } catch {}
      if (!p.moved) {
        onCloseMenu()
        return
      }
      let vx = p.vx * 16
      let vy = p.vy * 16
      if (Math.hypot(vx, vy) > 2.5) {
        const decay = () => {
          vx *= 0.9
          vy *= 0.9
          vp.current.x += vx
          vp.current.y += vy
          applyTransform()
          if (Math.hypot(vx, vy) > 0.4) inertiaRef.current = requestAnimationFrame(decay)
        }
        inertiaRef.current = requestAnimationFrame(decay)
      }
    },
    [applyTransform, onCloseMenu],
  )

  const onWheel = useCallback(
    (e: WheelEvent) => {
      e.preventDefault()
      const rect = svgRef.current!.getBoundingClientRect()
      const sx = e.clientX - rect.left
      const sy = e.clientY - rect.top
      if (e.ctrlKey) {
        // Trackpad pinch gesture — browsers report a two-finger pinch
        // as a wheel event with ctrlKey=true, regardless of whether
        // the actual Ctrl key is physically held. This is the ONLY
        // gesture that should zoom. MUST be instant (dur=0), not
        // eased: an eased tween restarted on every single event fights
        // the physical gesture (constant cancel-and-restart of a
        // catch-up animation chasing a moving target), which is
        // exactly what causes visible lag/stutter during a pinch.
        zoomAt(sx, sy, Math.exp(-e.deltaY * 0.012), 0)
      } else {
        // Plain two-finger scroll (trackpad) or mouse wheel — this
        // pans, it never zooms. Was previously mapped to zoom, which
        // meant every ordinary scroll gesture — including small,
        // accidental ones — zoomed the whole graph in or out. Natural
        // trackpad scroll reports both deltaX and deltaY (diagonal
        // scroll pans diagonally); Shift+wheel on a plain mouse
        // remaps vertical scroll to deltaX for horizontal-only pan.
        cancelAnimationFrame(animRef.current)
        vp.current.x -= e.shiftKey ? e.deltaY : e.deltaX
        vp.current.y -= e.shiftKey ? 0 : e.deltaY
        applyTransform()
      }
    },
    [zoomAt, applyTransform],
  )

  /**
   * FIX: was previously wired as a JSX `onWheel` prop. React 17+
   * registers wheel listeners as PASSIVE by default for scroll
   * performance — which means `e.preventDefault()` inside a
   * React-synthetic wheel handler silently does nothing for the
   * trackpad-pinch case (Chrome maps a two-finger pinch to a wheel
   * event with ctrlKey=true). The browser's native page-zoom fires
   * at the same time as our own zoom logic, which is exactly the
   * "everything zooms, not just the graph" bug this fixes. A real,
   * manually-attached, { passive: false } listener is required to
   * actually block the browser's native zoom.
   */
  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    el.addEventListener("wheel", onWheel, { passive: false })
    return () => el.removeEventListener("wheel", onWheel)
  }, [onWheel])

  /* ---------- node drag vs click ---------- */

  const onNodePointerDown = useCallback(
    (e: ReactPointerEvent<SVGGElement>, node: GraphNode) => {
      if (e.button !== 0) return
      e.stopPropagation()
      cancelAnimationFrame(animRef.current)
      cancelAnimationFrame(inertiaRef.current)
      const el = e.currentTarget
      el.setPointerCapture(e.pointerId)
      const w = screenToWorld(e.clientX, e.clientY)
      const cur = livePos.current[node.id]
      nodeDrag.current = {
        id: node.id,
        offsetX: cur.x - w.x,
        offsetY: cur.y - w.y,
        startX: e.clientX,
        startY: e.clientY,
        moved: false,
      }
    },
    [screenToWorld],
  )

  const onNodePointerMove = useCallback(
    (e: ReactPointerEvent<SVGGElement>) => {
      const d = nodeDrag.current
      if (!d) return
      const w = screenToWorld(e.clientX, e.clientY)
      const np = { x: w.x + d.offsetX, y: w.y + d.offsetY }
      livePos.current[d.id] = np
      nodeElRef.current.get(d.id)?.setAttribute("transform", `translate(${np.x} ${np.y})`)
      for (const idx of adjacency.get(d.id) || []) updateEdgeDom(idx)
      if (Math.hypot(e.clientX - d.startX, e.clientY - d.startY) > 5) d.moved = true
    },
    [screenToWorld, adjacency, updateEdgeDom],
  )

  const onNodePointerUp = useCallback(
    (e: ReactPointerEvent<SVGGElement>, node: GraphNode) => {
      const d = nodeDrag.current
      if (!d) return
      nodeDrag.current = null
      try {
        e.currentTarget.releasePointerCapture(e.pointerId)
      } catch {}
      if (!d.moved) {
        onSelectNode(node.id)
        onOpenMenu(node, { x: e.clientX, y: e.clientY })
      } else {
        forceRender((n) => n + 1) // commit dragged position into render tree
      }
    },
    [onSelectNode, onOpenMenu],
  )

  /* ---------- minimap navigation ---------- */

  const onMiniPointerDown = useCallback(
    (e: ReactPointerEvent<SVGSVGElement>) => {
      const rect = e.currentTarget.getBoundingClientRect()
      const mx = e.clientX - rect.left
      const my = e.clientY - rect.top
      const wx = mx / mini.scale + paddedBounds.minX
      const wy = my / mini.scale + paddedBounds.minY
      const s = sizeRef.current
      const k = vp.current.k
      animateTo({ k, x: s.w / 2 - wx * k, y: s.h / 2 - wy * k }, 360)
    },
    [animateTo, mini.scale, paddedBounds.minX, paddedBounds.minY],
  )

  /* ---------- derived highlight sets (render-time, infrequent) ---------- */

  const impactSeverity = useMemo(() => {
    const m = new Map<string, Severity>()
    impactData?.impacted_nodes.forEach((n) => m.set(n.node_id, n.severity))
    return m
  }, [impactData])

  const query = searchQuery.trim().toLowerCase()
  const matchSet = useMemo(() => {
    if (!query) return null
    const s = new Set<string>()
    for (const n of nodes) {
      if (n.name.toLowerCase().includes(query) || n.file_path.toLowerCase().includes(query)) s.add(n.id)
    }
    return s
  }, [query, nodes])

    if (nodes.length === 0) {
    return (
      <div ref={containerRef} style={{ width: "100%", height: "100%" }}>
        <div style={{ display: "grid", placeItems: "center", height: "100%", color: "var(--text-faint)" }}>
          <p>No graph indexed yet for this repository.</p>
        </div>
      </div>
    )
  }

  return (
    <div ref={containerRef} style={{ width: "100%", height: "100%" }}>
      <svg
        ref={svgRef}
        className="em-graph-svg"
        viewBox={`0 0 ${size.w} ${size.h}`}
        onPointerDown={onSvgPointerDown}
        onPointerMove={onSvgPointerMove}
        onPointerUp={onSvgPointerUp}
        onPointerLeave={onSvgPointerUp}
      >
        <defs>
          <radialGradient id="em-node-fill" cx="35%" cy="30%" r="80%">
            <stop offset="0%" stopColor="#59627a" />
            <stop offset="100%" stopColor="#3a4152" />
          </radialGradient>
        </defs>

        <g ref={gRef}>
          {/* edges */}
          <g>
            {edges.map((edge, i) => {
              const from = livePos.current[edge.from_id]
              const to = livePos.current[edge.to_id]
              if (!from || !to) return null
              const d = edgePath(from, to)

              // Structural edge — a file containing a function/class is
              // not a "depends on" relationship (impact_analysis_v1.md
              // deliberately excludes `contains` from traversal). Render
              // it as a quiet, static, dashed line — never part of the
              // impact/pulse/selection visual language, so it never
              // competes with real dependency signal.
              if (edge.type === "contains") {
                return (
                  <path
                    key={i}
                    className="em-edge em-edge-structural"
                    d={d}
                    fill="none"
                  />
                )
              }

              const touchesSel =
                selectedNodeId && (edge.from_id === selectedNodeId || edge.to_id === selectedNodeId)
              const toSev = impactSeverity.get(edge.to_id)
              const fromSev = impactSeverity.get(edge.from_id)
              const inImpact =
                impactData &&
                (edge.to_id === impactData.changed_node_id ||
                  edge.from_id === impactData.changed_node_id ||
                  toSev ||
                  fromSev)
              const flowSev = toSev || fromSev
              const isActive = Boolean(touchesSel || inImpact)

              let dim = false
              if (matchSet) dim = !(matchSet.has(edge.from_id) && matchSet.has(edge.to_id))
              else if (impactData) dim = !inImpact

              const flowColor = flowSev
                ? SEVERITY_VAR[flowSev]
                : "var(--accent)"
              const showFlow = ambientFlow || isActive

              return (
                <g key={i}>
                  <path
                    ref={(el) => {
                      if (el) edgeMainRef.current.set(i, el)
                      else edgeMainRef.current.delete(i)
                    }}
                    className={`em-edge${isActive ? " is-active" : ""}${dim ? " is-dim" : ""}`}
                    d={d}
                    stroke={isActive && flowSev ? SEVERITY_VAR[flowSev] : undefined}
                  />
                  {showFlow && (
                    <path
                      ref={(el) => {
                        if (el) edgeFlowRef.current.set(i, el)
                        else edgeFlowRef.current.delete(i)
                      }}
                      className={`em-edge-flow${isActive ? " is-flowing" : ""}`}
                      d={d}
                      stroke={flowColor}
                      style={
                        !isActive && ambientFlow
                          ? {
                              opacity: dim ? 0 : 0.16,
                              strokeDasharray: "2 22",
                              animation: `em-flow ${2 + (i % 5) * 0.3}s linear infinite`,
                            }
                          : undefined
                      }
                    />
                  )}
                </g>
              )
            })}
          </g>

          {/* impact waves at the changed node */}
          {impactData &&
            livePos.current[impactData.changed_node_id] &&
            [0, 1, 2].map((w) => {
              const p = livePos.current[impactData.changed_node_id]
              return (
                <circle
                  key={`wave-${w}`}
                  className="em-impact-wave is-live"
                  cx={p.x}
                  cy={p.y}
                  stroke="var(--accent)"
                  style={{ animationDelay: `${w * 0.8}s` }}
                />
              )
            })}

          {/* nodes */}
          <g>
            {nodes.map((node) => {
              const p = livePos.current[node.id]
              if (!p) return null
              const size2 = NODE_SIZE[node.type]
              const isSelected = node.id === selectedNodeId
              const isChanged = impactData?.changed_node_id === node.id
              const sev = impactSeverity.get(node.id)

              let fill = "url(#em-node-fill)"
              let stroke = TYPE_STROKE[node.type]
              let strokeWidth = 1.4
              if (isChanged) {
                fill = "var(--accent)"
                stroke = "var(--accent-bright)"
                strokeWidth = 2
              } else if (sev) {
                fill = SEVERITY_VAR[sev]
                stroke = SEVERITY_VAR[sev]
                strokeWidth = 1.4
              }
              if (isSelected) {
                stroke = "var(--accent-bright)"
                strokeWidth = 2.4
              }

              const isMatch = matchSet?.has(node.id)
              const isDead = !impactData && Boolean(deadNodeIds?.has(node.id))

              if (isDead && !isSelected) {
                fill = "var(--dead-code-fill)"
                stroke = "var(--dead-code-stroke)"
                strokeWidth = 1.4
              }

              let dim = false
              if (matchSet) dim = !isMatch
              else if (impactData) dim = !sev && !isChanged
              else if (deadNodeIds && deadNodeIds.size > 0) dim = !isDead

              const cls = [
                "em-node",
                isSelected ? "is-selected" : "",
                isMatch ? "is-match" : "",
                isDead ? "is-dead" : "",
                dim ? "is-dim" : "",
              ]
                .filter(Boolean)
                .join(" ")

              return (
                <g
                  key={node.id}
                  ref={(el) => {
                    if (el) nodeElRef.current.set(node.id, el)
                    else nodeElRef.current.delete(node.id)
                  }}
                  className={cls}
                  transform={`translate(${p.x} ${p.y})`}
                  onPointerDown={(e) => onNodePointerDown(e, node)}
                  onPointerMove={onNodePointerMove}
                  onPointerUp={(e) => onNodePointerUp(e, node)}
                  tabIndex={0}
                  role="button"
                  aria-label={`${node.type} ${node.name}`}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      onSelectNode(node.id)
                      const rect = nodeElRef.current.get(node.id)?.getBoundingClientRect()
                      if (rect) onOpenMenu(node, { x: rect.left + rect.width / 2, y: rect.top })
                    }
                  }}
                >
                  <circle className="em-node-hit" r={size2 + 8} />
                  <path
                    className="em-node-shape"
                    d={nodeShapePath(node.type, size2)}
                    fill={fill}
                    stroke={stroke}
                    strokeWidth={strokeWidth}
                  />
                  <text className="em-node-label" y={size2 + 14} textAnchor="middle">
                    {node.name.length > 24 ? `${node.name.slice(0, 22)}…` : node.name}
                  </text>
                </g>
              )
            })}
          </g>
        </g>
      </svg>

      {/* zoom controls */}
      <div className="em-float em-zoom-cluster">
        <button className="em-zoom-btn" aria-label="Zoom in" onClick={() => zoomAt(size.w / 2, size.h / 2, 1.35, 200)}>
          <Plus size={16} />
        </button>
        <span className="em-zoom-level" ref={zoomLabelRef}>
          100%
        </span>
        <button className="em-zoom-btn" aria-label="Zoom out" onClick={() => zoomAt(size.w / 2, size.h / 2, 0.74, 200)}>
          <Minus size={16} />
        </button>
        <div className="em-zoom-divider" />
        <button className="em-zoom-btn" aria-label="Fit to screen" onClick={() => fit(true)}>
          <Maximize2 size={15} />
        </button>
      </div>

      {/* minimap */}
      <div className="em-float em-minimap" ref={miniWrapRef}>
        <div className="em-minimap-title">
          <LocateFixed size={10} style={{ display: "inline", marginRight: 5, verticalAlign: "middle" }} />
          Overview
        </div>
        <svg
          className="em-minimap-svg"
          viewBox={`0 0 ${mini.mmW} ${mini.mmH}`}
          width={mini.mmW}
          height={mini.mmH}
          onPointerDown={onMiniPointerDown}
        >
          {edges.length <= 700 &&
            edges.map((edge, i) => {
              const a = livePos.current[edge.from_id]
              const b = livePos.current[edge.to_id]
              if (!a || !b) return null
              return (
                <line
                  key={i}
                  x1={(a.x - paddedBounds.minX) * mini.scale}
                  y1={(a.y - paddedBounds.minY) * mini.scale}
                  x2={(b.x - paddedBounds.minX) * mini.scale}
                  y2={(b.y - paddedBounds.minY) * mini.scale}
                  stroke="var(--border-strong)"
                  strokeWidth={0.3}
                  opacity={0.5}
                />
              )
            })}
          {nodes.map((node) => {
            const p = livePos.current[node.id]
            if (!p) return null
            const sev = impactSeverity.get(node.id)
            const isChanged = impactData?.changed_node_id === node.id
            return (
              <circle
                key={node.id}
                cx={(p.x - paddedBounds.minX) * mini.scale}
                cy={(p.y - paddedBounds.minY) * mini.scale}
                r={isChanged ? 2.2 : 1.3}
                fill={
                  isChanged
                    ? "var(--accent)"
                    : sev
                      ? SEVERITY_VAR[sev]
                      : "var(--node-neutral-border)"
                }
              />
            )
          })}
          <rect ref={miniRectRef} className="em-minimap-viewport" x={0} y={0} width={0} height={0} rx={2} />
        </svg>
      </div>
    </div>
  )
})