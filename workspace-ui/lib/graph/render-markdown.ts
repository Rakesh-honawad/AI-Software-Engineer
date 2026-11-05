/**
 * lib/graph/render-markdown.ts
 *
 * Fixes a real, confirmed bug: AI responses (per the structured
 * prompts in node_explain_narration.py, repo_qa.py, etc. — numbered
 * sections, **bold** headers, bullet lists) were rendering as a wall
 * of literal asterisks and dashes, because the old renderText() in
 * ai-panel.tsx only converted backtick code spans and otherwise
 * dumped raw text into a <div> with no paragraph/list structure —
 * even real newlines in the source text collapse visually without
 * explicit block-level HTML, regardless of CSS white-space handling.
 *
 * Deliberately NOT a full markdown spec implementation or an added
 * dependency (no `marked`/`react-markdown`) — covers exactly what
 * this app's own prompts actually produce: bold, inline code,
 * numbered lists, bullet lists, paragraphs. Same "cover the real
 * cases, don't guess the exotic ones" philosophy as the rest of this
 * codebase's parsers.
 */

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
}

/** Inline formatting within a single line/paragraph: **bold**, `code`. */
function renderInline(text: string): string {
  let html = escapeHtml(text)
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>")
  return html
}

const BULLET_LINE = /^\s*[-*]\s+(.+)/
const NUMBERED_LINE = /^\s*\d+[.)]\s+(.+)/

export function renderMarkdown(text: string): string {
  const blocks = text.split(/\n\s*\n/) // paragraph breaks = blank lines
  const htmlBlocks: string[] = []

  // Tracks an in-progress list across consecutive blocks — the AI
  // commonly puts each numbered/bulleted point in its own paragraph
  // (blank line between them) for readability, which would otherwise
  // produce N separate one-item lists (each visually showing "1.")
  // instead of one real list. Only closed when a non-list block
  // appears or the list TYPE changes (bullet vs numbered).
  let openListType: "ul" | "ol" | null = null
  let openListItems: string[] = []

  function flushList() {
    if (openListType && openListItems.length > 0) {
      htmlBlocks.push(`<${openListType}>${openListItems.join("")}</${openListType}>`)
    }
    openListType = null
    openListItems = []
  }

  for (const block of blocks) {
    const lines = block.split("\n").filter((l) => l.trim().length > 0)
    if (lines.length === 0) continue

    const isBulletList = lines.every((l) => BULLET_LINE.test(l))
    const isNumberedList = lines.every((l) => NUMBERED_LINE.test(l))

    if (isBulletList || isNumberedList) {
      const thisType = isBulletList ? "ul" : "ol"
      const pattern = isBulletList ? BULLET_LINE : NUMBERED_LINE
      if (openListType !== null && openListType !== thisType) {
        flushList() // type changed (e.g. bullets then numbers) — start fresh
      }
      openListType = thisType
      for (const l of lines) {
        const match = l.match(pattern)
        openListItems.push(`<li>${renderInline(match ? match[1] : l)}</li>`)
      }
    } else {
      flushList()
      const withBreaks = lines.map(renderInline).join("<br>")
      htmlBlocks.push(`<p>${withBreaks}</p>`)
    }
  }
  flushList()

  return htmlBlocks.join("")
}