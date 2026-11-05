"""
classifier.py

Calls BRAIN_MODEL_ID (cheap/fast) to score the incoming request against
each capability, estimate complexity, and recommend an execution mode.
Falls back to a simple keyword heuristic if the model call fails or
returns unparseable output — classification should never be a hard
dependency that blocks the whole pipeline.
"""

import json
import logging
from dataclasses import dataclass

from ai.router import model_catalog as cat
from ai.router.providers import complete, ProviderError

logger = logging.getLogger("ai_router.classifier")

CLASSIFIER_SYSTEM = (
    "You are a request classifier for a developer tool. Given a task "
    "description, respond ONLY with JSON, no other text, in this exact "
    "shape:\n"
    '{"capabilities": {"coding": 0.0, "reasoning": 0.0, "math": 0.0, '
    '"research": 0.0, "writing": 0.0, "analysis": 0.0, "architecture": 0.0, '
    '"general": 0.0}, "complexity": 0.0, "execution_mode": "single", '
    '"confidence": 0.0}\n'
    "capabilities: each 0-1, how much this task needs that skill. "
    "complexity: 0-1, how hard/multi-step the task is. "
    "execution_mode: one of single, consensus, multi_agent — use single "
    "for straightforward one-skill tasks, consensus when a second opinion "
    "would materially help correctness, multi_agent when the task clearly "
    "spans 2+ distinct capabilities that would each benefit from a "
    "specialist. confidence: 0-1, how sure you are in this classification."
)


@dataclass
class ClassificationResult:
    capabilities: dict[str, float]
    complexity: float
    execution_mode: str
    confidence: float
    top_capability: str

    @property
    def top_capabilities(self) -> list[str]:
        """Capabilities scoring within 0.15 of the top score, for multi_agent assignment."""
        top_score = self.capabilities.get(self.top_capability, 0)
        return sorted(
            [c for c, s in self.capabilities.items() if s >= top_score - 0.15],
            key=lambda c: self.capabilities[c], reverse=True,
        )


def _heuristic_fallback(prompt: str) -> ClassificationResult:
    """Rule-based backup — never blocks the pipeline if the classifier model is unavailable."""
    lowered = prompt.lower()
    scores = {c: 0.3 for c in cat.CAPABILITIES}
    keyword_map = {
        "coding": ["code", "function", "bug", "implement", "refactor"],
        "architecture": ["architecture", "design", "structure", "pattern"],
        "research": ["research", "compare", "find out", "look up"],
        "math": ["calculate", "math", "formula", "equation"],
        "writing": ["write", "draft", "essay", "blog"],
    }
    for cap, keywords in keyword_map.items():
        if any(k in lowered for k in keywords):
            scores[cap] = 0.75
    top = max(scores, key=scores.get)
    return ClassificationResult(
        capabilities=scores, complexity=0.5, execution_mode="single",
        confidence=0.3, top_capability=top,
    )


async def classify(prompt: str) -> ClassificationResult:
    try:
        resp = await complete(cat.BRAIN_PROVIDER, cat.BRAIN_MODEL_ID,
                               CLASSIFIER_SYSTEM, prompt, max_tokens=300)
        data = json.loads(resp.text.strip().strip("`").removeprefix("json").strip())
        capabilities = data["capabilities"]
        top = max(capabilities, key=capabilities.get)
        return ClassificationResult(
            capabilities=capabilities,
            complexity=float(data.get("complexity", 0.5)),
            execution_mode=data.get("execution_mode", "single"),
            confidence=float(data.get("confidence", 0.5)),
            top_capability=top,
        )
    except (ProviderError, json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("Classifier call/parse failed (%s), using heuristic fallback", e)
        return _heuristic_fallback(prompt)