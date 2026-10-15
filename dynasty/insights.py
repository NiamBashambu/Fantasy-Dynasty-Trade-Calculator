"""Read-only access to the artifacts produced by the offline ``datascience`` pipeline.

The web app never imports pandas or scikit-learn. It loads small JSON files
from ``dynasty/data/`` (regenerate them with ``python -m datascience.run_all``).
Every accessor degrades to a safe default if a file is missing, so the app
still works with plain market values.
"""

import functools
import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DYNASTY_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))

DEFAULT_DEPTH_DECAY = 0.92
DEFAULT_EVEN_THRESHOLD = 90
MIN_DEPTH_DECAY = 0.5
LONGEVITY_YEARS = 3
PICK_AGE = 21.0
SIGNAL_LABELS = {"buy": "Buy low", "sell": "Sell high", "hold": "Fair"}


@functools.lru_cache(maxsize=None)
def _load(name: str) -> Dict[str, Any]:
    path = os.path.join(DATA_DIR, name)
    try:
        with open(path) as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        logger.info("Artifact %s not available (%s); using defaults", name, exc)
        return {}


def reload() -> None:
    """Drop cached artifacts (used by tests and after re-running the pipeline)."""
    _load.cache_clear()


# -- calibration -------------------------------------------------------------

def calibration() -> Dict[str, Any]:
    return _load("calibration.json")


def _calibrated() -> bool:
    return calibration().get("status") == "ok"


def depth_decay() -> float:
    if _calibrated():
        try:
            return min(1.0, max(MIN_DEPTH_DECAY, float(calibration()["depth_decay"])))
        except (KeyError, TypeError, ValueError):
            pass
    return DEFAULT_DEPTH_DECAY


def even_threshold() -> int:
    if _calibrated():
        try:
            return int(min(95, max(80, int(calibration()["even_threshold"]))))
        except (KeyError, TypeError, ValueError):
            pass
    return DEFAULT_EVEN_THRESHOLD


def position_multipliers() -> Dict[str, float]:
    if not _calibrated():
        return {}
    raw = calibration().get("position_multipliers") or {}
    return {str(k): min(1.1, max(0.9, float(v))) for k, v in raw.items() if isinstance(v, (int, float))}


def te_premium_multiplier(bonus: float) -> float:
    """Relative TE value multiplier for a per-reception bonus (0 for none)."""
    lift = calibration().get("te_premium_lift")
    if not bonus or not isinstance(lift, (int, float)):
        return 1.0
    return 1.0 + min(float(bonus), 1.5) * float(lift)


# -- aging -------------------------------------------------------------------

def retention(position: Optional[str], age: float) -> float:
    """Expected next-season PPG multiplier at ``age`` (empirical curve, with a fallback)."""
    curves = _load("aging_curves.json").get("retention") or {}
    curve = curves.get(position or "")
    bucket = int(min(36, max(21, round(age))))
    if curve and str(bucket) in curve:
        return float(curve[str(bucket)])
    return max(0.6, 1.0 - 0.03 * max(0.0, age - 25.0))


def longevity(position: Optional[str], age: Optional[float], years: int = LONGEVITY_YEARS) -> float:
    """Expected production over the next ``years`` seasons, relative to this season (1.0 = one year).

    Product of year-over-year retention along the empirical aging curve. A
    young asset scores higher than an old one at the same position. Picks use
    the age of a rookie.
    """
    if age is None:
        age = PICK_AGE
    total, running = 0.0, 1.0
    for step in range(years):
        total += running
        running *= retention(position, age + step)
    return total


# -- model edges -------------------------------------------------------------

def model_meta() -> Dict[str, Any]:
    data = _load("model_edges.json")
    return {k: data.get(k) for k in ("generated", "snapshot_date", "data_season", "horizon", "metrics")}


def _format(num_qbs: int) -> Dict[str, Any]:
    return (_load("model_edges.json").get("formats") or {}).get("q2" if num_qbs >= 2 else "q1") or {}


def edges(num_qbs: int) -> Dict[str, Dict[str, Any]]:
    """{sleeper_id: edge record} for the league's QB format."""
    return _format(num_qbs).get("players") or {}


def bench_values(num_qbs: int) -> Dict[str, Dict[str, Any]]:
    return _format(num_qbs).get("bench") or {}


def diagnostics(num_qbs: int) -> Dict[str, Any]:
    return _format(num_qbs).get("diagnostics") or {}


def edge_for(asset_key: str, num_qbs: int) -> Optional[Dict[str, Any]]:
    record = edges(num_qbs).get(str(asset_key).split("@")[0])
    if record:
        return dict(record, label=SIGNAL_LABELS.get(str(record.get("signal")), ""))
    return None


def edge_fraction(asset_key: str, num_qbs: int, cap: float = 0.5) -> float:
    """Model edge as a fraction of market value, capped so one noisy player cannot dominate."""
    record = edges(num_qbs).get(str(asset_key).split("@")[0])
    if not record:
        return 0.0
    return max(-cap, min(cap, float(record.get("edge_pct", 0.0)) / 100.0))


def lists(num_qbs: int, limit: int = 12) -> Dict[str, List[Dict[str, Any]]]:
    players = list(edges(num_qbs).values())
    top = [p for p in players if p.get("market_value", 0) >= 1500]
    buy = sorted((p for p in top if p["signal"] == "buy"), key=lambda p: p["z"], reverse=True)[:limit]
    sell = sorted((p for p in top if p["signal"] == "sell"), key=lambda p: p["z"])[:limit]
    return {"buy": buy, "sell": sell}


# -- value history -----------------------------------------------------------

def history(num_qbs: int, sleeper_id: str) -> List[List[Any]]:
    data = (_load("value_history.json").get("history") or {}).get("q2" if num_qbs >= 2 else "q1") or {}
    return data.get(str(sleeper_id), [])


def sparkline_points(series: Sequence[float], width: int = 120, height: int = 28) -> str:
    """SVG polyline points string for a value series (empty if fewer than two points)."""
    if len(series) < 2:
        return ""
    lo, hi = min(series), max(series)
    span = (hi - lo) or 1.0
    step = width / (len(series) - 1)
    return " ".join(f"{i * step:.1f},{height - (v - lo) / span * (height - 2) - 1:.1f}" for i, v in enumerate(series))


def aging_table() -> Dict[str, Dict[int, float]]:
    curves = _load("aging_curves.json").get("retention") or {}
    return {pos: {int(a): float(v) for a, v in c.items()} for pos, c in curves.items()}
