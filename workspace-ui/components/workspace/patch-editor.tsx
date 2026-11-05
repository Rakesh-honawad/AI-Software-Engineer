"use client"

import { useEffect, useRef, useState } from "react"
import dynamic from "next/dynamic"
import { api, ApiError, type VerifyEditResult } from "@/services/api-client"

const DiffEditor = dynamic(() => import("@monaco-editor/react").then((m) => m.DiffEditor), {
  ssr: false,
  loading: () => <p className="ws-muted" style={{ padding: 12, margin: 0 }}>Loading editorâ€¦</p>,
})

const noBom = (s: string) => s.replace(/^\uFEFF/, "") // the server restores the file's own BOM and line endings
const LANG: Record<string, string> = {
  py: "python", ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
  json: "json", md: "markdown", java: "java", go: "go", css: "css", html: "html",
}
const langOf = (p: string) => LANG[p.split(".").pop()?.toLowerCase() ?? ""] ?? "plaintext"

export function PatchEditor({
  repositoryId,
  branch,
  files,
  wide = false,
  preVerified = false,
}: {
  repositoryId: string
  branch?: string | null
  files: { path: string; patched: string }[]
  /** Side-by-side diff when the panel is wide, inline diff when narrow. */
  wide?: boolean
  /** True when the investigation already proved this exact patch (tests failed before, pass after). */
  preVerified?: boolean
}) {
  const initial = useRef<Record<string, string>>(Object.fromEntries(files.map((f) => [f.path, noBom(f.patched)])))
  const [drafts, setDrafts] = useState<Record<string, string>>(initial.current)
  const [originals, setOriginals] = useState<Record<string, string>>({})
  const [active, setActive] = useState(files[0]?.path ?? "")
  const [busy, setBusy] = useState<
    "load" | "verify" | "apply" | "download" | "sync" | null
  >("load")
  const [verify, setVerify] = useState<VerifyEditResult | null>(null)
  const [verifiedKey, setVerifiedKey] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const [applied, setApplied] = useState(false)
  const [restoreToken, setRestoreToken] = useState<string | null>(null)
  const [appliedFiles, setAppliedFiles] = useState<
    { path: string; content: string }[]
  >([])
  const [diskCheck, setDiskCheck] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    let off = false
    Promise.all(files.map((f) => branch ? api.getGithubBranchFile(repositoryId, branch, f.path) : api.getFile(repositoryId, f.path)))
      .then((rs) => { if (!off) setOriginals(Object.fromEntries(rs.map((r) => [r.path, noBom(r.content)]))) })
      .catch((e) => { if (!off) setError(e instanceof Error ? e.message : "Could not load files") })
      .finally(() => { if (!off) setBusy(null) })
    return () => { off = true }
  }, [repositoryId, files, branch])

  const payload = () => files.map((f) => ({ path: f.path, content: drafts[f.path] ?? initial.current[f.path] }))
  const key = JSON.stringify(payload())
  const dirty = files.some((f) => (drafts[f.path] ?? "") !== initial.current[f.path])
  // Apply is only allowed for an edit the tests have actually passed on.
  // An edit is safe to apply when the tests agree it fixes something and it breaks nothing new.
  // Unrelated tests that were already failing before the edit do not block it.
  const verifiedGood = !!verify &&
    verify.complete &&
    verify.safe_to_apply &&
    verify.patch_applied &&
    verify.scope === "full"

  const canApply = dirty && (branch ? true : verifiedGood && verifiedKey === key)

  const fail = (e: unknown) => {
    if (e instanceof ApiError) {
      const d = (e.body as { detail?: unknown } | undefined)?.detail
      setError(typeof d === "string" ? d : d ? JSON.stringify(d) : e.message)
    } else setError(e instanceof Error ? e.message : "Request failed")
  }

  async function runTests() {
    setBusy("verify"); setError(null); setMessage(null); setVerify(null)
    const k = key
    try {
      setVerify(await api.verifyEdit(repositoryId, payload()))
      setVerifiedKey(k)
    } catch (e) { fail(e) } finally { setBusy(null) }
  }

  const norm = (t: string) => noBom(t).replace(/\r\n/g, "\n")

  /** Re-reads each file from disk and compares it with what we meant to write. */
  async function checkDisk(expected: { path: string; content: string }[], what: string) {
    const rs = await Promise.all(expected.map((f) => api.getFile(repositoryId, f.path)))
    const differs = expected.filter((f, i) => norm(rs[i].content) !== norm(f.content)).map((f) => f.path)
    const eolOnly = expected.filter((f, i) => norm(rs[i].content) === norm(f.content) && noBom(rs[i].content) !== noBom(f.content)).map((f) => f.path)
    if (differs.length) return { ok: false, text: `Checked on disk: ${differs.join(", ")} does not match ${what}.` }
    if (eolOnly.length) return { ok: false, text: `Checked on disk: the content of ${eolOnly.join(", ")} matches ${what}, but the line endings changed.` }
    return { ok: true, text: `Checked on disk: the saved files match ${what}.` }
  }

  async function apply() {
    const sent = payload()

    if (branch) {
      if (!window.confirm(`Save these changes to the isolated "${branch}" branch workspace? They will not be pushed to GitHub yet.`)) return

      setBusy("apply")
      setError(null)
      setMessage(null)

      try {
        const r = await api.applyGithubBranchEdits(repositoryId, branch, sent)
        const savedDrafts = Object.fromEntries(sent.map((f) => [f.path, noBom(f.content)]))
        initial.current = savedDrafts
        setDrafts(savedDrafts)
        setOriginals(savedDrafts)
        setVerify(null)
        setVerifiedKey(null)
        setApplied(true)
        setMessage(`Saved to ${branch}: ${r.saved.join(", ")}. ${r.note}`)
      } catch (e) {
        fail(e)
      } finally {
        setBusy(null)
      }
      return
    }

    if (!window.confirm("Write these changes to your repository? This modifies the files on disk.")) return

    setBusy("apply")
    setError(null)
    setMessage(null)
    setDiskCheck(null)

    try {
      const r = await api.applyEdit(repositoryId, sent)
      setAppliedFiles(sent)
      setRestoreToken(r.restore_token)
      setApplied(true)
      setMessage(`Applied to ${r.applied.join(", ")}. ${r.note}`)
      setDiskCheck(await checkDisk(sent, "your edit"))
    } catch (e) {
      fail(e)
    } finally {
      setBusy(null)
    }
  }

  async function downloadUpdatedZip() {
    setBusy("download")
    setError(null)

    try {
      const blob = await api.downloadRepositoryZip(repositoryId)
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")

      link.href = url
      link.download = `${repositoryId}-updated.zip`
      document.body.appendChild(link)
      link.click()
      link.remove()

      URL.revokeObjectURL(url)
      setMessage("Updated repository ZIP downloaded.")
    } catch (e) {
      fail(e)
    } finally {
      setBusy(null)
    }
  }

  async function syncToOriginalFolder() {
    if (!appliedFiles.length) return

    const pickerWindow = window as Window & {
      showDirectoryPicker?: () => Promise<{
        getDirectoryHandle: (name: string) => Promise<any>
        getFileHandle: (name: string) => Promise<any>
      }>
    }

    if (!pickerWindow.showDirectoryPicker) {
      setError("Folder write-back is not supported in this browser. Download the updated ZIP instead.")
      return
    }

    if (!window.confirm(
      `Select your ORIGINAL project folder. Only these ${appliedFiles.length} approved changed files will be overwritten.`
    )) return

    setBusy("sync")
    setError(null)
    setMessage(null)

    try {
      const root = await pickerWindow.showDirectoryPicker()

      for (const file of appliedFiles) {
        const parts = file.path.replace(/\\/g, "/").split("/")

        if (
          !parts.length ||
          parts.some((part) => !part || part === "." || part === "..")
        ) {
          throw new Error(`Unsafe file path: ${file.path}`)
        }

        let directory = root

        for (const part of parts.slice(0, -1)) {
          directory = await directory.getDirectoryHandle(part)
        }

        // Do not create missing files: only update existing project files.
        const handle = await directory.getFileHandle(parts[parts.length - 1])
        const writable = await handle.createWritable()

        try {
          await writable.write(file.content)
        } finally {
          await writable.close()
        }
      }

      setMessage(`Synced ${appliedFiles.length} approved changed files to the selected folder.`)
    } catch (e) {
      fail(e)
    } finally {
      setBusy(null)
    }
  }
  async function undo() {
    if (!restoreToken) return

    if (!window.confirm("Put the files back to how they were before the last Apply?")) return

    setBusy("apply")
    setError(null)
    setMessage(null)
    setDiskCheck(null)

    try {
      const r = await api.restoreEdit(repositoryId, restoreToken)

      setRestoreToken(null)
      setApplied(false)
      setMessage(r.note)

      const back = files.map((f) => ({
        path: f.path,
        content: originals[f.path],
      }))

      setDiskCheck(await checkDisk(back, "the original"))
    } catch (e) {
      fail(e)
    } finally {
      setBusy(null)
    }
  }

  function reset() {
    setDrafts(initial.current)
    setVerify(null)
    setVerifiedKey(null)
    setMessage(null)
    setError(null)
    setNonce((n) => n + 1)
  }

  if (!files.length) return null
  const ready = Object.keys(originals).length === files.length

 return (
  <div className="ws-stack">
    <p className="ws-muted" style={{ margin: 0 }}>
      {wide
        ? "Left is your current file, right is the proposed patch. "
        : "Changed lines are shown inline. "}
      {branch
        ? `Edit the branch copy and save it to ${branch}. Push it from the workspace toolbar when ready.`
        : "Edit the patch, run the tests on it, and apply once they pass."}
    </p>

    {files.length > 1 && (
      <div className="ws-row" style={{ flexWrap: "wrap" }}>
        {files.map((f) => (
          <button
            key={f.path}
            className="ws-file-tab"
            aria-pressed={f.path === active}
            onClick={() => setActive(f.path)}
          >
            {f.path}
          </button>
        ))}
      </div>
    )}

    {files.length === 1 && (
      <div className="ws-muted" style={{ fontSize: 12 }}>
        {files[0].path}
      </div>
    )}

    <div className="ws-editor">
      {ready && (
        <DiffEditor
          key={`${active}-${nonce}-${wide}`}
          height="min(420px, 50vh)"
          language={langOf(active)}
          theme="vs-dark"
          original={originals[active]}
          modified={drafts[active] ?? initial.current[active]}
          options={{
            renderSideBySide: wide,
            originalEditable: false,
            automaticLayout: true,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            fontSize: 13,
          }}
          onMount={(editor) => {
            const m = editor.getModifiedEditor()
            m.onDidChangeModelContent(() =>
              setDrafts((d) => ({ ...d, [active]: m.getValue() }))
            )
          }}
        />
      )}

      {!ready && busy === "load" && (
        <p className="ws-muted" style={{ padding: 12, margin: 0 }}>
          Loading files…
        </p>
      )}
    </div>

    {error && (
      <p className="ws-error" style={{ margin: 0 }}>
        {error}
      </p>
    )}

    {message && (
      <p className="ws-ok" style={{ margin: 0 }}>
        {message}
      </p>
    )}

    {diskCheck && (
      <p
        className={diskCheck.ok ? "ws-ok" : "ws-error"}
        style={{ margin: 0 }}
      >
        {diskCheck.text}
      </p>
    )}

    {!branch && verify && (
      <div className="ws-stack" style={{ gap: 6 }}>
        <div
          className={verifiedGood ? "ws-ok" : "ws-error"}
          style={{ fontWeight: 600 }}
        >
          {!verify.patch_applied
            ? "The edit could not be applied."
            : !verify.complete
              ? "Verification was incomplete. Apply is blocked."
              : verify.new_failures.length
                ? "Your edit introduces test regressions."
                : verify.safe_to_apply
                  ? verify.tests_pass_after
                    ? "Full test suite passes with your edit."
                    : "No new regressions, but some baseline failures remain."
                  : "The edit is not safe to apply."}
        </div>

        <div className="ws-muted" style={{ fontSize: 12 }}>
          Verification scope: {verify.scope}
        </div>

        {!!verify.still_failing.length && (
          <div className="ws-muted" style={{ fontSize: 12 }}>
            Still failing: {verify.still_failing.join(", ")}
          </div>
        )}

        {!!verify.new_failures.length && (
          <div className="ws-error" style={{ fontSize: 12 }}>
            New failures: {verify.new_failures.join(", ")}
          </div>
        )}

        {!!verify.errors.length && (
          <div className="ws-error" style={{ fontSize: 12 }}>
            {verify.errors.join(" ")}
          </div>
        )}

        {!verify.patch_applied && (
          <pre className="ws-pre">{verify.apply_log}</pre>
        )}

        {verify.patched && (
          <pre className="ws-pre">
            {verify.patched.output.replace(/\r/g, "")}
          </pre>
        )}
      </div>
    )}

    <div className="ws-sticky-actions ws-row">
      {!branch && (
        <button
          className="ws-btn ws-btn--primary"
          onClick={runTests}
          disabled={!!busy || !ready}
        >
          {busy === "verify" ? "Running tests..." : "Run tests"}
        </button>
      )}

      <button
        className="ws-btn ws-btn--primary"
        onClick={apply}
        disabled={!!busy || !ready || !canApply}
        title={
          branch
            ? "Save edits to the isolated GitHub branch workspace"
            : canApply
              ? ""
              : "Run the tests on your current edit first, and fix anything it breaks"
        }
      >
        {busy === "apply"
          ? "Saving..."
          : branch
            ? "Save to branch"
            : "Apply to my repo"}
      </button>

      {applied && !branch && (
        <>
          <button
            className="ws-btn"
            onClick={undo}
            disabled={!!busy}
          >
            Undo apply
          </button>

          <button
            className="ws-btn"
            onClick={syncToOriginalFolder}
            disabled={!!busy || !appliedFiles.length}
          >
            {busy === "sync"
              ? "Syncing..."
              : "Sync to original folder"}
          </button>

          <button
            className="ws-btn"
            onClick={downloadUpdatedZip}
            disabled={!!busy}
          >
            {busy === "download"
              ? "Preparing ZIP..."
              : "Download updated ZIP"}
          </button>
        </>
      )}

      <span className="ws-grow" />

      <button
        className="ws-link"
        onClick={reset}
        disabled={!dirty && !verify}
      >
        Reset to AI patch
      </button>
    </div>
  </div>
)}