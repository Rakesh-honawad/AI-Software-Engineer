"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Maximize2, Minimize2, X } from "lucide-react"
import { computeImpact, generateGraph, type Graph, type GraphNode, type ImpactData } from "@/lib/graph/data"
import type { Point } from "@/lib/graph/geometry"
import { adaptGraph, adaptImpact } from "@/lib/graph/adapters"
import { api, ApiError, type InvestigationSuspect } from "@/services/api-client"
import { useForceLayout } from "./use-force-layout"
import { GraphCanvas, type GraphCanvasHandle } from "./graph-canvas"
import { NodeInspector, type NodeAction } from "./node-inspector"
import { NodeActionMenu } from "./node-action-menu"
import { AiPanel } from "./ai-panel"
import { TopBar } from "./top-bar"
import { Legend } from "./legend"
import { WorkspaceSidebar, FilesPanel, type PanelKey } from "./workspace-sidebar"
import { InvestigationPanel } from "./investigation-panel"
import { SandboxPanel } from "./sandbox-panel"

const DEMO_REPO_LABEL = "acme/platform (demo)"
const BRANCH = "main"
const PANEL_NARROW = 420
const PANEL_WIDE = 760

/**
 * Live vs. demo data. If `repositoryId` is passed, this fetches the REAL graph from
 * GET /repositories/{id}/graph. If it's omitted, the workspace falls back to the
 * synthetic mock graph (dev/demo only). A failed real fetch surfaces as loadError and
 * never falls back to fake data.
 */
function useRepositoryGraph(repositoryId: string | null) {
  const [graph, setGraph] = useState<Graph | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    if (!repositoryId) {
      setGraph(generateGraph(1))
      setLoadError(null)
      setIsLoading(false)
      return
    }

    setIsLoading(true)
    setLoadError(null)
    api
      .getGraph(repositoryId)
      .then((res) => {
        if (cancelled) return
        setGraph(adaptGraph(res))
        setIsLoading(false)
      })
      .catch((err) => {
        if (cancelled) return
        const message =
          err instanceof ApiError
            ? err.status === 404
              ? "This repository hasn't been indexed yet."
              : `Failed to load graph (${err.status}).`
            : "Failed to reach the knowledge-engine API."
        setLoadError(message)
        setIsLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [repositoryId])

  return { graph, loadError, isLoading }
}

export function EngineerWorkspace({ repositoryId = null }: { repositoryId?: string | null }) {
  const { graph: fetchedGraph, loadError, isLoading } = useRepositoryGraph(repositoryId)

  const graph = fetchedGraph ?? { nodes: [], edges: [] }
  const layout = useForceLayout(graph.nodes, graph.edges)

  const nodeById = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes])

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [inspectorOpen, setInspectorOpen] = useState(true)
  const [panel, setPanel] = useState<PanelKey | null>(null)
  const [panelWide, setPanelWide] = useState(false)
  const [impactActive, setImpactActive] = useState(false)
  const [impactData, setImpactData] = useState<ImpactData | null>(null)
  const [impactError, setImpactError] = useState<string | null>(null)

  const [menu, setMenu] = useState<{ node: GraphNode; screen: Point } | null>(null)
  const [seedPrompt, setSeedPrompt] = useState<string | null>(null)
  const [repositoryFiles, setRepositoryFiles] = useState<string[]>([])
  const [filesLoading, setFilesLoading] = useState(false)
  const [filesError, setFilesError] = useState<string | null>(null)
  const [filesTruncated, setFilesTruncated] = useState(false)
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null)
  const [sandboxOpenRequest, setSandboxOpenRequest] = useState(0)
  const [previewContent, setPreviewContent] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)

  // GitHub branch workspaces created during this workspace session.
  const [activeBranch, setActiveBranch] = useState("")
  const [githubBranches, setGithubBranches] = useState<string[]>([])
  const [branchBusy, setBranchBusy] = useState<"create" | "push" | null>(null)
  const [branchMessage, setBranchMessage] = useState<string | null>(null)
  const [branchError, setBranchError] = useState<string | null>(null)

  const canvasRef = useRef<GraphCanvasHandle>(null)

  const selectedNode = selectedId ? nodeById.get(selectedId) ?? null : null

  const matchCount = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return 0
    return graph.nodes.filter(
      (n) =>
        n.name.toLowerCase().includes(q) ||
        n.file_path.toLowerCase().includes(q) ||
        n.module.toLowerCase().includes(q),
    ).length
  }, [searchQuery, graph.nodes])

  // Recompute impact whenever selection changes while impact mode is on.
  useEffect(() => {
  let cancelled = false

  setRepositoryFiles([])
  setSelectedFilePath(null)
  setPreviewContent(null)
  setFilesError(null)
  setFilesTruncated(false)

  if (!repositoryId) {
    setFilesLoading(false)
    return
  }

  setFilesLoading(true)

  const filesRequest = activeBranch
    ? api.getGithubBranchFiles(repositoryId, activeBranch)
    : api.getRepositoryFiles(repositoryId)
  filesRequest.then((result) => {
      if (cancelled) return

      setRepositoryFiles(result.files)
      setFilesTruncated(result.truncated)
    })
    .catch((error) => {
      if (cancelled) return

      setFilesError(
        error instanceof ApiError
          ? `Could not load repository files (${error.status}).`
          : "Could not reach the repository file-list API.",
      )
    })
    .finally(() => {
      if (!cancelled) setFilesLoading(false)
    })

  return () => {
    cancelled = true
  }
}, [repositoryId, activeBranch])
  useEffect(() => {
    if (!impactActive || !selectedId) {
      setImpactData(null)
      setImpactError(null)
      return
    }

    if (!repositoryId) {
      setImpactData(computeImpact(graph, selectedId))
      setImpactError(null)
      return
    }

    let cancelled = false
    const node = nodeById.get(selectedId)
    if (!node) return

    api
      .getImpact(repositoryId, node.name)
      .then((res) => {
        if (!cancelled) {
          setImpactData(adaptImpact(res))
          setImpactError(null)
        }
      })
      .catch((err) => {
        if (cancelled) return
        setImpactData(null)
        if (err instanceof ApiError && err.status === 409) {
          setImpactError(`"${node.name}" matches multiple symbols â€” select the exact node on the canvas instead of by name.`)
        } else if (err instanceof ApiError && err.status === 404) {
          setImpactError(`Couldn't find "${node.name}" in the indexed graph.`)
        } else {
          setImpactError("Impact analysis failed.")
        }
      })

    return () => {
      cancelled = true
    }
  }, [impactActive, selectedId, graph, repositoryId, nodeById])

  const handleSelect = useCallback((id: string) => {
    setSelectedId(id)
    setInspectorOpen(true)
  }, [])

  const handleSelectRepositoryFile = async (path: string) => {
  if (!repositoryId) return

  setSelectedFilePath(path)
  setPreviewContent(null)
  setPreviewError(null)
  setPreviewLoading(true)

  try {
    const result = activeBranch
      ? await api.getGithubBranchFile(repositoryId, activeBranch, path)
      : await api.getFile(repositoryId, path)
    setPreviewContent(result.content)
  } catch (error) {
    setPreviewError(
      error instanceof ApiError
        ? `Could not open ${path} (${error.status}).`
        : `Could not open ${path}.`,
    )
  } finally {
    setPreviewLoading(false)
  }
}

  const focusSuspect = useCallback(
    (s: InvestigationSuspect) => {
      const norm = (p: string | null) => (p ?? "").replace(/\\/g, "/")
      const node =
        nodeById.get(s.node_id) ??
        graph.nodes.find((n) => n.name === s.name && norm(n.file_path) === norm(s.file_path))

      if (!node) return

      handleSelect(node.id)
      canvasRef.current?.focusNode(node.id)
    },
    [nodeById, graph.nodes, handleSelect],
  )

  const handleOpenMenu = useCallback((node: GraphNode, screen: Point) => {
    setSelectedId(node.id)
    setMenu({ node, screen })
  }, [])

  const runAction = useCallback(
    (action: NodeAction) => {
      const node = menu?.node ?? selectedNode
      if (!node) return
      switch (action) {
        case "open":
          // vscode://file deep link: only resolves if this path exists on the viewing machine.
          if (node.file_path) {
            window.location.href = `vscode://file/${node.file_path}`
          }
          setInspectorOpen(true)
          canvasRef.current?.focusNode(node.id)
          break
        case "connections":
          setImpactActive(true)
          setSelectedId(node.id)
          canvasRef.current?.focusNode(node.id)
          break
        case "explain":
          setSeedPrompt(`Explain ${node.name}`)
          break
        case "agent":
          setSeedPrompt(`What is the impact of changing ${node.name}?`)
          break
      }
    },
    [menu, selectedNode],
  )

  async function handleCreateGithubBranch(name: string) {
    if (!repositoryId) return

    setBranchBusy("create")
    setBranchError(null)
    setBranchMessage(null)

    try {
      const result = await api.createGithubBranch(repositoryId, name)
      setGithubBranches((current) =>
        current.includes(result.branch) ? current : [...current, result.branch]
      )
      setActiveBranch(result.branch)
      setBranchMessage(`Created ${result.branch} from ${result.base_branch}.`)
    } catch (error) {
      const detail = error instanceof ApiError
        ? (error.body as { detail?: unknown } | undefined)?.detail
        : null
      setBranchError(typeof detail === "string" ? detail : error instanceof Error ? error.message : "Could not create GitHub branch.")
    } finally {
      setBranchBusy(null)
    }
  }

  async function handlePushGithubBranch() {
    if (!repositoryId || !activeBranch) return
    if (!window.confirm(`Commit and push the saved changes on "${activeBranch}" to GitHub?`)) return

    setBranchBusy("push")
    setBranchError(null)
    setBranchMessage(null)

    try {
      const result = await api.pushGithubBranch(repositoryId, activeBranch)
      setBranchMessage(result.branch_url
        ? `Pushed ${result.branch}. ${result.branch_url}`
        : `Pushed ${result.branch}. Commit: ${result.commit_sha}`)
    } catch (error) {
      const detail = error instanceof ApiError
        ? (error.body as { detail?: unknown } | undefined)?.detail
        : null
      setBranchError(typeof detail === "string" ? detail : error instanceof Error ? error.message : "Could not push GitHub branch.")
    } finally {
      setBranchBusy(null)
    }
  }

  const repoLabel = repositoryId ?? DEMO_REPO_LABEL

  const topBar = (loaded: boolean) => (
    <TopBar
      repo={repoLabel}
      branch={activeBranch}
      branches={githubBranches}
      branchEnabled={!!repositoryId}
      branchBusy={branchBusy}
      branchMessage={branchMessage}
      branchError={branchError}
      onSelectBranch={(value) => {
        setActiveBranch(value)
        setBranchError(null)
        setBranchMessage(null)
      }}
      onCreateBranch={handleCreateGithubBranch}
      onPushBranch={handlePushGithubBranch}
      nodeCount={loaded ? graph.nodes.length : 0}
      edgeCount={loaded ? graph.edges.length : 0}
      searchQuery={loaded ? searchQuery : ""}
      matchCount={loaded ? matchCount : 0}
      onSearch={loaded ? setSearchQuery : () => {}}
      inspectorOpen={loaded ? inspectorOpen : false}
      onToggleInspector={loaded ? () => setInspectorOpen((o) => !o) : () => {}}
    />
  )

  if (loadError) {
    return (
      <main className="engineer-mode">
        {topBar(false)}
        <div className="em-loading">
          <div className="em-loading-text">Couldn't load this repository's graph: {loadError}</div>
        </div>
      </main>
    )
  }

  if (isLoading) {
    return (
      <main className="engineer-mode">
        {topBar(false)}
        <div className="em-loading">
          <div className="em-loading-text">Loading graphâ€¦</div>
        </div>
      </main>
    )
  }

  const panelTitle = panel === "files" ? "Files" : panel === "sandbox" ? "Sandbox" : "Investigate a bug"
  const filePaths = repositoryId
  ? repositoryFiles
  : graph.nodes.filter((n) => n.type === "file").map((n) => n.file_path)

  return (
    <main className="engineer-mode">
      {topBar(true)}

      <div className="em-stage">
        <WorkspaceSidebar
          panel={panel}
          onPanel={setPanel}
          impactActive={impactActive}
          onToggleImpact={() => setImpactActive((a) => !a)}
        />

        {/* Always mounted so investigation results survive switching to the Graph view. */}
        <aside
          className="ws-panel"
          hidden={panel === null}
          style={{ width: panelWide ? PANEL_WIDE : PANEL_NARROW }}
          aria-label={panelTitle}
        >
          <header className="ws-panel-head">
            <span>{panelTitle}</span>
            <span className="ws-grow" />
            {(panel === "investigate" || panel === "sandbox") && (
              <button
                className="ws-icon-btn"
                onClick={() => setPanelWide((w) => !w)}
                aria-label={panelWide ? "Narrow panel" : "Widen panel"}
              >
                {panelWide ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
              </button>
            )}
            <button className="ws-icon-btn" onClick={() => setPanel(null)} aria-label="Close panel">
              <X size={16} />
            </button>
          </header>

          <div className="ws-panel-body">
            <div hidden={panel !== "investigate"}>
              {repositoryId ? (
                <InvestigationPanel
                  repositoryId={repositoryId}
                  branch={activeBranch || null}
                  selectedName={selectedNode?.name ?? null}
                  wide={panelWide}
                  onWideChange={setPanelWide}
                  onSelectSuspect={focusSuspect}
                  onResult={(r) => {
                    if (!r || r.suspects.length === 0) return
                    const patched = r.files?.[0]?.path
                    focusSuspect(r.suspects.find((s) => s.file_path === patched) ?? r.suspects[0])
                  }}
                />
              ) : (
                <p className="ws-muted">Open an indexed repository to investigate bugs (the workspace is in demo mode).</p>
              )}
            </div>
            <div hidden={panel !== "sandbox"}>
              <SandboxPanel
                repositoryId={repositoryId}
                branch={activeBranch || null}
                filePaths={filePaths}
                selectedPath={selectedFilePath ?? selectedNode?.file_path ?? null}
                openRequest={sandboxOpenRequest}
                wide={panelWide}
                onWideChange={setPanelWide}
              />
            </div>
            {/* {panel === "files" && (
              <FilesPanel
                data={graph}
                onSelectNode={(id) => {
                  handleSelect(id)
                  canvasRef.current?.focusNode(id)
                }}
              />
            )} */}
            {panel === "files" && (
              <FilesPanel
                files={filePaths}
                loading={filesLoading}
                error={filesError}
                selectedPath={selectedFilePath}
                content={previewContent}
                previewLoading={previewLoading}
                previewError={previewError}
                truncated={filesTruncated}
                onSelectFile={handleSelectRepositoryFile}
                onEditInSandbox={() => {
                  setSandboxOpenRequest((n) => n + 1)
                  setPanel("sandbox")
                }}   
              />
          )}
          </div>
        </aside>

        <div className="em-canvas-wrap">
          <GraphCanvas
            ref={canvasRef}
            nodes={graph.nodes}
            edges={graph.edges}
            layout={layout}
            impactData={impactData}
            deadNodeIds={null}
            selectedNodeId={selectedId}
            searchQuery={searchQuery}
            onSelectNode={handleSelect}
            onOpenMenu={handleOpenMenu}
            onCloseMenu={() => setMenu(null)}
          />

          <Legend impactActive={impactActive} />

          {impactError && (
            <div className="em-float em-impact-error" role="alert">
              {impactError}
            </div>
          )}

          {menu && (
            <NodeActionMenu
              node={menu.node}
              screen={menu.screen}
              onAction={runAction}
              onClose={() => setMenu(null)}
            />
          )}
        </div>

        <NodeInspector
          node={selectedNode}
          impactData={impactData}
          isOpen={inspectorOpen}
          onClose={() => setInspectorOpen(false)}
          onAction={runAction}
        />

        <AiPanel
          repositoryId={repositoryId}
          selectedNode={selectedNode}
          allNodes={graph.nodes}
          seedPrompt={seedPrompt}
          onConsumeSeed={() => setSeedPrompt(null)}
        />
      </div>
    </main>
  )
}

