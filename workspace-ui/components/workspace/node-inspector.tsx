"use client"

import { X, FileCode2, Network, Sparkles, Bot, ShieldCheck, ShieldAlert } from "lucide-react"
import type { GraphNode, ImpactData, Severity } from "@/lib/graph/data"

const SEVERITY_LABEL: Record<Severity, string> = {
  high: "High risk",
  medium: "Medium risk",
  low: "Low risk",
}

export type NodeAction = "open" | "connections" | "explain" | "agent"

interface NodeInspectorProps {
  node: GraphNode | null
  impactData: ImpactData | null
  isOpen: boolean
  onClose: () => void
  onAction: (action: NodeAction) => void
}

export function NodeInspector({ node, impactData, isOpen, onClose, onAction }: NodeInspectorProps) {
  if (!node) {
    return (
      <aside className={`em-inspector em-inspector--empty${isOpen ? " is-open" : ""}`}>
        <p>Select a node to inspect its role, dependents, and change impact.</p>
      </aside>
    )
  }

  const impactedEntry = impactData?.impacted_nodes.find((n) => n.node_id === node.id)
  const isChanged = impactData?.changed_node_id === node.id

  return (
    <aside className={`em-inspector${isOpen ? " is-open" : ""}`}>
      <button className="em-icon-btn em-inspector-close" aria-label="Close inspector" onClick={onClose}>
        <X size={16} />
      </button>

      <div className="em-inspector-header">
        <span className="em-inspector-type">
          <FileCode2 size={12} />
          {node.type}
        </span>
        <h2 className="em-inspector-name">{node.name}</h2>
        <p className="em-inspector-path">{node.file_path}</p>
      </div>

      {isChanged && impactData && (
        <div className="em-inspector-section em-inspector-section--accent">
          <p className="em-inspector-stat">
            <b>{impactData.total_affected}</b> affected · <b>{impactData.high_severity_count}</b> high ·{" "}
            <b>{impactData.medium_severity_count}</b> medium · <b>{impactData.low_severity_count}</b> low
          </p>
        </div>
      )}

      {impactedEntry && (
        <div className={`em-inspector-section em-sev-${impactedEntry.severity}`}>
          <p className="em-sev-label">
            {impactedEntry.is_tested ? <ShieldCheck size={14} /> : <ShieldAlert size={14} />}
            {SEVERITY_LABEL[impactedEntry.severity]}
          </p>
          <dl className="em-facts">
            <dt>Distance</dt>
            <dd>
              {impactedEntry.distance} step{impactedEntry.distance !== 1 ? "s" : ""}
            </dd>
            <dt>Depended on by</dt>
            <dd>
              {impactedEntry.fan_in} thing{impactedEntry.fan_in !== 1 ? "s" : ""}
            </dd>
            <dt>Test coverage</dt>
            <dd>{impactedEntry.is_tested ? "Covered" : "None"}</dd>
          </dl>
        </div>
      )}

      {!impactData && (
        <div className="em-inspector-section">
          <dl className="em-facts">
            <dt>Module</dt>
            <dd>{node.module}</dd>
            {/*
              fan_in / is_tested are only known once impact analysis
              has run for this node (real API: ImpactedNode fields,
              not raw GraphNode fields — see adapters.ts). Showing a
              fake 0 / "None" here would misreport untested code as
              covered-or-not when we simply haven't checked yet.
            */}
            {node.fan_in !== undefined ? (
              <>
                <dt>Depended on by</dt>
                <dd>
                  {node.fan_in} thing{node.fan_in !== 1 ? "s" : ""}
                </dd>
              </>
            ) : (
              <>
                <dt>Dependents</dt>
                <dd className="em-fact-unknown">Run "Check connections" to see</dd>
              </>
            )}
            {node.is_tested !== undefined ? (
              <>
                <dt>Test coverage</dt>
                <dd>{node.is_tested ? "Covered" : "None"}</dd>
              </>
            ) : (
              <>
                <dt>Test coverage</dt>
                <dd className="em-fact-unknown">Unknown until analyzed</dd>
              </>
            )}
          </dl>
        </div>
      )}

      {impactData?.narration && isChanged && (
        <div className="em-inspector-section">
          <p className="em-narration">{impactData.narration}</p>
        </div>
      )}

      <div className="em-inspector-actions">
        <button className="em-btn" onClick={() => onAction("open")}>
          <FileCode2 size={15} />
          Open file
        </button>
        <button className="em-btn" onClick={() => onAction("connections")}>
          <Network size={15} />
          Check connections
        </button>
        <button className="em-btn" onClick={() => onAction("explain")}>
          <Sparkles size={15} />
          Explain this node
        </button>
        <button className="em-btn em-btn--accent" onClick={() => onAction("agent")}>
          <Bot size={15} />
          Connect to AI agent
        </button>
      </div>
    </aside>
  )
}