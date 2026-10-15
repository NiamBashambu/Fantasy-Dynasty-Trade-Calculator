"""Optional OpenAI layer that only *explains* numbers computed elsewhere.

The model never decides values, picks players, or scores fairness. If there is
no API key, the call fails, or the client is rate limited, callers keep the
deterministic explanation and nothing breaks.
"""

import json
import logging
from typing import Dict, Optional, Sequence

from .trades import Evaluation, Suggestion, describe_assets

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a dynasty fantasy football analyst. You are given trades with values that were "
    "already computed from market data. Explain them in plain language. Never change, "
    "recompute, or invent numbers, and never mention players or picks that are not listed. "
    "Respond only with the requested JSON."
)


class Explainer:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini", timeout: float = 20, client=None):
        self.model = model
        if client is not None:
            self.client = client
        else:
            from openai import OpenAI

            self.client = OpenAI(api_key=api_key, timeout=timeout, max_retries=1)

    def _chat_json(self, payload: dict, max_tokens: int) -> Optional[dict]:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload)},
                ],
                response_format={"type": "json_object"},
                max_tokens=max_tokens,
                temperature=0.4,
            )
            data = json.loads(response.choices[0].message.content)
            return data if isinstance(data, dict) else None
        except Exception as exc:  # network, quota, bad JSON: all fall back silently
            logger.warning("LLM explanation failed: %s", exc)
            return None

    def explain_suggestions(
        self, league_name: str, strategy: str, suggestions: Sequence[Suggestion]
    ) -> Dict[int, str]:
        """Return {index: explanation} for suggestions the model explained."""
        if not suggestions:
            return {}
        payload = {
            "task": (
                "For each trade write 2 sentences (max 60 words) explaining why it makes sense "
                "for the user's strategy. Return JSON: {\"explanations\": [{\"index\": 0, \"text\": \"...\"}]}"
            ),
            "league": league_name,
            "strategy": strategy,
            "trades": [
                {
                    "index": i,
                    "user_gives": describe_assets(s.give),
                    "user_receives": describe_assets(s.receive),
                    "partner": s.partner_name,
                    "fairness_percent": s.fairness,
                    "facts": s.reasons,
                }
                for i, s in enumerate(suggestions)
            ],
        }
        data = self._chat_json(payload, max_tokens=900)
        result: Dict[int, str] = {}
        items = (data or {}).get("explanations")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                index, text = item.get("index"), item.get("text")
                if isinstance(index, int) and 0 <= index < len(suggestions) and isinstance(text, str) and text.strip():
                    result[index] = text.strip()[:600]
        return result

    def explain_evaluation(self, evaluation: Evaluation, balancers: Sequence = ()) -> Optional[str]:
        payload = {
            "task": (
                "In 3 sentences (max 90 words) explain this trade result and what could balance it. "
                "Return JSON: {\"analysis\": \"...\"}"
            ),
            "team_a_gives": describe_assets(evaluation.give_a),
            "team_b_gives": describe_assets(evaluation.give_b),
            "team_a_value": round(evaluation.total_a),
            "team_b_value": round(evaluation.total_b),
            "fairness_percent": evaluation.fairness,
            "verdict": evaluation.winner,
            "possible_balancers": describe_assets(balancers),
        }
        data = self._chat_json(payload, max_tokens=300)
        text = (data or {}).get("analysis")
        return text.strip()[:800] if isinstance(text, str) and text.strip() else None


def template_evaluation_text(evaluation: Evaluation) -> str:
    a = ", ".join(describe_assets(evaluation.give_a))
    b = ", ".join(describe_assets(evaluation.give_b))
    base = (
        f"Team A gives {a} (value {evaluation.total_a:,.0f}). "
        f"Team B gives {b} (value {evaluation.total_b:,.0f}). "
    )
    if evaluation.verdict == "even":
        return base + f"The two sides are within {100 - evaluation.fairness}% of each other, so this is a fair deal."
    receiver = evaluation.winner
    return base + (
        f"{receiver} receives about {evaluation.gap:,.0f} more value, "
        f"so the sides are {evaluation.fairness}% balanced."
    )
