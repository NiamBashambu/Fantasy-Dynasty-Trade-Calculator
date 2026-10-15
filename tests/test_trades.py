from dynasty.trades import (
    DEPTH_DECAY,
    Preferences,
    adjusted_total,
    build_team_assets,
    evaluate_trade,
    generate_suggestions,
)


def test_adjusted_total_discounts_depth(book):
    a, b = book.by_name["jamarr chase"], book.by_name["bijan robinson"]
    assert adjusted_total([a]) == a.value
    assert adjusted_total([a, b]) == a.value + b.value * DEPTH_DECAY
    assert adjusted_total([]) == 0


def test_evaluate_even_and_winner_is_receiver(book):
    chase, bijan = book.by_name["jamarr chase"], book.by_name["bijan robinson"]
    even = evaluate_trade([chase], [bijan], book)
    assert even.verdict == "even" and even.fairness >= 90

    # Team A gives much more, so Team B receives more value and wins.
    lopsided = evaluate_trade([chase, bijan], [book.by_name["depth guy"]], book)
    assert lopsided.verdict == "favors_b" and lopsided.winner == "Team B"
    assert lopsided.balancers  # suggestions to close the gap
    flipped = evaluate_trade([book.by_name["depth guy"]], [chase, bijan], book)
    assert flipped.winner == "Team A"


def test_empty_sides_do_not_crash(book):
    result = evaluate_trade([], [], book)
    assert result.fairness == 100 and result.verdict == "even"


def test_build_team_assets_includes_traded_picks(league, book):
    teams = {t.roster_id: t for t in build_team_assets(league, book)}
    alpha_names = {a.name for a in teams[1].assets}
    bravo_names = {a.name for a in teams[2].assets}
    # Alpha owns its own 2027 1st? No: it traded for Bravo's, and kept its own.
    assert "2027 1st" in alpha_names
    assert "Ja'Marr Chase" in alpha_names
    assert "Bijan Robinson" in bravo_names
    # Unvalued players are skipped silently.
    assert all(a.value > 0 for a in teams[1].assets)


def test_generate_respects_tolerance_and_strategy(league, book):
    teams = build_team_assets(league, book)
    mine = next(t for t in teams if t.roster_id == 1)

    contend = generate_suggestions(mine, teams, Preferences(strategy="contend", risk="medium"), limit=5)
    assert contend
    for s in contend:
        assert s.fairness >= 90
        assert sum(a.redraft_value for a in s.receive) > sum(a.redraft_value for a in s.give)
        assert s.partner_roster_id == 2

    rebuild = generate_suggestions(mine, teams, Preferences(strategy="rebuild", risk="high"), limit=5)
    for s in rebuild:
        assert s.fairness >= 85


def test_needs_are_rewarded_and_unknown_inputs_are_sanitized(league, book):
    teams = build_team_assets(league, book)
    mine = next(t for t in teams if t.roster_id == 1)
    prefs = Preferences(strategy="nonsense", risk="???", needs={"WR", "K", "DEF"})
    results = generate_suggestions(mine, teams, prefs, limit=3)
    assert results
    assert all(r.fairness >= 90 for r in results)  # falls back to medium tolerance
    assert any("need" in " ".join(r.reasons) for r in results)


def test_no_asset_reuse_across_partner_side(league, book):
    teams = build_team_assets(league, book)
    mine = next(t for t in teams if t.roster_id == 1)
    results = generate_suggestions(mine, teams, Preferences(risk="high"), limit=6)
    received = [a.key for r in results for a in r.receive]
    assert len(received) == len(set(received))


def test_traded_picks_are_distinct_assets(league, book):
    teams = {t.roster_id: t for t in build_team_assets(league, book)}
    firsts = [a for a in teams[1].assets if a.is_pick and a.name.startswith("2027 1st")]
    assert len(firsts) == 2
    assert len({a.key for a in firsts}) == 2
    assert any("from Bravo" in a.name for a in firsts)
    assert not any(a.name.startswith("2027 1st") for a in teams[2].assets)
