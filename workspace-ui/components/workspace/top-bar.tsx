"use client"

import { useEffect, useRef } from "react"
import { GitBranch, PanelRightClose, PanelRightOpen, Search } from "lucide-react"

interface TopBarProps {
  repo: string
  branch: string
  branches: string[]
  branchEnabled: boolean
  branchBusy: "create" | "push" | null
  branchMessage: string | null
  branchError: string | null
  onSelectBranch: (branch: string) => void
  onCreateBranch: (name: string) => void
  onPushBranch: () => void
  nodeCount: number
  edgeCount: number
  searchQuery: string
  matchCount: number
  onSearch: (value: string) => void
  inspectorOpen: boolean
  onToggleInspector: () => void
}

export function TopBar({
  repo,
  branch,
  branches,
  branchEnabled,
  branchBusy,
  branchMessage,
  branchError,
  onSelectBranch,
  onCreateBranch,
  onPushBranch,
  nodeCount,
  edgeCount,
  searchQuery,
  matchCount,
  onSearch,
  inspectorOpen,
  onToggleInspector,
}: TopBarProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        inputRef.current?.focus()
        inputRef.current?.select()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  function createBranch() {
    const name = window.prompt(
      "Enter a name for the new GitHub branch:",
      "feature/"
    )
    if (name?.trim()) onCreateBranch(name.trim())
  }

  return (
    <header className="ws-topbar">
      <span className="ws-top-title">Engineer Mode</span>

      <span className="ws-top-repo">
        <GitBranch size={13} />
        {repo}
      </span>

      {branchEnabled ? (
        <span className="ws-row" style={{ gap: 6, alignItems: "center" }}>
          <select
            className="ws-field"
            aria-label="Active GitHub branch"
            value={branch}
            onChange={(e) => onSelectBranch(e.target.value)}
            style={{ width: 155, padding: "5px 7px", fontSize: 12 }}
          >
            <option value="">Indexed repository (local)</option>
            {branches.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>

          <button
            className="ws-btn"
            onClick={createBranch}
            disabled={!!branchBusy}
            title="Create an isolated GitHub branch from the latest default branch"
          >
            {branchBusy === "create" ? "Creating..." : "New branch"}
          </button>

          {branch && (
            <button
              className="ws-btn ws-btn--primary"
              onClick={onPushBranch}
              disabled={!!branchBusy}
              title="Commit and push saved branch changes to GitHub"
            >

              {branchBusy === "push" ? "Pushing..." : "Push"}
            </button>
          )}
        </span>
      ) : (
        <span className="ws-muted">{branch}</span>
      )}

      <label className="ws-search">
        <Search size={14} />
        <input
          ref={inputRef}
          value={searchQuery}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search symbols and files"
          aria-label="Search graph"
          spellCheck={false}
        />
        {searchQuery && (
          <span>
            {matchCount} match{matchCount !== 1 ? "es" : ""}
          </span>
        )}
      </label>

      <span className="ws-grow" />

      <span className="ws-muted">
        {nodeCount.toLocaleString()} nodes, {edgeCount.toLocaleString()} edges
      </span>

      <button
        className="ws-icon-btn"
        onClick={onToggleInspector}
        aria-label={inspectorOpen ? "Hide inspector" : "Show inspector"}
      >
        {inspectorOpen ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
      </button>

      {(branchError || branchMessage) && (
        <span
          role={branchError ? "alert" : "status"}
          className={branchError ? "ws-error" : "ws-ok"}
          style={{ position: "absolute", top: "100%", right: 12, zIndex: 20, maxWidth: 420 }}
        >
          {branchError ?? branchMessage}
        </span>
      )}
    </header>
  )
}


