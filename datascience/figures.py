"""Static charts for the README and notebooks."""

import os
from typing import Dict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def make_figures(table, curves, folds: pd.DataFrame, formats: Dict[str, dict], calibration: dict, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)

    fig, ax = plt.subplots(figsize=(6, 4))
    for pos, curve in curves.items():
        ages = sorted(curve)
        ax.plot(ages, [curve[a] for a in ages], label=pos)
    ax.axhline(1.0, color="grey", lw=0.8, ls="--")
    ax.set_xlabel("Age")
    ax.set_ylabel("Next-season PPG retention")
    ax.set_title("Empirical aging curves")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "aging_curves.png"), dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 4))
    for method, group in folds.groupby("method"):
        ax.plot(group["season"], group["mae"], marker="o", label=method)
    ax.set_xlabel("Test season (features from this year)")
    ax.set_ylabel("MAE (PPR points, next season)")
    ax.set_title("Walk-forward evaluation")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "walk_forward_mae.png"), dpi=130)
    plt.close(fig)

    fit = calibration.get("depth_decay_fit")
    if fit:
        curve = fit["curve"]
        xs = sorted(float(k) for k in curve)
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot(xs, [curve[str(x)] if str(x) in curve else curve[repr(x)] for x in xs])
        ax.axvline(calibration["depth_decay"], color="C1", label="fitted %.3f" % calibration["depth_decay"])
        ax.axvline(0.92, color="grey", ls="--", label="old 0.92")
        ax.set_xlabel("DEPTH_DECAY")
        ax.set_ylabel("Mean squared log imbalance of real trades")
        ax.set_title("Calibrating the depth discount")
        ax.legend()
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "depth_decay_calibration.png"), dpi=130)
        plt.close(fig)

    players = list(formats["q2"]["players"].values())
    if players:
        fig, ax = plt.subplots(figsize=(6, 5))
        x = np.array([p["fundamental_value"] for p in players])
        y = np.array([p["market_value"] for p in players])
        colors = ["tab:green" if p["signal"] == "buy" else "tab:red" if p["signal"] == "sell" else "tab:grey" for p in players]
        ax.scatter(x, y, c=colors, s=12)
        top = max(x.max(), y.max())
        ax.plot([0, top], [0, top], color="black", lw=0.8)
        ax.set_xlabel("Production-implied value (out-of-fold)")
        ax.set_ylabel("Market value")
        ax.set_title("Market vs. fundamentals (superflex)")
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "market_vs_fundamentals.png"), dpi=130)
        plt.close(fig)
