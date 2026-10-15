"""Backtest and calibrate the trade-fairness heuristics against real trades.

Idea
----
Every trade in the FantasyCalc trade feed was accepted by two managers, so on
average real trades should look roughly even to a good fairness function. We
therefore choose the depth-discount ``DEPTH_DECAY`` that makes real trades look
most balanced (minimum mean squared log-ratio of the two sides' totals), report
bootstrap intervals, and compare against "no discount" and the old hand-picked
0.92. The ``EVEN_THRESHOLD`` is set to the median fairness of real trades.

Caveats (also stated in the README): trades are valued with today's market
values (they are recent, so drift is small); accepted trades are not provably
fair; and the sample is whatever ``collect trades`` has gathered so far.

If at least two snapshots more than ``MIN_FORWARD_DAYS`` apart exist, a forward
test also checks whether the side the heuristic says "wins" really gained
more market value afterwards.
"""

import datetime as dt
import logging
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

OLD_DECAY = 0.92
DECAY_GRID = np.round(np.arange(0.50, 1.0001, 0.005), 3)
MIN_DECAY = 0.5  # lower edge of the search; a fit at the edge is flagged in the report
MIN_FORWARD_DAYS = 7
MIN_TRADES = 40  # below this, keep the existing constants rather than over-fit
POSITIONS = ("QB", "RB", "WR", "TE")


def side_total(values: Sequence[float], decay: float) -> float:
    ordered = sorted(values, reverse=True)
    return float(sum(v * decay ** i for i, v in enumerate(ordered)))


def _nearest_ppr(ppr: Optional[float]) -> float:
    return min((0.0, 0.5, 1.0), key=lambda option: abs(option - (ppr if ppr is not None else 1.0)))


def value_lookup(snapshots: pd.DataFrame, day: Optional[str] = None) -> Dict[Tuple[int, float], Dict[str, dict]]:
    """{(num_qbs, ppr): {sleeper_id: {value, position}}} from the snapshot at or before ``day`` (else latest)."""
    out: Dict[Tuple[int, float], Dict[str, dict]] = {}
    if snapshots.empty:
        return out
    for (qbs, ppr), group in snapshots.groupby(["num_qbs", "ppr"]):
        dates = sorted(group["date"].unique())
        chosen = dates[-1]
        if day is not None:
            earlier = [d for d in dates if d <= day]
            chosen = earlier[-1] if earlier else dates[0]
        snap = group[group["date"] == chosen]
        out[(int(qbs), float(ppr))] = {
            str(r.sleeper_id): {"value": float(r.value), "position": r.position}
            for r in snap.itertuples()
            if pd.notna(r.value)
        }
    return out


def trade_table(trades: List[dict], lookup: Dict[Tuple[int, float], Dict[str, dict]]) -> List[dict]:
    """Attach market values to each trade; drop trades with any unpriced asset."""
    rows = []
    for trade in trades:
        book = lookup.get((int(trade.get("num_qbs") or 1), _nearest_ppr(trade.get("ppr"))))
        if not book:
            continue
        sides = []
        ok = True
        for key in ("side1", "side2"):
            assets = []
            for a in trade.get(key) or []:
                info = book.get(str(a.get("sleeper_id")))
                if info is None:
                    ok = False
                    break
                assets.append({"value": info["value"], "position": info["position"]})
            if not ok or not assets:
                ok = False
                break
            sides.append(assets)
        if ok:
            rows.append({"id": trade["id"], "date": trade.get("date"), "sides": sides})
    return rows


def _loss(rows: List[dict], decay: float, pos_mult: Optional[Dict[str, float]] = None) -> np.ndarray:
    pos_mult = pos_mult or {}
    losses = np.empty(len(rows))
    for i, row in enumerate(rows):
        a, b = (
            side_total([x["value"] * pos_mult.get(x["position"], 1.0) for x in side], decay)
            for side in row["sides"]
        )
        losses[i] = np.log(max(a, 1.0) / max(b, 1.0)) ** 2
    return losses


def fit_depth_decay(rows: List[dict], boot: int = 300, seed: int = 11) -> Dict[str, object]:
    """Grid-search the decay on real trades with bootstrap confidence interval."""
    # Precompute loss matrix: trades x grid
    matrix = np.stack([_loss(rows, d) for d in DECAY_GRID], axis=1)
    mean_loss = matrix.mean(axis=0)
    best = float(DECAY_GRID[int(np.argmin(mean_loss))])
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(boot):
        idx = rng.integers(0, len(rows), len(rows))
        boots.append(float(DECAY_GRID[int(np.argmin(matrix[idx].mean(axis=0)))]))
    lo, hi = np.percentile(boots, [5, 95])

    def at(decay: float) -> float:
        return float(mean_loss[int(np.argmin(np.abs(DECAY_GRID - decay)))])

    return {
        "best": best,
        "ci90": [float(lo), float(hi)],
        "loss_best": round(at(best), 5),
        "loss_old_092": round(at(OLD_DECAY), 5),
        "loss_no_discount": round(at(1.0), 5),
        "curve": {str(d): round(float(loss), 5) for d, loss in zip(DECAY_GRID[::5], mean_loss[::5])},
    }


def fairness_scores(rows: List[dict], decay: float) -> np.ndarray:
    scores = []
    for row in rows:
        a, b = (side_total([x["value"] for x in side], decay) for side in row["sides"])
        high = max(a, b)
        scores.append(100.0 if high == 0 else 100 * min(a, b) / high)
    return np.array(scores)


def fit_position_adjustments(rows: List[dict], decay: float, boot: int = 300, seed: int = 5) -> Dict[str, object]:
    """Test whether a position's market values are systematically over/under-paid in real trades.

    Regress the signed log ratio of the two sides on the difference in each
    position's share of value. A coefficient is only *applied* if its 90%
    bootstrap interval excludes zero, and effects are capped at +/-10%.
    """
    x, y = [], []
    for row in rows:
        a_vals = [v for v in row["sides"][0]]
        b_vals = [v for v in row["sides"][1]]
        ta, tb = side_total([v["value"] for v in a_vals], decay), side_total([v["value"] for v in b_vals], decay)
        sa, sb = sum(v["value"] for v in a_vals) or 1.0, sum(v["value"] for v in b_vals) or 1.0
        feats = [
            sum(v["value"] for v in a_vals if v["position"] == p) / sa
            - sum(v["value"] for v in b_vals if v["position"] == p) / sb
            for p in POSITIONS
        ]
        x.append(feats)
        y.append(np.log(max(ta, 1) / max(tb, 1)))
        # mirror each trade so the fit is symmetric in how sides are labelled
        x.append([-f for f in feats])
        y.append(-y[-1])
    x_arr, y_arr = np.array(x), np.array(y)

    def ridge(xm: np.ndarray, ym: np.ndarray) -> np.ndarray:
        return np.linalg.solve(xm.T @ xm + 5.0 * np.eye(xm.shape[1]), xm.T @ ym)

    coef = ridge(x_arr, y_arr)
    rng = np.random.default_rng(seed)
    n = len(rows)
    samples = []
    for _ in range(boot):
        idx = rng.integers(0, n, n)
        full = np.concatenate([2 * idx, 2 * idx + 1])
        samples.append(ridge(x_arr[full], y_arr[full]))
    lo, hi = np.percentile(np.array(samples), [5, 95], axis=0)
    applied: Dict[str, float] = {}
    report = {}
    for i, pos in enumerate(POSITIONS):
        significant = bool(lo[i] > 0 or hi[i] < 0)
        report[pos] = {"coef": round(float(coef[i]), 4), "ci90": [round(float(lo[i]), 4), round(float(hi[i]), 4)], "significant": significant}
        if significant:
            # a positive coefficient means the market over-prices that position relative to what trades clear at
            applied[pos] = round(float(np.clip(np.exp(-coef[i] * 0.5), 0.9, 1.1)), 3)
    return {"report": report, "applied": applied}


def forward_test(
    trades: List[dict], snapshots: pd.DataFrame, decay: float, min_days: int = MIN_FORWARD_DAYS
) -> Dict[str, object]:
    """Does the side the heuristic favors actually gain more market value later?"""
    if snapshots.empty:
        return {"status": "no snapshots"}
    dates = sorted(snapshots["date"].unique())
    first, last = dates[0], dates[-1]
    span = (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days
    if span < min_days:
        return {"status": f"insufficient history ({span} days between snapshots; need {min_days})", "snapshot_days": span}

    outcomes = []
    for trade in trades:
        day = (trade.get("date") or "")[:10]
        if not day or day >= last:
            continue
        early = value_lookup(snapshots, day)
        late = value_lookup(snapshots)
        key = (int(trade.get("num_qbs") or 1), _nearest_ppr(trade.get("ppr")))
        if key not in early or key not in late:
            continue
        ok, nows, laters = True, [], []
        for side in ("side1", "side2"):
            now_vals, later_vals = [], []
            for a in trade.get(side) or []:
                sid = str(a.get("sleeper_id"))
                if sid not in early[key] or sid not in late[key]:
                    ok = False
                    break
                now_vals.append(early[key][sid]["value"])
                later_vals.append(late[key][sid]["value"])
            if not ok or not now_vals:
                ok = False
                break
            nows.append(now_vals)
            laters.append(later_vals)
        if not ok:
            continue
        # side1 gives `side1`, so side2 receives side1's assets
        recv_now = (side_total(nows[1], decay), side_total(nows[0], decay))  # (side1 receives, side2 receives)
        heuristic_winner = 0 if recv_now[0] > recv_now[1] else 1
        gain = (sum(laters[1]) - sum(nows[1]), sum(laters[0]) - sum(nows[0]))
        actual_winner = 0 if gain[0] > gain[1] else 1
        outcomes.append(heuristic_winner == actual_winner)
    if len(outcomes) < 20:
        return {"status": f"only {len(outcomes)} comparable trades", "n": len(outcomes)}
    hit = float(np.mean(outcomes))
    return {"status": "ok", "n": len(outcomes), "winner_hit_rate": round(hit, 3), "snapshot_days": span}


def calibrate(
    trades: List[dict],
    snapshots: pd.DataFrame,
    boot: int = 300,
) -> Dict[str, object]:
    lookup = value_lookup(snapshots)
    rows = trade_table(trades, lookup)
    result: Dict[str, object] = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "n_trades_collected": len(trades),
        "n_trades_used": len(rows),
    }
    if len(rows) < MIN_TRADES:
        result["status"] = f"too few priced trades ({len(rows)} < {MIN_TRADES}); constants unchanged"
        result["depth_decay"] = OLD_DECAY
        result["even_threshold"] = 90
        result["position_multipliers"] = {}
        return result

    fit = fit_depth_decay(rows, boot=boot)
    decay = float(np.clip(float(fit["best"]), MIN_DECAY, 1.0))  # type: ignore[arg-type]
    scores = fairness_scores(rows, decay)
    even = int(np.clip(round(float(np.median(scores))), 80, 95))
    positions = fit_position_adjustments(rows, decay, boot=boot)
    result.update(
        status="ok",
        depth_decay=decay,
        depth_decay_fit=fit,
        even_threshold=even,
        fairness_percentiles={str(p): round(float(np.percentile(scores, p)), 1) for p in (10, 25, 50, 75, 90)},
        position_multipliers=positions["applied"],
        position_fit=positions["report"],
        forward_test=forward_test(trades, snapshots, decay),
    )
    return result
