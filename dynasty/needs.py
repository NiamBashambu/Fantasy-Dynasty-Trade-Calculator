"""Roster-need model: starting lineups, replacement level, and value over replacement.

For each team we fill the league's starting slots greedily with the most
valuable eligible players. From every team's lineup we derive, per position:

* ``starter_value``: the median value of a league starter (a "good starter" bar),
* ``replacement``: the value of the best non-starter at that position,
  league-wide (what a team could pick up for free).

A team's *need* at a position is how far its starters sit below the bar; its
*surplus* is the value of bench players who would start for many other teams.
VORP (value over replacement) sums how much each starter adds above that
free-agent baseline, a standard way to compare lineups across positions.
"""

from collections import Counter
from dataclasses import dataclass, field
from statistics import median
from typing import Dict, List, Sequence, Tuple

from .values import Asset

POSITIONS = ("QB", "RB", "WR", "TE")

# Slot name -> positions that may fill it. Ordered narrowest first when filling.
SLOT_ELIGIBLE: Dict[str, Tuple[str, ...]] = {
    "QB": ("QB",),
    "RB": ("RB",),
    "WR": ("WR",),
    "TE": ("TE",),
    "WRRB_FLEX": ("RB", "WR"),
    "REC_FLEX": ("WR", "TE"),
    "FLEX": ("RB", "WR", "TE"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
}
_FILL_ORDER = ["QB", "RB", "WR", "TE", "WRRB_FLEX", "REC_FLEX", "FLEX", "SUPER_FLEX"]
DEFAULT_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}


def starter_slots(roster_positions: Sequence[str]) -> Dict[str, int]:
    counts = Counter(p for p in roster_positions if p in SLOT_ELIGIBLE)
    return dict(counts) if counts else dict(DEFAULT_SLOTS)


def fill_lineup(assets: Sequence[Asset], slots: Dict[str, int]) -> List[Tuple[str, Asset]]:
    """Greedy lineup: narrow slots first, each taking the best unused eligible player."""
    players = sorted((a for a in assets if not a.is_pick and a.position in POSITIONS), key=lambda a: a.value, reverse=True)
    used = set()
    lineup: List[Tuple[str, Asset]] = []
    for slot in _FILL_ORDER:
        for _ in range(slots.get(slot, 0)):
            pick = next((a for a in players if a.key not in used and a.position in SLOT_ELIGIBLE[slot]), None)
            if pick is not None:
                used.add(pick.key)
                lineup.append((slot, pick))
    return lineup


@dataclass
class TeamProfile:
    roster_id: int
    name: str
    need: Dict[str, float] = field(default_factory=dict)  # 0 (strong) .. ~slots (empty)
    surplus: Dict[str, float] = field(default_factory=dict)  # tradeable bench value, in "starter" units
    vorp: float = 0.0
    lineup_value: float = 0.0


@dataclass
class LeagueNeeds:
    slots: Dict[str, int]
    starter_value: Dict[str, float]
    replacement: Dict[str, float]
    teams: Dict[int, TeamProfile]

    def quality(self, asset: Asset) -> float:
        """How much of a starter this asset is, 0 to 1."""
        bar = self.starter_value.get(asset.position, 0.0)
        return 0.0 if bar <= 0 or asset.is_pick else min(1.0, asset.value / bar)

    def trade_fit(self, me: int, partner: int, give: Sequence[Asset], receive: Sequence[Asset]) -> Tuple[float, float]:
        """Return (my_fit, partner_fit), each roughly -1..1; positive means the trade fits that team."""
        mine, theirs = self.teams.get(me), self.teams.get(partner)
        if not mine or not theirs:
            return 0.0, 0.0
        my_fit = sum(mine.need.get(a.position, 0) * self.quality(a) for a in receive) - sum(
            mine.need.get(a.position, 0) * self.quality(a) for a in give
        )
        partner_fit = sum(theirs.need.get(a.position, 0) * self.quality(a) for a in give) - sum(
            theirs.need.get(a.position, 0) * self.quality(a) for a in receive
        )
        return my_fit, partner_fit

    def partner_ranking(self, me: int) -> List[Tuple[TeamProfile, float]]:
        """Teams ranked by how well their surplus matches my needs and vice versa."""
        mine = self.teams.get(me)
        if not mine:
            return []
        ranked = []
        for roster_id, team in self.teams.items():
            if roster_id == me:
                continue
            score = sum(mine.need.get(p, 0) * team.surplus.get(p, 0) + team.need.get(p, 0) * mine.surplus.get(p, 0) for p in POSITIONS)
            ranked.append((team, score))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked


def analyze(roster_positions: Sequence[str], teams: Sequence) -> LeagueNeeds:
    """``teams`` is a sequence of objects with roster_id, name and assets (see trades.TeamAssets)."""
    slots = starter_slots(roster_positions)
    lineups = {t.roster_id: fill_lineup(t.assets, slots) for t in teams}

    starters_by_pos: Dict[str, List[float]] = {p: [] for p in POSITIONS}
    for lineup in lineups.values():
        for _, asset in lineup:
            starters_by_pos[asset.position].append(asset.value)
    starter_value = {p: float(median(v)) if v else 0.0 for p, v in starters_by_pos.items()}

    starter_keys = {a.key for lineup in lineups.values() for _, a in lineup}
    replacement: Dict[str, float] = {}
    for p in POSITIONS:
        bench = [a.value for t in teams for a in t.assets if not a.is_pick and a.position == p and a.key not in starter_keys]
        replacement[p] = float(max(bench)) if bench else 0.0

    profiles: Dict[int, TeamProfile] = {}
    for t in teams:
        lineup = lineups[t.roster_id]
        profile = TeamProfile(t.roster_id, t.name)
        for _, asset in lineup:
            profile.lineup_value += asset.value
            profile.vorp += max(0.0, asset.value - replacement.get(asset.position, 0.0))
        for p in POSITIONS:
            bar = starter_value[p]
            starters_here = sorted((a.value for _, a in lineup if a.position == p), reverse=True)
            if bar <= 0:
                profile.need[p] = 0.0
            else:
                profile.need[p] = sum(max(0.0, (bar - v) / bar) for v in starters_here)
        # An empty slot is unfilled demand, shared across the positions eligible for it.
        filled_by_slot = Counter(slot for slot, _ in lineup)
        for slot, count in slots.items():
            empty = count - filled_by_slot.get(slot, 0)
            if empty > 0:
                eligible = [p for p in SLOT_ELIGIBLE[slot] if p in POSITIONS]
                for p in eligible:
                    profile.need[p] = profile.need.get(p, 0.0) + empty / len(eligible)
        starter_ids = {a.key for _, a in lineup}
        for p in POSITIONS:
            bar = starter_value[p]
            if bar <= 0:
                profile.surplus[p] = 0.0
                continue
            profile.surplus[p] = sum(
                (a.value - replacement[p]) / bar
                for a in t.assets
                if not a.is_pick and a.position == p and a.key not in starter_ids and a.value > replacement[p]
            )
        profiles[t.roster_id] = profile
    return LeagueNeeds(slots=slots, starter_value=starter_value, replacement=replacement, teams=profiles)
