"""Deterministic trade math and trade suggestion search.

All numbers come from market values (see values.py). Nothing here calls an
LLM; language models may only be used afterwards to explain these results.
"""

from dataclasses import dataclass, field, replace
from itertools import combinations
from typing import Dict, Iterable, List, Optional, Sequence, Set

from . import insights
from .needs import LeagueNeeds
from .sleeper import League
from .values import Asset, ValueBook

# The Nth best asset on a side counts at DEPTH_DECAY**N of its value. Raw values
# are additive, but two mid assets are not worth one star because only so many
# players start and roster spots are limited. The constant is calibrated on real
# accepted trades by `python -m datascience.backtest` (see calibration.json);
# without that artifact it falls back to the earlier hand-picked 0.92.
DEPTH_DECAY = insights.depth_decay()

# Assets below this value are roster filler and are ignored when searching.
MIN_TRADE_ASSET_VALUE = 800
SEARCH_POOL_SIZE = 12
EVEN_THRESHOLD = insights.even_threshold()  # median fairness of real trades (default 90)

RISK_TOLERANCE = {"low": 0.06, "medium": 0.10, "high": 0.15}
POSITIONS = ("QB", "RB", "WR", "TE")
STRATEGIES = ("contend", "rebuild", "balanced")


def adjusted_total(assets: Iterable[Asset]) -> float:
    values = sorted((a.value for a in assets), reverse=True)
    return sum(v * (DEPTH_DECAY ** i) for i, v in enumerate(values))


@dataclass
class Evaluation:
    give_a: List[Asset]
    give_b: List[Asset]
    total_a: float
    total_b: float
    fairness: int
    verdict: str  # "even", "favors_a", "favors_b"
    winner: str  # "Even", "Team A", "Team B"
    gap: float
    balancers: List[Asset] = field(default_factory=list)


def evaluate_trade(
    give_a: Sequence[Asset], give_b: Sequence[Asset], book: Optional[ValueBook] = None
) -> Evaluation:
    """Compare what Team A gives with what Team B gives.

    The side that *receives* more value wins: if Team A gives more than Team B,
    the verdict is "favors_b".
    """
    total_a = adjusted_total(give_a)
    total_b = adjusted_total(give_b)
    high = max(total_a, total_b)
    fairness = 100 if high == 0 else round(100 * min(total_a, total_b) / high)
    if fairness >= EVEN_THRESHOLD:
        verdict, winner = "even", "Even"
    elif total_a > total_b:
        verdict, winner = "favors_b", "Team B"  # B receives the larger package
    else:
        verdict, winner = "favors_a", "Team A"
    gap = abs(total_a - total_b)
    evaluation = Evaluation(
        give_a=list(give_a),
        give_b=list(give_b),
        total_a=total_a,
        total_b=total_b,
        fairness=fairness,
        verdict=verdict,
        winner=winner,
        gap=gap,
    )
    if book is not None and verdict != "even":
        evaluation.balancers = suggest_balancers(evaluation, book)
    return evaluation


def suggest_balancers(evaluation: Evaluation, book: ValueBook, limit: int = 3) -> List[Asset]:
    """Assets the *losing-value* side could add to close the gap."""
    short_side = evaluation.give_a if evaluation.total_a < evaluation.total_b else evaluation.give_b
    # The added asset would be the smallest on that side, so it is discounted.
    needed = evaluation.gap / (DEPTH_DECAY ** len(short_side)) if short_side else evaluation.gap
    in_trade = {a.key for a in evaluation.give_a} | {a.key for a in evaluation.give_b}
    candidates = [
        a for a in book.assets
        if a.key not in in_trade and a.value > 0 and not a.estimated
        and not (a.is_pick and ("(" in a.name))  # prefer generic picks over tiers
    ]
    candidates.sort(key=lambda a: abs(a.value - needed))
    return candidates[:limit]


# -- teams ---------------------------------------------------------------

@dataclass
class TeamAssets:
    roster_id: int
    name: str
    assets: List[Asset]

    def value(self) -> float:
        return adjusted_total(self.assets)


def build_team_assets(league: League, book: ValueBook) -> List[TeamAssets]:
    """Map each roster to valued players plus owned draft picks."""
    ownership: Dict[int, List] = {}
    if book.pick_years and book.pick_rounds:
        rounds = [r for r in book.pick_rounds if r <= max(1, league.draft_rounds)]
        ownership = league.pick_ownership(book.pick_years, rounds)

    names = {t.roster_id: t.name for t in league.teams}
    result: List[TeamAssets] = []
    for team in league.teams:
        assets: List[Asset] = []
        for player_id in team.player_ids:
            asset = book.player_by_sleeper_id(player_id)
            if asset:
                assets.append(asset)
        for season, rnd, original in ownership.get(team.roster_id, []):
            pick = book.pick(season, rnd)
            if pick:
                # Each pick is a distinct asset even though same-round picks share a value.
                label = pick.name if original == team.roster_id else f"{pick.name} (from {names.get(original, 'another team')})"
                assets.append(replace(pick, key=f"{pick.key}@{original}", name=label))
        assets.sort(key=lambda a: a.value, reverse=True)
        result.append(TeamAssets(team.roster_id, team.name, assets))
    return result


# -- suggestions ---------------------------------------------------------

@dataclass
class Preferences:
    strategy: str = "balanced"
    risk: str = "medium"
    needs: Set[str] = field(default_factory=set)

    def cleaned(self) -> "Preferences":
        return Preferences(
            strategy=self.strategy if self.strategy in STRATEGIES else "balanced",
            risk=self.risk if self.risk in RISK_TOLERANCE else "medium",
            needs={n for n in self.needs if n in POSITIONS},
        )


@dataclass
class Suggestion:
    partner_roster_id: int
    partner_name: str
    give: List[Asset]
    receive: List[Asset]
    give_total: float
    receive_total: float
    fairness: int
    score: float
    reasons: List[str]
    explanation: Optional[str] = None
    model_edge: float = 0.0  # fraction of value the model thinks you gain (positive = good for you)
    my_fit: float = 0.0
    partner_fit: float = 0.0


def _weighted_longevity(assets: Sequence[Asset]) -> float:
    """Value-weighted expected production over the next three seasons (empirical aging curves)."""
    total = sum(a.value for a in assets)
    if total <= 0:
        return insights.longevity(None, None)
    return sum(insights.longevity(None if a.is_pick else a.position, a.age) * a.value for a in assets) / total


def _model_edge(give: Sequence[Asset], receive: Sequence[Asset], num_qbs: int) -> float:
    """Net model-implied value change for you, as a fraction of what you send.

    Each asset contributes market value times its (capped) edge fraction from the
    projection model: positive edge means the market prices it below similar
    profiles. Assets without a model estimate (picks, rookies) contribute zero.
    """
    gain = sum(a.value * insights.edge_fraction(a.key, num_qbs) for a in receive if not a.is_pick)
    loss = sum(a.value * insights.edge_fraction(a.key, num_qbs) for a in give if not a.is_pick)
    return (gain - loss) / max(1.0, sum(a.value for a in give))


def _packages(pool: Sequence[Asset], sizes=(1, 2)) -> List[tuple]:
    packages: List[tuple] = []
    for size in sizes:
        packages.extend(combinations(pool, size))
    return packages


def _score(
    give: Sequence[Asset],
    receive: Sequence[Asset],
    fairness: int,
    prefs: Preferences,
    model_edge: float = 0.0,
    my_fit: float = 0.0,
    partner_fit: float = 0.0,
    partner_name: str = "The other team",
):
    """Return (score, reasons) or None if the trade does not fit the strategy."""
    reasons: List[str] = []
    give_total = max(1.0, sum(a.value for a in give))
    recv_total = max(1.0, sum(a.value for a in receive))
    score = fairness / 100 * 0.5

    now_delta = (sum(a.redraft_value for a in receive) - sum(a.redraft_value for a in give)) / give_total
    long_give, long_recv = _weighted_longevity(give), _weighted_longevity(receive)
    age_delta = long_recv - long_give  # positive means you get assets that last longer
    picks_in = sum(a.value for a in receive if a.is_pick) / recv_total

    if prefs.strategy == "contend":
        if now_delta <= 0:
            return None
        score += min(now_delta, 1.5)
        reasons.append("Adds current-season production: higher win-now value coming back than going out.")
    elif prefs.strategy == "rebuild":
        if age_delta <= 0 and picks_in <= 0:
            return None
        score += max(0.0, min(age_delta, 1.0)) + 0.3 * picks_in
        if age_delta > 0:
            reasons.append(
                f"Gets longer-lasting: expected 3-year production {long_recv:.2f}x vs {long_give:.2f}x going out "
                "(from empirical aging curves)."
            )
        if picks_in > 0:
            reasons.append("Brings back draft capital, which suits a rebuild.")
    else:
        score += 0.25 * max(-1.0, min(now_delta, 1.0)) + 0.25 * max(-1.0, min(age_delta, 1.0))

    if prefs.needs:
        gained = {a.position for a in receive if not a.is_pick} & prefs.needs
        lost = {a.position for a in give if not a.is_pick} & prefs.needs
        if gained:
            score += 0.5 * len(gained)
            reasons.append("Fills a stated need: " + ", ".join(sorted(gained)) + ".")
        if lost - gained:
            score -= 0.25 * len(lost - gained)
    if model_edge:
        score += 0.4 * max(-1.0, min(model_edge * 2, 1.0))
        if model_edge >= 0.05:
            reasons.append(
                f"Projection model: the assets you get are priced about {model_edge:.0%} below similar production/age profiles."
            )
        elif model_edge <= -0.05:
            reasons.append(
                f"Projection model: the assets you send look underpriced by about {-model_edge:.0%}; consider holding."
            )
    if my_fit or partner_fit:
        score += 0.4 * max(-1.0, min(my_fit, 1.0)) + 0.4 * max(-1.0, min(partner_fit, 1.0))
        if my_fit >= 0.3:
            reasons.append("Roster fit: fills a weak spot in your starting lineup.")
        if partner_fit >= 0.3:
            reasons.append(f"Partner fit: {partner_name} is thin where you are sending, so they have a reason to accept.")
    return score, reasons


def generate_suggestions(
    mine: TeamAssets,
    others: Sequence[TeamAssets],
    prefs: Preferences,
    limit: int = 5,
    num_qbs: int = 1,
    needs: Optional[LeagueNeeds] = None,
) -> List[Suggestion]:
    prefs = prefs.cleaned()
    tolerance = RISK_TOLERANCE[prefs.risk]
    min_fairness = round(100 * (1 - tolerance))

    my_pool = [a for a in mine.assets if a.value >= MIN_TRADE_ASSET_VALUE][:SEARCH_POOL_SIZE]
    my_packages = _packages(my_pool)

    candidates: List[Suggestion] = []
    for partner in others:
        if partner.roster_id == mine.roster_id:
            continue
        their_pool = [a for a in partner.assets if a.value >= MIN_TRADE_ASSET_VALUE][:SEARCH_POOL_SIZE]
        their_packages = _packages(their_pool)
        for give in my_packages:
            give_total = adjusted_total(give)
            for receive in their_packages:
                if len(give) == 2 and len(receive) == 2:
                    continue
                recv_total = adjusted_total(receive)
                high = max(give_total, recv_total)
                if high == 0:
                    continue
                fairness = round(100 * min(give_total, recv_total) / high)
                if fairness < min_fairness:
                    continue
                model_edge = _model_edge(give, receive, num_qbs)
                my_fit, partner_fit = (
                    needs.trade_fit(mine.roster_id, partner.roster_id, give, receive) if needs else (0.0, 0.0)
                )
                scored = _score(give, receive, fairness, prefs, model_edge, my_fit, partner_fit, partner.name)
                if scored is None:
                    continue
                score, reasons = scored
                reasons = [
                    f"Balanced on market value: you send {give_total:,.0f} and receive {recv_total:,.0f} ({fairness}% even)."
                ] + reasons
                if len(give) > len(receive):
                    reasons.append("Consolidates depth into a single, higher-impact asset.")
                candidates.append(
                    Suggestion(
                        partner_roster_id=partner.roster_id,
                        partner_name=partner.name,
                        give=list(give),
                        receive=list(receive),
                        give_total=give_total,
                        receive_total=recv_total,
                        fairness=fairness,
                        score=score,
                        reasons=reasons,
                        model_edge=model_edge,
                        my_fit=my_fit,
                        partner_fit=partner_fit,
                    )
                )

    candidates.sort(key=lambda s: s.score, reverse=True)
    chosen: List[Suggestion] = []
    per_partner: Dict[int, int] = {}
    my_usage: Dict[str, int] = {}
    their_used: Set[str] = set()
    for cand in candidates:
        if len(chosen) >= limit:
            break
        if per_partner.get(cand.partner_roster_id, 0) >= 2:
            continue
        if any(my_usage.get(a.key, 0) >= 2 for a in cand.give):
            continue
        if any(a.key in their_used for a in cand.receive):
            continue
        chosen.append(cand)
        per_partner[cand.partner_roster_id] = per_partner.get(cand.partner_roster_id, 0) + 1
        for a in cand.give:
            my_usage[a.key] = my_usage.get(a.key, 0) + 1
        their_used.update(a.key for a in cand.receive)
    return chosen


def describe_assets(assets: Iterable[Asset]) -> List[str]:
    return [a.name if a.is_pick else f"{a.name} ({a.position})" for a in assets]
