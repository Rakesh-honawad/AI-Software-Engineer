"use client"

import { useState } from "react"
import { ChevronDown, ChevronUp } from "lucide-react"
import { nodeShapePath } from "@/lib/graph/geometry"
import type { NodeType } from "@/lib/graph/data"

const TYPES: { type: NodeType; label: string; color: string }[] = [
  { type: "function", label: "Function", color: "var(--type-function)" },
  { type: "class", label: "Class", color: "var(--type-class)" },
  { type: "file", label: "File", color: "var(--type-file)" },
  { type: "module", label: "Module", color: "var(--type-module)" },
  { type: "service", label: "Service", color: "var(--type-service)" },
  { type: "feature", label: "Feature", color: "var(--type-feature)" },
  { type: "external_dependency", label: "External dependency", color: "var(--type-external)" },
]

const SEVERITY = [
  { label: "High risk", color: "var(--severity-high)" },
  { label: "Medium risk", color: "var(--severity-medium)" },
  { label: "Low risk", color: "var(--severity-low)" },
]

function Shape({ type, color }: { type: NodeType; color: string }) {
  const r = 7
  return (
    <svg width={18} height={18} viewBox={`${-r - 2} ${-r - 2} ${(r + 2) * 2} ${(r + 2) * 2}`}>
      <path d={nodeShapePath(type, r)} fill={color} stroke={color} strokeWidth={1} />
    </svg>
  )
}

export function Legend({ impactActive }: { impactActive: boolean }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="ws-legend">
      <button className="ws-legend-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {impactActive ? "Severity" : "Legend"}
        {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
      </button>

      {open && (
        <div className="ws-legend-body">
          {impactActive
            ? SEVERITY.map((s) => (
                <div key={s.label} className="ws-legend-row">
                  <span className="ws-swatch" style={{ background: s.color }} />
                  {s.label}
                </div>
              ))
            : TYPES.map((t) => (
                <div key={t.type} className="ws-legend-row">
                  <Shape type={t.type} color={t.color} />
                  {t.label}
                </div>
              ))}
          <div className="ws-legend-note">
            {impactActive ? "Select a node to trace its impact" : "Turn on Impact in the left bar to color by risk"}
          </div>
        </div>
      )}
    </div>
  )
}