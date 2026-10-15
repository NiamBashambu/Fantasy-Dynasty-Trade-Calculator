"""Dynasty trade values from the public FantasyCalc API.

Values are market consensus numbers (derived from real trades), adjusted for
league format. We cache them in memory and mirror them to a JSON file so a
restart or a short FantasyCalc outage does not take the app down. No database.
"""

import difflib
import json
import logging
import os
import re
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


class ValuesUnavailable(Exception):
    """Raised when values cannot be fetched and no cached copy exists."""


@dataclass(frozen=True)
class ValueSettings:
    """League format inputs that change player values."""

    num_qbs: int = 1
    ppr: float = 1.0
    num_teams: int = 12

    def normalized(self) -> "ValueSettings":
        num_qbs = 2 if self.num_qbs >= 2 else 1
        ppr = min((0.0, 0.5, 1.0), key=lambda option: abs(option - self.ppr))
        teams = max(8, min(16, int(self.num_teams)))
        if teams % 2:
            teams += 1
        return ValueSettings(num_qbs=num_qbs, ppr=ppr, num_teams=teams)

    def cache_key(self) -> str:
        return f"q{self.num_qbs}_p{self.ppr}_t{self.num_teams}"


@dataclass(frozen=True)
class Asset:
    """A player or draft pick with a trade value."""

    key: str
    name: str
    position: str
    team: Optional[str]
    age: Optional[float]
    value: int
    redraft_value: int
    rank: int
    kind: str  # "player" or "pick"

    @property
    def is_pick(self) -> bool:
        return self.kind == "pick"


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_ORDINALS = {"1": "1st", "2": "2nd", "3": "3rd", "4": "4th"}
_PICK_RE = re.compile(
    r"^(?P<year>20\d{2})\s+"
    r"(?:(?P<ord>[1-4])(?:st|nd|rd|th)(?:\s+round)?(?:\s+pick)?"
    r"|round\s+(?P<rnd>[1-4])(?:\s+pick)?)"
    r"(?:\s*\(?(?P<tier>early|mid|late)\)?)?$"
)


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[.'`\u2019\-]", "", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    parts = [p for p in text.split() if p not in _SUFFIXES]
    return " ".join(parts)


def parse_pick(text: str) -> Optional[Tuple[int, int, Optional[str]]]:
    """Parse '2027 1st', '2027 round 2 pick', '2028 1st (early)' -> (year, round, tier)."""
    match = _PICK_RE.match(text.strip().lower())
    if not match:
        return None
    rnd = int(match.group("ord") or match.group("rnd"))
    return int(match.group("year")), rnd, match.group("tier")


def pick_name(year: int, rnd: int, tier: Optional[str] = None) -> str:
    base = f"{year} {_ORDINALS[str(rnd)]}"
    return f"{base} ({tier.title()})" if tier else base


class ValueBook:
    """All assets for one league format, with lookup helpers."""

    def __init__(self, assets: List[Asset]):
        self.assets = sorted(assets, key=lambda a: a.value, reverse=True)
        self.by_key: Dict[str, Asset] = {a.key: a for a in self.assets}
        self.by_name: Dict[str, Asset] = {}
        for asset in self.assets:  # highest value wins on duplicate names
            self.by_name.setdefault(normalize_name(asset.name), asset)
        self.pick_years = sorted(
            {p[0] for a in self.assets if a.is_pick and (p := parse_pick(a.name))}
        )
        self.pick_rounds = sorted(
            {p[1] for a in self.assets if a.is_pick and (p := parse_pick(a.name))}
        )

    def player_by_sleeper_id(self, sleeper_id: str) -> Optional[Asset]:
        asset = self.by_key.get(str(sleeper_id))
        return asset if asset and not asset.is_pick else None

    def pick(self, year: int, rnd: int) -> Optional[Asset]:
        return self.by_name.get(normalize_name(pick_name(year, rnd)))

    def resolve(self, text: str) -> Tuple[Optional[Asset], List[str]]:
        """Resolve free text to an asset. Returns (asset, suggestions).

        Exact (normalized) matches and picks resolve directly. Near-misses
        (typos) resolve only when the match is very close and unambiguous;
        otherwise the caller gets suggestions to show the user.
        """
        cleaned = text.strip()
        if not cleaned:
            return None, []

        parsed = parse_pick(normalize_pick_text(cleaned))
        if parsed:
            year, rnd, tier = parsed
            asset = self.by_name.get(normalize_name(pick_name(year, rnd, tier)))
            if asset:
                return asset, []
            years = ", ".join(str(y) for y in self.pick_years) or "none"
            return None, [f"Pick values are available for: {years}"]

        norm = normalize_name(cleaned)
        asset = self.by_name.get(norm)
        if asset:
            return asset, []

        player_names = [n for n, a in self.by_name.items() if not a.is_pick]
        close = difflib.get_close_matches(norm, player_names, n=3, cutoff=0.8)
        if close:
            best = difflib.SequenceMatcher(None, norm, close[0]).ratio()
            if best >= 0.93 and (len(close) == 1 or difflib.SequenceMatcher(None, norm, close[1]).ratio() < best - 0.05):
                return self.by_name[close[0]], []
        tokens = norm.split()
        contains = [n for n in player_names if tokens and all(t in n.split() for t in tokens)]
        suggestions = []
        for candidate in close + contains:
            display = self.by_name[candidate].name
            if display not in suggestions:
                suggestions.append(display)
        return None, suggestions[:3]


def normalize_pick_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _parse_book(payload: List[Dict[str, Any]]) -> ValueBook:
    assets: List[Asset] = []
    for item in payload:
        player = item.get("player") or {}
        name = player.get("name")
        key = player.get("sleeperId")
        value = item.get("value")
        if not name or not key or not isinstance(value, (int, float)):
            continue
        is_pick = player.get("position") == "PICK"
        age = player.get("maybeAge")
        assets.append(
            Asset(
                key=str(key),
                name=str(name),
                position=str(player.get("position") or ""),
                team=player.get("maybeTeam"),
                age=float(age) if isinstance(age, (int, float)) else None,
                value=int(value),
                redraft_value=int(item.get("redraftValue") or 0),
                rank=int(item.get("overallRank") or 0),
                kind="pick" if is_pick else "player",
            )
        )
    if not assets:
        raise ValueError("no assets in payload")
    return ValueBook(assets)


class ValuesClient:
    def __init__(
        self,
        base_url: str = "https://api.fantasycalc.com/values/current",
        timeout: float = 15,
        ttl: float = 6 * 3600,
        cache_dir: Optional[str] = None,
        http: Optional[requests.Session] = None,
    ):
        self.base_url = base_url
        self.timeout = timeout
        self.ttl = ttl
        self.cache_dir = cache_dir
        self.http = http or requests.Session()
        self._books: Dict[str, Tuple[float, ValueBook]] = {}
        self._lock = threading.Lock()

    # -- public ---------------------------------------------------------
    def get_book(self, settings: Optional[ValueSettings] = None) -> ValueBook:
        settings = (settings or ValueSettings()).normalized()
        key = settings.cache_key()
        now = time.monotonic()

        with self._lock:
            entry = self._books.get(key)
            if entry and now - entry[0] < self.ttl:
                return entry[1]

            try:
                payload = self._fetch(settings)
                book = _parse_book(payload)
            except (requests.RequestException, ValueError) as exc:
                logger.warning("Could not refresh values (%s): %s", key, exc)
                if entry:
                    return entry[1]  # stale copy in memory
                book = self._read_disk(key)
                if book is None:
                    raise ValuesUnavailable(
                        "Trade values are temporarily unavailable. Please try again soon."
                    ) from exc
                self._books[key] = (now - self.ttl + 300, book)  # retry in five minutes
                return book

            self._books[key] = (now, book)
            self._write_disk(key, payload)
            return book

    # -- internals ------------------------------------------------------
    def _fetch(self, settings: ValueSettings) -> List[Dict[str, Any]]:
        response = self.http.get(
            self.base_url,
            params={
                "isDynasty": "true",
                "numQbs": settings.num_qbs,
                "numTeams": settings.num_teams,
                "ppr": settings.ppr,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError("unexpected payload shape")
        return data

    def _disk_path(self, key: str) -> Optional[str]:
        if not self.cache_dir:
            return None
        return os.path.join(self.cache_dir, f"fantasycalc_{key}.json")

    def _write_disk(self, key: str, payload: List[Dict[str, Any]]) -> None:
        path = self._disk_path(key)
        if not path:
            return
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.cache_dir, suffix=".tmp")
            with os.fdopen(fd, "w") as handle:
                json.dump(payload, handle)
            os.replace(tmp, path)
        except OSError as exc:  # read-only or ephemeral filesystem is fine
            logger.info("Skipping disk cache write: %s", exc)

    def _read_disk(self, key: str) -> Optional[ValueBook]:
        path = self._disk_path(key)
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path) as handle:
                return _parse_book(json.load(handle))
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable value cache %s: %s", path, exc)
            return None
