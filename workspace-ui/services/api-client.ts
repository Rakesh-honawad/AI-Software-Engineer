
import type {
  ApiDeadCodeExplainResponse,
  ApiDeadCodeResult,
  ApiGraphResponse,
  ApiImpactAnalysisResult,
  ApiImpactNarrateResult,
  ApiRepositoryStatus,
} from "@/lib/graph/api-types"

import { createSupabaseClient } from "@/lib/supabase/client"

const API_BASE =
  process.env.NEXT_PUBLIC_KNOWLEDGE_ENGINE_API_URL ?? "http://localhost:8000"

export interface InvestigationSuspect {
  node_id: string
  name: string
  type: string
  file_path: string | null
  line_range: number[] | null
  score: number
  reasons: string[]
  blast_radius: number
}

export interface InvestigationCommand {
  command: string[]
  exit_code: number | null
  output: string
  duration_s: number
  timed_out: boolean
}

export interface InvestigationAttempt {
  attempt: number
  root_cause: string
  explanation: string
  confidence: string
  diff: string
  error: string | null
  verification: {
    patch_applied: boolean
    apply_log: string
    patched: InvestigationCommand | null
    fixed: boolean
  } | null
}

export interface InvestigationResult {
  status:
    | "fixed"
    | "not_fixed"
    | "unverified"
    | "fixed_partial"
    | "no_patch"
    | "no_suspects"
    | "tests_could_not_run"
    | "needs_review"
  suspects: InvestigationSuspect[]
  baseline: InvestigationCommand | null
  root_cause?: string
  explanation?: string
  confidence?: string
  diff?: string
  attempts: InvestigationAttempt[]
  message?: string
  files?: { path: string; patched: string }[]
  evidence?: "generated_reproduction" | "existing_failing_test" | "none"
  failing_tests?: string[]
  reproduction?: {
    status: string
    note?: string
    file?: string
    code?: string
    expectation?: string
    quote?: string
  }
  fixed_tests?: string[]
  remaining_failures?: string[]
  verdict?: "code_bug" | "test_or_report_wrong" | "unclear"
  verdict_reason?: string
}

export interface VerifyCommandSummary {
  exit_code: number | null
  duration_s: number
  timed_out: boolean
  junit_valid: boolean
  collection_errors: boolean
  total: number
  passed: number
  failed: number
  errors: number
  skipped: number
  failed_tests: string[]
  output: string
}

export interface VerifyEditResult {
  diff: string
  patch_applied: boolean
  apply_log: string
  scope: "quick" | "full"
  complete: boolean
  safe_to_apply: boolean
  verdict: string
  tests_pass_after: boolean
  baseline: VerifyCommandSummary | null
  patched: VerifyCommandSummary | null
  baseline_failures: string[]
  still_failing: string[]
  new_failures: string[]
  errors: string[]
}


export interface GithubBranchWorkspace {
  branch: string
  base_branch: string
  base_sha: string
  workspace_ready: boolean
}

export interface GithubBranchFileList {
  files: string[]
  count: number
  truncated: boolean
}

export interface GithubBranchApplyResult {
  saved: string[]
  diff: string
  verification: string
  note: string
}

export interface GithubBranchPushResult {
  pushed: boolean
  branch: string
  commit_sha: string
  branch_url: string
  note: string
}
export class ApiError extends Error {
  status: number
  body: unknown

  constructor(status: number, message: string, body?: unknown) {
    super(message)
    this.status = status
    this.body = body
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const supabase = createSupabaseClient()
  const {
    data: { session },
    error,
  } = await supabase.auth.getSession()

  if (error) {
    throw new Error("Unable to retrieve authentication session.")
  }

  const token = session?.access_token
  const isFormData =
    typeof FormData !== "undefined" &&
    init?.body instanceof FormData

  const headers = new Headers(init?.headers)

  if (!isFormData && init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }

  if (token) {
    headers.set("Authorization", `Bearer ${token}`)
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  })

  if (!res.ok) {
    let body: unknown

    try {
      body = await res.json()
    } catch {
      body = undefined
    }

    const detail =
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof body.detail === "string"
        ? body.detail
        : `Request failed: ${res.status} ${res.statusText}`

    throw new ApiError(res.status, detail, body)
  }

  return res.json() as Promise<T>
}

export async function getCurrentUser() {
  return request<{
    authenticated: boolean
    user_id: string
    email: string | null
  }>("/auth/me")
}

export interface GithubInstallation {
  installation_id: number
  account_login: string
  account_type: string
  created_at: string
  updated_at: string
}

export interface GithubRepository {
  id: number
  name: string
  full_name: string
  private: boolean
  default_branch: string
  html_url: string
}

export const api = {
  createGithubBranch: (
    repositoryId: string,
    branchName?: string,
  ) =>
    request<GithubBranchWorkspace>(
      `/repositories/${encodeURIComponent(repositoryId)}/github/branches`,
      {
        method: "POST",
        body: JSON.stringify({
          branch_name: branchName || undefined,
        }),
      },
    ),

  getGithubBranchFiles: (
    repositoryId: string,
    branch: string,
  ) =>
    request<GithubBranchFileList>(
      `/repositories/${encodeURIComponent(repositoryId)}/github/branch/files?branch=${encodeURIComponent(branch)}`,
    ),

  getGithubBranchFile: (
    repositoryId: string,
    branch: string,
    path: string,
  ) =>
    request<{ path: string; content: string }>(
      `/repositories/${encodeURIComponent(repositoryId)}/github/branch/file?branch=${encodeURIComponent(branch)}&path=${encodeURIComponent(path)}`,
    ),

  applyGithubBranchEdits: (
    repositoryId: string,
    branch: string,
    files: { path: string; content: string }[],
  ) =>
    request<GithubBranchApplyResult>(
      `/repositories/${encodeURIComponent(repositoryId)}/github/branch/apply`,
      {
        method: "POST",
        body: JSON.stringify({
          branch,
          files,
          confirm: true,
        }),
      },
    ),

  pushGithubBranch: (
    repositoryId: string,
    branch: string,
  ) =>
    request<GithubBranchPushResult>(
      `/repositories/${encodeURIComponent(repositoryId)}/github/branch/push`,
      {
        method: "POST",
        body: JSON.stringify({
          branch,
          confirm: true,
        }),
      },
    ),

  getRepositoryStatus: (repositoryId: string) =>
    request<ApiRepositoryStatus>(
      `/repositories/${encodeURIComponent(repositoryId)}`,
    ),

  getGraph: (repositoryId: string) =>
    request<ApiGraphResponse>(
      `/repositories/${encodeURIComponent(repositoryId)}/graph`,
    ),

  getImpact: (
    repositoryId: string,
    symbolName: string,
    nodeType?: string,
  ) =>
    request<ApiImpactAnalysisResult>(
      `/repositories/${encodeURIComponent(repositoryId)}/impact`,
      {
        method: "POST",
        body: JSON.stringify({
          symbol_name: symbolName,
          node_type: nodeType,
        }),
      },
    ),

  downloadRepositoryZip: async (repositoryId: string): Promise<Blob> => {
    const supabase = createSupabaseClient()
    const {
      data: { session },
      error,
    } = await supabase.auth.getSession()

    if (error) {
      throw new Error("Unable to retrieve authentication session.")
    }

    const headers = new Headers()
    if (session?.access_token) {
      headers.set("Authorization", `Bearer ${session.access_token}`)
    }

    const response = await fetch(
      `${API_BASE}/repositories/${encodeURIComponent(repositoryId)}/export.zip`,
      { headers },
    )

    if (!response.ok) {
      throw new Error(
        (await response.text()) || "Could not export repository ZIP.",
      )
    }

    return response.blob()
  },
  getRepositoryFiles: (repositoryId: string) =>
    request<{
      files: string[]
      count: number
      truncated: boolean
    }>(`/repositories/${encodeURIComponent(repositoryId)}/files`),

  getFile: (repositoryId: string, path: string) =>
    request<{ path: string; content: string }>(
      `/repositories/${encodeURIComponent(repositoryId)}/file?path=${encodeURIComponent(path)}`,
    ),

  verifyEdit: (
    repositoryId: string,
    files: { path: string; content: string }[],
    testPath?: string,
  ) =>
    request<VerifyEditResult>(
      `/repositories/${encodeURIComponent(repositoryId)}/verify`,
      {
        method: "POST",
        body: JSON.stringify({
          files,
          test_path: testPath || undefined,
        }),
      },
    ),

  applyEdit: (
    repositoryId: string,
    files: { path: string; content: string }[],
  ) =>
    request<{
      applied: string[]
      diff: string
      verification: {
        complete: boolean
        safe_to_apply: boolean
        tests_pass_after: boolean
        verdict: string
        baseline: VerifyCommandSummary | null
        patched: VerifyCommandSummary | null
        baseline_failures: string[]
        still_failing: string[]
        new_failures: string[]
        errors: string[]
      }
      restore_token: string
      note: string
    }>(`/repositories/${encodeURIComponent(repositoryId)}/apply`, {
      method: "POST",
      body: JSON.stringify({ files, confirm: true }),
    }),

  restoreEdit: (repositoryId: string, token: string) =>
    request<{ restored: string[]; note: string }>(
      `/repositories/${encodeURIComponent(repositoryId)}/restore`,
      {
        method: "POST",
        body: JSON.stringify({ token, confirm: true }),
      },
    ),

  narrateImpact: (
    repositoryId: string,
    symbolName: string,
    nodeType?: string,
  ) =>
    request<ApiImpactNarrateResult>(
      `/repositories/${encodeURIComponent(repositoryId)}/impact/narrate`,
      {
        method: "POST",
        body: JSON.stringify({
          symbol_name: symbolName,
          node_type: nodeType,
        }),
      },
    ),

  beginGithubInstall: () =>
    request<{ install_url: string; expires_at: string }>(
      "/auth/github/install",
    ),

  confirmGithubInstall: (state: string) =>
    request<{
      connected: boolean
      installation_id: number
      account_login: string
      account_type: string
    }>("/auth/github/confirm", {
      method: "POST",
      body: JSON.stringify({ state }),
    }),

  getGithubInstallations: () =>
    request<{ installations: GithubInstallation[] }>(
      "/auth/github/installations",
    ),

  getGithubInstallationRepositories: (installationId: number) =>
    request<{ repositories: GithubRepository[] }>(
      `/auth/github/installations/${installationId}/repositories`,
    ),

  disconnectGithubInstallation: (installationId: number) =>
    request<{ disconnected: boolean }>(
      `/auth/github/installations/${installationId}`,
      { method: "DELETE" },
    ),

  indexGithubAppRepository: (params: {
    repositoryId: string
    installationId: number
    githubRepositoryId: number
  }) =>
    request<{ repository_id: string }>(
      "/repositories/index/github-app",
      {
        method: "POST",
        body: JSON.stringify({
          repository_id: params.repositoryId,
          installation_id: params.installationId,
          github_repository_id: params.githubRepositoryId,
        }),
      },
    ),

  indexRepository: (params: {
    repositoryId: string
    localPath?: string
    githubOwner?: string
    githubRepo?: string
    githubToken?: string
  }) =>
    request<{ repository_id: string }>("/repositories/index", {
      method: "POST",
      body: JSON.stringify({
        repository_id: params.repositoryId,
        local_path: params.localPath,
        github_owner: params.githubOwner,
        github_repo: params.githubRepo,
        github_token: params.githubToken,
      }),
    }),

  uploadFolder: (
    repositoryId: string,
    files: File[],
  ) => {
    const formData = new FormData()
    formData.append("repository_id", repositoryId)

    const relativePaths = files.map((file) => {
      const relativePath =
        (file as File & { webkitRelativePath?: string })
          .webkitRelativePath || file.name

      return relativePath
    })

    formData.append(
      "relative_paths_json",
      JSON.stringify(relativePaths),
    )

    for (const file of files) {
      formData.append("files", file, file.name)
    }

    return request<ApiRepositoryStatus>(
      "/repositories/upload-folder",
      {
        method: "POST",
        body: formData,
      },
    )
  },

  uploadZip: (
    repositoryId: string,
    file: File,
  ) => {
    const formData = new FormData()
    formData.append("repository_id", repositoryId)
    formData.append("file", file, file.name)

    return request<ApiRepositoryStatus>(
      "/repositories/upload-zip",
      {
        method: "POST",
        body: formData,
      },
    )
  },

  getDeadCode: (repositoryId: string) =>
    request<ApiDeadCodeResult>(
      `/repositories/${encodeURIComponent(repositoryId)}/dead-code`,
    ),

  explainDeadCode: (repositoryId: string, nodeId: string) =>
    request<ApiDeadCodeExplainResponse>(
      `/repositories/${encodeURIComponent(repositoryId)}/dead-code/explain`,
      {
        method: "POST",
        body: JSON.stringify({ node_id: nodeId }),
      },
    ),

  explainNode: (repositoryId: string, nodeId: string) =>
    request<{
      node_id: string
      name: string
      file_path: string | null
      explanation: string
    }>(`/repositories/${encodeURIComponent(repositoryId)}/explain`, {
      method: "POST",
      body: JSON.stringify({ node_id: nodeId }),
    }),

  askRepo: (repositoryId: string, question: string) =>
    request<{
      answer: string
      search_terms: string[]
      matched_node_count: number
    }>(`/repositories/${encodeURIComponent(repositoryId)}/ask`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),

  investigate: (
    repositoryId: string,
    bugReport: string,
    testPath?: string,
  ) =>
    request<InvestigationResult>(
      `/repositories/${encodeURIComponent(repositoryId)}/investigate`,
      {
        method: "POST",
        body: JSON.stringify({
          bug_report: bugReport,
          test_path: testPath || undefined,
        }),
      },
    ),
}

