"""Collect raw data from public sources.

Sources
-------
* FantasyCalc ``/values/current``  -> dated market-value snapshots (one CSV per format per day)
* FantasyCalc ``/trades``          -> recent real dynasty trades (appended and de-duplicated)
* nflverse ``stats_player_reg_*``  -> season-level NFL player stats
* DynastyProcess ``db_playerids``  -> maps Sleeper ids to nflverse (gsis) ids and birthdates

Snapshots and trades only accumulate if you keep running this (a daily cron is
enough). The model and calibration code work with whatever has been collected.

Usage: ``python -m datascience.collect [snapshots|trades|stats|ids|all]``
"""

import datetime as dt
import json
import logging
import os
import sys
import time
from typing import Dict, Iterable, List, Optional

import pandas as pd
import requests

from . import RAW_DIR, SNAPSHOT_DIR, TRADES_PATH

logger = logging.getLogger(__name__)

FC_BASE = "https://api.fantasycalc.com"
NFLVERSE_STATS = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_reg_{season}.csv"
PLAYER_IDS_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
FORMATS = [(qbs, ppr) for qbs in (1, 2) for ppr in (0.0, 0.5, 1.0)]
FIRST_SEASON = 2012
HEADERS = {"User-Agent": "dynasty-trade-analyzer-research/1.0"}


def _get(url: str, params: Optional[dict] = None, timeout: float = 60) -> requests.Response:
    last: Optional[Exception] = None
    for attempt in range(3):
        try:
            response = requests.get(url, params=params, timeout=timeout, headers=HEADERS)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def snapshot_path(day: str, qbs: int, ppr: float) -> str:
    return os.path.join(SNAPSHOT_DIR, f"{day}_q{qbs}_p{ppr}.csv")


def flatten_values(payload: List[dict]) -> pd.DataFrame:
    rows = []
    for item in payload:
        p = item.get("player") or {}
        draft = p.get("maybeDraftInfo") or {}
        rows.append(
            {
                "sleeper_id": str(p.get("sleeperId") or ""),
                "name": p.get("name"),
                "position": p.get("position"),
                "team": p.get("maybeTeam"),
                "age": p.get("maybeAge"),
                "birthday": p.get("maybeBirthday"),
                "years_exp": p.get("maybeYoe"),
                "draft_year": draft.get("year"),
                "draft_round": draft.get("round"),
                "draft_pick": draft.get("pick"),
                "value": item.get("value"),
                "redraft_value": item.get("redraftValue"),
                "overall_rank": item.get("overallRank"),
                "position_rank": item.get("positionRank"),
                "trend_30d": item.get("trend30Day"),
            }
        )
    return pd.DataFrame(rows)


def collect_snapshots(day: Optional[str] = None) -> List[str]:
    day = day or dt.date.today().isoformat()
    written = []
    for qbs, ppr in FORMATS:
        response = _get(
            f"{FC_BASE}/values/current",
            {"isDynasty": "true", "numQbs": qbs, "numTeams": 12, "ppr": ppr},
        )
        frame = flatten_values(response.json())
        frame.insert(0, "date", day)
        path = snapshot_path(day, qbs, ppr)
        frame.to_csv(path, index=False)
        written.append(path)
        logger.info("snapshot %s: %d assets", os.path.basename(path), len(frame))
    return written


def load_snapshots(qbs: Optional[int] = None, ppr: Optional[float] = None) -> pd.DataFrame:
    frames = []
    for name in sorted(os.listdir(SNAPSHOT_DIR)):
        if not name.endswith(".csv"):
            continue
        day, fmt = name[:-4].split("_", 1)
        q, p = fmt.split("_")
        q_val, p_val = int(q[1:]), float(p[1:])
        if (qbs is not None and q_val != qbs) or (ppr is not None and p_val != ppr):
            continue
        frame = pd.read_csv(os.path.join(SNAPSHOT_DIR, name), dtype={"sleeper_id": str})
        frame["num_qbs"], frame["ppr"] = q_val, p_val
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _read_trades() -> Dict[str, dict]:
    trades: Dict[str, dict] = {}
    if os.path.exists(TRADES_PATH):
        with open(TRADES_PATH) as handle:
            for line in handle:
                line = line.strip()
                if line:
                    record = json.loads(line)
                    trades[record["id"]] = record
    return trades


def _slim_trade(raw: dict) -> dict:
    def side(assets: Iterable[dict]) -> List[dict]:
        return [
            {
                "sleeper_id": str(a.get("sleeperId") or ""),
                "name": a.get("name"),
                "position": a.get("position"),
                "age": a.get("maybeAge"),
            }
            for a in assets
        ]

    return {
        "id": raw["id"],
        "date": raw.get("date"),
        "side1": side(raw.get("side1") or []),
        "side2": side(raw.get("side2") or []),
        "num_qbs": 2 if (raw.get("numQbs") or 1) >= 2 else 1,
        "ppr": raw.get("ppr"),
        "num_teams": raw.get("numTeams"),
        "te_premium": raw.get("tePremium"),
        "fc_value_diff": raw.get("maybeTradedValueDiff"),
    }


def collect_trades() -> int:
    """Append recent real trades. The endpoint returns only the latest ~50 per query."""
    existing = _read_trades()
    before = len(existing)
    for qbs, ppr in FORMATS:
        try:
            response = _get(
                f"{FC_BASE}/trades",
                {"isDynasty": "true", "numQbs": qbs, "numTeams": 12, "ppr": ppr, "limit": 50},
            )
        except RuntimeError as exc:
            logger.warning("%s", exc)
            continue
        for raw in response.json():
            if raw.get("id") and raw["id"] not in existing:
                existing[raw["id"]] = _slim_trade(raw)
    os.makedirs(os.path.dirname(TRADES_PATH), exist_ok=True)
    with open(TRADES_PATH, "w") as handle:
        for record in sorted(existing.values(), key=lambda r: r.get("date") or ""):
            handle.write(json.dumps(record) + "\n")
    added = len(existing) - before
    logger.info("trades: +%d (total %d)", added, len(existing))
    return added


def load_trades() -> List[dict]:
    return sorted(_read_trades().values(), key=lambda r: r.get("date") or "")


def collect_stats(first: int = FIRST_SEASON, last: Optional[int] = None) -> List[int]:
    last = last or dt.date.today().year
    got = []
    for season in range(first, last + 1):
        path = os.path.join(RAW_DIR, f"stats_{season}.csv")
        if os.path.exists(path) and season < last - 1:
            got.append(season)
            continue  # old seasons never change
        try:
            response = _get(NFLVERSE_STATS.format(season=season), timeout=120)
        except RuntimeError:
            logger.info("no stats for %s yet", season)
            continue
        with open(path, "wb") as handle:
            handle.write(response.content)
        got.append(season)
        logger.info("stats %s downloaded", season)
    return got


def collect_ids() -> str:
    path = os.path.join(RAW_DIR, "player_ids.csv")
    with open(path, "wb") as handle:
        handle.write(_get(PLAYER_IDS_URL, timeout=120).content)
    return path


def main(argv: Optional[List[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    what = (argv or sys.argv[1:] or ["all"])[0]
    if what in ("snapshots", "all"):
        collect_snapshots()
    if what in ("trades", "all"):
        collect_trades()
    if what in ("stats", "all"):
        collect_stats()
    if what in ("ids", "all"):
        collect_ids()


if __name__ == "__main__":
    main()
