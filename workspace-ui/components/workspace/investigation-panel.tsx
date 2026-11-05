"use client"

import { useEffect, useState } from "react"
import { api, ApiError, type InvestigationCommand, type InvestigationResult, type InvestigationSuspect } from "@/services/api-client"
import { PatchEditor } from "./patch-editor"

const clean = (s: string) => s.replace(/\r/g, "").replace(/\uFEFF/g, "")

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="ws-pre">
      {clean(diff).split("\n").map((line, i) => {
        const cls =
          line.startsWith("+") && !line.startsWith("+++") ? "ws-add"
          : line.startsWith("-") && !line.startsWith("---") ? "ws-del"
          : line.startsWith("@@") ? "ws-hunk"
          : ""
        return <div key={i} className={cls}>{line || " "}</div>
      })}
    </pre>
  )
}

function TestRun({ title, run }: { title: string; run: InvestigationCommand | null | undefined }) {
  if (!run) return null
  const passed = run.exit_code === 0
  return (
    <div className="ws-stack" style={{ gap: 6 }}>
      <div className="ws-row">
        <span style={{ fontWeight: 600 }}>{title}</span>
        <span className={`ws-badge ${passed ? "ws-badge--good" : "ws-badge--bad"}`}>
          {passed ? "passed" : `failed (exit ${run.exit_code ?? "none"})`}
        </span>
        <span className="ws-muted">{run.duration_s.toFixed(1)}s</span>
      </div>
      <pre className="ws-pre">{clean(run.output)}</pre>
    </div>
  )
}

const BANNER: Record<InvestigationResult["status"], { cls: string; text: string }> = {
  fixed: { cls: "good", text: "Fix verified: the tests failed before the patch and pass after it." },
  fixed_partial: { cls: "good", text: "Fix verified for the failing test(s) below. Other tests still fail and are not caused by this patch." },
  needs_review: { cls: "warn", text: "The code may be correct. The test or the bug report might be wrong, so no patch was proposed. Review below." },
  not_fixed: { cls: "warn", text: "A patch was proposed but the tests still fail. Not verified." },
  unverified: { cls: "warn", text: "The tests already pass, so a fix cannot be proven. Patch is unverified." },
  no_patch: { cls: "bad", text: "No valid patch could be produced." },
  no_suspects: { cls: "bad", text: "No code matched the bug report or the test output." },
  tests_could_not_run: { cls: "bad", text: "The test command timed out or could not start. Fix the environment and retry." },
}

export function InvestigationPanel({
  repositoryId,
  branch,
  selectedName,
  onResult,
  onSelectSuspect,
  wide = false,
  onWideChange,
}: {
  repositoryId?: string | null
  branch?: string | null
  selectedName?: string | null
  onResult?: (r: InvestigationResult | null) => void
  onSelectSuspect?: (s: InvestigationSuspect) => void
  /** Whether the host panel is currently wide (the sandbox uses this for a side-by-side diff). */
  wide?: boolean
  /** Asks the host to widen/narrow itself, e.g. when the Sandbox tab opens. */
  onWideChange?: (wide: boolean) => void
} = {}) {
  const [repoInput, setRepoInput] = useState("")
  const repoId = repositoryId ?? repoInput
  const [bug, setBug] = useState("")
  const [testPath, setTestPath] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<InvestigationResult | null>(null)
  const [copied, setCopied] = useState(false)
  const [tab, setTab] = useState<"findings" | "sandbox">("findings")

  useEffect(() => {
    onWideChange?.(tab === "sandbox")
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  async function run() {
    setLoading(true); setError(null); setResult(null); setTab("findings"); onResult?.(null)
    try {
      const res = await api.investigate(repoId.trim(), bug.trim(), testPath.trim() || undefined)
      setResult(res)
      onResult?.(res)
    } catch (e) {
      if (e instanceof ApiError) {
        const detail = (e.body as { detail?: unknown } | undefined)?.detail
        setError(typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : e.message)
      } else {
        setError(e instanceof Error ? e.message : "Request failed")
      }
    } finally {
      setLoading(false)
    }
  }

  const banner = result
  ? BANNER[result.status] ?? {
      cls: "warn",
      text: `Investigation returned an unrecognized status: ${String(result.status)}. Review the results below.`,
    }
  : null

  const best = result
    ? [...(result.attempts ?? [])].reverse().find((a) => a.diff)
    : undefined
  const patchedRun = best?.verification?.patched
  const canEdit = Boolean(best && result?.files && result.files.length > 0)

  return (
    <div className="ws-stack">
      {!repositoryId && <h1 style={{ margin: 0, fontSize: 18 }}>AI Software Engineer</h1>}

      <div className="ws-stack" style={{ gap: 10 }}>
        {!repositoryId && (
          <label className="ws-label">Repository ID
            <input className="ws-field" value={repoInput} onChange={(e) => setRepoInput(e.target.value)} />
          </label>
        )}
        <label className="ws-label">Bug report
          <textarea
            className="ws-field"
            value={bug}
            placeholder="Describe the bug in plain language, or paste a stack trace."
            onChange={(e) => setBug(e.target.value)}
          />
        </label>
        <label className="ws-label">Test file or folder (optional)
          <input className="ws-field" value={testPath} placeholder="tests/test_cart.py" onChange={(e) => setTestPath(e.target.value)} />
        </label>

        {selectedName && !bug.includes(selectedName) && (
          <button type="button" className="ws-link" onClick={() => setBug((b) => `${b.trim()} (involving ${selectedName})`.trim())}>
            Mention selected node: {selectedName}
          </button>
        )}

        <div>
          <button className="ws-btn ws-btn--primary" onClick={run} disabled={loading || !repoId.trim() || !bug.trim()}>
            {loading ? "Investigating, this can take up to a minute" : "Investigate"}
          </button>
        </div>
        {error && <p className="ws-error" style={{ margin: 0 }}>{error}</p>}
      </div>

      {result && (
        <>
          <div className={`ws-banner ws-banner--${banner!.cls}`}>
            {banner!.text}
            {(result.attempts?.length ?? 0) > 1 && (
              <span className="ws-muted">
                ({result.attempts.length} attempts)
              </span>
            )}
          </div>
          {result.status === "fixed_partial" && (
            <p className="ws-muted" style={{ margin: 0, fontSize: 12 }}>
              Fixed: {(result.fixed_tests ?? []).join(", ")}. Still failing: {(result.remaining_failures ?? []).join(", ")}
            </p>
          )}

          {(result.status === "no_patch" || result.status === "tests_could_not_run") && result.message && (
            <p className="ws-muted" style={{ margin: 0 }}>{result.message}</p>
          )}

          {result.status === "needs_review" && (
            <div className="ws-stack" style={{ gap: 8 }}>
              <p style={{ margin: 0 }}>{result.verdict_reason}</p>
              {result.root_cause && <p className="ws-muted" style={{ margin: 0 }}>{result.root_cause}</p>}
              <p className="ws-muted" style={{ margin: 0, fontSize: 12 }}>
                Check the expected value in the failing test or your report against the real requirement. If the code is wrong after all, re-run with a more specific report.
              </p>
            </div>
          )}

          {result.evidence === "existing_failing_test" && (result.failing_tests?.length ?? 0) > 0 && (
            <p className="ws-muted" style={{ margin: 0, fontSize: 12 }}>
              Evidence: existing failing test(s): {result.failing_tests!.join(", ")}
            </p>
          )}

          {result.evidence === "generated_reproduction" && result.reproduction && (
            <div className="ws-stack" style={{ gap: 8 }}>
              <div className="ws-banner ws-banner--warn">
                Verified against a generated test. Review it before trusting this fix.
              </div>
              {result.reproduction.quote && (
                <p style={{ margin: 0 }}>From your report: <q>{result.reproduction.quote}</q></p>
              )}
              {result.reproduction.expectation && (
                <p style={{ margin: 0 }}>Expected behaviour tested: {result.reproduction.expectation}</p>
              )}
              {result.reproduction.code && (
                <pre className="ws-pre">{clean(result.reproduction.code)}</pre>
              )}
            </div>
          )}

          {canEdit && (
            <div className="ws-tabs" role="tablist">
              <button className="ws-tab" role="tab" aria-selected={tab === "findings"} onClick={() => setTab("findings")}>Findings</button>
              <button className="ws-tab" role="tab" aria-selected={tab === "sandbox"} onClick={() => setTab("sandbox")}>Sandbox</button>
            </div>
          )}

          <div className="ws-stack" hidden={tab !== "findings"}>
            <section>
              <h3 className="ws-h">Where it looked</h3>
              {result.suspects.length === 0 && <p className="ws-muted" style={{ margin: 0 }}>{result.message ?? "No suspects."}</p>}
              {result.suspects.map((s) => (
                <div
                  key={s.node_id}
                  onClick={() => onSelectSuspect?.(s)}
                  className={`ws-suspect${onSelectSuspect ? " is-clickable" : ""}`}
                >
                  <div style={{ fontWeight: 600 }}>
                    {s.name}{" "}
                    <span className="ws-muted" style={{ fontWeight: 400 }}>
                      {s.file_path}{s.line_range ? `:${s.line_range[0]}-${s.line_range[1]}` : ""}
                    </span>
                  </div>
                  <ul>{s.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
                  <div className="ws-muted" style={{ marginTop: 4, fontSize: 12 }}>{s.blast_radius} other symbols depend on this</div>
                </div>
              ))}
            </section>

            {best && (
              <>
                <section>
                  <h3 className="ws-h">Root cause <span className="ws-muted" style={{ fontWeight: 400 }}>(confidence: {result.confidence})</span></h3>
                  <p style={{ margin: 0 }}>{result.root_cause}</p>
                </section>
                <section>
                  <h3 className="ws-h">What changed and why</h3>
                  <p style={{ margin: 0 }}>{result.explanation}</p>
                </section>
                <section>
                  <div className="ws-row ws-row--between">
                    <h3 className="ws-h">Proposed patch</h3>
                    <button
                      className="ws-link"
                      onClick={() => { navigator.clipboard.writeText(clean(result.diff ?? "")); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
                    >
                      {copied ? "Copied" : "Copy diff"}
                    </button>
                  </div>
                  <DiffView diff={result.diff ?? ""} />
                </section>
              </>
            )}

            <section className="ws-stack" style={{ gap: 10 }}>
              <h3 className="ws-h" style={{ marginBottom: 0 }}>Test results</h3>
              <TestRun title="Before the patch" run={result.baseline} />
              <TestRun title="After the patch" run={patchedRun} />
              {best && !patchedRun && best.verification && (
                <p className="ws-error" style={{ margin: 0 }}>The patch did not apply: {best.verification.apply_log}</p>
              )}
            </section>

            <p className="ws-muted" style={{ margin: 0, fontSize: 12 }}>
              Tests were run in a temporary copy. Your original repository was not modified.
            </p>
          </div>

          {canEdit && (
            <div hidden={tab !== "sandbox"}>
              <PatchEditor
                key={result.diff}
                repositoryId={repoId.trim()}
                branch={branch}
                files={result.files!}
                wide={wide}
                preVerified={result.status === "fixed" || result.status === "fixed_partial"}
              />
            </div>
          )}
        </>
      )}
    </div>
  )
}

