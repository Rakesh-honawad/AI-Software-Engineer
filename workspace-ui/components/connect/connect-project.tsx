
"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import {
  GitBranch,
  FolderUp,
  FileArchive,
  Loader2,
  Upload,
} from "lucide-react"
import {
  api,
  type GithubInstallation,
  type GithubRepository,
} from "@/services/api-client"
import styles from "./connect-project.module.css"

type Phase = "idle" | "indexing" | "failed"
type Source = "github" | "folder" | "zip"

function parseGithub(input: string): { owner: string; repo: string } | null {
  const value = input.trim().replace(/\.git$/, "").replace(/\/+$/, "")
  const match = value.match(
    /^(?:https?:\/\/(?:www\.)?github\.com\/)?([\w.-]+)\/([\w.-]+)$/
  )

  return match ? { owner: match[1], repo: match[2] } : null
}

function createRepositoryId(value: string): string {
  const id = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100)

  return id || `local-project-${Date.now()}`
}

function getFolderName(files: File[]): string {
  const firstPath = (
    files[0] as File & { webkitRelativePath?: string }
  )?.webkitRelativePath

  return firstPath?.split("/")[0] || "local-project"
}

const IGNORED_DIRECTORIES = new Set([
  "node_modules",
  ".git",
  ".next",
  ".turbo",
  ".vercel",
  "dist",
  "build",
  "coverage",
  ".cache",
  ".venv",
  "venv",
  "__pycache__",
  ".pytest_cache",
  ".mypy_cache",
  ".ruff_cache",
  "target",
])

const MAX_FOLDER_FILES = 5000
const MAX_FOLDER_TOTAL_BYTES = 300 * 1024 * 1024

function shouldIgnoreUploadFile(file: File): boolean {
  const relativePath =
    (file as File & { webkitRelativePath?: string }).webkitRelativePath ||
    file.name

  const segments = relativePath.split("/").filter(Boolean)
  const filename = segments[segments.length - 1]?.toLowerCase() || ""

  if (segments.some((segment) => IGNORED_DIRECTORIES.has(segment.toLowerCase()))) {
    return true
  }

  if (
    filename === ".env" ||
    (filename.startsWith(".env.") &&
      ![".env.example", ".env.sample", ".env.template"].includes(filename))
  ) {
    return true
  }

  if (
    filename.endsWith(".pem") ||
    filename.endsWith(".key") ||
    filename.endsWith(".p12") ||
    filename.endsWith(".pfx") ||
    ["id_rsa", "id_ed25519", "id_ecdsa"].includes(filename)
  ) {
    return true
  }

  return false
}

function filterFolderFiles(files: File[]): File[] {
  return files.filter((file) => !shouldIgnoreUploadFile(file))
}

function getZipName(file: File): string {
  return file.name.replace(/\.zip$/i, "") || "uploaded-project"
}

export function ConnectProject() {
  const router = useRouter()

  const [source, setSource] = useState<Source>("github")
  const [repository, setRepository] = useState("")
  const [token, setToken] = useState("")
  const [githubInstallations, setGithubInstallations] =
    useState<GithubInstallation[]>([])
  const [selectedInstallationId, setSelectedInstallationId] = useState("")
  const [githubRepositories, setGithubRepositories] =
    useState<GithubRepository[]>([])
  const [selectedGithubRepositoryId, setSelectedGithubRepositoryId] =
    useState("")
  const [githubLoading, setGithubLoading] = useState(false)
  const [githubMode, setGithubMode] = useState<"app" | "pat">("app")
  const [folderFiles, setFolderFiles] = useState<File[]>([])
  const [zipFile, setZipFile] = useState<File | null>(null)

  const [phase, setPhase] = useState<Phase>("idle")
  const [status, setStatus] = useState("")
  const [error, setError] = useState<string | null>(null)

  const active = useRef(true)
  const polling = useRef(false)

  useEffect(() => {
    active.current = true

    return () => {
      active.current = false
    }
  }, [])

  // Complete the one-time installation confirmation after GitHub redirects
  // the browser back to this page with github_state.
  useEffect(() => {
    let cancelled = false

    async function initializeGithub() {
      const params = new URLSearchParams(window.location.search)
      const pendingState = params.get("github_state")

      try {
        if (pendingState) {
          setGithubLoading(true)
          setStatus("Confirming GitHub installation?")
          await api.confirmGithubInstall(pendingState)

          params.delete("github_state")
          const remainingQuery = params.toString()
          window.history.replaceState(
            {},
            "",
            `${window.location.pathname}${remainingQuery ? `?${remainingQuery}` : ""}`,
          )
        }

        const result = await api.getGithubInstallations()
        if (cancelled) return

        setGithubInstallations(result.installations)
        if (result.installations.length > 0) {
          setSelectedInstallationId(
            String(result.installations[0].installation_id),
          )
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "Could not load GitHub installations.",
          )
        }
      } finally {
        if (!cancelled) setGithubLoading(false)
      }
    }

    void initializeGithub()

    return () => {
      cancelled = true
    }
  }, [])

  // Load repositories only for the installation selected by this user.
  useEffect(() => {
    let cancelled = false

    async function loadGithubRepositories() {
      if (!selectedInstallationId) {
        setGithubRepositories([])
        setSelectedGithubRepositoryId("")
        return
      }

      setGithubLoading(true)
      setError(null)

      try {
        const result = await api.getGithubInstallationRepositories(
          Number(selectedInstallationId),
        )
        if (cancelled) return

        setGithubRepositories(result.repositories)
        setSelectedGithubRepositoryId(
          result.repositories.length > 0
            ? String(result.repositories[0].id)
            : "",
        )
      } catch (err) {
        if (!cancelled) {
          setGithubRepositories([])
          setSelectedGithubRepositoryId("")
          setError(
            err instanceof Error
              ? err.message
              : "Could not load repositories from GitHub.",
          )
        }
      } finally {
        if (!cancelled) setGithubLoading(false)
      }
    }

    void loadGithubRepositories()

    return () => {
      cancelled = true
    }
  }, [selectedInstallationId])

  function selectSource(nextSource: Source) {
    if (polling.current) return

    setSource(nextSource)
    setError(null)
    setPhase("idle")
  }

  async function waitUntilReady(repositoryId: string) {
    const started = Date.now()
    const timeoutMs = 5 * 60 * 1000

    while (active.current && Date.now() - started < timeoutMs) {
      await new Promise((resolve) => setTimeout(resolve, 2000))

      if (!active.current) return

      const result = await api.getRepositoryStatus(repositoryId)
      const indexStatus = String(result.index_status ?? "").toLowerCase()

      if (indexStatus === "ready") return

      if (indexStatus === "failed") {
        const details = (
          result as typeof result & { error_message?: string }
        ).error_message

        throw new Error(details || "Repository indexing failed.")
      }

      setStatus(
        indexStatus
          ? `Indexing repository — ${indexStatus}…`
          : "Indexing repository…"
      )
    }

    if (active.current) {
      throw new Error(
        "Indexing timed out. Please check the repository status and retry."
      )
    }
  }

  async function finishIndexing(repositoryId: string) {
    if (!active.current) return

    setStatus("Upload received. Starting repository indexing…")
    await waitUntilReady(repositoryId)

    if (active.current) {
      router.push(`/workspace/${encodeURIComponent(repositoryId)}`)
    }
  }

  async function startGithubInstallation() {
    setError(null)
    setGithubLoading(true)

    try {
      const result = await api.beginGithubInstall()
      window.location.assign(result.install_url)
    } catch (err) {
      setGithubLoading(false)
      setError(
        err instanceof Error
          ? err.message
          : "Could not start GitHub App installation.",
      )
    }
  }

  async function connectGithubApp() {
    const installationId = Number(selectedInstallationId)
    const selectedRepository = githubRepositories.find(
      (item) => item.id === Number(selectedGithubRepositoryId),
    )

    if (!installationId || !selectedRepository) {
      throw new Error("Choose a connected GitHub account and repository.")
    }

    const repositoryId = createRepositoryId(selectedRepository.full_name)

    setStatus(`Starting indexing for ${selectedRepository.full_name}?`)

    await api.indexGithubAppRepository({
      repositoryId,
      installationId,
      githubRepositoryId: selectedRepository.id,
    })

    await finishIndexing(repositoryId)
  }

  async function connectGithub() {
    const parsed = parseGithub(repository)

    if (!parsed) {
      throw new Error(
        "Enter a valid GitHub repository as owner/repo or paste its GitHub URL."
      )
    }

    const repositoryId = createRepositoryId(
      `${parsed.owner}-${parsed.repo}`
    )

    setStatus("Connecting to GitHub…")

    await api.indexRepository({
      repositoryId,
      githubOwner: parsed.owner,
      githubRepo: parsed.repo,
      githubToken: token.trim() || undefined,
    })

    await finishIndexing(repositoryId)
  }

  async function connectFolder() {
    if (folderFiles.length === 0) {
      throw new Error("Choose a local folder before continuing.")
    }

    if (folderFiles.length > MAX_FOLDER_FILES) {
      throw new Error(`Maximum ${MAX_FOLDER_FILES} files allowed.`)
    }

    const totalBytes = folderFiles.reduce(
      (total, file) => total + file.size,
      0,
    )

    if (totalBytes > MAX_FOLDER_TOTAL_BYTES) {
      throw new Error("The selected folder exceeds the 300 MB upload limit.")
    }

    const repositoryId = createRepositoryId(getFolderName(folderFiles))

    setStatus(`Uploading ${folderFiles.length} files…`)

    await api.uploadFolder(repositoryId, folderFiles)

    await finishIndexing(repositoryId)
  }

  async function connectZip() {
    if (!zipFile) {
      throw new Error("Choose a ZIP file before continuing.")
    }

    if (!zipFile.name.toLowerCase().endsWith(".zip")) {
      throw new Error("Please select a valid .zip file.")
    }

    const repositoryId = createRepositoryId(getZipName(zipFile))

    setStatus("Uploading ZIP archive…")

    await api.uploadZip(repositoryId, zipFile)

    await finishIndexing(repositoryId)
  }

  async function connect() {
    if (polling.current) return

    polling.current = true
    setError(null)
    setPhase("indexing")

    try {
      if (source === "github") {
        if (githubMode === "app") {
          await connectGithubApp()
        } else {
          await connectGithub()
        }
      } else if (source === "folder") {
        await connectFolder()
      } else {
        await connectZip()
      }
    } catch (err) {
      if (active.current) {
        setPhase("failed")
        setError(
          err instanceof Error
            ? err.message
            : "Could not connect or index this project."
        )
      }
    } finally {
      polling.current = false
    }
  }

  if (phase === "indexing") {
    return (
      <main className={styles.loading}>
        <Loader2 className={styles.spin} size={28} />
        <p>{status || "Preparing repository…"}</p>
        <p className={styles.muted}>
          This may take a few minutes depending on repository size.
          Keep this page open while indexing runs.
        </p>
      </main>
    )
  }

  return (
    <main className={styles.root}>
      <div className={styles.header}>
        <GitBranch size={24} />
        <h1>Connect a project</h1>
        <p>
          Choose how you want to provide your codebase to Engineer Mode.
        </p>
      </div>

      <div className="mx-auto w-full max-w-lg space-y-5">
        <div className={styles.sources}>
          <button
            type="button"
            className={
              source === "github" ? styles.sourceActive : styles.source
            }
            onClick={() => selectSource("github")}
          >
            <GitBranch size={17} />
            GitHub
          </button>

          <button
            type="button"
            className={
              source === "folder" ? styles.sourceActive : styles.source
            }
            onClick={() => selectSource("folder")}
          >
            <FolderUp size={17} />
            Local folder
          </button>

          <button
            type="button"
            className={
              source === "zip" ? styles.sourceActive : styles.source
            }
            onClick={() => selectSource("zip")}
          >
            <FileArchive size={17} />
            ZIP upload
          </button>
        </div>

        {source === "github" && (
          <div className="space-y-5">
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setGithubMode("app")}
                className={`rounded-lg border px-3 py-2 text-sm ${
                  githubMode === "app"
                    ? "border-white/30 bg-white/10 text-white"
                    : "border-white/10 text-white/50"
                }`}
              >
                GitHub App
              </button>
              <button
                type="button"
                onClick={() => setGithubMode("pat")}
                className={`rounded-lg border px-3 py-2 text-sm ${
                  githubMode === "pat"
                    ? "border-white/30 bg-white/10 text-white"
                    : "border-white/10 text-white/50"
                }`}
              >
                Personal access token
              </button>
            </div>

            {githubMode === "app" ? (
              <div className="space-y-4">
                <div className="rounded-lg border border-white/10 bg-[#1c1d24] p-4">
                  <div className="mb-2 text-sm font-medium">
                    Connect your GitHub account
                  </div>
                  <p className="mb-4 text-xs text-white/50">
                    Authorize the Engineer Mode GitHub App, then choose a
                    repository that the installation can access.
                  </p>
                  <button
                    type="button"
                    onClick={() => void startGithubInstallation()}
                    disabled={githubLoading}
                    className="rounded-lg bg-white px-4 py-2.5 text-sm font-semibold text-black disabled:opacity-50"
                  >
                    {githubLoading ? "Please wait?" : "Connect GitHub"}
                  </button>
                </div>

                {githubInstallations.length > 0 && (
                  <>
                    <label className="block space-y-2 text-sm">
                      <span>Connected GitHub account</span>
                      <select
                        value={selectedInstallationId}
                        onChange={(event) =>
                          setSelectedInstallationId(event.target.value)
                        }
                        className="w-full rounded-lg border border-white/10 bg-[#1c1d24] p-3 text-white"
                      >
                        {githubInstallations.map((installation) => (
                          <option
                            key={installation.installation_id}
                            value={String(installation.installation_id)}
                          >
                            {installation.account_login} ({installation.account_type})
                          </option>
                        ))}
                      </select>
                    </label>

                    <label className="block space-y-2 text-sm">
                      <span>Repository</span>
                      <select
                        value={selectedGithubRepositoryId}
                        onChange={(event) =>
                          setSelectedGithubRepositoryId(event.target.value)
                        }
                        disabled={
                          githubLoading || githubRepositories.length === 0
                        }
                        className="w-full rounded-lg border border-white/10 bg-[#1c1d24] p-3 text-white disabled:opacity-50"
                      >
                        {githubRepositories.length === 0 ? (
                          <option value="">No accessible repositories found</option>
                        ) : (
                          githubRepositories.map((repo) => (
                            <option key={repo.id} value={String(repo.id)}>
                              {repo.full_name}{repo.private ? " ? Private" : ""}
                            </option>
                          ))
                        )}
                      </select>
                    </label>

                    <p className="text-xs text-white/40">
                      Indexing uses a repository-scoped installation token.
                      Your default branch is not modified by indexing.
                    </p>
                  </>
                )}
              </div>
            ) : (
              <div className="space-y-5">
                <label className="block space-y-2 text-sm">
                  <span>GitHub repository</span>
                  <input
                    value={repository}
                    onChange={(event) => setRepository(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") void connect()
                    }}
                    placeholder="owner/repo or https://github.com/owner/repo"
                    autoComplete="url"
                    className="w-full rounded-lg border border-white/10 bg-[#1c1d24] p-3 text-white outline-none focus:border-white/30"
                  />
                </label>

                <label className="block space-y-2 text-sm">
                  <span>GitHub access token</span>
                  <input
                    type="password"
                    value={token}
                    onChange={(event) => setToken(event.target.value)}
                    placeholder="Enter token for private repositories"
                    autoComplete="off"
                    className="w-full rounded-lg border border-white/10 bg-[#1c1d24] p-3 text-white outline-none focus:border-white/30"
                  />
                </label>

                <p className="text-xs text-white/40">
                  Legacy compatibility option. Use a fine-grained token with
                  repository contents read access.
                </p>
              </div>
            )}
          </div>
        )}

        {source === "folder" && (
          <div className="space-y-4">
            <div className="rounded-lg border border-white/10 bg-[#1c1d24] p-5">
              <div className="mb-3 flex items-center gap-2 text-sm font-medium">
                <FolderUp size={19} />
                Select a project folder
              </div>

              <p className="mb-4 text-xs text-white/50">
                Choose a folder from your computer. Its files and relative
                directory structure will be uploaded for indexing.
              </p>

              <label className="flex cursor-pointer items-center justify-center gap-2 rounded-lg border border-white/15 bg-white/5 px-4 py-3 text-sm transition hover:bg-white/10">
                <Upload size={17} />
                Browse local folder
                <input
                  type="file"
                  multiple
                  className="sr-only"
                  {...{ webkitdirectory: "", directory: "" }}
                  onChange={(event) => {
                    const selectedFiles = Array.from(event.target.files || [])
                    const filteredFiles = filterFolderFiles(selectedFiles)

                    setFolderFiles(filteredFiles)
                    setError(null)

                    if (filteredFiles.length === 0) {
                      setError("No eligible source files found in the selected folder.")
                    } else if (filteredFiles.length > MAX_FOLDER_FILES) {
                      setError(
                        `This folder contains ${filteredFiles.length} eligible files. Maximum allowed is ${MAX_FOLDER_FILES}.`
                      )
                    } else {
                      const totalBytes = filteredFiles.reduce(
                        (total, file) => total + file.size,
                        0,
                      )

                      if (totalBytes > MAX_FOLDER_TOTAL_BYTES) {
                        setError("The selected folder exceeds the 300 MB upload limit.")
                      }
                    }
                  }}
                />
              </label>

              {folderFiles.length > 0 && (
                <p className="mt-3 text-xs text-green-300">
                  {getFolderName(folderFiles)} — {folderFiles.length} files
                  selected
                </p>
              )}
            </div>

            <p className="text-xs text-white/40">
              Hidden files may be included by your browser. Do not upload
              secrets, credentials, private keys, or environment files.
            </p>
          </div>
        )}

        {source === "zip" && (
          <div className="space-y-4">
            <div className="rounded-lg border border-white/10 bg-[#1c1d24] p-5">
              <div className="mb-3 flex items-center gap-2 text-sm font-medium">
                <FileArchive size={19} />
                Upload a ZIP archive
              </div>

              <p className="mb-4 text-xs text-white/50">
                Select a .zip archive containing your project files.
              </p>

              <label className="flex cursor-pointer items-center justify-center gap-2 rounded-lg border border-white/15 bg-white/5 px-4 py-3 text-sm transition hover:bg-white/10">
                <Upload size={17} />
                Browse ZIP file
                <input
                  type="file"
                  accept=".zip,application/zip"
                  className="sr-only"
                  onChange={(event) => {
                    setZipFile(event.target.files?.[0] || null)
                    setError(null)
                  }}
                />
              </label>

              {zipFile && (
                <p className="mt-3 break-all text-xs text-green-300">
                  {zipFile.name} — {(zipFile.size / (1024 * 1024)).toFixed(2)} MB
                </p>
              )}
            </div>

            <p className="text-xs text-white/40">
              ZIP archives are checked and safely extracted by the backend
              before indexing.
            </p>
          </div>
        )}

        {error && (
          <div className={styles.error} role="alert">
            {error}
          </div>
        )}

        <button
          type="button"
          onClick={() => void connect()}
          disabled={
            (source === "github" &&
              (githubMode === "app"
                ? (!selectedInstallationId || !selectedGithubRepositoryId || githubLoading)
                : !repository.trim())) ||
            (source === "folder" && folderFiles.length === 0) ||
            (source === "zip" && !zipFile)
          }
          className="w-full rounded-lg bg-white px-4 py-3 text-sm font-semibold text-black transition hover:bg-white/90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {source === "github"
            ? githubMode === "app"
              ? "Index selected repository"
              : "Connect and index repository"
            : source === "folder"
              ? "Upload folder and index"
              : "Upload ZIP and index"}
        </button>
      </div>
    </main>
  )
}
