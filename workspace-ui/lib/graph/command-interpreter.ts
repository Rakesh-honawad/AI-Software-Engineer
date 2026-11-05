/**
 * lib/graph/command-interpreter.ts
 *
 * Closes a real gap: the AI panel's chat input had zero connection to
 * the graph canvas — typed commands like "show me chat related files"
 * fell straight into the generic fallback string, and there was no
 * code path anywhere that could move/isolate nodes based on what the
 * user typed.
 *
 * DELIBERATELY NOT an AI/model call for the common case: matching
 * "show me chat related files" against real node names/file paths is
 * a fast, free, deterministic string-matching problem — no need to
 * spend a model call (or wait on one) just to find which nodes
 * contain "chat" in their name or path. This keeps the graph-view
 * control instant. If this stops being good enough (e.g. someone
 * asks for something conceptual like "show me the auth-related
 * stuff" with no literal "auth" in any filename), that's the future
 * upgrade path — swap the matching step for a real classifier call,
 * everything downstream (the isolate/reset contract) stays the same.
 */

import type { GraphNode } from "@/lib/graph/data"

export type GraphCommand =
  | { kind: "isolate"; query: string; matchedNodeIds: string[] }
  | { kind: "isolate-empty"; query: string }
  | { kind: "reset" }
  | { kind: "none" }

const ISOLATE_TRIGGERS: RegExp[] = [
  /^show me\s+(.+)/i,
  /^highlight\s+(.+)/i,
  /^focus on\s+(.+)/i,
  /^isolate\s+(.+)/i,
  /^find\s+(.+)/i,
  /^where('?s| is)\s+(.+)/i,
]

const RESET_TRIGGERS: RegExp[] = [
  /^(reset|back to normal|show everything|show all|normal view|clear( view)?|deselect)\b/i,
]

// Stripped out of the matched phrase so "chat related files" and
// "chat" produce the same search — these words describe WHAT the
// user wants shown, not something to match against node names.
const FILLER_WORDS = /\b(related|relevant|files?|nodes?|components?|feature|stuff|things?|all|the)\b/gi

function tokenize(s: string): string[] {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .split(" ")
    .filter((t) => t.length > 1) // drop single-char tokens, too noisy to match on
}

/**
 * Returns the graph-view command implied by free text, or "none" if
 * this doesn't look like a view-control command at all (in which
 * case the caller should fall through to normal impact-narration /
 * fallback handling — this function never touches that path).
 */
export function interpretGraphCommand(prompt: string, nodes: GraphNode[]): GraphCommand {
  const trimmed = prompt.trim()
  if (!trimmed) return { kind: "none" }

  if (RESET_TRIGGERS.some((r) => r.test(trimmed))) {
    return { kind: "reset" }
  }

  for (const trigger of ISOLATE_TRIGGERS) {
    const match = trimmed.match(trigger)
    if (!match) continue

    // last capture group holds the query regardless of which trigger matched
    const rawQuery = match[match.length - 1] ?? ""
    const query = rawQuery.replace(FILLER_WORDS, " ").replace(/\s+/g, " ").trim()
    const queryTokens = tokenize(query)

    if (queryTokens.length === 0) {
      return { kind: "none" } // e.g. "show me" with nothing after it — not a real command
    }

    const matchedNodeIds = nodes
      .filter((n) => {
        const haystack = `${n.name} ${n.file_path ?? ""}`.toLowerCase()
        if (haystack.includes(query.toLowerCase())) return true
        return queryTokens.every((t) => haystack.includes(t))
      })
      .map((n) => n.id)

    return matchedNodeIds.length > 0
      ? { kind: "isolate", query, matchedNodeIds }
      : { kind: "isolate-empty", query }
  }

  return { kind: "none" }
}