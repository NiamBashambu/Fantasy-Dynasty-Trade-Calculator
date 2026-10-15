import logging
from typing import List, Optional, Tuple

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from itsdangerous import BadSignature, URLSafeSerializer

from . import insights
from .llm import template_evaluation_text
from .needs import analyze as analyze_needs
from .sleeper import League, NotFound, SleeperError, Team
from .trades import (
    DEPTH_DECAY,
    EVEN_THRESHOLD,
    POSITIONS,
    Preferences,
    build_team_assets,
    evaluate_trade,
    generate_suggestions,
)
from .values import Asset, ValueBook, ValueSettings, ValuesUnavailable, normalize_name, pick_name

logger = logging.getLogger(__name__)
bp = Blueprint("main", __name__)

MAX_LINES_PER_SIDE = 12
MAX_LINE_LENGTH = 60


def services():
    return current_app.extensions["dynasty"]


def client_key() -> str:
    return request.remote_addr or "unknown"


# -- session helpers ---------------------------------------------------------

def load_session_league() -> Optional[League]:
    league_id = session.get("league_id")
    if not league_id:
        return None
    try:
        return services().sleeper.load_league(league_id)
    except NotFound:
        session.clear()
        flash("That league could not be found anymore. Please reconnect.", "error")
    except SleeperError as exc:
        flash(str(exc), "error")
    return None


def selected_team(league: League) -> Optional[Team]:
    roster_id = session.get("roster_id")
    return league.team(roster_id) if roster_id is not None else None


def settings_for(league: Optional[League]) -> ValueSettings:
    if league is None:
        return ValueSettings()
    return ValueSettings(
        num_qbs=league.num_qbs, ppr=league.ppr, num_teams=league.total_rosters, te_premium=league.te_premium
    )


def get_book(settings: ValueSettings) -> Optional[ValueBook]:
    try:
        return services().values.get_book(settings)
    except ValuesUnavailable as exc:
        flash(str(exc), "error")
        return None


def remember(league: League, team: Optional[Team]) -> None:
    session.permanent = True
    session["league_id"] = league.league_id
    session["league_name"] = league.name
    if team:
        session["roster_id"] = team.roster_id
        session["team_name"] = team.name
    else:
        session.pop("roster_id", None)
        session.pop("team_name", None)


@bp.app_context_processor
def inject_context():
    return {
        "current_league_name": session.get("league_name"),
        "current_team_name": session.get("team_name"),
        "positions": POSITIONS,
        "ai_enabled": services().explainer is not None,
        "depth_decay": DEPTH_DECAY,
        "even_threshold": EVEN_THRESHOLD,
        "edge_for": insights.edge_for,
        "sparkline": insights.sparkline_points,
    }


# -- pages -------------------------------------------------------------------

@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/privacy")
def privacy():
    return render_template("privacy.html", ai_enabled=services().explainer is not None)


@bp.route("/health")
def health():
    return jsonify(status="ok")


@bp.route("/connect", methods=["POST"])
def connect():
    league_id = request.form.get("league_id", "").strip()
    username = request.form.get("username", "").strip()
    try:
        league = services().sleeper.load_league(league_id)
    except SleeperError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.index"))

    team: Optional[Team] = None
    if username:
        try:
            user_id = services().sleeper.get_user_id(username)
            team = next((t for t in league.teams if t.owner_id == user_id), None)
            if team is None:
                flash(f'"{username}" does not own a team in this league. Pick your team below.', "info")
        except SleeperError as exc:
            flash(f"{exc} Pick your team below instead.", "info")

    session.clear()
    remember(league, team)
    if team:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("main.team"))


@bp.route("/team", methods=["GET", "POST"])
def team():
    league = load_session_league()
    if league is None:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        try:
            roster_id = int(request.form.get("roster_id", ""))
        except ValueError:
            roster_id = None
        chosen = league.team(roster_id) if roster_id is not None else None
        if chosen is None:
            flash("Please choose a team from the list.", "error")
            return redirect(url_for("main.team"))
        remember(league, chosen)
        return redirect(url_for("main.dashboard"))

    return render_template("team.html", league=league, selected=selected_team(league))


@bp.route("/dashboard")
def dashboard():
    league = load_session_league()
    if league is None:
        return redirect(url_for("main.index"))
    mine = selected_team(league)
    if mine is None:
        return redirect(url_for("main.team"))

    book = get_book(settings_for(league))
    roster: List[Asset] = []
    standing: Optional[Tuple[int, int]] = None
    profile = None
    league_needs = None
    if book is not None:
        teams = build_team_assets(league, book)
        ranked = sorted(teams, key=lambda t: t.value(), reverse=True)
        for position, team_assets in enumerate(ranked, start=1):
            if team_assets.roster_id == mine.roster_id:
                roster = team_assets.assets
                standing = (position, len(ranked))
        league_needs = analyze_needs(league.roster_positions, teams)
        profile = league_needs.teams.get(mine.roster_id)
    settings = settings_for(league).normalized()
    return render_template(
        "dashboard.html",
        league=league,
        team=mine,
        roster=roster,
        standing=standing,
        settings=settings,
        profile=profile,
        league_needs=league_needs,
        vorp_rank=_vorp_rank(league_needs, mine.roster_id),
        num_qbs=settings.num_qbs,
    )


def _vorp_rank(league_needs, roster_id: int) -> Optional[Tuple[int, int]]:
    if league_needs is None:
        return None
    ordered = sorted(league_needs.teams.values(), key=lambda t: t.vorp, reverse=True)
    for rank, team in enumerate(ordered, start=1):
        if team.roster_id == roster_id:
            return rank, len(ordered)
    return None


@bp.route("/suggestions", methods=["POST"])
def suggestions():
    league = load_session_league()
    if league is None:
        return redirect(url_for("main.index"))
    mine = selected_team(league)
    if mine is None:
        return redirect(url_for("main.team"))

    prefs = Preferences(
        strategy=request.form.get("strategy", "balanced"),
        risk=request.form.get("risk_tolerance", "medium"),
        needs=set(request.form.getlist("position_needs")),
    ).cleaned()

    book = get_book(settings_for(league))
    if book is None:
        return redirect(url_for("main.dashboard"))

    teams = build_team_assets(league, book)
    my_assets = next((t for t in teams if t.roster_id == mine.roster_id), None)
    if my_assets is None or not my_assets.assets:
        flash("None of the players on your roster have trade values yet.", "error")
        return redirect(url_for("main.dashboard"))

    settings = settings_for(league).normalized()
    league_needs = analyze_needs(league.roster_positions, teams)
    results = generate_suggestions(
        my_assets,
        teams,
        prefs,
        limit=current_app.config["MAX_SUGGESTIONS"],
        num_qbs=settings.num_qbs,
        needs=league_needs,
    )
    partners = [(t, score) for t, score in league_needs.partner_ranking(mine.roster_id)[:3] if score > 0]

    ai_used = ai_limited = False
    explainer = services().explainer
    if explainer and results:
        if services().llm_limiter.allow(client_key()):
            explanations = explainer.explain_suggestions(league.name, prefs.strategy, results)
            for index, text in explanations.items():
                results[index].explanation = text
            ai_used = bool(explanations)
        else:
            ai_limited = True

    return render_template(
        "suggestions.html",
        league=league,
        team=mine,
        suggestions=results,
        prefs=prefs,
        ai_used=ai_used,
        ai_limited=ai_limited,
        partners=partners,
        league_needs=league_needs,
    )


def _split_entries(text: str) -> List[str]:
    entries = []
    for chunk in text.replace(",", "\n").splitlines():
        chunk = chunk.strip()
        if chunk:
            entries.append(chunk[:MAX_LINE_LENGTH])
    return entries


@bp.route("/calculator", methods=["GET", "POST"])
def calculator():
    league = load_session_league() if session.get("league_id") else None
    defaults = settings_for(league)

    if request.method == "GET":
        return render_template("calculator.html", form=_form_state(defaults), result=None, errors=[], settings=defaults)

    return _calculate(_form_state(defaults, request.form), defaults)


def _share_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="shared-trade")


@bp.route("/t/<token>")
def shared_trade(token: str):
    """Re-run a trade from a signed link. Nothing is stored on the server."""
    try:
        data = _share_serializer().loads(token)
    except BadSignature:
        return render_template("error.html", code=404, message="That shared trade link is not valid."), 404
    if not isinstance(data, dict):
        return render_template("error.html", code=404, message="That shared trade link is not valid."), 404
    league = load_session_league() if session.get("league_id") else None
    defaults = settings_for(league)
    form = _form_state(defaults, {k: str(v) for k, v in data.items()})
    return _calculate(form, defaults)


def _calculate(form: dict, defaults: ValueSettings):
    settings = ValueSettings(
        num_qbs=form["qbs"], ppr=form["ppr"], num_teams=defaults.num_teams, te_premium=form["te"]
    ).normalized()

    side_a_names = _split_entries(form["team_a"])
    side_b_names = _split_entries(form["team_b"])
    errors: List[str] = []
    if not side_a_names or not side_b_names:
        errors.append("Enter at least one player or pick for both teams.")
    if len(side_a_names) > MAX_LINES_PER_SIDE or len(side_b_names) > MAX_LINES_PER_SIDE:
        errors.append(f"Please limit each side to {MAX_LINES_PER_SIDE} assets.")
    if errors:
        return render_template("calculator.html", form=form, result=None, errors=errors, settings=settings), 400

    book = get_book(settings)
    if book is None:
        return render_template("calculator.html", form=form, result=None, errors=[], settings=settings), 503

    side_a: List[Asset] = []
    side_b: List[Asset] = []
    for names, bucket in ((side_a_names, side_a), (side_b_names, side_b)):
        for name in names:
            asset, suggestions_ = book.resolve(name)
            if asset is None:
                hint = f' Did you mean: {", ".join(suggestions_)}?' if suggestions_ else ""
                errors.append(f'Could not find "{name}".{hint}')
            else:
                bucket.append(asset)
    if errors:
        return render_template("calculator.html", form=form, result=None, errors=errors, settings=settings), 400

    for side in (side_a, side_b):
        keys = [a.key for a in side]
        if len(keys) != len(set(keys)):
            errors.append("The same asset is listed more than once on one side.")
            break
    if {a.key for a in side_a} & {a.key for a in side_b}:
        errors.append("An asset cannot be on both sides of the trade.")
    if errors:
        return render_template("calculator.html", form=form, result=None, errors=errors, settings=settings), 400

    evaluation = evaluate_trade(side_a, side_b, book)
    analysis = template_evaluation_text(evaluation)
    ai_used = False
    explainer = services().explainer
    if explainer:
        if services().llm_limiter.allow(client_key()):
            text = explainer.explain_evaluation(evaluation, evaluation.balancers)
            if text:
                analysis, ai_used = text, True
    share_url = url_for(
        "main.shared_trade",
        token=_share_serializer().dumps(
            {k: form[k] for k in ("team_a", "team_b", "qbs", "ppr", "te")}
        ),
        _external=True,
    )
    return render_template(
        "calculator.html",
        form=form,
        result=evaluation,
        analysis=analysis,
        ai_used=ai_used,
        errors=[],
        settings=settings,
        model_view=_model_view(side_a, side_b, settings.num_qbs),
        share_url=share_url,
    )


def _model_view(side_a: List[Asset], side_b: List[Asset], num_qbs: int) -> Optional[dict]:
    """Net model edge for each side's incoming assets (what Team A receives is side B's list)."""
    def total(assets: List[Asset]) -> Optional[float]:
        known = [a for a in assets if not a.is_pick and insights.edge_for(a.key, num_qbs)]
        if not known:
            return None
        return sum(a.value * insights.edge_fraction(a.key, num_qbs) for a in known)

    a_recv, b_recv = total(side_b), total(side_a)
    if a_recv is None and b_recv is None:
        return None
    return {"team_a": a_recv or 0.0, "team_b": b_recv or 0.0}


def _form_state(defaults: ValueSettings, data=None) -> dict:
    d = defaults.normalized()
    if data is None:
        return {"team_a": "", "team_b": "", "qbs": d.num_qbs, "ppr": d.ppr, "te": d.te_premium}
    try:
        qbs = 2 if int(data.get("qbs", d.num_qbs)) >= 2 else 1
    except ValueError:
        qbs = d.num_qbs
    try:
        ppr = float(data.get("ppr", d.ppr))
    except ValueError:
        ppr = d.ppr
    try:
        te = float(data.get("te", d.te_premium))
    except ValueError:
        te = d.te_premium
    return {
        "team_a": data.get("team_a", "")[:2000],
        "team_b": data.get("team_b", "")[:2000],
        "qbs": qbs,
        "ppr": ppr,
        "te": min((0.0, 0.5, 1.0), key=lambda option: abs(option - max(0.0, te))),
    }


@bp.route("/api/assets")
def asset_search():
    """Name autocomplete for the calculator (JSON, no side effects)."""
    query = normalize_name(request.args.get("q", "")[:MAX_LINE_LENGTH])
    if len(query) < 2:
        return jsonify(results=[])
    try:
        qbs = 2 if int(request.args.get("qbs", 1)) >= 2 else 1
    except ValueError:
        qbs = 1
    try:
        book = services().values.get_book(ValueSettings(num_qbs=qbs))
    except ValuesUnavailable:
        return jsonify(results=[])
    starts, contains = [], []
    for norm, asset in book.by_name.items():
        if asset.is_pick or asset.estimated:
            continue
        if norm.startswith(query) or any(part.startswith(query) for part in norm.split()):
            starts.append(asset)
        elif query in norm:
            contains.append(asset)
    ranked = sorted(starts, key=lambda a: -a.value) + sorted(contains, key=lambda a: -a.value)
    picks = [pick_name(y, r) for y in book.pick_years for r in book.pick_rounds if 1 <= r <= 4]
    pick_hits = [p for p in picks if query.replace(" ", "") in p.lower().replace(" ", "")] if query[:1].isdigit() else []
    results = [a.name for a in ranked[:8]] + pick_hits[:4]
    return jsonify(results=results[:10])


@bp.route("/insights")
def insights_page():
    league = load_session_league() if session.get("league_id") else None
    default_qbs = settings_for(league).normalized().num_qbs if league else 2
    try:
        num_qbs = 2 if int(request.args.get("qbs", default_qbs)) >= 2 else 1
    except ValueError:
        num_qbs = default_qbs

    movers = insights.lists(num_qbs)
    spark = {}
    players = {**{p["name"]: p for p in movers["buy"]}, **{p["name"]: p for p in movers["sell"]}}
    ids = {pid: rec for pid, rec in insights.edges(num_qbs).items() if rec["name"] in players}
    for pid in ids:
        series = [v for _, v in insights.history(num_qbs, pid)]
        spark[ids[pid]["name"]] = insights.sparkline_points(series)
    return render_template(
        "insights.html",
        num_qbs=num_qbs,
        movers=movers,
        spark=spark,
        meta=insights.model_meta(),
        diagnostics=insights.diagnostics(num_qbs),
        calibration=insights.calibration(),
        aging=insights.aging_table(),
        ages=list(range(22, 36, 2)),
        has_model=bool(insights.edges(num_qbs)),
    )


@bp.route("/reset", methods=["POST"])
def reset():
    session.clear()
    flash("Disconnected from your league.", "info")
    return redirect(url_for("main.index"))
