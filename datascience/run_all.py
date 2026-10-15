"""Run the full pipeline: collect, features, model, backtest, export artifacts, figures.

Usage: ``python -m datascience.run_all [--skip-collect] [--no-figures]``
"""

import argparse
import datetime as dt
import json
import logging
import os
import warnings
from typing import Dict

import pandas as pd

from . import ARTIFACT_DIR, FIGURE_DIR
from .backtest import calibrate
from .collect import collect_ids, collect_snapshots, collect_stats, collect_trades, load_snapshots, load_trades
from .features import (
    POSITIONS,
    aging_curves,
    current_features,
    season_features,
    te_premium_lift,
    training_frame,
)
from .model import build_edges, fit_production_model, summarize_walk_forward, walk_forward

logger = logging.getLogger(__name__)


def _write(name: str, payload: dict) -> str:
    path = os.path.join(ARTIFACT_DIR, name)
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True)
    logger.info("wrote %s", path)
    return path


def value_history(snapshots: pd.DataFrame, top: int = 250) -> Dict[str, Dict[str, list]]:
    """Per-format value history for the highest-valued assets (feeds sparklines)."""
    out: Dict[str, Dict[str, list]] = {}
    for qbs in (1, 2):
        sub = snapshots[(snapshots["num_qbs"] == qbs) & (snapshots["ppr"] == 1.0)]
        if sub.empty:
            continue
        latest = sub[sub["date"] == sub["date"].max()].nlargest(top, "value")
        keep = set(latest["sleeper_id"])
        history: Dict[str, list] = {}
        for row in sub[sub["sleeper_id"].isin(keep)].sort_values("date").itertuples():
            history.setdefault(str(row.sleeper_id), []).append([row.date, int(row.value)])
        out["q%d" % qbs] = history
    return out


def run(collect: bool = True, figures: bool = True) -> None:
    warnings.filterwarnings("ignore")
    if collect:
        collect_snapshots()
        collect_trades()
        collect_stats()
        collect_ids()

    table = season_features()
    curves = aging_curves(table)
    folds = walk_forward(table, curves)
    summary = summarize_walk_forward(folds)
    logger.info("walk-forward summary: %s", summary)

    last_season = int(table["season"].max())
    production = fit_production_model(training_frame(table))
    current = current_features(table)

    snapshots = load_snapshots()
    latest_day = snapshots["date"].max()
    formats = {}
    for qbs in (1, 2):
        market = snapshots[(snapshots["date"] == latest_day) & (snapshots["num_qbs"] == qbs) & (snapshots["ppr"] == 1.0)]
        market = market[market["position"].isin(POSITIONS)]
        formats["q%d" % qbs] = build_edges(market, current, production)

    generated = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    _write(
        "model_edges.json",
        {
            "generated": generated,
            "snapshot_date": latest_day,
            "data_season": last_season,
            "horizon": "points in the season after %d" % last_season,
            "metrics": summary,
            "formats": formats,
        },
    )
    _write("aging_curves.json", {"generated": generated, "data_season": last_season, "retention": curves})

    calibration = calibrate(load_trades(), snapshots)
    calibration["te_premium_lift"] = round(te_premium_lift(table), 4)
    _write("calibration.json", calibration)
    _write("value_history.json", {"generated": generated, "history": value_history(snapshots)})

    if figures:
        from .figures import make_figures

        make_figures(table, curves, folds, formats, calibration, FIGURE_DIR)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument("--no-figures", action="store_true")
    args = parser.parse_args()
    run(collect=not args.skip_collect, figures=not args.no_figures)


if __name__ == "__main__":
    main()
