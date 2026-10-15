"""Generate and execute the analysis notebooks.

Run from the repo root after ``python -m datascience.run_all``:

    python notebooks/build_notebooks.py

Notebooks are generated from this file so diffs stay readable; the executed
copies (with outputs) are what you open to read the analysis.
"""

import os
import sys

import nbformat
from nbclient import NotebookClient
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SETUP = """\
import os, sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.abspath(".."))
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
%matplotlib inline
from datascience.features import *
from datascience.model import *
from datascience.collect import load_snapshots, load_trades
"""

NOTEBOOKS = {
    "01_eda_and_aging.ipynb": [
        ("md", "# 1. Exploratory analysis and aging curves\n\nWhat does a skill-position career look like, and how fast does production decay by position?"),
        ("code", SETUP + "table = season_features()\nprint(table.shape, 'seasons', table.season.min(), '-', table.season.max())\ntable[['name','position','season','age','games','ppg','next_points']].tail()"),
        ("md", "## Production by position and age\nMedian points per game by age shows the shape of a typical career."),
        ("code", "sub = table[(table.games >= 8) & table.age.notna()].copy()\nsub['age_b'] = sub.age.round().clip(21, 36)\npivot = sub.pivot_table(index='age_b', columns='position', values='ppg', aggfunc='median')\npivot.plot(figsize=(7, 4), marker='o', title='Median PPR points per game by age (8+ games)')\nplt.ylabel('PPG'); plt.show()"),
        ("md", "## Empirical aging curves\nRetention is the ratio of next-season to current-season PPG, pooled over players with 8+ games in both seasons. The running back cliff after 26 is visible; QBs hold value longest."),
        ("code", "curves = aging_curves(table)\npd.DataFrame(curves).loc[21:36].round(3)"),
        ("code", "pd.DataFrame(curves).loc[21:36].plot(figsize=(7, 4), title='Next-season PPG retention by age')\nplt.axhline(1.0, color='grey', ls='--'); plt.ylabel('retention'); plt.show()"),
        ("md", "## TE premium\nA per-reception bonus helps tight ends because they score a large share of their points on receptions."),
        ("code", "print('receptions per PPR point, top-12 TEs over last 5 seasons:', round(te_premium_lift(table), 3))\nprint('=> a +0.5 bonus lifts a starting TE by about', f'{0.5*te_premium_lift(table):.0%}')"),
    ],
    "02_production_model.ipynb": [
        ("md", "# 2. Next-season production model\n\nGoal: predict a player's next-season PPR points. Evaluation is **walk-forward**: for each test season the model trains only on earlier seasons, then is scored on players it has never seen. Baselines: *persistence* (next year = this year) and *age curve* (this year x position/age retention)."),
        ("code", SETUP + "table = season_features()\ncurves = aging_curves(table)\nfolds = walk_forward(table, curves)\nfolds.round(2)"),
        ("code", "summary = summarize_walk_forward(folds)\npd.DataFrame(summary).T"),
        ("code", "pivot = folds.pivot(index='season', columns='method', values='mae')\npivot.plot(marker='o', figsize=(7, 4), title='MAE by test season'); plt.ylabel('MAE (points)'); plt.show()"),
        ("md", "## Reading the result\nThe model beats both baselines on every fold, but only modestly (roughly 10% lower MAE than persistence). Next-season football production is mostly noise: injuries, role changes, and team context are not in the features. The 20-80% quantile band should contain roughly 60% of outcomes if calibrated; `band_coverage` reports the realised share."),
        ("code", "usable = training_frame(table)\nmodel = fit_production_model(usable[usable.season < usable.season.max()])\ntest = usable[usable.season == usable.season.max()]\npred = model.predict(test)\nres = test[['name','position','points','next_points']].join(pred)\nres['err'] = res.pred_points - res.next_points\nres.sort_values('next_points', ascending=False).head(10).round(1)"),
        ("code", "from sklearn.inspection import permutation_importance\nx = feature_matrix(test); y = test.next_points.to_numpy()\nimp = permutation_importance(model.mean, x, y, n_repeats=5, random_state=0)\npd.Series(imp.importances_mean, index=FEATURE_COLUMNS).sort_values().plot.barh(figsize=(6, 5), title='Permutation importance'); plt.show()"),
    ],
    "03_market_edges.ipynb": [
        ("md", "# 3. Market vs. fundamentals (the edge signal)\n\nWe fit how the market prices a profile (projected points, age, experience, draft capital, position) using **out-of-fold** predictions. The residual says whether a player is priced above or below similar profiles. It is a relative-value signal, not a forecast that the market is wrong. Rookies without NFL history are excluded."),
        ("code", SETUP + "import json\nedges = json.load(open('../dynasty/data/model_edges.json'))\nprint(edges['snapshot_date'], edges['data_season'], edges['metrics']['model'])\nq2 = pd.DataFrame(edges['formats']['q2']['players']).T\nq2['z'] = q2.z.astype(float)\nprint(edges['formats']['q2']['diagnostics'])\nq2.sort_values('z', ascending=False).head(10)[['name','position','market_value','fundamental_value','edge','z','pred_points']]"),
        ("code", "q2.sort_values('z').head(10)[['name','position','market_value','fundamental_value','edge','z','pred_points']]"),
        ("code", "fig, ax = plt.subplots(figsize=(6, 5))\ncolors = q2.signal.map({'buy':'tab:green','sell':'tab:red','hold':'tab:grey'})\nax.scatter(q2.fundamental_value.astype(float), q2.market_value.astype(float), c=colors, s=14)\nm = max(q2.fundamental_value.astype(float).max(), q2.market_value.astype(float).max())\nax.plot([0, m], [0, m], 'k-', lw=0.8); ax.set_xlabel('production-implied value'); ax.set_ylabel('market value'); plt.show()"),
        ("md", "## Caveats\nThe value model explains roughly half the variance of log market value, so edges are noisy. Injured players (a lost season looks like low production) and players whose role changed show up as 'sell' even if the market is right. Use the signal as a prompt to look closer."),
    ],
    "04_trade_calibration.ipynb": [
        ("md", "# 4. Calibrating the trade heuristics on real trades\n\n`DEPTH_DECAY` discounts each additional asset on a side. Instead of guessing, we pick the value that makes **real accepted trades** look most balanced, with bootstrap intervals."),
        ("code", SETUP + "import json\nimport datascience.backtest as bt\nsnaps = load_snapshots(); trades = load_trades()\nrows = bt.trade_table(trades, bt.value_lookup(snaps))\nprint(len(trades), 'trades collected;', len(rows), 'fully priced')"),
        ("code", "grid = [0.5, 0.6, 0.645, 0.7, 0.8, 0.92, 1.0]\npd.DataFrame({'decay': grid, 'mean_sq_log_imbalance': [bt._loss(rows, d).mean() for d in grid], 'median_fairness': [np.median(bt.fairness_scores(rows, d)) for d in grid]}).round(4)"),
        ("code", "fit = bt.fit_depth_decay(rows, boot=300)\n{k: v for k, v in fit.items() if k != 'curve'}"),
        ("code", "ds = np.array(sorted(float(k) for k in fit['curve']))\nplt.plot(ds, [fit['curve'][str(d)] for d in ds]); plt.axvline(fit['best'], color='C1', label='fitted'); plt.axvline(0.92, color='grey', ls='--', label='old'); plt.legend(); plt.xlabel('DEPTH_DECAY'); plt.ylabel('loss'); plt.show()"),
        ("md", "## Fairness of real trades\nThe median real trade is far from perfectly even, so the 'even' threshold is set at that median (clamped to 80-95) instead of an arbitrary 90."),
        ("code", "scores = bt.fairness_scores(rows, fit['best'])\nplt.hist(scores, bins=20); plt.xlabel('fairness %'); plt.ylabel('trades'); plt.show()\npd.Series(scores).describe().round(1)"),
        ("md", "## Position adjustments\nA position is only adjusted if its bootstrap interval excludes zero."),
        ("code", "pos = bt.fit_position_adjustments(rows, fit['best'])\npd.DataFrame(pos['report']).T, pos['applied']"),
        ("md", "## Forward test\nOnce snapshots span a week or more (run `python -m datascience.collect snapshots` daily), this checks whether the side the heuristic favors really gained more market value afterwards."),
        ("code", "bt.forward_test(trades, snaps, fit['best'])"),
    ],
}


def build() -> None:
    for name, cells in NOTEBOOKS.items():
        nb = new_notebook()
        nb.cells = [new_markdown_cell(src) if kind == "md" else new_code_cell(src) for kind, src in cells]
        nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
        client = NotebookClient(nb, timeout=300, kernel_name="python3", resources={"metadata": {"path": HERE}})
        client.execute()
        path = os.path.join(HERE, name)
        nbformat.write(nb, path)
        print("wrote", path)


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    build()
