# AI Software Engineer

> **A repository-aware engineering workspace that understands a codebase, investigates bugs, proposes fixes, and verifies them with real tests.**

**Graph first. AI second. Tests decide.**

AI Software Engineer (Engineer Mode) does not paste a whole repository into an LLM and hope for the best. It first **parses** the code, **extracts symbols and relationships**, and builds a **knowledge graph**. AI is then used only to explain, reason, and propose changes over *retrieved evidence*. Every proposed fix is a reviewable diff, and **automated tests**, not the model, decide whether a change is safe.

- Live workspace: https://mini-ai-workspace.vercel.app
- Backend health check: https://ai-software-engineer-84ow.onrender.com/health

---

## Table of contents

1. [Screenshots](#1-screenshots)
2. [What it does](#2-what-it-does)
3. [How a developer uses it](#3-how-a-developer-uses-it)
4. [Core philosophy](#4-core-philosophy)
5. [System architecture](#5-system-architecture)
6. [Repository sources and working modes](#6-repository-sources-and-working-modes)
7. [Knowledge Engine](#7-knowledge-engine)
8. [AI Engineering Engine](#8-ai-engineering-engine)
9. [Investigate](#9-investigate)
10. [Workspace: files, sandbox, and patches](#10-workspace-files-sandbox-and-patches)
11. [Verification Engine](#11-verification-engine)
12. [GitHub integration and delivery](#12-github-integration-and-delivery)
13. [Deployment](#13-deployment)
14. [Configuration](#14-configuration)
15. [Quick start](#15-quick-start)
16. [API overview](#16-api-overview)
17. [Security model](#17-security-model)
18. [Implementation status](#18-implementation-status)
19. [Known limitations](#19-known-limitations)
20. [Roadmap](#20-roadmap)
21. [Project structure](#21-project-structure)
22. [Development principles](#22-development-principles)
23. [License](#23-license)

---

## Product Screenshots

| Feature | Screenshot |
|---|---|
| **Connect Repository** — Connect a repository to the engineering workspace. | <a href="docs/images/connect_page.png"><img src="docs/images/connect_page.png" width="220"></a> |
| **Direct Repository Access** — Access the connected repository directly. | <a href="docs/images/direct_repo_access.png"><img src="docs/images/direct_repo_access.png" width="220"></a> |
| **Dependency Graph** — Visualize repository files, nodes, and relationships. | <a href="docs/images/graph_view.png"><img src="docs/images/graph_view.png" width="220"></a> |
| **Structured Graph Visualization** — Explore nodes, edges, imports, and connections. | <a href="docs/images/Nodes_edges_view.png"><img src="docs/images/Nodes_edges_view.png" width="220"></a> <a href="docs/images/strcured_graph_view.png"><img src="docs/images/strcured_graph_view.png" width="220"></a> |
| **Impact Analysis** — Identify dependencies and understand the potential impact of code changes. | <a href="docs/images/impact_analyze.png"><img src="docs/images/impact_analyze.png" width="220"></a> <a href="docs/images/Impact_connection.png"><img src="docs/images/Impact_connection.png" width="220"></a> |
| **Node Exploration and Actions** — Select nodes, inspect details, and access contextual actions. | <a href="docs/images/Node_panel.png"><img src="docs/images/Node_panel.png" width="220"></a> <a href="docs/images/node_click.png"><img src="docs/images/node_click.png" width="220"></a> |
| **AI Assistant Panel** — Get AI assistance and additional repository context. | <a href="docs/images/Ai_panel.png"><img src="docs/images/Ai_panel.png" width="220"></a> <a href="docs/images/Moreinfo_aipanel.png"><img src="docs/images/Moreinfo_aipanel.png" width="220"></a> |
| **Bug Investigation** — Investigate reported bugs and explore relevant repository evidence. | <a href="docs/images/Bug_Investigation_panel.png"><img src="docs/images/Bug_Investigation_panel.png" width="220"></a> <a href="docs/images/Investigate_fix_result.png"><img src="docs/images/Investigate_fix_result.png" width="220"></a> |
| **File Manager and Code Editor** — Browse repository files and edit source code. | <a href="docs/images/file_manager.png"><img src="docs/images/file_manager.png" width="220"></a> <a href="docs/images/editor.png"><img src="docs/images/editor.png" width="220"></a> |
| **Sandbox Editor** — Edit source code in the integrated Monaco editor. | <a href="docs/images/sandbox_monaco_editor.png"><img src="docs/images/sandbox_monaco_editor.png" width="220"></a> |
| **AI-Assisted Code Changes** — Review highlighted changes and AI-assisted edits. | <a href="docs/images/code_change_highlight.png"><img src="docs/images/code_change_highlight.png" width="220"></a> <a href="docs/images/incode_auto_edit.png"><img src="docs/images/incode_auto_edit.png" width="220"></a> |
| **Branch Management** — Create branches for isolated development and code changes. | <a href="docs/images/branch_create.png"><img src="docs/images/branch_create.png" width="220"></a> <a href="docs/images/branch_creation.png"><img src="docs/images/branch_creation.png" width="220"></a> |
| **Automated Testing** — Run verification and inspect automated testing results. | <a href="docs/images/auto_testing.png"><img src="docs/images/auto_testing.png" width="220"></a> |
---

## 2. What it does

| Capability | Description |
|---|---|
| **Repository ingestion** | Connect a GitHub repository, upload a local folder, or upload a ZIP |
| **AST-based parsing** | Tree-sitter parsing extracts functions, methods, classes, imports, and calls |
| **Code knowledge graph** | Typed, directed graph of files, symbols, and relationships |
| **Impact analysis** | Reverse traversal answers "what breaks if I change this?" |
| **Dead-code detection** | Reachability from real entry points finds unreferenced code (with caveats) |
| **Repository Q&A** | Free-form questions answered from retrieved source and graph evidence |
| **AI explanations** | Structured Purpose / How it works / Things to know for any node |
| **Graph control from chat** | Commands such as "show me X" highlight nodes on the canvas |
| **Investigate** | Bug report to ranked suspects, root cause, candidate patch |
| **Files and Sandbox** | Browse files, open a node's file in the workspace sandbox, edit, test |
| **Patch lifecycle** | Diff review, apply to a workspace copy, verify, restore |
| **Remote verification** | Baseline and patched test runs on GitHub Actions with regression comparison |
| **GitHub branches** | Create a feature branch, save edits to it, and push |
| **Multi-provider AI router** | Capability-based model selection with fallback across providers |
| **Entitlements** | Server-authoritative trial gating for Engineer Mode |

---

## 3. How a developer uses it

```mermaid
flowchart LR
    A[Connect repository] --> B[Index and build graph]
    B --> C[Explore graph and files]
    C --> D[Impact analysis / Q&A / Explain]
    D --> E[Submit a bug report]
    E --> F[Ranked suspects + root cause]
    F --> G[Candidate patch as a diff]
    G --> H[Review and apply to workspace copy]
    H --> I[Baseline vs patched tests]
    I --> J[Export or push a feature branch]
```

1. **Connect** a repository (GitHub, local folder, or ZIP) and let it index.
2. **Explore** the graph. Click any node for actions: open its file in the Sandbox, check connections, explain it, or ask the AI agent.
3. **Understand impact** before changing anything.
4. **Investigate** a bug: the engine ranks suspects using graph and source evidence, then reasons about the root cause.
5. **Review** the proposed patch as a diff. Nothing is applied automatically.
6. **Verify** with baseline and patched test runs; new regressions are called out separately from pre-existing failures.
7. **Deliver** by exporting the workspace or pushing a feature branch.

---

## 4. Core philosophy

| Principle | Meaning |
|---|---|
| **Graph first** | Repository facts (calls, dependencies, reachability) come from parsing and graph algorithms, never from an LLM's guess |
| **AI second** | AI explains and reasons over retrieved evidence; it does not invent relationships or impact counts |
| **Tests decide** | A generated patch is a hypothesis until execution-based verification supports it |
| **Evidence based** | Suspects, answers, and explanations carry the source references they came from |
| **Isolation** | Proposed changes land in a workspace copy; the original repository is never silently modified |
| **Developer control** | Every patch is reviewed and approved by a person; nothing is merged automatically |

> **The AI is not the source of truth for repository structure.** The Knowledge Engine owns the facts; the AI interprets them.

---

## 5. System architecture

The product is organized as **three engineering subsystems** on top of a **workspace platform** and **shared infrastructure**.

| Subsystem | Role | Contains |
|---|---|---|
| **A. Knowledge Engine** | Repository understanding | Ingestion, parsing, symbol extraction, graph, dependency resolution, impact, dead-code, retrieval |
| **B. AI Engineering Engine** | Reasoning and modification | Model routing, Q&A, explanations, Investigate, root-cause analysis, patch generation |
| **C. Execution and Verification Engine** | Validation | Environment preparation, baseline and patched runs, regression comparison, result reporting |
| **D. Workspace Platform** | Working with real files | File browser, editor and sandbox, patch apply/restore, branches, export |
| **E. Infrastructure** | Running it for real | Authentication, persistence, job orchestration, deployment, secrets |

These are logical boundaries. They run as a **modular FastAPI backend** plus separately executed verification workers, not as a fleet of microservices.

### 5.1 Technology stack

| Layer | Technology |
|---|---|
| Frontend | Next.js (App Router), React, TypeScript |
| Backend | Python, FastAPI, Pydantic |
| Parsing | Tree-sitter |
| Graph | Typed directed graph with deterministic algorithms (BFS reachability and reverse traversal) |
| Database and auth | Supabase (PostgreSQL) |
| AI providers | Groq, Google Gemini, OpenRouter (behind a provider-independent router) |
| Repository access | GitHub App (installation-scoped) |
| Remote execution | GitHub Actions (private runner repository) |
| Hosting | Vercel (frontend), Render (backend) |

### 5.2 High-level flow

```mermaid
flowchart TD
    USER[Developer] --> FE[Next.js Workspace]
    FE --> API[FastAPI Backend]

    API --> CONN[Repository Connectors]
    CONN --> ING[Ingestion]
    ING --> PARSE[Tree-sitter Parsing]
    PARSE --> SYM[Symbol and Relationship Extraction]
    SYM --> GRAPH[Knowledge Graph]

    GRAPH --> IMPACT[Impact Analyzer]
    GRAPH --> DEAD[Dead Code Analyzer]
    GRAPH --> RET[Retrieval and Context Builder]

    RET --> AI[AI Reasoning Layer]
    AI --> INV[Investigate]
    INV --> PATCH[Patch Generation and Management]

    PATCH --> WS[Workspace Copy]
    WS --> VER[Verification Job Service]
    VER --> GHA[GitHub Actions Runner]
    GHA --> RES[Baseline vs Patched Results]
    RES --> API

    API <--> DB[(Supabase PostgreSQL)]
    API --> FE
```

### 5.3 The twelve layers

The same system, viewed by responsibility:

| # | Layer | Responsibility |
|---|---|---|
| 1 | Frontend workspace | Navigation, graph, files, sandbox, Investigate UI, diff and verification display |
| 2 | API and backend | Request validation, orchestration, job creation, persistence |
| 3 | Repository connectors | GitHub, local folder, ZIP access |
| 4 | Ingestion | File inventory, ignore rules, source reading |
| 5 | Parsing and symbols | Syntax-aware extraction of entities and relationships |
| 6 | Knowledge graph | Typed nodes and edges |
| 7 | Deterministic analysis | Impact, reachability, dead-code candidates |
| 8 | Retrieval and context builder | Selecting bounded, relevant evidence |
| 9 | AI reasoning | Explanations, root cause, repair proposals |
| 10 | Patch management | Diff generation, validation, apply, restore |
| 11 | Verification engine | Baseline and patched test execution, comparison |
| 12 | Persistence and deployment | Database, hosting, secrets |

### 5.4 The retrieval boundary

Only the retrieval layer connects graph knowledge to AI reasoning:

```text
Graph Engine -> Deterministic Analysis -> Retriever -> Context Builder -> AI Provider
```

The AI never independently invents call relationships, dependency paths, symbol locations, impact counts, or reachability results. Those are Knowledge Engine responsibilities. The AI interprets evidence; it does not replace the algorithms.

---

## 6. Repository sources and working modes

Three input types feed one common internal repository abstraction. They differ in **how changes are handled**.

| Mode | Acquire | Index | Edit | Deliver |
|---|---|---|---|---|
| **GitHub** | Clone through the GitHub App installation | Yes | Workspace copy | Push a **feature branch** |
| **Local folder** | Browser collects supported files and uploads them | Yes | Server-side workspace copy | Explicit export (write-back needs explicit browser permission) |
| **ZIP archive** | Upload and safely extract | Yes | Extracted workspace copy | Download an updated ZIP |

**Indexing and editing never modify the original repository.** Changes only leave the workspace through an explicit export or a pushed branch.

### 6.1 Connector contract

All connectors implement the same contract (`base_connector.py`); GitHub-specific behaviour lives in `github.py`.

- Validate access and read repository metadata
- Shallow clone and update, including private repositories via temporary authentication headers
- Return the local repository path for indexing
- Capabilities a connector does not support (for example, pull requests for a local folder) are explicit rather than faked

GitLab and Bitbucket are deferred.

### 6.2 Ingestion rules

Before parsing, ingestion enumerates files and applies ignore rules so thousands of irrelevant files never reach the parser.

Excluded by default: `.git`, `node_modules`, virtual environments, `dist`, `build`, `.next`, `coverage`, caches, compiled binaries, environment files, and credentials.

ZIP archives are validated for path traversal, unsafe symbolic links, entry counts, and total uncompressed size.

> **Large folders.** Browser multipart uploads cap at 1,000 form fields. A folder of tens of thousands of files (for example a project with `node_modules` included) will fail. Filter on the client, or upload as a ZIP.

---

## 7. Knowledge Engine

The Knowledge Engine is the deterministic foundation. It contains no LLM calls.

### 7.1 Parsing and symbol extraction

Tree-sitter provides syntax-aware parsing rather than regex guesses. The engine extracts functions, methods, classes, modules, imports, and calls.

```python
def calculate_total(items):
    return sum(items)

def create_order(items):
    total = calculate_total(items)
    return total
```

The parser identifies `calculate_total`, `create_order`, and the call from `create_order` to `calculate_total`. That relationship becomes a graph edge.

### 7.2 Cross-file relationship resolution

A function can depend on code in a completely different file. Without cross-file resolution, the graph is incomplete and impact analysis misses affected components.

The resolver handles:

- Function calls across files
- Method calls using `self` and `this`
- Object-based method calls
- Class and inheritance relationships
- Cross-file imports and re-export chains (for example through `__init__.py`)
- Python relative imports
- TypeScript and JavaScript path aliases from `tsconfig.json` / `jsconfig.json`
- JSX component usage (`<Foo />`)
- Components wrapped in `forwardRef`, `memo`, and similar higher-order wrappers
- Constructor instantiation (`new X()`) and dynamic `import()`

Unresolved references are retained as candidates rather than discarded, so later resolver improvements can pick them up.

**Framework awareness.** Entry points are detected through a data-driven registry covering FastAPI, Flask, Django, Celery, Next.js (App Router, Pages Router, middleware), Express, NestJS, Fastify, and Remix. Adding a framework is a single configuration entry.

### 7.3 Graph model

The graph is directed and typed.

**Node types:** Repository, File, Module, Function, Method, Class, Service, Feature, External dependency.

**Edge types:** `CONTAINS`, `IMPORTS`, `CALLS`, `INHERITS`, `REFERENCES`, `DEPENDS_ON`.

```text
create_order()
      | CALLS
      v
cart_total()
      | CALLS
      v
final_price()
      | CALLS
      v
apply_discount()
```

A change to `apply_discount()` may affect everything above it. The graph answers this directly, without an LLM rediscovering it each time.

**Feature and Service nodes** are intended to lift the view from individual functions toward application architecture (for example *Feature: Order Management* containing *Service: Payment Service*). Current extraction coverage of these higher-level nodes is partial; see [Implementation status](#18-implementation-status).

### 7.4 Indexing

Indexing runs in the background. Starting an index returns immediately, and the UI polls the repository status until it completes.

| Mode | Behaviour |
|---|---|
| **Full indexing** | Parse every eligible file and build a complete graph |
| **Incremental indexing** | *Design target.* Re-parse only changed files, using file hashes, and invalidate relationships that depended on old symbols |

A correct incremental implementation must also remove stale edges when a file changes or is deleted, and respect parser and graph-schema versions. It should not be described as complete until dependency invalidation has been verified.

### 7.5 Impact Analyzer

**Question answered:** *what could a change to this node affect?*

1. Select a function, class, or node.
2. Resolve it in the graph.
3. Traverse incoming dependency edges (reverse impact traversal) breadth-first.
4. Collect direct and indirect dependents and affected files.
5. Optionally generate an AI narration of the result.

The deterministic analyzer produces the facts; the AI explains what they mean. Impact shows a **change surface**; it does not mean every connected function is defective.

### 7.6 Dead Code Analyzer

1. Identify entry points (framework-aware).
2. Run forward reachability from those roots.
3. Compare reachable nodes with all eligible nodes.
4. Report unreachable candidates with a standing caveat.

> **Unreachable does not mean safe to delete.** Dynamic imports, reflection, framework registration, and plugin systems create runtime usage that static analysis cannot see. Every result includes a caveat field, and "reachability" counts should be read as a lower bound, not a verdict.

### 7.7 Static analysis limitations

Static parsing cannot fully capture reflection, dynamic imports, runtime-generated functions, dynamic dispatch, dependency injection, ambiguous object types, or framework-specific registration. **A missing edge does not prove the runtime relationship does not exist.**

### 7.8 Analysis result contract

Every deterministic result is designed to carry the same shape so the UI, AI layer, and Investigate can reuse it:

```json
{
  "repository_id": "uuid",
  "analysis_type": "impact",
  "root_node_id": "uuid",
  "results": [],
  "evidence": [],
  "limitations": []
}
```

---

## 8. AI Engineering Engine

### 8.1 Retrieval and context building

Instead of sending every file to a model, the retrieval layer selects bounded evidence:

- Matching symbols and source ranges
- Callers, dependencies, and graph paths
- Related files and tests
- Existing test output

For repository questions, the flow is two model calls around real retrieval:

1. **Plan:** a fast model expands the question into search terms (handling synonyms, such as "sign in page" matching `login`, `signin`, `auth`) and detects whether it is a relationship question.
2. **Answer:** matched nodes are resolved, relational questions run the same deterministic traversals, source for the top match is pulled with a larger budget, and a second call synthesizes a grounded answer.

Context is budgeted: lower-priority evidence is dropped first, and critical source is never silently truncated.

### 8.2 Provider-independent router

A capability-based router sits between the product and the model providers.

- **Task routing:** each task maps to a capability and mode (for example explain-node, impact narration, repo Q&A planning and synthesis)
- **Model catalog:** families and providers with capability metadata; a premium-tier hook is ready for higher-end models
- **Fallback:** on provider failure, the router classifies the error, retries or switches to a compatible provider, and preserves the original evidence
- **Transparent failures:** when every family fails, the error carries each provider's real error text

### 8.3 Explanations and Q&A

| Feature | Input | Output |
|---|---|---|
| **Explain this node** | Selected node | Purpose, how it works, things to know |
| **Impact narration** | A deterministic impact result | Plain-language explanation of what is affected and why it matters |
| **Repository Q&A** | A free-form question | Grounded answer with the matched code behind it |

Explanations use the node's line range when available, so only relevant lines are read.

### 8.4 Graph Copilot resolution order

Chat input is handled in this order:

1. **View commands** ("show me X", "highlight X", "reset"): instant, no model call
2. **Explain** on the selected node
3. **Impact or connection** questions on the selected node
4. **Everything else:** repository Q&A

---

## 9. Investigate

Investigate turns a bug report into a focused root-cause hypothesis and a candidate repair.

### 9.1 Flow

```mermaid
flowchart TD
    A[Bug report] --> B[Normalize]
    B --> C[Search symbols and source]
    C --> D[Graph relationship analysis]
    D --> E[Rank suspects]
    E --> F[Retrieve source evidence]
    F --> G[AI root-cause analysis]
    G --> H[Repair plan]
    H --> I[Candidate patch]
    I --> J[Verify]
```

### 9.2 Suspect ranking

Suspects are ranked by combining:

- Symbol-name and file-path matches
- Source-text and error-message matches
- Graph proximity and callers
- Related tests
- Blast radius

A suspect carries its node, file path, line range, score, reasons, and blast radius. **The score is a ranking signal, not a probability.** A score of 0.9 does not mean a 90% chance of being the defect.

### 9.3 Test environment preparation

A missing test tool is not a code bug. Investigate and Verify are intended to share one preparation path:

1. Detect language and project type
2. Identify dependency manifests and the test framework
3. Install declared test dependencies
4. Discover test commands
5. Run baseline, then patched
6. Classify environment failures separately from code failures

Supported ecosystems: Python (`pytest`, `unittest`), Node.js (`package.json` scripts), Java (Maven or Gradle) where supported.

### 9.4 Source normalization

Source-format problems should not abort the whole workflow. Normalization runs on a **disposable copy**, never the original:

- Detect encoding and a UTF-8 byte-order mark (`U+FEFF`)
- Identify malformed source
- Apply safe normalization, then re-validate
- Distinguish an encoding problem from a real syntax error

### 9.5 Error classification

| Failure | Category |
|---|---|
| Syntax or compilation error | Source |
| BOM or encoding issue | Source preparation |
| Missing `pytest` or a failed dependency install | Environment |
| Test collection failure, no tests found | Test discovery |
| Assertion failure | Test |
| Invalid diff, patch conflict | Patch |
| Runner timeout, lost worker | Infrastructure |
| Provider failure | AI infrastructure |

An infrastructure failure must never be reported as an application bug.

### 9.6 Investigate versus Verify

| | Investigate | Verify |
|---|---|---|
| Purpose | Understand the defect, find suspects, propose a repair | Evaluate a candidate change |
| Output | Suspects, root cause, repair plan, candidate patch | Baseline vs patched test comparison |
| Shared | Environment detection, dependency preparation, test discovery, error classification | (same) |

---

## 10. Workspace: files, sandbox, and patches

The workspace is where repository intelligence meets real files. It is **not just a viewer**: the graph tells you *where* a change belongs, and the workspace lets you inspect and modify the source there.

### 10.1 Files panel

- Repository-relative file list with search
- Click a file to preview its contents
- **Edit in Sandbox** opens the file in the editor
- Branch-aware: when a feature branch is active, the file list and contents come from that branch

### 10.2 Sandbox

- Edit a file and run the repository's tests against your change
- Opens directly from the graph: **Open file** on any node loads that node's file in the Sandbox (the path is resolved to the repository-relative file)
- Edits are verified against baseline results before being applied

### 10.3 Patch lifecycle

Five distinct states, never treated as interchangeable:

```mermaid
flowchart LR
    A[AI proposal] --> B[Validated patch]
    B --> C[Diff preview]
    C --> D[Applied to workspace copy]
    D --> E[Verified]
    E --> F[Published: export or branch]
    D -. restore .-> B
```

| Stage | What happens |
|---|---|
| **Proposed** | AI generates a candidate unified diff |
| **Validated** | Structure, paths, and applicability checked; absolute paths and path traversal rejected |
| **Applied** | Applied to the workspace copy only; a restore token is returned |
| **Verified** | Baseline and patched tests compared |
| **Published** | Exported, or pushed through a feature branch |

Generating a patch and applying it are deliberately separate. Restoring reverts the workspace copy; it never rewrites the original repository.

---

## 11. Verification Engine

Verification evaluates a candidate change by running tests twice under equivalent conditions and comparing the outcomes.

### 11.1 Execution model

The Render backend coordinates; **GitHub Actions executes**. Keeping test execution out of the web process protects API availability.

```mermaid
sequenceDiagram
    participant UI as Next.js
    participant API as FastAPI
    participant DB as Supabase
    participant GH as GitHub Actions
    participant ENG as Trusted Engine

    UI->>API: Start verification
    API->>DB: Create job (queued)
    API->>GH: Dispatch workflow
    GH->>API: Claim job
    API->>DB: Mark claimed / running
    GH->>API: Request source ZIP
    API-->>GH: Source archive
    GH->>GH: Validate archive
    GH->>ENG: Execute trusted engine
    ENG->>ENG: Baseline tests
    ENG->>ENG: Apply candidate patch
    ENG->>ENG: Patched tests
    ENG->>API: Submit result
    API->>DB: Store result
    UI->>API: Read result
```

The job is persisted **before** the workflow is dispatched, so a transient dispatch failure never loses a requested verification.

### 11.2 Job lifecycle

```text
queued -> claimed -> running -> completed
                          \-> failed | expired | cancelled
```

- **Persistent state:** the runner and backend share no memory, so job state lives in PostgreSQL (`verification_jobs`)
- **Claiming and leases:** a runner claims a job and holds a lease; if the lease expires the job becomes recoverable
- **Idempotency:** claiming and result submission are designed to be safely repeatable

### 11.3 Baseline and patched runs

Both runs use the same source revision, test configuration, dependencies, runtime, and command. The only intended difference is the candidate patch.

### 11.4 Result fields

| Field | Meaning |
|---|---|
| `complete` | Verification finished |
| `safe_to_apply` | No new failures under the configured regression policy |
| `tests_pass_after` | The patched run passed |
| `baseline`, `patched` | Test results of each run |
| `baseline_failures` | Failures that already existed |
| `still_failing` | Failures that remain after the patch |
| `new_failures` | Regressions introduced by the patch |
| `verdict` | Overall decision |

> **`safe_to_apply: true` does not mean every test passes.** If the baseline has 6 passed and 4 failed, and the patched run also has 6 passed and 4 failed, the patch introduced no regressions but fixed nothing. Only `tests_pass_after: true` means the patched suite is green.

### 11.5 Verdicts

| Condition | Verdict |
|---|---|
| All patched tests pass | `passed` |
| Existing failures remain, none new | `no_new_regressions` |
| New failures introduced | `regression_detected` |
| Patch cannot be applied | `patch_failed` |
| Dependencies cannot be prepared | `environment_failed` |
| Tests cannot be discovered | `test_discovery_failed` |
| Runner crash or timeout | `infrastructure_failed` |

---

## 12. GitHub integration and delivery

### 12.1 GitHub App

The **MiniAI Engineer Mode** GitHub App provides installation-scoped access to selected repositories only. Its private key stays in backend and Actions secrets.

### 12.2 Repositories

| Repository | Purpose |
|---|---|
| `Rakesh-honawad/AI-Software-Engineer` | Main application and the trusted verification engine |
| `Rakesh-honawad/knowledge-engine-runner` (private) | GitHub Actions verification workflow |

Separating the workflow entry point from the trusted engine keeps orchestration and implementation distinct.

### 12.3 Runner workflow

1. Receive `workflow_dispatch`
2. Check out runner tools and create a GitHub App installation token
3. Check out the trusted engine
4. Claim the job and download the source archive
5. Validate the archive (paths, symlinks, entry counts, total size)
6. Install dependencies, validate source, run verification
7. Report the structured result

### 12.4 Branch workflow

Engine edits are never pushed to the default branch.

```mermaid
flowchart TD
    A[Connected GitHub repository] --> B[Create feature branch from base]
    B --> C[Save edits to the branch workspace]
    C --> D[Verify]
    D --> E[Commit and push the branch]
    E --> F[Pull request for human review]
    F --> G[Administrator merges]
```

In the workspace, the branch selector creates a branch and switches the Files and Sandbox panels onto it; **Push** commits the saved changes after a confirmation prompt. Pull-request creation and automatic merging are described in [Implementation status](#18-implementation-status); the engine never merges on its own.

---

## 13. Deployment

| Service | Responsibility | Host |
|---|---|---|
| Workspace UI | Next.js frontend (`workspace-ui/`) | Vercel |
| Backend | FastAPI control plane (`api.main:app`) | Render |
| Database | PostgreSQL and authentication | Supabase |
| Runner | Remote test execution | GitHub Actions |
| Repository access | Installation-scoped permissions | GitHub App |
| AI | Reasoning | External provider APIs |

```text
Browser -> Vercel Frontend -> Render FastAPI -+-> Supabase
                                              +-> GitHub App
                                              +-> AI providers

Verification: Render -> GitHub Actions -> Trusted engine -> Backend runner API -> Supabase -> Frontend
```

- Frontend: https://mini-ai-workspace.vercel.app
- Backend health: https://ai-software-engineer-84ow.onrender.com/health (the root path `/` may return 404; use `/health`)

The API does not run unbounded indexing or arbitrary repository tests inside the web process.

---

## 14. Configuration

Configuration lives in each deployment environment and is **never committed**. Values below are names only.

### Backend

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection |
| `RUNNER_AUTH_TOKEN` | Authenticates runner callbacks |
| `RUNNER_GITHUB_INSTALLATION_ID` | GitHub App installation |
| GitHub App private key | Installation token generation |
| AI provider API keys | Model access (Groq, Gemini, OpenRouter) |
| `ENTITLEMENT_SIGNING_SECRET` | Signs the trial entitlement token |
| Supabase settings | Server-side authentication verification |

### Runner repository (Actions secrets)

| Secret | Purpose |
|---|---|
| `APP_ID` | GitHub App ID |
| `APP_PRIVATE_KEY` | GitHub App private key |
| `RUNNER_AUTH_TOKEN` | Callback authentication |

### Frontend (public variables only)

| Variable | Purpose |
|---|---|
| `NEXT_PUBLIC_KNOWLEDGE_ENGINE_API_URL` | Backend base URL |
| Supabase public URL and anon key | Browser authentication |

**Never expose** database passwords, GitHub private keys, runner tokens, Supabase service-role keys, or AI provider keys to the browser.

---

## 15. Quick start

### Prerequisites

- Python 3.11+ and Node.js
- A Supabase project, a GitHub App (for GitHub repositories), and at least one AI provider key

### Backend

```powershell
cd knowledge-engine
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# create .env with the backend variables listed above
uvicorn api.main:app --reload --port 8000
```

Check it:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

### Frontend

```powershell
cd knowledge-engine\workspace-ui
npm install
# .env.local
#   NEXT_PUBLIC_KNOWLEDGE_ENGINE_API_URL=http://localhost:8000
#   plus the public Supabase URL and anon key
npm run dev
```

Open http://localhost:3000, connect a repository, and wait for indexing to finish.

### Calling the API from PowerShell

```powershell
$body = @{ repository_id = "my-repo"; local_path = "G:\path\to\repo" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/repositories/index `
  -ContentType "application/json" -Body $body
```

---

## 16. API overview

The live OpenAPI document (`/docs`) is the authoritative route list. Main route families:

| Family | Responsibility |
|---|---|
| Repositories | Index (GitHub, local, ZIP), status, file list, file read |
| Graph | Graph retrieval |
| Impact | Impact analysis and AI narration |
| Dead code | Reachability analysis and explanation |
| Explain / Ask | Node explanation, repository Q&A |
| Investigate | Bug investigation |
| Workspace edits | Verify an edit, apply, restore |
| GitHub branches | Create branch, list branch files, read file, apply edits, push |
| Entitlements | Trial start and gating |
| Runner | Claim job, download source, submit result |

Runner endpoints include:

```http
POST /api/v1/runner/jobs/{job_id}/claim
GET  /api/v1/runner/jobs/{job_id}/source.zip
```

Long-running operations return immediately and are polled for status; the frontend never holds a request open for indexing or verification.

---

## 17. Security model

| Boundary | Control |
|---|---|
| **Repository content is untrusted** | Source, README text, build scripts, test output, and generated patches are treated as data, never as instructions |
| **Prompt injection** | Retrieved content is separated from system instructions; the AI has no direct shell, file, or secret access, and all actions go through backend-controlled interfaces |
| **Archive validation** | Path traversal, symlinks, entry count, and uncompressed size checked |
| **Patch validation** | Paths and source version validated; patches cannot overwrite secrets or backend configuration |
| **Runner authentication** | Callbacks require the shared runner token |
| **GitHub permissions** | App scoped to selected repositories and needed operations |
| **Authentication** | Backend must verify tokens server-side and enforce repository ownership on every protected operation |
| **Default branch** | Automated changes go to a feature branch, never the default branch |

> **Current runner isolation is not a hardened sandbox.** Verification runs repository tests in subprocesses on GitHub-hosted ephemeral runners, separated from the Render web process. Arbitrary repository tests can execute untrusted code. Docker isolation was removed from the active workflow to make remote verification operational. Stronger isolation (no privileged secrets during test execution, restricted network, resource limits, dedicated ephemeral environments) is a hardening requirement before running untrusted repositories at scale.

> **Authentication note.** The entitlement layer currently identifies users through an `X-User-Id` header as a stand-in. Real server-side auth and per-repository authorization must back every endpoint before multi-user production use.

---

## 18. Implementation status

This section separates what is proven from what is intended. Items are moved up only after end-to-end testing.

### 18.1 Implemented and verified

- FastAPI backend on Render and Next.js workspace on Vercel
- GitHub App connection and shallow cloning of repositories
- Repository indexing (GitHub, local folder, ZIP) with background execution and status polling
- Tree-sitter parsing with Python and JavaScript/TypeScript graph building, including cross-file resolution, re-export chains, path aliases, JSX usage, and framework-aware entry points
- Impact analysis and AI narration
- Dead-code analysis with caveats
- Repository Q&A and node explanations
- Multi-provider AI router with fallback
- Trial entitlements (server-authoritative)
- Files panel and Sandbox, including **Open file** from any graph node
- Remote verification through GitHub Actions: job claim, source download, archive validation, baseline and patched runs, structured result

### 18.2 Implemented, needs end-to-end confirmation

- Investigate with automatic patch generation (known setup issues below)
- Apply and restore of edits
- GitHub feature-branch creation, saving to a branch, and push
- Chat-controlled graph view (dim and highlight only)

### 18.3 Partially implemented

- Feature and Service node extraction
- Local folder and ZIP round trip: updated-ZIP download and local write-back are not fully exposed in the UI
- JavaScript/TypeScript reachability, which improves with each resolver fix but remains far from complete
- Verification isolation (see Security)

### 18.4 Planned

- Pull-request creation from the workspace
- Incremental indexing with stale-edge invalidation
- Repository snapshots and workspace revisions as first-class entities
- Optimistic-concurrency saves in the editor
- Real spatial node movement and persisted node positions
- Real multi-agent "Connect to AI agent"
- Hardened per-job execution sandbox
- Automated test suite for the engine
- Full authentication and authorization

---

## 19. Known limitations

- **Investigate environment setup.** A run can stop with `No module named pytest` because test dependencies were not installed before baseline execution. This is an environment failure, not a code regression, and automatic dependency preparation is still being completed.
- **Source encoding.** Source files with a UTF-8 BOM (`U+FEFF`) can halt an investigation before the AI repair stage. Safe normalization on a disposable copy is the intended fix.
- **Investigate and Verify parity.** Dependency installation and test discovery rules should be identical across both paths.
- **Large uploads.** Very large local folders can exceed browser multipart limits; use ZIP or filter files.
- **Graph coverage.** Dynamic imports, reflection, and framework registration are not fully captured, so reachability figures understate real usage.
- **Isolation.** The subprocess runner is not a hardened sandbox.
- **Tests.** The engine has no automated unit-test suite yet; verification so far rests on live end-to-end runs.

---

## 20. Roadmap

| Phase | Focus |
|---|---|
| **1. Repository foundation** | Repository identity, immutable snapshots, source manifests, persisted indexing jobs |
| **2. Knowledge Engine correctness** | Broader language coverage, resolver completeness, snapshot consistency, incremental indexing |
| **3. Workspace** | Revision history, change tracking, restore, ZIP export, local write-back |
| **4. Repository intelligence** | Retrieval quality, evidence references, evaluation of Q&A and explanations |
| **5. AI engineering** | Better root-cause ranking, multi-step repair attempts, investigation history |
| **6. Verification reliability** | Environment detection, dependency installation, test discovery, source normalization, shared with Investigate |
| **7. Delivery and hardening** | Pull requests, tenant isolation, runner hardening, monitoring, retries, performance benchmarks |

Possible integrations: VS Code extension, Jira, pull-request workflows, Git history analysis, additional repository providers.

### Acceptance test

The product's central value is demonstrated by one end-to-end scenario:

1. Take a real repository with a deliberately introduced defect
2. Connect and index it
3. Locate the affected function in the graph
4. Submit a bug report
5. Confirm Investigate identifies the right suspect, with evidence referencing real source lines
6. Generate and review a patch diff
7. Apply it to a workspace copy
8. Run baseline and patched tests remotely and confirm the report matches reality
9. Export, or push a feature branch
10. Confirm the original source was never silently modified

---

## 21. Project structure

A conceptual map; always check the real tree for exact filenames.

```text
AI-Software-Engineer/
├── api/                  FastAPI app and routes
├── ai/                   Router, context builder, narration, reasoning, Q&A
├── analysis/             Impact and dead-code analysis
├── graph/                Schema, builders, algorithms
├── ingestion/            Repository connectors and file processing
├── parsing/              Tree-sitter parsing and symbol extraction
├── query/                Graph queries and symbol retrieval
├── repository/           Repository management
├── storage/              Graph storage and repository profiles
├── entitlements/         Trial gating
├── sandbox/              Sandbox runner
├── workspace-ui/         Next.js frontend
├── docs/                 Schema docs and images
├── execute_verification.py
└── README.md
```

---

## 22. Development principles

1. Repository facts come from parsing and graph algorithms.
2. AI reasons over retrieved evidence.
3. Every investigation explains why each suspect was ranked.
4. Every proposed patch is a reviewable diff.
5. Verification compares baseline against patched results.
6. Existing failures are distinguished from newly introduced ones.
7. Environment failures are never reported as business-logic defects.
8. Original source is not modified during disposable verification.
9. Secrets are never committed or exposed in the browser.
10. Documentation separates what is implemented from what is planned.

---

## 23. License

Add the project's selected license before accepting external contributions.

---

**The intended result:** an engineering workspace that does not merely suggest code, but understands the repository, explains its reasoning, proposes focused changes, and checks them against real tests.
