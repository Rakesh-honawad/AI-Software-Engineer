"use client"

import { useEffect, useRef, useState } from "react"
import { FileCode2, Network, Sparkles, Bot } from "lucide-react"
import type { GraphNode } from "@/lib/graph/data"
import type { Point } from "@/lib/graph/geometry"
import type { NodeAction } from "./node-inspector"

interface NodeActionMenuProps {
  node: GraphNode
  screen: Point
  onAction: (action: NodeAction) => void
  onClose: () => void
}

const ITEMS: { key: NodeAction; label: string; icon: typeof FileCode2; accent?: boolean }[] = [
  { key: "open", label: "Open file", icon: FileCode2 },
  { key: "connections", label: "Check connections", icon: Network },
  { key: "explain", label: "Explain this node", icon: Sparkles },
  { key: "agent", label: "Connect to AI agent", icon: Bot, accent: true },
]

export function NodeActionMenu({ node, screen, onAction, onClose }: NodeActionMenuProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [closing, setClosing] = useState(false)

  // Keep the menu on-screen near the node.
  const menuW = 200
  const menuH = 190
  const pad = 12
  const left = Math.min(Math.max(pad, screen.x + 14), (typeof window !== "undefined" ? window.innerWidth : 1200) - menuW - pad)
  const top = Math.min(Math.max(pad + 52, screen.y - 10), (typeof window !== "undefined" ? window.innerHeight : 800) - menuH - pad)

  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) beginClose()
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") beginClose()
    }
    document.addEventListener("mousedown", onDown)
    document.addEventListener("keydown", onKey)
    return () => {
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function beginClose() {
    setClosing(true)
    window.setTimeout(onClose, 110)
  }

  return (
    <div
      ref={ref}
      className={`em-action-menu${closing ? " is-closing" : ""}`}
      style={{ left, top }}
      role="menu"
      aria-label={`Actions for ${node.name}`}
    >
      <div className="em-action-menu-head">
        <div className="em-action-menu-name">{node.name}</div>
        <div className="em-action-menu-type">{node.type}</div>
      </div>
      {ITEMS.map((item) => {
        const Icon = item.icon
        return (
          <button
            key={item.key}
            role="menuitem"
            className={`em-action-item${item.accent ? " em-action-item--accent" : ""}`}
            onClick={() => {
              onAction(item.key)
              beginClose()
            }}
          >
            <Icon size={15} />
            {item.label}
          </button>
        )
      })}
    </div>
  )
}
