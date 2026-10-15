"""Tests for the offline pipeline. Skipped when the data science extras are not installed."""

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")
pytest.importorskip("scipy")

from datascience import backtest as bt  # noqa: E402
from datascience import collect, features, model  # noqa: E402


def synthetic_stats(seasons=range(2015, 2025), players=120, seed=3):
    """Players who peak at 26 and decline, with noise; the last season is complete."""
    rng = np.random.default_rng(seed)
    rows, ids = [], []
    for pid in range(players):
        pos = ["QB", "RB", "WR", "TE"][pid % 4]
        birth_year = 2015 - rng.integers(21, 27)
        skill = rng.uniform(8, 20)
        ids.append({"gsis_id": f"00-{pid}", "sleeper_id": str(pid), "birthdate": f"{birth_year}-06-01", "draft_year": birth_year + 22, "draft_ovr": rng.integers(1, 200)})
        for season in seasons:
            age = season - birth_year
            ppg = skill * max(0.2, 1 - 0.06 * abs(age - 26)) * rng.uniform(0.85, 1.15)
            games = int(rng.integers(8, 17))
            rows.append(
                {"gsis_id": f"00-{pid}", "name": f"P{pid}", "position": pos, "team": "XXX", "season": season, "games": games,
                 "fantasy_points_ppr": ppg * games, "receptions": ppg * games * 0.3, "targets": ppg * games * 0.4, "carries": 50.0,
                 "attempts": 100.0, "target_share": 0.1, "wopr": 0.2, "air_yards_share": 0.1, "receiving_yards": 100.0,
                 "rushing_yards": 50.0, "passing_yards": 0.0}
            )
    stats = pd.DataFrame(rows)
    stats.loc[stats["season"] == max(seasons), "games"] = 17  # mark the final season complete
    ids_df = pd.DataFrame(ids)
    ids_df["birthdate"] = pd.to_datetime(ids_df["birthdate"])
    return stats, ids_df


def test_season_features_build_target_without_leakage():
    stats, ids = synthetic_stats()
    # add an in-progress season that must be ignored
    partial = stats[stats["season"] == 2024].copy()
    partial["season"], partial["games"] = 2025, 3
    table = features.season_features(pd.concat([stats, partial]), ids)
    assert table["season"].max() == 2024  # incomplete season dropped
    one = table[(table["gsis_id"] == "00-5") & (table["season"] == 2020)].iloc[0]
    nxt = table[(table["gsis_id"] == "00-5") & (table["season"] == 2021)].iloc[0]
    assert one["next_points"] == pytest.approx(nxt["points"])
    assert one["ppg_prev"] == pytest.approx(table[(table["gsis_id"] == "00-5") & (table["season"] == 2019)].iloc[0]["ppg"])
    assert table[table["season"] == 2024]["next_points"].isna().all()  # no future leak into the last season
    assert 20 < one["age"] < 35


def test_aging_curve_recovers_peak_shape():
    stats, ids = synthetic_stats(players=400)
    table = features.season_features(stats, ids)
    curves = features.aging_curves(table)
    for pos in features.POSITIONS:
        assert curves[pos][23] > 1.0 > curves[pos][32]  # improving before the peak, declining after


def test_walk_forward_never_trains_on_test_season_and_beats_persistence():
    stats, ids = synthetic_stats(players=300)
    table = features.season_features(stats, ids)
    curves = features.aging_curves(table)
    folds = model.walk_forward(table, curves, first_test=2020)
    assert set(folds["season"]) == {2020, 2021, 2022, 2023}
    summary = model.summarize_walk_forward(folds)
    assert summary["model"]["mae"] < summary["persistence"]["mae"]
    assert 0 < summary["model"]["band_coverage"] <= 1


def test_build_edges_flags_mispriced_player_and_estimates_bench():
    stats, ids = synthetic_stats(players=200)
    table = features.season_features(stats, ids)
    prod = model.fit_production_model(features.training_frame(table))
    current = features.current_features(table)
    proj = current.copy()
    proj[["pred_points", "pred_low", "pred_high"]] = prod.predict(proj)
    market = proj.iloc[:150][["sleeper_id", "name", "position", "age", "years_exp", "pred_points"]].copy()
    market["value"] = (market["pred_points"] * 12 + 400).round()
    cheap = market.index[0]
    market.loc[cheap, "value"] = 300  # the market badly underprices this player
    result = model.build_edges(market, current, prod, min_value=100)
    record = result["players"][str(market.loc[cheap, "sleeper_id"])]
    assert record["edge"] > 0 and record["z"] > 0
    assert set(result["players"]).isdisjoint(result["bench"])
    assert result["bench"], "unpriced players should receive estimated values"
    assert all(b["value"] <= result["diagnostics"]["bench_cap"] for b in result["bench"].values())


# -- calibration ----------------------------------------------------------------

def make_rows(true_decay, n=150, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        multi = list(rng.uniform(1500, 5000, size=int(rng.integers(2, 4))))
        target = bt.side_total(multi, true_decay) * rng.uniform(0.97, 1.03)
        rows.append(
            {"id": str(_), "date": None,
             "sides": [[{"value": v, "position": "WR"} for v in multi], [{"value": target, "position": "RB"}]]}
        )
    return rows


def test_depth_decay_fit_recovers_planted_value():
    fit = bt.fit_depth_decay(make_rows(0.75), boot=60)
    assert abs(fit["best"] - 0.75) <= 0.02
    assert fit["ci90"][0] <= fit["best"] <= fit["ci90"][1]
    assert fit["loss_best"] < fit["loss_old_092"]


def test_position_adjustment_only_applied_when_significant():
    balanced = bt.fit_position_adjustments(make_rows(0.8), 0.8, boot=60)
    assert balanced["applied"] == {}
    rng = np.random.default_rng(2)
    rows = []
    for i in range(200):  # QBs are systematically overpaid: sides with QB value need less raw value
        qb = float(rng.uniform(3000, 6000))
        wr = float(qb * 0.8 * rng.uniform(0.97, 1.03))
        rows.append({"id": str(i), "date": None, "sides": [[{"value": qb, "position": "QB"}], [{"value": wr, "position": "WR"}]]})
    tilted = bt.fit_position_adjustments(rows, 0.8, boot=60)
    assert tilted["report"]["QB"]["significant"]


def test_calibrate_keeps_defaults_with_too_few_trades():
    snaps = pd.DataFrame(
        {"date": ["2026-01-01"], "num_qbs": [1], "ppr": [1.0], "sleeper_id": ["1"], "value": [5000.0], "position": ["WR"]}
    )
    trades = [{"id": "t", "date": "2026-01-01T00:00:00Z", "side1": [{"sleeper_id": "1"}], "side2": [{"sleeper_id": "1"}], "num_qbs": 1, "ppr": 1.0}]
    result = bt.calibrate(trades, snaps)
    assert result["depth_decay"] == 0.92 and result["even_threshold"] == 90
    assert "too few" in result["status"]


def test_trade_table_drops_unpriced_trades():
    snaps = pd.DataFrame(
        {"date": ["2026-01-01"] * 2, "num_qbs": [1, 1], "ppr": [1.0, 1.0], "sleeper_id": ["1", "2"], "value": [5000.0, 4000.0], "position": ["WR", "RB"]}
    )
    good = {"id": "a", "side1": [{"sleeper_id": "1"}], "side2": [{"sleeper_id": "2"}], "num_qbs": 1, "ppr": 1.0}
    bad = {"id": "b", "side1": [{"sleeper_id": "1"}], "side2": [{"sleeper_id": "999"}], "num_qbs": 1, "ppr": 1.0}
    rows = bt.trade_table([good, bad], bt.value_lookup(snaps))
    assert [r["id"] for r in rows] == ["a"]


def test_forward_test_reports_insufficient_history_then_hit_rate():
    one_day = pd.DataFrame({"date": ["2026-01-01"], "num_qbs": [1], "ppr": [1.0], "sleeper_id": ["1"], "value": [1.0], "position": ["WR"]})
    assert "insufficient" in bt.forward_test([], one_day, 0.8)["status"]

    rng = np.random.default_rng(0)
    snaps, trades = [], []
    for i in range(40):
        a, b = f"a{i}", f"b{i}"
        v_a, v_b = float(rng.uniform(2000, 4000)), float(rng.uniform(2000, 4000))
        # the asset the heuristic says is more valuable also keeps gaining
        later_a, later_b = (v_a * 1.2, v_b * 0.9) if v_a > v_b else (v_a * 0.9, v_b * 1.2)
        for day, va, vb in (("2026-01-01", v_a, v_b), ("2026-02-01", later_a, later_b)):
            snaps += [
                {"date": day, "num_qbs": 1, "ppr": 1.0, "sleeper_id": a, "value": va, "position": "WR"},
                {"date": day, "num_qbs": 1, "ppr": 1.0, "sleeper_id": b, "value": vb, "position": "WR"},
            ]
        trades.append({"id": str(i), "date": "2026-01-01T12:00:00Z", "side1": [{"sleeper_id": a}], "side2": [{"sleeper_id": b}], "num_qbs": 1, "ppr": 1.0})
    result = bt.forward_test(trades, pd.DataFrame(snaps), 0.8)
    assert result["status"] == "ok" and result["winner_hit_rate"] == 1.0


def test_flatten_values_and_trade_slimming():
    payload = [{"player": {"sleeperId": 4984, "name": "Josh Allen", "position": "QB", "maybeAge": 30.3, "maybeDraftInfo": {"year": 2018, "round": 1, "pick": 7}},
                "value": 10000, "redraftValue": 9000, "overallRank": 1, "positionRank": 1, "trend30Day": 12}]
    frame = collect.flatten_values(payload)
    assert frame.loc[0, "sleeper_id"] == "4984" and frame.loc[0, "draft_year"] == 2018
    slim = collect._slim_trade({"id": "x", "date": "d", "side1": [{"sleeperId": 1, "name": "A", "position": "WR"}], "side2": [], "numQbs": 2.0, "ppr": 1.0, "numTeams": 12})
    assert slim["num_qbs"] == 2 and slim["side1"][0]["sleeper_id"] == "1"
