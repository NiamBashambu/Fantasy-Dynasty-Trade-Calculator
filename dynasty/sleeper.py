"""Client for the public Sleeper API (read-only, no key required)."""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

from .cache import TTLCache

logger = logging.getLogger(__name__)

LEAGUE_ID_RE = re.compile(r"^\d{6,25}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")


class SleeperError(Exception):
    """Raised for any problem talking to Sleeper. The message is user-safe."""


class NotFound(SleeperError):
    pass


@dataclass
class Team:
    roster_id: int
    owner_id: Optional[str]
    name: str
    player_ids: List[str]


@dataclass
class League:
    league_id: str
    name: str
    season: str
    total_rosters: int
    roster_positions: List[str]
    scoring_settings: Dict[str, Any]
    draft_rounds: int
    teams: List[Team]
    traded_picks: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def num_qbs(self) -> int:
        positions = self.roster_positions
        if "SUPER_FLEX" in positions or positions.count("QB") >= 2:
            return 2
        return 1

    @property
    def ppr(self) -> float:
        try:
            rec = float(self.scoring_settings.get("rec", 0) or 0)
        except (TypeError, ValueError):
            rec = 0.0
        return min((0.0, 0.5, 1.0), key=lambda option: abs(option - rec))

    @property
    def te_premium(self) -> float:
        """Extra points per reception for tight ends (Sleeper's ``bonus_rec_te``)."""
        try:
            bonus = float(self.scoring_settings.get("bonus_rec_te", 0) or 0)
        except (TypeError, ValueError):
            bonus = 0.0
        return min((0.0, 0.5, 1.0), key=lambda option: abs(option - max(0.0, bonus)))

    def team(self, roster_id: int) -> Optional[Team]:
        for team in self.teams:
            if team.roster_id == roster_id:
                return team
        return None

    def pick_ownership(
        self, seasons: List[int], rounds: List[int]
    ) -> Dict[int, List[Tuple[int, int, int]]]:
        """Return {roster_id: [(season, round, original_roster_id), ...]} for picks each team owns.

        Every team starts with its own picks; Sleeper's traded_picks list moves them.
        """
        owners: Dict[Tuple[int, int, int], int] = {}
        for team in self.teams:
            for season in seasons:
                for rnd in rounds:
                    owners[(season, rnd, team.roster_id)] = team.roster_id
        for trade in self.traded_picks:
            try:
                key = (int(trade["season"]), int(trade["round"]), int(trade["roster_id"]))
                new_owner = int(trade["owner_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if key in owners:
                owners[key] = new_owner
        result: Dict[int, List[Tuple[int, int, int]]] = {team.roster_id: [] for team in self.teams}
        for (season, rnd, original), owner in sorted(owners.items()):
            if owner in result:
                result[owner].append((season, rnd, original))
        return result


class SleeperClient:
    def __init__(
        self,
        base_url: str = "https://api.sleeper.app/v1",
        timeout: float = 10,
        cache_ttl: float = 300,
        http: Optional[requests.Session] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.http = http or requests.Session()
        self._leagues = TTLCache(ttl=cache_ttl, max_size=128)

    def _get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = self.http.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            logger.warning("Sleeper request failed: %s (%s)", path, exc)
            raise SleeperError("Could not reach Sleeper. Please try again in a moment.") from exc
        if response.status_code == 404:
            raise NotFound("Not found on Sleeper.")
        if response.status_code == 429:
            raise SleeperError("Sleeper is rate limiting requests. Please try again shortly.")
        if not response.ok:
            logger.warning("Sleeper returned %s for %s", response.status_code, path)
            raise SleeperError("Sleeper returned an error. Please try again later.")
        try:
            return response.json()
        except ValueError as exc:
            raise SleeperError("Sleeper returned an unreadable response.") from exc

    def get_user_id(self, username: str) -> str:
        if not USERNAME_RE.match(username):
            raise SleeperError("That does not look like a valid Sleeper username.")
        try:
            data = self._get(f"/user/{username}")
        except NotFound:
            data = None
        if not data or not data.get("user_id"):
            raise NotFound(f'No Sleeper user named "{username}" was found.')
        return str(data["user_id"])

    def load_league(self, league_id: str) -> League:
        if not LEAGUE_ID_RE.match(league_id or ""):
            raise SleeperError("League IDs are numeric (find yours in the Sleeper league URL).")
        cached = self._leagues.get(league_id)
        if cached is not None:
            return cached

        try:
            info = self._get(f"/league/{league_id}")
        except NotFound:
            info = None
        if not info:
            raise NotFound("No Sleeper league was found with that ID.")

        users = self._get(f"/league/{league_id}/users") or []
        rosters = self._get(f"/league/{league_id}/rosters") or []
        if not rosters:
            raise SleeperError("That league has no rosters yet.")
        try:
            traded = self._get(f"/league/{league_id}/traded_picks") or []
        except SleeperError:
            logger.warning("Could not load traded picks for league %s", league_id)
            traded = []

        users_by_id = {str(u.get("user_id")): u for u in users}
        teams: List[Team] = []
        for roster in rosters:
            owner_id = roster.get("owner_id")
            owner = users_by_id.get(str(owner_id)) if owner_id else None
            metadata = (owner or {}).get("metadata") or {}
            name = (
                metadata.get("team_name")
                or (owner or {}).get("display_name")
                or f"Team {roster.get('roster_id')}"
            )
            ids: List[str] = []
            for key in ("players", "reserve", "taxi"):
                for player_id in roster.get(key) or []:
                    if player_id not in ids:
                        ids.append(str(player_id))
            teams.append(
                Team(
                    roster_id=int(roster["roster_id"]),
                    owner_id=str(owner_id) if owner_id else None,
                    name=str(name),
                    player_ids=ids,
                )
            )

        settings = info.get("settings") or {}
        league = League(
            league_id=league_id,
            name=info.get("name") or "Unnamed league",
            season=str(info.get("season") or ""),
            total_rosters=int(info.get("total_rosters") or len(teams)),
            roster_positions=list(info.get("roster_positions") or []),
            scoring_settings=info.get("scoring_settings") or {},
            draft_rounds=int(settings.get("draft_rounds") or 4),
            teams=teams,
            traded_picks=traded,
        )
        self._leagues.set(league_id, league)
        return league
