"use client"

import type React from "react"

import { useCallback, useEffect, useRef, useState } from "react"
import { Bot, Check, Copy, Maximize2, Minimize2, Send, Sparkles, X } from "lucide-react"
import type { GraphNode } from "@/lib/graph/data"
import { api, ApiError } from "@/services/api-client"
import { interpretGraphCommand } from "@/lib/graph/command-interpreter"
import { renderMarkdown } from "@/lib/graph/render-markdown"

type Message = { id: string; role: "user" | "assistant"; text: string }


const GENERIC_SUGGESTIONS = [
  "What's the impact of changing this?",
  "Explain this node",
  "Show me chat related files",
  "What connects to the sign in page?",
]

/**
 * FULL RESOLUTION ORDER, current as of this version:
 *   1. Graph-view commands ("show me X" / "reset") — handled entirely
 *      in send(), before this function is even called. Instant, no
 *      model call, see command-interpreter.ts.
 *   2. "Explain X" — calls POST /explain (ai/narration/node_explain_narration.py),
 *      a real structured PURPOSE/HOW IT WORKS/THINGS TO KNOW answer
 *      grounded in the node's actual source. Requires a selected node.
 *   3. Impact/risk/connection questions about a selected node — calls
 *      POST /impact/narrate.
 *   4. Anything else — calls POST /ask (ai/reasoning/repo_qa.py), the
 *      real freeform Q&A path: AI expands the question into real
 *      search terms (handles synonyms), matches real nodes, walks the
 *      real graph if relational, synthesizes a grounded answer. Works
 *      with or without a selected node — it finds its own targets.
 * Demo-mode (no repositoryId) still short-circuits before any of this.
 */
async function resolveReply(repositoryId: string | null, prompt: string, node: GraphNode | null): Promise<string> {
  if (!repositoryId) {
    return "This is demo data — connect a repository to ask real questions grounded in its actual dependency graph."
  }

  const looksExplain = node && /explain|what does|what is this/i.test(prompt)
  const looksImpact = node && /impact|risk|change|connection/i.test(prompt)

  if (looksExplain && node) {
    try {
      const result = await api.explainNode(repositoryId, node.id)
      return result.explanation
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        return "No AI model is currently available to explain this."
      }
      if (err instanceof ApiError && (err.status === 402 || err.status === 403)) {
        return "Your Engineer Mode trial or subscription isn't active — that's needed before I can call the AI model."
      }
      return "Something went wrong explaining that node."
    }
  }

  if (looksImpact && node) {
    try {
      const result = await api.narrateImpact(repositoryId, node.name)
      return result.narration
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        return "Impact analysis for this node succeeded, but no AI model is currently configured to narrate it (ai/router has no available provider). The structured impact data is still shown in the inspector."
      }
      if (err instanceof ApiError && err.status === 409) {
        return `"${node.name}" matches more than one symbol in this repo — select the exact node on the canvas rather than asking by name.`
      }
      if (err instanceof ApiError && err.status === 404) {
        return `Couldn't find "${node.name}" in the indexed graph.`
      }
      if (err instanceof ApiError && (err.status === 402 || err.status === 403)) {
        return "Your Engineer Mode trial or subscription isn't active — that's needed before I can call the AI model."
      }
      return "Something went wrong analyzing that node."
    }
  }

  // Real freeform Q&A — the fallback for everything else. Handles
  // synonyms ("sign in page" -> login), relational questions
  // ("what connects to X", "what depends on X"), and general
  // questions with no node selected at all.
  try {
    const result = await api.askRepo(repositoryId, prompt)
    return result.answer
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) {
      return "No AI model is currently available to answer that."
    }
    if (err instanceof ApiError && (err.status === 402 || err.status === 403)) {
      return "Your Engineer Mode trial or subscription isn't active — that's needed before I can call the AI model."
    }
    return "Something went wrong answering that question."
  }
}

export function AiPanel({
  repositoryId = null,
  selectedNode,
  allNodes,
  seedPrompt,
  onConsumeSeed,
  onIsolateNodes,
  onResetView,
}: {
  repositoryId?: string | null
  selectedNode: GraphNode | null
  /** Full current node list — needed to match free-text view-control
   * commands ("show me chat related files") against real names/paths.
   * Pass whatever the canvas is already rendering; this never mutates it. */
  allNodes: GraphNode[]
  seedPrompt: string | null
  onConsumeSeed: () => void
  /** Called with the matched node ids when the user issues a real
   * "show me X" / "highlight X" / "focus on X" command. The canvas
   * component owns what "isolate" actually looks like visually
   * (currently: dim/highlight via searchQuery) — this panel only
   * detects intent and matches nodes. */
  onIsolateNodes?: (nodeIds: string[], query: string) => void
  /** Called when the user asks to return to the normal view
   * ("reset", "show everything", "back to normal", ...). */
  onResetView?: () => void
}) {
  const [open, setOpen] = useState(false)
  const [width, setWidth] = useState(380)
  const [dragging, setDragging] = useState(false)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [typing, setTyping] = useState(false)

  const scrollRef = useRef<HTMLDivElement>(null)
  const typingTimer = useRef<ReturnType<typeof setInterval> | null>(null)

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [])

  const send = useCallback(
    async (raw: string) => {
      const text = raw.trim()
      if (!text || typing) return
      const userMsg: Message = { id: crypto.randomUUID(), role: "user", text }
      setMessages((m) => [...m, userMsg])
      setInput("")
      setTyping(true)

      const assistantId = crypto.randomUUID()
      setMessages((m) => [...m, { id: assistantId, role: "assistant", text: "" }])

      // Graph-view control is checked FIRST, before any AI/backend
      // call — it's a local, instant, deterministic match against
      // the real node list, so there's no reason to wait on a model
      // for something this direct. See lib/graph/command-interpreter.ts.
      const command = interpretGraphCommand(text, allNodes)
      let full: string

      if (command.kind === "isolate") {
        onIsolateNodes?.(command.matchedNodeIds, command.query)
        full = `Highlighted ${command.matchedNodeIds.length} node${command.matchedNodeIds.length === 1 ? "" : "s"} matching "${command.query}" — everything else is dimmed. Say "reset view" to go back to normal.`
      } else if (command.kind === "isolate-empty") {
        // Literal matching found nothing — try the real AI-powered
        // search (handles synonyms) before giving up entirely.
        try {
          const result = await api.askRepo(repositoryId ?? "", `What files/code relate to "${command.query}"?`)
          full = result.answer
        } catch {
          full = `Nothing in the graph matches "${command.query}" by name or file path.`
        }
      } else if (command.kind === "reset") {
        onResetView?.()
        full = "Back to the normal view."
      } else {
        // Not a recognized view-control command — impact/explain/ask.
        full = await resolveReply(repositoryId, text, selectedNode)
      }

      let i = 0
      if (typingTimer.current) clearInterval(typingTimer.current)
      typingTimer.current = setInterval(() => {
        i += 2
        setMessages((m) =>
          m.map((msg) => (msg.id === assistantId ? { ...msg, text: full.slice(0, i) } : msg)),
        )
        requestAnimationFrame(scrollToBottom)
        if (i >= full.length) {
          if (typingTimer.current) clearInterval(typingTimer.current)
          setTyping(false)
        }
      }, 12)
    },
    [repositoryId, selectedNode, allNodes, onIsolateNodes, onResetView, typing, scrollToBottom],
  )

  // Consume a seed prompt (e.g. "Ask AI about X" from the action menu).
  useEffect(() => {
    if (seedPrompt) {
      setOpen(true)
      send(seedPrompt)
      onConsumeSeed()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedPrompt])

  useEffect(() => {
    return () => {
      if (typingTimer.current) clearInterval(typingTimer.current)
    }
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  // Resize handle logic.
  useEffect(() => {
    if (!dragging) return
    const onMove = (e: PointerEvent) => {
      const next = Math.min(700, Math.max(340, window.innerWidth - e.clientX - 20))
      setWidth(next)
    }
    const onUp = () => setDragging(false)
    window.addEventListener("pointermove", onMove)
    window.addEventListener("pointerup", onUp)
    return () => {
      window.removeEventListener("pointermove", onMove)
      window.removeEventListener("pointerup", onUp)
    }
  }, [dragging])

  const suggestions = selectedNode
    ? [`Explain \`${selectedNode.name}\``, "What's the impact of changing this?", "Any risks here?"]
    : GENERIC_SUGGESTIONS

    if (!open) {
    // Docked rail — a real flex child, not a floating button. This is
    // what makes it sit right after the inspector in the layout.
    return (
      <div className="em-ai-rail">
        <button className="em-ai-toggle" onClick={() => setOpen(true)} aria-label="Open AI assistant">
          <Sparkles size={16} />
          <span>Ask AI</span>
        </button>
      </div>
    )
  }

  return (
    <aside
      className={`em-ai-panel ${dragging ? "is-dragging" : ""}`}
      style={{ width }}
      aria-label="AI assistant"
    >
      <div className="em-ai-resize" onPointerDown={() => setDragging(true)} role="separator" aria-orientation="vertical" />

      <header className="em-ai-header">
        <span className="em-ai-dot" aria-hidden />
        <div className="em-ai-header-title">
          <span className="em-ai-header-name">Graph Copilot</span>
          <span className="em-ai-header-sub">
            {selectedNode ? `context: ${selectedNode.name}` : "no context selected"}
          </span>
        </div>
        <div className="em-ai-header-btns">
                    <button
            className="em-ai-hbtn"
            onClick={() => setWidth((w) => (w >= 560 ? 380 : 640))}
            aria-label={width >= 560 ? "Narrow panel" : "Widen panel"}
          >
            {width >= 560 ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
          <button className="em-ai-hbtn" onClick={() => setOpen(false)} aria-label="Close AI panel">
            <X size={15} />
          </button>
        </div>
      </header>

      <div className="em-ai-messages" ref={scrollRef}>
        {messages.length === 0 ? (
          <div className="em-ai-empty">
            <Bot size={28} className="em-ai-empty-icon" />
            <p>Ask anything about the repo — I'll search real code and walk the real dependency graph to answer.</p>
            <div className="em-ai-suggestions">
              {suggestions.map((s) => (
                <button key={s} className="em-ai-suggestion" onClick={() => send(s.replace(/`/g, ""))}>
                  {s.replace(/`/g, "")}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((m) => (
            <MessageBubble key={m.id} role={m.role} text={m.text} />
          ))
        )}
      </div>

      <form
        className="em-ai-input"
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about the graph, or say 'show me X'…"
          onKeyDown={(e: React.KeyboardEvent<HTMLInputElement>) => {
            if (e.nativeEvent.isComposing || e.keyCode === 229) return
            if (e.key === "Enter") {
              e.preventDefault()
              send(input)
            }
          }}
        />
        <button className="em-ai-send" type="submit" disabled={!input.trim() || typing} aria-label="Send message">
          <Send size={15} />
        </button>
      </form>
    </aside>
  )
}

/**
 * Single message bubble. Assistant messages get real markdown
 * rendering (bold/lists/paragraphs — see render-markdown.ts, fixes
 * the "messy, no structure" bug where /numbered-list syntax
 * rendered as literal characters) and a copy-to-clipboard button
 * (copies the RAW text, not the rendered HTML, so pasting elsewhere
 * gives clean markdown/plain text rather than HTML tags).
 */
function MessageBubble({ role, text }: { role: "user" | "assistant"; text: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard API can fail (permissions, non-HTTPS context in
      // some browsers) — fail quietly rather than throwing in the UI;
      // the text is still fully visible/selectable either way.
    }
  }, [text])

  if (role === "user") {
    return <div className="em-msg em-msg--user">{text}</div>
  }

  return (
    <div className="em-msg em-msg--assistant em-msg--with-copy">
      <div dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }} />
      {text.length > 0 && (
        <button
          className="em-msg-copy-btn"
          onClick={handleCopy}
          aria-label="Copy response"
          title="Copy response"
          type="button"
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </button>
      )}
    </div>
  )
}