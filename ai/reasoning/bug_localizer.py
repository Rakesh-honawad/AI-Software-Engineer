"""
ai/reasoning/bug_localizer.py

Deterministic bug localization: NO model call. Ranks graph nodes by
evidence from:
  (1) stack-trace frames
  (2) exact identifiers in the bug report / failing test output
  (3) LEXICAL relevance: report words (split + lightly stemmed) vs node
      name tokens, file path tokens and, when repo_root is given, the
      function body. So "orders" -> create_order, "stock" -> check_stock.
  (4) call graph: callees AND callers of the strongest evidence-backed seeds
  (5) focus node (the node selected in the UI): a weak prior only. It never
      becomes a seed, so a previous selection cannot hijack the search.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from graph.algorithms.dependency.reachability import DEFAULT_REACHABILITY_EDGE_TYPES
from graph.algorithms.impact.traversal import reverse_impact_traversal
from graph.schema.edge_types import GraphEdge
from graph.schema.node_types import GraphNode


_FRAME_RES = [
    re.compile(r'File "([^"]+)", line (\d+)'),            # python traceback
    re.compile(r"^\s*([\w./\\-]+\.\w+):(\d+):", re.M),    # pytest --tb=short
]
_TEST_PATH_RE = re.compile(r"(^|/)(tests?/|test_[^/]*$|conftest\.py$)|_test\.\w+$")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_WORD_RE = re.compile(r"[a-z]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_STOP = {"the", "and", "for", "not", "with", "this", "that", "from", "self", "none",
         "true", "false", "error", "assert", "test", "tests", "def", "return", "import"}
_WORD_STOP = _STOP | {
    "why", "can", "customers", "customer", "users", "user", "than", "greater", "less",
    "more", "are", "was", "were", "has", "have", "does", "did", "how", "what", "when",
    "where", "which", "should", "would", "could", "into", "out", "all", "any", "its",
    "investigate", "trace", "chain", "dependency", "identify", "exact", "root", "cause",
    "propose", "fix", "patch", "bug", "report", "available", "place", "placed", "wrong",
    "incorrect", "expected", "actual", "function", "code", "issue", "problem", "fails",
    "failing", "failed", "get", "gets", "set", "make", "makes", "involving",
}
# "(involving cart_total)" style suffixes some UIs append from the selected node
_SELECTED_SUFFIX_RE = re.compile(r"\(\s*involving\s+[^)]*\)\s*$", re.I)

MAX_BODY_CHARS = 4000


@dataclass
class Suspect:
    node: GraphNode
    score: float
    reasons: list[str] = field(default_factory=list)
    blast_radius: int = 0   # how many other symbols depend on this one

    def to_dict(self) -> dict:
        lr = self.node.line_range
        return {
            "node_id": self.node.id,
            "name": self.node.name,
            "type": self.node.type.value,
            "file_path": self.node.file_path,
            "line_range": list(lr) if lr else None,
            "score": self.score,
            "reasons": self.reasons,
            "blast_radius": self.blast_radius,
        }


def _norm(p: str) -> str:
    return p.replace("\\", "/").removeprefix("./")


def is_test_path(p: str) -> bool:
    return bool(_TEST_PATH_RE.search(_norm(p).lower()))


def strip_selected_suffix(text: str) -> str:
    return _SELECTED_SUFFIX_RE.sub("", text).strip()
_SECTION_RE = re.compile(r"^_{3,}\s+(.+?)\s+_{3,}$", re.M)


def focus_failure_output(output: str, bug_report: str, max_sections: int = 3) -> str:
    """With several failing tests, keep only the tracebacks that relate to the report."""
    cut = output.find("short test summary info")
    body = output[:cut] if cut != -1 else output
    marks = list(_SECTION_RE.finditer(body))
    if len(marks) <= 1:
        return output
    q = set(_tokens(strip_selected_suffix(bug_report)))
    scored = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        text = body[m.start():end]
        scored.append((len(q & set(_tokens(text))), i, text))
    related = [s for s in scored if s[0] > 0] or scored[:1]
    best = sorted(related, key=lambda s: (-s[0], s[1]))[:max_sections]
    return "\n".join(t for _, _, t in sorted(best, key=lambda s: s[1]))

def extract_frames(text: str) -> list[tuple[str, int]]:
    found = []
    for rx in _FRAME_RES:
        for m in rx.finditer(text):
            found.append((m.start(), m.group(1).replace("\\", "/"), int(m.group(2))))
    found.sort()
    ordered, seen = [], set()
    for _, path, line in found:
        if (path, line) not in seen:
            seen.add((path, line))
            ordered.append((path, line))
    return ordered


def _best_file(frame_path: str, files: set[str]) -> str | None:
    frame_norm = _norm(frame_path).casefold()

    matches = [
        f for f in files
        if (
            frame_norm == _norm(f).casefold()
            or frame_norm.endswith("/" + _norm(f).casefold())
        )
    ]

    return max(matches, key=len) if matches else None
# ---------- lexical matching ----------

def _stem(w: str) -> str:
    """Tiny normalizer: validation/validate/validated -> 'validat', creation/create -> 'creat'."""
    for suf in ("ation", "ition", "ing", "ion", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            if suf in ("ation", "ition"):
                w += "at" if suf == "ation" else "it"
            break
    if w.endswith("e") and len(w) > 3:
        w = w[:-1]
    return w


def _tokens(text: str, drop_stop: bool = True) -> list[str]:
    """snake_case + camelCase aware, lowercase, lightly stemmed."""
    out: list[str] = []
    for raw in _IDENT_RE.findall(text):
        for part in _CAMEL_RE.sub("_", raw).lower().split("_"):
            for w in _WORD_RE.findall(part):
                if len(w) < 3:
                    continue
                if drop_stop and w in _WORD_STOP:
                    continue
                out.append(_stem(w))
    return out


def _body(repo_root: str | None, node: GraphNode, cache: dict[str, list[str]]) -> str:
    if not repo_root or not node.file_path or not node.line_range:
        return ""
    try:
        key = _norm(node.file_path)
        if key not in cache:   # read each source file once per localize() call
            cache[key] = (Path(repo_root) / key).read_text(encoding="utf-8", errors="replace").splitlines()
        lines = cache[key]
        lo, hi = node.line_range
        return "\n".join(lines[max(0, lo - 1):hi])[:MAX_BODY_CHARS]
    except OSError:
        return ""


# Node types that are real code symbols (patchable, rankable as suspects).
_SYMBOL_KINDS = {"function", "class", "interface", "endpoint", "service", "repositorylayer"}
# Only these have a body of their own; a Class/Interface body would swallow its members.
_BODY_KINDS = {"function", "endpoint"}


def _kind(n: GraphNode) -> str:
    return str(getattr(n.type, "value", n.type)).lower()


def _is_symbol(n: GraphNode) -> bool:
    return bool(n.file_path) and _kind(n) in _SYMBOL_KINDS and not getattr(n, "is_deleted", False)


def _has_own_body(n: GraphNode) -> bool:
    return _kind(n) in _BODY_KINDS


def _callee_depths(seed: str, adj: dict[str, list[str]], max_depth: int) -> dict[str, int]:
    """Bounded forward BFS over call/reference/inherit edges: {node_id: hops}."""
    depths: dict[str, int] = {}
    frontier, seen = [seed], {seed}
    for d in range(1, max_depth + 1):
        nxt = []
        for cur in frontier:
            for nb in adj.get(cur, []):
                if nb not in seen:
                    seen.add(nb)
                    depths[nb] = d
                    nxt.append(nb)
        frontier = nxt
    return depths


def _related(a: str, b: str) -> bool:
    """Same stem, or one is a prefix of the other (reduct~reduc, cancellat~cancel)."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 5 and long_.startswith(short)


def _lexical_scores(
    report_tokens: list[str], nodes: list[GraphNode], repo_root: str | None
) -> dict[str, tuple[float, list[str]]]:
    """IDF-weighted overlap. Name tokens count most, then path, then body."""
    if not report_tokens:
        return {}
    q = set(report_tokens)
    docs: dict[str, tuple[Counter, Counter, Counter]] = {}
    df: Counter = Counter()
    cache: dict[str, list[str]] = {}
    for n in nodes:
        name_t = Counter(_tokens(n.name, drop_stop=False))
        path_t = Counter(_tokens(n.file_path or "", drop_stop=False))
        body_t = Counter(_tokens(_body(repo_root, n, cache), drop_stop=False)) if _has_own_body(n) else Counter()
        docs[n.id] = (name_t, path_t, body_t)
        for t in set(name_t) | set(path_t) | set(body_t):
            df[t] += 1
    # query token -> related vocabulary tokens (computed once, not per node)
    equiv = {t: [v for v in df if _related(t, v)] for t in q}
    total = max(len(nodes), 1)
    out: dict[str, tuple[float, list[str]]] = {}
    for nid, (name_t, path_t, body_t) in docs.items():
        score, hits = 0.0, []
        for t in q:
            best = (0.0, "", "")
            for v in equiv[t]:
                idf = math.log(1 + total / (1 + df[v]))
                for weight, where, bag in ((3.0, "name", name_t), (1.5, "path", path_t), (1.0, "body", body_t)):
                    if v in bag and weight * idf > best[0]:
                        best = (weight * idf, where, v)
            if best[0] > 0:
                score += best[0]
                hits.append(f"{best[1]}~'{best[2]}'")
        if score > 0:
            out[nid] = (score, hits)
    return out

_LIBRARY_FRAME_RE = re.compile(
    r"(?:^|/)(?:"
    r"(?:\.venv|venv|env)/lib(?:/|$)"
    r"|site-packages(?:/|$)"
    r"|dist-packages(?:/|$)"
    r"|python\d+(?:\.\d+)?/lib(?:/|$)"
    r"|lib/python\d+(?:\.\d+)?(?:/|$)"
    r"|lib64/python\d+(?:\.\d+)?(?:/|$)"
    r")",
    re.IGNORECASE,
)


def _is_library_frame(path: str) -> bool:
    low = path.replace("\\", "/").casefold()
    return bool(_LIBRARY_FRAME_RE.search(low))


def localize(
    bug_report: str,
    failure_output: str,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    top_k: int = 6,
    focus_node_ids: list[str] | None = None,
    repo_root: str | None = None,
) -> list[Suspect]:
    if top_k <= 0:
        return []

    bug_report = strip_selected_suffix(bug_report)
    nodes = [n for n in nodes if not getattr(n, "is_deleted", False)]
    combined = f"{failure_output}\n{bug_report}"

    by_id = {n.id: n for n in nodes}
    scores: dict[str, float] = {}
    why: dict[str, list[str]] = {}
    seeds: dict[str, float] = {}

    def add(n: GraphNode, pts: float, reason: str) -> None:
        scores[n.id] = scores.get(n.id, 0.0) + pts
        why.setdefault(n.id, []).append(reason)

    files = {_norm(n.file_path) for n in nodes if n.file_path}

    # 1. Stack frames -> most specific node containing that line.
    for idx, (path, line) in enumerate(extract_frames(combined)):
        if _is_library_frame(path):
            continue

        match = _best_file(path, files)
        if not match:
            continue

        cands = [
            n for n in nodes
            if n.file_path
            and _norm(n.file_path) == match
            and n.line_range
            and n.line_range[0] <= line <= n.line_range[1]
        ]

        if not cands:
            continue

        node = min(cands, key=lambda n: n.line_range[1] - n.line_range[0])
        is_test = is_test_path(match)

        add(
            node,
            2.0 if is_test else 10.0 + idx * 0.1,
            f"appears in the stack trace at {match}:{line}",
        )
        seeds[node.id] = max(
            seeds.get(node.id, 0),
            2.0 if is_test else 10.0,
        )

    # 2. Exact identifier evidence.
    # Identifiers from traceback function names must retain their file context.
    frame_ident_targets: set[str] = set()
    unmatched_frame_idents: set[str] = set()
    non_frame_lines: list[str] = []

    for line in failure_output.splitlines():
        frames = extract_frames(line)

        if not frames:
            non_frame_lines.append(line)
            continue

        function_match = re.search(
            r"\bin\s+([A-Za-z_][A-Za-z0-9_]*)\s*$",
            line.strip(),
        )

        if not function_match:
            continue

        function_name = function_match.group(1).lower()

        for path, _ in frames:
            if _is_library_frame(path):
                continue

            match = _best_file(path, files)

            if match is None:
                unmatched_frame_idents.add(function_name)
                continue

            for node in nodes:
                if (
                    node.file_path
                    and _norm(node.file_path).casefold()
                    == _norm(match).casefold()
                    and node.name.lower() == function_name
                ):
                    frame_ident_targets.add(node.id)

    idents = {
        i.lower()
        for i in _IDENT_RE.findall("\n".join(non_frame_lines))
    } - _STOP

    idents.update(
        i.lower()
        for i in _IDENT_RE.findall(bug_report)
    )

    idents.update(unmatched_frame_idents)

    for n in nodes:
        if (
            n.id in frame_ident_targets
            or (len(n.name) >= 3 and n.name.lower() in idents)
        ):
            is_test = bool(n.file_path) and is_test_path(n.file_path)

            add(
                n,
                1.0 if is_test else 4.0,
                f"'{n.name}' is mentioned in the bug report / test output",
            )

            if not is_test:
                seeds[n.id] = max(seeds.get(n.id, 0), 4.0)

    # 3. Lexical relevance of report wording to names / paths / bodies.
    lex = _lexical_scores(
        _tokens(bug_report),
        [n for n in nodes if _is_symbol(n)],
        repo_root,
    )

    for nid, (pts, hits) in lex.items():
        n = by_id[nid]

        if is_test_path(n.file_path):
            continue

        add(
            n,
            min(pts, 8.0),
            "report wording matches: " + ", ".join(hits[:4]),
        )
        seeds[nid] = max(seeds.get(nid, 0), pts)

    # 4. Call graph around strongest seeds, bounded to 2 hops.
    adj: dict[str, list[str]] = {}

    for e in edges:
        if e.type in DEFAULT_REACHABILITY_EDGE_TYPES:
            adj.setdefault(e.from_id, []).append(e.to_id)

    top_seeds = sorted(
        seeds,
        key=lambda i: scores.get(i, 0),
        reverse=True,
    )[:3]

    for sid in top_seeds:
        seed = by_id[sid]

        for nid, hops in _callee_depths(sid, adj, 2).items():
            n = by_id.get(nid)

            if n is not None:
                add(
                    n,
                    1.5 / hops,
                    f"called {'directly' if hops == 1 else 'indirectly (2 hops)'} by {seed.name}",
                )

        for nid, hops in reverse_impact_traversal(
            sid, edges, max_depth=2
        ).items():
            n = by_id.get(nid)

            if n is not None and not is_test_path(n.file_path or ""):
                add(
                    n,
                    1.0 / hops,
                    f"{'calls' if hops == 1 else 'indirectly depends on'} {seed.name}",
                )

    # 5. UI-selected node: weak prior only, never a seed.
    for fid in focus_node_ids or []:
        n = by_id.get(fid)

        if n is not None:
            add(n, 1.5, "selected in the graph view (weak hint)")

    ranked = [
        i
        for i in sorted(scores, key=lambda i: scores[i], reverse=True)
        if _is_symbol(by_id[i])
    ]

    non_test = [
        i for i in ranked
        if not is_test_path(by_id[i].file_path)
    ]

    out = []

    for nid in (non_test or ranked)[:top_k]:
        n = by_id[nid]
        radius = len(reverse_impact_traversal(n.id, edges))

        out.append(
            Suspect(n, round(scores[nid], 1), why[nid], radius)
        )

    return out
