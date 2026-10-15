"""Projection model, relative-value ("edge") model, and evaluation.

Two stages, deliberately kept separate:

1. **Production model**: predicts next-season PPR points from a player's season
   profile (age, usage, prior production, draft capital). Evaluated with
   walk-forward splits against naive baselines.
2. **Relative-value model**: learns how the *market* prices a profile (projected
   points, age, position, draft capital), using out-of-fold predictions so a
   player never helps price himself. The residual is the "edge": positive means
   the market is cheaper than similar profiles, negative means pricier.

The edge is a relative-value signal, not a claim that the market is wrong.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import KFold

from .features import POSITIONS, feature_matrix, training_frame

logger = logging.getLogger(__name__)

SEED = 7
SIGNAL_Z = 1.25  # |z| needed to call a buy/sell; keeps the signal conservative


def _gbm(**kwargs) -> HistGradientBoostingRegressor:
    params = dict(max_depth=3, learning_rate=0.05, max_iter=250, min_samples_leaf=25, l2_regularization=1.0, random_state=SEED)
    params.update(kwargs)
    return HistGradientBoostingRegressor(**params)


# -- stage 1: production model ------------------------------------------------

@dataclass
class ProductionModel:
    mean: HistGradientBoostingRegressor
    low: HistGradientBoostingRegressor
    high: HistGradientBoostingRegressor

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        x = feature_matrix(frame)
        out = pd.DataFrame(index=frame.index)
        out["pred_points"] = np.clip(self.mean.predict(x), 0, None)
        out["pred_low"] = np.clip(self.low.predict(x), 0, None)
        out["pred_high"] = np.clip(self.high.predict(x), 0, None)
        out["pred_high"] = np.maximum(out["pred_high"], out["pred_points"])
        out["pred_low"] = np.minimum(out["pred_low"], out["pred_points"])
        return out


def fit_production_model(train: pd.DataFrame) -> ProductionModel:
    x, y = feature_matrix(train), train["next_points"].to_numpy(dtype=float)
    return ProductionModel(
        mean=_gbm(loss="squared_error").fit(x, y),
        low=_gbm(loss="quantile", quantile=0.2).fit(x, y),
        high=_gbm(loss="quantile", quantile=0.8).fit(x, y),
    )


def _baseline_persistence(frame: pd.DataFrame) -> np.ndarray:
    return frame["points"].to_numpy(dtype=float)


def _baseline_aging(frame: pd.DataFrame, curves: Dict[str, Dict[int, float]]) -> np.ndarray:
    preds = []
    for pos, age, pts in zip(frame["position"], frame["age"], frame["points"]):
        curve = curves.get(pos, {})
        bucket = int(min(max(round(age), 21), 36)) if pd.notna(age) else 26
        preds.append(pts * curve.get(bucket, 1.0))
    return np.array(preds)


def _metrics(y: np.ndarray, pred: np.ndarray, frame: pd.DataFrame) -> Dict[str, float]:
    err = pred - y
    rho_by_pos = []
    for pos in POSITIONS:
        mask = (frame["position"] == pos).to_numpy()
        if mask.sum() > 10 and np.std(pred[mask]) > 0:
            rho_by_pos.append(spearmanr(pred[mask], y[mask])[0])
    return {
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "spearman": float(np.mean(rho_by_pos)) if rho_by_pos else float("nan"),
    }


def walk_forward(table: pd.DataFrame, curves: Dict[str, Dict[int, float]], first_test: Optional[int] = None) -> pd.DataFrame:
    """Evaluate on each test season using only earlier seasons for training."""
    usable = training_frame(table)
    seasons = sorted(usable["season"].unique())
    first_test = first_test or seasons[len(seasons) // 2]
    rows: List[Dict[str, object]] = []
    for test_season in [s for s in seasons if s >= first_test]:
        train = usable[usable["season"] < test_season]
        test = usable[usable["season"] == test_season]
        if len(train) < 200 or test.empty:
            continue
        model = fit_production_model(train)
        pred = model.predict(test)
        y = test["next_points"].to_numpy(dtype=float)
        coverage = float(((y >= pred["pred_low"]) & (y <= pred["pred_high"])).mean())
        for name, values in (
            ("model", pred["pred_points"].to_numpy()),
            ("persistence", _baseline_persistence(test)),
            ("age_curve", _baseline_aging(test, curves)),
        ):
            row: Dict[str, object] = dict(_metrics(y, values, test))
            row.update(season=int(test_season), method=name, n=len(test), band_coverage=coverage if name == "model" else np.nan)
            rows.append(row)
    return pd.DataFrame(rows)


def summarize_walk_forward(results: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    summary: Dict[str, Dict[str, float]] = {}
    for method, group in results.groupby("method"):
        summary[method] = {
            "mae": round(float(group["mae"].mean()), 2),
            "rmse": round(float(group["rmse"].mean()), 2),
            "spearman": round(float(group["spearman"].mean()), 3),
            "folds": int(len(group)),
        }
        if method == "model":
            summary[method]["band_coverage"] = round(float(group["band_coverage"].mean()), 3)
    return summary


# -- stage 2: relative value ("edge") model ---------------------------------

VALUE_FEATURES = ["pred_points", "points", "age", "years_exp", "draft_ovr", "pos_QB", "pos_RB", "pos_WR", "pos_TE"]


def _value_matrix(frame: pd.DataFrame) -> np.ndarray:
    return frame[VALUE_FEATURES].to_numpy(dtype=float)


def fit_value_model(frame: pd.DataFrame) -> HistGradientBoostingRegressor:
    """Market value (log scale) from production-based features."""
    params = dict(max_depth=3, learning_rate=0.06, max_iter=200, min_samples_leaf=8, l2_regularization=1.0)
    return _gbm(**params).fit(_value_matrix(frame), np.log(frame["value"].to_numpy(dtype=float)))


def out_of_fold_log_value(frame: pd.DataFrame, folds: int = 5) -> np.ndarray:
    oof = np.zeros(len(frame))
    x, y = _value_matrix(frame), np.log(frame["value"].to_numpy(dtype=float))
    params = dict(max_depth=3, learning_rate=0.06, max_iter=200, min_samples_leaf=8, l2_regularization=1.0)
    for train_idx, test_idx in KFold(folds, shuffle=True, random_state=SEED).split(x):
        oof[test_idx] = _gbm(**params).fit(x[train_idx], y[train_idx]).predict(x[test_idx])
    return oof


def build_edges(
    market: pd.DataFrame,
    current: pd.DataFrame,
    production: ProductionModel,
    min_value: float = 300.0,
) -> Dict[str, object]:
    """Join the market snapshot with projections and compute edges.

    ``market`` is one FantasyCalc snapshot (players only); ``current`` is the
    latest complete season feature rows. Returns dict with priced players,
    estimated bench values for unpriced players, and fit diagnostics.
    """
    proj = current.copy()
    proj[["pred_points", "pred_low", "pred_high"]] = production.predict(proj)

    market = market[(market["position"].isin(POSITIONS)) & (market["value"] > 0)].copy()
    market["sleeper_id"] = market["sleeper_id"].astype(str)
    joined = market.merge(
        proj[["sleeper_id", "gsis_id", "pred_points", "pred_low", "pred_high", "points", "draft_ovr", "age", "pos_QB", "pos_RB", "pos_WR", "pos_TE"]],
        on="sleeper_id",
        how="inner",
        suffixes=("_mkt", ""),
    )
    # prefer the market's current age (it is fresher than the season-start age)
    joined["age"] = joined["age_mkt"].where(joined["age_mkt"].notna(), joined["age"])
    joined["years_exp"] = pd.to_numeric(joined["years_exp"], errors="coerce")
    fit_rows = joined[(joined["value"] >= min_value) & joined["age"].notna()].reset_index(drop=True)
    if len(fit_rows) < 60:
        raise ValueError("too few players to fit the relative-value model")

    oof = out_of_fold_log_value(fit_rows)
    residual = np.log(fit_rows["value"].to_numpy(dtype=float)) - oof
    sigma = float(np.std(residual))
    r2 = 1 - float(np.sum(residual ** 2) / np.sum((np.log(fit_rows["value"]) - np.log(fit_rows["value"]).mean()) ** 2))
    fundamental = np.exp(oof)
    players: Dict[str, dict] = {}
    for i, row in fit_rows.iterrows():
        z = float(-residual[i] / sigma)  # positive = market is cheaper than the fundamentals imply
        fund = float(fundamental[i])
        edge = fund - float(row["value"])
        signal = "buy" if z >= SIGNAL_Z else ("sell" if z <= -SIGNAL_Z else "hold")
        players[str(row["sleeper_id"])] = {
            "name": row["name"],
            "position": row["position"],
            "market_value": int(row["value"]),
            "fundamental_value": int(round(fund)),
            "edge": int(round(edge)),
            "edge_pct": round(edge / max(1.0, float(row["value"])) * 100, 1),
            "z": round(z, 2),
            "signal": signal,
            "pred_points": round(float(row["pred_points"]), 1),
            "pred_low": round(float(row["pred_low"]), 1),
            "pred_high": round(float(row["pred_high"]), 1),
        }

    # Estimated values for players the market does not price (deep bench).
    full_model = fit_value_model(fit_rows)
    priced = set(market["sleeper_id"])
    bench_pool = proj[
        (~proj["sleeper_id"].isin(priced)) & (proj["sleeper_id"] != "") & (proj["pred_points"] > 20) & proj["age"].notna()
    ].copy()
    bench_pool["years_exp"] = bench_pool["years_exp"] + 1
    cap = float(np.percentile(market["value"], 10)) if len(market) else 500.0
    bench: Dict[str, dict] = {}
    if not bench_pool.empty:
        est = np.exp(full_model.predict(_value_matrix(bench_pool)))
        for (_, row), value in zip(bench_pool.iterrows(), est):
            bench[str(row["sleeper_id"])] = {
                "name": row["name"],
                "position": row["position"],
                "team": row.get("team"),
                "age": round(float(row["age"]) + 1, 1),
                "value": int(max(10, min(value, cap * 0.9))),
            }
    return {
        "players": players,
        "bench": bench,
        "diagnostics": {
            "n_fit": int(len(fit_rows)),
            "oof_r2_log_value": round(r2, 3),
            "residual_sigma_log": round(sigma, 3),
            "bench_cap": int(cap * 0.9),
        },
    }
