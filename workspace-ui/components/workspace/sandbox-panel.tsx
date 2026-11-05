"use client"

import { useEffect, useMemo, useState } from "react"
import { api, ApiError } from "@/services/api-client"
import { PatchEditor } from "./patch-editor"

/**
 * Direct sandbox: pick any file from the graph, edit it, run the tests on the edit
 * (in a temporary copy), and apply it to the repo once they pass. No AI step involved.
 */
export function SandboxPanel({
  repositoryId,
  branch,
  filePaths,
  selectedPath,
  openRequest,
  wide,
  onWideChange,
}: {
  repositoryId: string | null
  branch: string | null
  filePaths: string[]
  /** File of the node currently selected on the graph, if any. */
  selectedPath: string | null
  openRequest: number
  wide: boolean
  onWideChange: (wide: boolean) => void
}) {
  const [query, setQuery] = useState("")
  const [path, setPath] = useState<string | null>(null)
  const [content, setContent] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    const all = Array.from(new Set(filePaths)).sort()
    return (q ? all.filter((p) => p.toLowerCase().includes(q)) : all).slice(0, 40)
  }, [filePaths, query])

  useEffect(() => {
    onWideChange(path !== null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path])

  async function open(p: string) {
    if (!repositoryId) return
    setLoading(true); setError(null)
    try {
      const r = branch
        ? await api.getGithubBranchFile(repositoryId, branch, p)
        : await api.getFile(repositoryId, p)
      setContent(r.content)
      setPath(p)
    } catch (e) {
      setError(e instanceof ApiError ? `Could not open ${p} (${e.status}).` : `Could not open ${p}.`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
  if (openRequest === 0 || !selectedPath || !repositoryId) return

  let cancelled = false

  async function openSelectedFile() {
    setLoading(true)
    setError(null)

    try {
      const r = branch
        ? await api.getGithubBranchFile(repositoryId!, branch, selectedPath!)
        : await api.getFile(repositoryId!, selectedPath!)

      if (cancelled) return

      setContent(r.content)
      setPath(selectedPath!)
    } catch (e) {
      if (cancelled) return

      setError(
        e instanceof ApiError
          ? `Could not open ${selectedPath} (${e.status}).`
          : `Could not open ${selectedPath}.`
      )
    } finally {
      if (!cancelled) setLoading(false)
    }
  }

  void openSelectedFile()

  return () => {
    cancelled = true
  }
}, [openRequest, selectedPath, repositoryId, branch])

  if (!repositoryId) {
    return <p className="ws-muted">Open an indexed repository to use the sandbox (the workspace is in demo mode).</p>
  }

  if (path !== null && content !== null) {
    return (
      <div className="ws-stack">
        <div className="ws-row ws-row--between">
          <strong>{path}</strong>
          <button className="ws-link" onClick={() => { setPath(null); setContent(null) }}>Choose another file</button>
        </div>
        <PatchEditor key={`${branch ?? "local"}:${path}`} repositoryId={repositoryId} branch={branch} files={[{ path, patched: content }]} wide={wide} preVerified={false} />
      </div>
    )
  }

  return (
    <div className="ws-stack">
      <p className="ws-muted" style={{ margin: 0 }}>
        {branch ? `Editing ${branch}. Changes are saved only to this isolated branch workspace; push the branch when ready.` : "Pick a file, change it, run the tests on your change, then apply it. Nothing is written to your repo until you apply."}
      </p>

      {selectedPath && (
        <button className="ws-btn" onClick={() => open(selectedPath)} disabled={loading}>
          Edit {selectedPath}
        </button>
      )}

      <input className="ws-field" value={query} placeholder="Search files" onChange={(e) => setQuery(e.target.value)} />

      <div>
        {matches.map((p) => (
          <button
            key={p}
            className="ws-rail-btn"
            style={{ flexDirection: "row", width: "100%", padding: "7px 8px", fontSize: 12, textAlign: "left" }}
            onClick={() => open(p)}
            disabled={loading}
          >
            {p}
          </button>
        ))}
        {matches.length === 0 && <p className="ws-muted">No files match.</p>}
      </div>

      {error && <p className="ws-error" style={{ margin: 0 }}>{error}</p>}
    </div>
  )
}
