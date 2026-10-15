import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dynasty import Services, create_app  # noqa: E402
from dynasty.cache import SlidingWindowLimiter  # noqa: E402
from dynasty.sleeper import League, Team  # noqa: E402
from dynasty.values import Asset, ValueBook  # noqa: E402


def player(key, name, pos, value, age=25.0, redraft=None, rank=1, team="XXX"):
    return Asset(key, name, pos, team, age, value, value if redraft is None else redraft, rank, "player")


def pick(year, rnd, value):
    names = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}
    return Asset(f"FP_{year}_{rnd}", f"{year} {names[rnd]}", "PICK", None, None, value, 0, 0, "pick")


def make_book():
    return ValueBook(
        [
            player("1", "Ja'Marr Chase", "WR", 8500, 26, rank=1),
            player("2", "Bijan Robinson", "RB", 8400, 24, rank=2),
            player("3", "Josh Allen", "QB", 7000, 30, redraft=9000, rank=3),
            player("4", "Amon-Ra St. Brown", "WR", 6000, 27, rank=4),
            player("5", "Travis Kelce", "TE", 2500, 36, redraft=4000, rank=5),
            player("6", "Rookie Back", "RB", 2400, 21, redraft=800, rank=6),
            player("7", "Saquon Barkley", "RB", 5000, 29, redraft=6500, rank=7),
            player("8", "Puka Nacua", "WR", 6100, 25, rank=8),
            player("9", "Brian Thomas Jr.", "WR", 4800, 23, rank=9),
            player("10", "Depth Guy", "WR", 900, 28, rank=10),
            pick(2027, 1, 3000),
            pick(2027, 2, 1500),
            pick(2028, 1, 2800),
        ]
    )


def make_league():
    return League(
        league_id="123456789",
        name="Test League",
        season="2026",
        total_rosters=2,
        roster_positions=["QB", "RB", "WR", "SUPER_FLEX"],
        scoring_settings={"rec": 1.0},
        draft_rounds=2,
        teams=[
            Team(1, "u1", "Alpha", ["1", "3", "5", "6", "10"]),
            Team(2, "u2", "Bravo", ["2", "4", "7", "8", "9"]),
        ],
        traded_picks=[{"season": "2027", "round": 1, "roster_id": 2, "owner_id": 1}],
    )


class FakeSleeper:
    def __init__(self):
        self.league = make_league()

    def load_league(self, league_id):
        from dynasty.sleeper import NotFound

        if league_id != self.league.league_id:
            raise NotFound("No Sleeper league was found with that ID.")
        return self.league

    def get_user_id(self, username):
        from dynasty.sleeper import NotFound

        if username == "alpha_owner":
            return "u1"
        raise NotFound("No such user")


class FakeValues:
    def __init__(self):
        self.book = make_book()
        self.calls = []

    def get_book(self, settings=None):
        self.calls.append(settings)
        return self.book


@pytest.fixture
def book():
    return make_book()


@pytest.fixture
def league():
    return make_league()


@pytest.fixture
def app():
    services = Services(
        sleeper=FakeSleeper(),
        values=FakeValues(),
        explainer=None,
        llm_limiter=SlidingWindowLimiter(5, 3600),
        request_limiter=SlidingWindowLimiter(1000, 60),
    )
    application = create_app(
        {"TESTING": True, "SECRET_KEY": "test", "WTF_CSRF_ENABLED": False, "CACHE_DIR": None},
        services=services,
    )
    return application


@pytest.fixture
def client(app):
    return app.test_client()
