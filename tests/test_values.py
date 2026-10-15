import json

import pytest
import requests

from dynasty.values import (
    ValueSettings,
    ValuesClient,
    ValuesUnavailable,
    normalize_name,
    parse_pick,
)


def test_normalize_name_handles_punctuation_suffixes_accents():
    assert normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert normalize_name("Amon-Ra St. Brown") == "amonra st brown"
    assert normalize_name("Brian Thomas Jr.") == "brian thomas"
    assert normalize_name("Dak Prescótt") == "dak prescott"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2027 1st", (2027, 1, None)),
        ("2027 1st round pick", (2027, 1, None)),
        ("2028 round 2", (2028, 2, None)),
        ("2027 1st (Early)", (2027, 1, "early")),
        ("1st 2027", None),
        ("Josh Allen", None),
    ],
)
def test_parse_pick(text, expected):
    assert parse_pick(text) == expected


def test_resolve_exact_pick_and_typo(book):
    asset, _ = book.resolve("jamarr chase")
    assert asset.name == "Ja'Marr Chase"
    asset, _ = book.resolve("Ja'Marr Chasse")  # one-letter typo
    assert asset.name == "Ja'Marr Chase"
    asset, _ = book.resolve("2027 1st round pick")
    assert asset.name == "2027 1st"


def test_resolve_unknown_gives_suggestions_not_a_guess(book):
    asset, suggestions = book.resolve("Chase")
    assert asset is None
    assert "Ja'Marr Chase" in suggestions
    asset, suggestions = book.resolve("2035 1st")
    assert asset is None and suggestions


def test_settings_normalization():
    s = ValueSettings(num_qbs=3, ppr=0.7, num_teams=11).normalized()
    assert (s.num_qbs, s.ppr, s.num_teams) == (2, 0.5, 12)
    assert ValueSettings(num_teams=40).normalized().num_teams == 16
    assert ValueSettings(num_teams=2).normalized().num_teams == 8


def _payload():
    return [
        {"player": {"name": "Josh Allen", "sleeperId": "4984", "position": "QB", "maybeAge": 30.3, "maybeTeam": "BUF"},
         "value": 10802, "redraftValue": 10903, "overallRank": 1},
        {"player": {"name": "2027 1st", "sleeperId": "FP_2027_0", "position": "PICK"},
         "value": 5000, "redraftValue": 0, "overallRank": 20},
        {"player": {"name": "No Id", "sleeperId": None, "position": "WR"}, "value": 1},
    ]


class FakeHttp:
    def __init__(self, payload=None, fail=False):
        self.payload, self.fail, self.calls = payload, fail, 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.fail:
            raise requests.ConnectionError("down")
        payload = self.payload

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        return R()


def test_client_caches_in_memory_and_skips_bad_rows():
    http = FakeHttp(_payload())
    client = ValuesClient(http=http, cache_dir=None)
    book = client.get_book()
    client.get_book()
    assert http.calls == 1
    assert len(book.assets) == 2
    assert book.player_by_sleeper_id("4984").value == 10802
    assert book.player_by_sleeper_id("FP_2027_0") is None  # picks are not players


def test_client_falls_back_to_disk_then_raises(tmp_path):
    ValuesClient(http=FakeHttp(_payload()), cache_dir=str(tmp_path)).get_book()
    assert list(tmp_path.glob("fantasycalc_*.json"))

    down = ValuesClient(http=FakeHttp(fail=True), cache_dir=str(tmp_path))
    assert down.get_book().player_by_sleeper_id("4984") is not None

    empty = ValuesClient(http=FakeHttp(fail=True), cache_dir=str(tmp_path / "nothing"))
    with pytest.raises(ValuesUnavailable):
        empty.get_book()


def test_client_keeps_stale_copy_when_refresh_fails():
    http = FakeHttp(_payload())
    client = ValuesClient(http=http, ttl=0, cache_dir=None)
    client.get_book()
    http.fail = True
    assert client.get_book().player_by_sleeper_id("4984") is not None


def test_corrupt_disk_cache_is_ignored(tmp_path):
    key = ValueSettings().normalized().cache_key()
    (tmp_path / f"fantasycalc_{key}.json").write_text("{not json")
    with pytest.raises(ValuesUnavailable):
        ValuesClient(http=FakeHttp(fail=True), cache_dir=str(tmp_path)).get_book()
    json.dumps({})  # keep json import used
