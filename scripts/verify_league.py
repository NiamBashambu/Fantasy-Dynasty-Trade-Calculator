"""Sanity-check the app against a real Sleeper league from the command line.

    python scripts/verify_league.py LEAGUE_ID [SLEEPER_USERNAME]

Prints every roster's value, lineup value over replacement, and (for your team)
each player's market value next to the projection-model signal, so you can
check the numbers against your own expectations.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dynasty import insights  # noqa: E402
from dynasty.needs import analyze  # noqa: E402
from dynasty.sleeper import SleeperClient, SleeperError  # noqa: E402
from dynasty.trades import DEPTH_DECAY, EVEN_THRESHOLD, build_team_assets  # noqa: E402
from dynasty.values import ValuesClient, ValueSettings  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    league_id = sys.argv[1]
    username = sys.argv[2] if len(sys.argv) > 2 else None
    try:
        league = SleeperClient().load_league(league_id)
        owner_id = SleeperClient().get_user_id(username) if username else None
    except SleeperError as exc:
        print(f"Sleeper error: {exc}")
        return 1

    settings = ValueSettings(
        num_qbs=league.num_qbs, ppr=league.ppr, num_teams=league.total_rosters, te_premium=league.te_premium
    ).normalized()
    book = ValuesClient(cache_dir=os.path.join(os.getcwd(), ".cache")).get_book(settings)
    teams = build_team_assets(league, book)
    needs = analyze(league.roster_positions, teams)

    print(f"{league.name}: {league.total_rosters} teams, qbs={settings.num_qbs}, ppr={settings.ppr}, te_premium={settings.te_premium}")
    print(f"depth decay {DEPTH_DECAY}, even threshold {EVEN_THRESHOLD}, replacement values {needs.replacement}\n")
    print(f"{'Team':32} {'Roster value':>12} {'VORP':>8}")
    for team in sorted(teams, key=lambda t: t.value(), reverse=True):
        profile = needs.teams[team.roster_id]
        print(f"{team.name[:32]:32} {team.value():>12,.0f} {profile.vorp:>8,.0f}")

    mine = next((t for t in league.teams if t.owner_id == owner_id), None) if owner_id else None
    if mine:
        print(f"\n{mine.name}: asset, market value, model signal")
        for asset in next(t for t in teams if t.roster_id == mine.roster_id).assets:
            edge = insights.edge_for(asset.key, settings.num_qbs)
            tag = f"{edge['label']} (z={edge['z']:+.1f}, model {edge['fundamental_value']:,})" if edge else ""
            print(f"  {asset.name[:30]:30} {asset.position:5} {asset.value:>7,} {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
