import json

import pytest

from dynasty import insights
from dynasty.needs import analyze, fill_lineup, starter_slots
from dynasty.sleeper import League
from dynasty.trades import (
    Preferences,
    TeamAssets,
    build_team_assets,
    generate_suggestions,
)
from dynasty.values import ValueSettings, adjust_book

from .conftest import make_book, make_league, player


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    """Point the app at a temporary artifact directory and restore afterwards."""

    def write(**files):
        for name, payload in files.items():
            (tmp_path / f"{name}.json").write_text(json.dumps(payload))
        monkeypatch.setattr(insights, "DATA_DIR", str(tmp_path))
        insights.reload()

    yield write
    monkeypatch.undo()
    insights.reload()


def test_defaults_without_artifacts(artifacts):
    artifacts()  # empty directory
    assert insights.depth_decay() == 0.92
    assert insights.even_threshold() == 90
    assert insights.position_multipliers() == {}
    assert insights.edges(2) == {}
    assert insights.te_premium_multiplier(0.5) == 1.0


def test_calibration_is_clamped_and_requires_ok_status(artifacts):
    artifacts(calibration={"status": "ok", "depth_decay": 0.1, "even_threshold": 50, "position_multipliers": {"QB": 5, "TE": 0.95}})
    assert insights.depth_decay() == insights.MIN_DEPTH_DECAY
    assert insights.even_threshold() == 80
    assert insights.position_multipliers() == {"QB": 1.1, "TE": 0.95}
    artifacts(calibration={"status": "too few priced trades", "depth_decay": 0.6})
    assert insights.depth_decay() == 0.92  # an unvalidated fit is ignored


def test_longevity_prefers_young_and_declining_positions(artifacts):
    artifacts(aging_curves={"retention": {"RB": {str(a): (1.0 if a < 26 else 0.7) for a in range(21, 37)}}})
    young, old = insights.longevity("RB", 22), insights.longevity("RB", 30)
    assert young > old
    assert insights.longevity("RB", 30) == pytest.approx(1 + 0.7 + 0.49)
    assert insights.longevity(None, None) > 0  # picks


def test_sparkline_needs_two_points():
    assert insights.sparkline_points([5]) == ""
    points = insights.sparkline_points([1, 3, 2]).split()
    assert len(points) == 3


def test_edge_fraction_is_capped(artifacts):
    artifacts(model_edges={"formats": {"q2": {"players": {"9": {"edge_pct": 400.0, "signal": "buy", "z": 3}}}}})
    assert insights.edge_fraction("9", 2) == 0.5
    assert insights.edge_fraction("9@3", 2) == 0.5  # pick-style suffixes still resolve
    assert insights.edge_fraction("404", 2) == 0.0
    assert insights.edge_for("9", 2)["label"] == "Buy low"
    assert insights.edge_for("9", 1) is None  # other format has no data


def test_adjust_book_applies_multipliers_te_premium_and_bench(artifacts):
    artifacts(
        calibration={"status": "ok", "depth_decay": 0.7, "even_threshold": 85, "position_multipliers": {"RB": 0.9}, "te_premium_lift": 0.4},
        model_edges={"formats": {"q1": {"bench": {"99": {"name": "Deep Bench", "position": "WR", "age": 24, "value": 200}}}}},
    )
    book = make_book()
    adjusted = adjust_book(book, ValueSettings(num_qbs=1, te_premium=0.5).normalized())
    assert adjusted.by_name["bijan robinson"].value == round(8400 * 0.9)
    assert adjusted.by_name["travis kelce"].value == round(2500 * (1 + 0.5 * 0.4))
    assert adjusted.by_name["jamarr chase"].value == 8500  # WR untouched
    assert adjusted.by_name["2027 1st"].value == 3000  # picks untouched
    bench = adjusted.player_by_sleeper_id("99")
    assert bench and bench.estimated and bench.value == 200
    # superflex format has no bench artifact, so nothing is added
    assert adjust_book(book, ValueSettings(num_qbs=2).normalized()).player_by_sleeper_id("99") is None


def test_te_premium_setting_normalizes_and_league_reads_it():
    assert ValueSettings(te_premium=0.4).normalized().te_premium == 0.5
    assert ValueSettings(te_premium=-1).normalized().te_premium == 0.0
    league = make_league()
    league.scoring_settings = {"rec": 1.0, "bonus_rec_te": 0.5}
    assert league.te_premium == 0.5
    assert isinstance(league, League)
    assert ValueSettings(te_premium=0.5).normalized().cache_key() == ValueSettings().normalized().cache_key()


# -- roster needs ---------------------------------------------------------------

def test_starter_slots_and_lineup_fill():
    slots = starter_slots(["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "BN", "BN"])
    assert slots == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPER_FLEX": 1}
    assets = [
        player("1", "QB1", "QB", 5000), player("2", "QB2", "QB", 4000),
        player("3", "RB1", "RB", 3000), player("4", "WR1", "WR", 3500), player("5", "WR2", "WR", 2000),
    ]
    lineup = dict()
    for slot, asset in fill_lineup(assets, slots):
        lineup.setdefault(slot, []).append(asset.name)
    assert lineup["QB"] == ["QB1"]
    assert lineup["SUPER_FLEX"] == ["QB2"]  # best leftover goes to superflex
    assert lineup["RB"] == ["RB1"] and "TE" not in lineup  # empty slots stay empty


def test_needs_flag_thin_positions_and_rank_partners(league, book):
    teams = build_team_assets(league, book)
    needs = analyze(league.roster_positions, teams)
    alpha, bravo = needs.teams[1], needs.teams[2]
    # Bravo has no QB at all, Alpha has two valuable ones, so Bravo needs QB and Alpha has no such need.
    assert bravo.need["QB"] > alpha.need["QB"]
    assert alpha.vorp >= 0 and bravo.vorp >= 0
    ranking = needs.partner_ranking(1)
    assert [team.roster_id for team, _ in ranking] == [2]
    josh = book.by_name["josh allen"]
    my_fit, partner_fit = needs.trade_fit(1, 2, [josh], [book.by_name["bijan robinson"]])
    assert partner_fit > 0  # sending a QB to the team that has none fits them


def test_suggestions_use_needs_and_model_edges(artifacts, league, book):
    artifacts(model_edges={"formats": {"q2": {"players": {"2": {"edge_pct": 40.0, "signal": "buy", "z": 2.0}}}}})
    teams = build_team_assets(league, book)
    mine = next(t for t in teams if t.roster_id == 1)
    needs = analyze(league.roster_positions, teams)
    results = generate_suggestions(mine, teams, Preferences(risk="high"), limit=6, num_qbs=2, needs=needs)
    assert results
    with_bijan = [r for r in results if any(a.key == "2" for a in r.receive)]
    assert with_bijan and all(r.model_edge > 0 for r in with_bijan)
    assert any("Projection model" in " ".join(r.reasons) for r in with_bijan)


def test_team_assets_value_helper(book):
    team = TeamAssets(1, "x", [book.by_name["jamarr chase"]])
    assert team.value() == 8500
