import pytest
from dynasty import Services, create_app
from dynasty.cache import SlidingWindowLimiter, TTLCache
from dynasty.config import load_config
from dynasty.llm import Explainer
from dynasty.sleeper import League


def connect(client):
    return client.post("/connect", data={"league_id": "123456789", "username": "alpha_owner"}, follow_redirects=False)


def test_health_and_landing(client):
    assert client.get("/health").json == {"status": "ok"}
    assert b"Connect your Sleeper league" in client.get("/").data


def test_removed_routes_are_gone(client):
    for path in ("/update-plan", "/auth", "/upgrade", "/create-checkout-session"):
        assert client.get(path).status_code == 404
        assert client.post(path).status_code in (404, 405)


def test_connect_with_username_selects_team(client):
    response = connect(client)
    assert response.status_code == 302 and response.location.endswith("/dashboard")
    page = client.get("/dashboard").data.decode()
    assert "Alpha" in page and "Ja&#39;Marr Chase" in page and "Superflex" in page


def test_connect_without_username_goes_to_team_picker(client):
    response = client.post("/connect", data={"league_id": "123456789"})
    assert response.location.endswith("/team")
    assert client.get("/dashboard").status_code == 302
    client.post("/team", data={"roster_id": "2"})
    assert b"Bravo" in client.get("/dashboard").data


def test_bad_inputs(client):
    for league_id in ("abc", "", "999999999", "1; DROP TABLE"):
        r = client.post("/connect", data={"league_id": league_id}, follow_redirects=True)
        assert r.status_code == 200 and b"alert-error" in r.data
    client.post("/connect", data={"league_id": "123456789"})
    r = client.post("/team", data={"roster_id": "99"}, follow_redirects=True)
    assert b"choose a team" in r.data
    r = client.post("/team", data={"roster_id": "x"}, follow_redirects=True)
    assert b"choose a team" in r.data


def test_suggestions_flow(client):
    connect(client)
    r = client.post("/suggestions", data={"strategy": "contend", "risk_tolerance": "medium", "position_needs": ["WR"]})
    assert r.status_code == 200
    assert b"Trade with Bravo" in r.data
    assert b"% even" in r.data


def test_suggestions_require_league(client):
    assert client.post("/suggestions", data={}).status_code == 302


def test_calculator(client):
    r = client.post("/calculator", data={"team_a": "Ja'Marr Chase", "team_b": "Bijan Robinson", "qbs": "2", "ppr": "1.0"})
    assert r.status_code == 200 and b"Fair trade" in r.data
    r = client.post("/calculator", data={"team_a": "Ja'Marr Chase\n2027 1st", "team_b": "Depth Guy"})
    assert b"Favors Team B" in r.data and b"To even it out" in r.data


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("", "Josh Allen", b"at least one"),
        ("Nobody Real", "Josh Allen", b"Could not find"),
        ("Josh Allen", "Josh Allen", b"both sides"),
        ("Josh Allen\nJosh Allen", "Travis Kelce", b"more than once"),
        ("\n".join(["Josh Allen"] * 13), "Travis Kelce", b"limit each side"),
    ],
)
def test_calculator_errors(client, a, b, expected):
    r = client.post("/calculator", data={"team_a": a, "team_b": b})
    assert r.status_code == 400 and expected in r.data


def test_calculator_escapes_user_text(client):
    r = client.post("/calculator", data={"team_a": "<script>alert(1)</script>", "team_b": "Josh Allen"})
    assert b"<script>alert(1)</script>" not in r.data


def test_csrf_enforced_outside_testing():
    services = Services(None, None, None, SlidingWindowLimiter(100, 60), SlidingWindowLimiter(100, 60))
    app = create_app({"TESTING": False, "SECRET_KEY": "x", "CACHE_DIR": None}, services=services)
    assert app.test_client().post("/calculator", data={}).status_code == 400


def test_reset_clears_session(client):
    connect(client)
    client.post("/reset")
    assert client.get("/dashboard").status_code == 302


def test_request_rate_limit():
    from tests.conftest import FakeSleeper, FakeValues

    services = Services(FakeSleeper(), FakeValues(), None, SlidingWindowLimiter(5, 60), SlidingWindowLimiter(2, 60))
    app = create_app({"TESTING": True, "SECRET_KEY": "x", "WTF_CSRF_ENABLED": False, "CACHE_DIR": None}, services=services)
    c = app.test_client()
    codes = [c.post("/calculator", data={"team_a": "Josh Allen", "team_b": "Travis Kelce"}).status_code for _ in range(3)]
    assert codes[-1] == 429


class FakeOpenAI:
    def __init__(self, content):
        self.content = content
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        content = self.content

        class M:
            pass

        m = M()
        m.message = M()
        m.message.content = content
        r = M()
        r.choices = [m]
        return r


def _llm_app(content, limit=5):
    from tests.conftest import FakeSleeper, FakeValues

    services = Services(
        FakeSleeper(), FakeValues(), Explainer("k", client=FakeOpenAI(content)),
        SlidingWindowLimiter(limit, 3600), SlidingWindowLimiter(1000, 60),
    )
    return create_app({"TESTING": True, "SECRET_KEY": "x", "WTF_CSRF_ENABLED": False, "CACHE_DIR": None}, services=services)


def test_llm_explanations_used_and_malformed_output_ignored():
    c = _llm_app('{"explanations": [{"index": 0, "text": "Great fit."}, {"index": 99, "text": "x"}, "junk"]}').test_client()
    connect(c)
    r = c.post("/suggestions", data={"strategy": "contend"})
    assert b"Great fit." in r.data

    c = _llm_app("not json at all").test_client()
    connect(c)
    r = c.post("/suggestions", data={"strategy": "contend"})
    assert r.status_code == 200 and b"Trade with Bravo" in r.data


def test_llm_rate_limit_falls_back_gracefully():
    c = _llm_app('{"analysis": "Model text."}', limit=1).test_client()
    data = {"team_a": "Josh Allen", "team_b": "Travis Kelce"}
    assert b"Model text." in c.post("/calculator", data=data).data
    second = c.post("/calculator", data=data).data
    assert b"Model text." not in second and b"Team A gives" in second


def test_config_requires_secret_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        load_config()
    monkeypatch.setenv("SECRET_KEY", "abc")
    cfg = load_config()
    assert cfg["SESSION_COOKIE_SECURE"] and not cfg["DEBUG"]


def test_ttl_cache_and_limiter_expire():
    now = [0.0]
    cache = TTLCache(ttl=10, max_size=2, clock=lambda: now[0])
    cache.set("a", 1)
    assert cache.get("a") == 1
    now[0] = 11
    assert cache.get("a") is None
    cache.set("a", 1); cache.set("b", 2); cache.set("c", 3)
    assert sum(cache.get(k) is not None for k in "abc") == 2

    t = [0.0]
    limiter = SlidingWindowLimiter(2, 60, clock=lambda: t[0])
    assert limiter.allow("x") and limiter.allow("x") and not limiter.allow("x")
    t[0] = 61
    assert limiter.allow("x")
    assert not SlidingWindowLimiter(0, 60).allow("x")


def test_league_properties():
    league = League("1", "n", "2026", 10, ["QB", "RB", "FLEX"], {"rec": 0.5}, 4, [])
    assert league.num_qbs == 1 and league.ppr == 0.5
    league.roster_positions = ["QB", "QB"]
    assert league.num_qbs == 2
    league.scoring_settings = {}
    assert league.ppr == 0.0
