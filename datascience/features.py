"""Feature engineering: one row per skill-position player season.

For a player-season ``t`` we describe how the player looked at the end of season
``t`` and attach the target ``next_points`` (PPR points scored in ``t + 1``, zero
if the player did not appear). Training only ever uses rows whose target season
is already complete, and walk-forward evaluation (see ``model.py``) never lets a
later season leak into an earlier training fold.
"""

import os
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import RAW_DIR

POSITIONS = ["QB", "RB", "WR", "TE"]
MIN_COMPLETE_GAMES = 16  # a season counts as complete once someone has played this many games

STAT_COLUMNS = [
    "games",
    "fantasy_points_ppr",
    "receptions",
    "targets",
    "carries",
    "attempts",
    "target_share",
    "wopr",
    "air_yards_share",
    "receiving_yards",
    "rushing_yards",
    "passing_yards",
]

FEATURE_COLUMNS = [
    "age",
    "years_exp",
    "draft_ovr",
    "games",
    "ppg",
    "ppg_prev",
    "ppg_prev2",
    "points",
    "points_prev",
    "ppg_change",
    "targets_pg",
    "carries_pg",
    "attempts_pg",
    "target_share",
    "wopr",
    "pos_QB",
    "pos_RB",
    "pos_WR",
    "pos_TE",
]


def load_stats(raw_dir: str = RAW_DIR) -> pd.DataFrame:
    frames = []
    for name in sorted(os.listdir(raw_dir)):
        if name.startswith("stats_") and name.endswith(".csv"):
            frames.append(pd.read_csv(os.path.join(raw_dir, name), low_memory=False))
    if not frames:
        raise FileNotFoundError("No stats files found. Run `python -m datascience.collect stats` first.")
    stats = pd.concat(frames, ignore_index=True)
    stats = stats[stats["player_id"].notna() & stats["position"].isin(POSITIONS)].copy()
    stats = stats[stats["season_type"] == "REG"]
    for col in STAT_COLUMNS:
        if col not in stats:
            stats[col] = 0.0
    stats[STAT_COLUMNS] = stats[STAT_COLUMNS].fillna(0.0)
    stats = stats.rename(columns={"player_id": "gsis_id", "player_display_name": "name", "recent_team": "team"})
    return stats[["gsis_id", "name", "position", "team", "season"] + STAT_COLUMNS]


def load_ids(raw_dir: str = RAW_DIR) -> pd.DataFrame:
    ids = pd.read_csv(os.path.join(raw_dir, "player_ids.csv"), low_memory=False)
    ids = ids[ids["gsis_id"].notna()].copy()
    ids["sleeper_id"] = ids["sleeper_id"].map(lambda v: str(int(v)) if pd.notna(v) else "")
    ids["birthdate"] = pd.to_datetime(ids["birthdate"], errors="coerce")
    keep = ["gsis_id", "sleeper_id", "birthdate", "draft_year", "draft_ovr"]
    return ids[keep].drop_duplicates("gsis_id")


def latest_complete_season(stats: pd.DataFrame) -> int:
    by_season = stats.groupby("season")["games"].max()
    complete = by_season[by_season >= MIN_COMPLETE_GAMES]
    if complete.empty:
        raise ValueError("no complete season in stats")
    return int(complete.index.max())


def season_features(stats: Optional[pd.DataFrame] = None, ids: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Return the player-season table with features and ``next_points`` target.

    Seasons that are not complete (the current one) are dropped.
    """
    stats = load_stats() if stats is None else stats
    ids = load_ids() if ids is None else ids
    last = latest_complete_season(stats)
    df = stats[stats["season"] <= last].copy()
    df = df.merge(ids, on="gsis_id", how="left")

    games = df["games"].clip(lower=1)
    df["points"] = df["fantasy_points_ppr"]
    df["ppg"] = df["points"] / games
    df["targets_pg"] = df["targets"] / games
    df["carries_pg"] = df["carries"] / games
    df["attempts_pg"] = df["attempts"] / games

    season_start = pd.to_datetime(df["season"].astype(int).astype(str) + "-09-01")
    df["age"] = (season_start - df["birthdate"]).dt.days / 365.25
    df["years_exp"] = df["season"] - df["draft_year"]
    df["draft_ovr"] = pd.to_numeric(df["draft_ovr"], errors="coerce")

    df = df.sort_values(["gsis_id", "season"])
    key = df[["gsis_id", "season", "ppg", "points"]].copy()
    for lag, suffix in ((1, "_prev"), (2, "_prev2")):
        shifted = key.copy()
        shifted["season"] = shifted["season"] + lag
        shifted = shifted.rename(columns={"ppg": "ppg" + suffix, "points": "points" + suffix})
        cols = ["gsis_id", "season", "ppg" + suffix] + (["points_prev"] if lag == 1 else [])
        df = df.merge(shifted[cols], on=["gsis_id", "season"], how="left")
    df["ppg_change"] = df["ppg"] - df["ppg_prev"]

    nxt = key[["gsis_id", "season", "points", "ppg"]].copy()
    nxt["season"] = nxt["season"] - 1
    nxt = nxt.rename(columns={"points": "next_points", "ppg": "next_ppg"})
    df = df.merge(nxt, on=["gsis_id", "season"], how="left")
    has_next = df["season"] < last
    df["next_points"] = np.where(has_next, df["next_points"].fillna(0.0), np.nan)
    df["next_ppg"] = np.where(has_next, df["next_ppg"], np.nan)

    for pos in POSITIONS:
        df["pos_" + pos] = (df["position"] == pos).astype(float)
    return df.reset_index(drop=True)


def training_frame(table: pd.DataFrame, min_games: int = 4) -> pd.DataFrame:
    """Rows usable for supervised learning: enough games and a known target."""
    return table[(table["games"] >= min_games) & table["next_points"].notna()].copy()


def aging_curves(table: pd.DataFrame, min_games: int = 8, age_range=(21, 36)) -> Dict[str, Dict[int, float]]:
    """Empirical year-over-year retention of points per game, by position and age.

    Retention at age ``a`` is sum(ppg at a+1) / sum(ppg at a) over players who
    played at least ``min_games`` in both seasons, smoothed over neighbouring
    ages. Using sums (not a mean of ratios) keeps low-volume players from
    dominating.
    """
    lo, hi = age_range
    pairs = table[(table["games"] >= min_games) & (table["next_ppg"] > 0) & table["age"].notna()].copy()
    # require the following season to also have min_games: approximate via next_points / next_ppg
    pairs["next_games"] = pairs["next_points"] / pairs["next_ppg"]
    pairs = pairs[pairs["next_games"] >= min_games - 0.5]
    pairs["age_bucket"] = pairs["age"].round().clip(lo, hi).astype(int)

    curves: Dict[str, Dict[int, float]] = {}
    for pos in POSITIONS:
        sub = pairs[pairs["position"] == pos]
        ages = list(range(lo, hi + 1))
        cur = sub.groupby("age_bucket")["ppg"].sum().reindex(ages).fillna(0.0).to_numpy()
        nxt = sub.groupby("age_bucket")["next_ppg"].sum().reindex(ages).fillna(0.0).to_numpy()
        kernel = np.array([1.0, 2.0, 1.0])
        num = np.convolve(nxt, kernel, mode="same")
        den = np.convolve(cur, kernel, mode="same")
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(den > 0, num / den, np.nan)
        series = pd.Series(ratio, index=ages).ffill().bfill()
        curves[pos] = {int(a): round(float(v), 4) for a, v in series.items()}
    return curves


def te_premium_lift(table: pd.DataFrame, seasons: int = 5, top_n: int = 12) -> float:
    """Receptions per PPR point for starting-caliber tight ends.

    A TE-premium bonus of ``b`` per reception raises a TE's score by roughly
    ``b * lift`` as a fraction of his points, while other positions are
    unchanged. The app uses ``1 + b * lift`` as a relative TE value multiplier.
    """
    last = int(table["season"].max())
    recent = table[(table["position"] == "TE") & (table["season"] > last - seasons) & (table["games"] >= 8)]
    top = recent.sort_values(["season", "points"], ascending=[True, False]).groupby("season").head(top_n)
    return float(top["receptions"].sum() / max(1.0, top["points"].sum()))


def current_features(table: pd.DataFrame) -> pd.DataFrame:
    """Rows for the most recent complete season (no target), used to project forward."""
    last = int(table["season"].max())
    return table[(table["season"] == last) & (table["games"] >= 1)].copy()


def feature_matrix(frame: pd.DataFrame, columns: Optional[List[str]] = None) -> np.ndarray:
    return frame[columns or FEATURE_COLUMNS].to_numpy(dtype=float)
