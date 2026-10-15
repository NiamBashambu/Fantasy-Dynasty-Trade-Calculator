"""Offline data science pipeline for the Dynasty Trade Analyzer.

Nothing in here is imported by the web app at request time. The pipeline reads
public data, trains models, and writes small JSON artifacts into
``dynasty/data/`` which the app loads with the standard library only.

Run the whole thing with ``python -m datascience.run_all``.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("DS_DATA_DIR", os.path.join(ROOT, "data"))
RAW_DIR = os.path.join(DATA_DIR, "raw")
SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")
TRADES_PATH = os.path.join(DATA_DIR, "trades.jsonl")
ARTIFACT_DIR = os.path.join(ROOT, "dynasty", "data")
FIGURE_DIR = os.path.join(ROOT, "notebooks", "figures")

for _path in (RAW_DIR, SNAPSHOT_DIR, ARTIFACT_DIR):
    os.makedirs(_path, exist_ok=True)
