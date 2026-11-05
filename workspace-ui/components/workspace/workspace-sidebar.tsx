// "use client"

// import { Bug, FileCode2, FlaskConical, Network, Waypoints } from "lucide-react"
// import type { Graph } from "@/lib/graph/data"

// export type PanelKey = "investigate" | "sandbox" | "files"

"use client"

import { useMemo, useState } from "react"
import { Bug, FileCode2, FlaskConical, Network, Waypoints } from "lucide-react"

export type PanelKey = "investigate" | "sandbox" | "files"

export function WorkspaceSidebar({
  panel,
  onPanel,
  impactActive,
  onToggleImpact,
}: {
  panel: PanelKey | null
  onPanel: (p: PanelKey | null) => void
  impactActive: boolean
  onToggleImpact: () => void
}) {
  const toggle = (k: PanelKey) => onPanel(panel === k ? null : k)

  return (
    <nav className="ws-rail" aria-label="Workspace">
      <button
        className={`ws-rail-btn${panel === null ? " is-active" : ""}`}
        onClick={() => onPanel(null)}
        title="Show the graph full width"
      >
        <Network size={19} />
        Graph
      </button>
      <button
        className={`ws-rail-btn${panel === "investigate" ? " is-active" : ""}`}
        onClick={() => toggle("investigate")}
        title="Find and fix a bug"
      >
        <Bug size={19} />
        Investigate
      </button>
      <button
        className={`ws-rail-btn${panel === "sandbox" ? " is-active" : ""}`}
        onClick={() => toggle("sandbox")}
        title="Edit a file yourself and run the tests on it"
      >
        <FlaskConical size={19} />
        Sandbox
      </button>
      <button
        className={`ws-rail-btn${panel === "files" ? " is-active" : ""}`}
        onClick={() => toggle("files")}
        title="Files sorted by how many things depend on them"
      >
        <FileCode2 size={19} />
        Files
      </button>

      <div className="ws-rail-sep" />

      <button
        className={`ws-rail-btn${impactActive ? " is-active" : ""}`}
        onClick={onToggleImpact}
        aria-pressed={impactActive}
        title="Select a node to see what a change to it would affect"
      >
        <Waypoints size={19} />
        Impact
      </button>
    </nav>
  )
}

export function FilesPanel({
  files,
  loading,
  error,
  selectedPath,
  content,
  previewLoading,
  previewError,
  truncated,
  onSelectFile,
  onEditInSandbox,
}: {
  files: string[]
  loading: boolean
  error: string | null
  selectedPath: string | null
  content: string | null
  previewLoading: boolean
  previewError: string | null
  truncated: boolean
  onSelectFile: (path: string) => void
  onEditInSandbox: () => void
}) {
  const [query, setQuery] = useState("")

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    return files.filter((path) => !q || path.toLowerCase().includes(q))
  }, [files, query])

  return (
    <div className="ws-stack">
      <p className="ws-muted" style={{ margin: 0 }}>
        {files.length} repository files
        {truncated ? " (list truncated)" : ""}
      </p>

      {loading && <p className="ws-muted">Loading repository files...</p>}

      {error && <p className="ws-error">{error}</p>}

      {!loading && !error && (
        <>
          <input
            className="ws-field"
            value={query}
            placeholder="Search repository files..."
            onChange={(e) => setQuery(e.target.value)}
          />

          <div style={{ maxHeight: 300, overflowY: "auto" }}>
            {matches.map((path) => (
              <button
                key={path}
                className="ws-rail-btn"
                style={{
                  flexDirection: "row",
                  width: "100%",
                  padding: "7px 8px",
                  fontSize: 12,
                  textAlign: "left",
                  background: selectedPath === path ? "var(--ws-active, rgba(128,128,128,0.15))" : undefined,
                }}
                onClick={() => onSelectFile(path)}
              >
                <FileCode2 size={14} />
                <span
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {path}
                </span>
              </button>
            ))}

            {matches.length === 0 && (
              <p className="ws-muted">No files match your search.</p>
            )}
          </div>

          {selectedPath && (
            <div className="ws-stack">
              <div className="ws-row ws-row--between">
                <strong style={{ overflowWrap: "anywhere" }}>
                  {selectedPath}
                </strong>
                <button className="ws-link" onClick={onEditInSandbox}>
                  Edit in Sandbox
                </button>
              </div>

              {previewLoading && (
                <p className="ws-muted">Loading file contents...</p>
              )}

              {previewError && (
                <p className="ws-error">{previewError}</p>
              )}

              {content !== null && !previewLoading && (
                <pre
                  style={{
                    maxHeight: 450,
                    overflow: "auto",
                    padding: 12,
                    fontSize: 12,
                    lineHeight: 1.6,
                    whiteSpace: "pre",
                    borderRadius: 6,
                    background: "var(--ws-code-bg, rgba(128,128,128,0.10))",
                  }}
                >
                  {content}
                </pre>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
 