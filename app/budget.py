"""Persistent daily spend cap.

Why SQLite and not a Python variable? On Fly the machine stops when idle and restarts on demand;
a counter in memory would reset to $0 every time, so the cap would be decoration. SQLite is in
the standard library, survives restarts when it lives on a volume, and increments atomically.

Known limitation (worth saying out loud in an interview): we check BEFORE a request and record
AFTER it, so N concurrent requests can overshoot the cap by at most N x (cost of one request),
which is a fraction of a cent each. That's acceptable here; the Anthropic Console spend limit
is the hard ceiling behind this soft one.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class DailyBudget:
    def __init__(self, path: Path, cap_usd: float, today: Callable[[], str] = _utc_today):
        self.path = Path(path)
        self.cap_usd = cap_usd
        self._today = today

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=5)
        con.execute("CREATE TABLE IF NOT EXISTS spend (day TEXT PRIMARY KEY, usd REAL NOT NULL, requests INTEGER NOT NULL)")
        return con

    def spent_today(self) -> float:
        con = self._connect()
        try:
            row = con.execute("SELECT usd FROM spend WHERE day = ?", (self._today(),)).fetchone()
        finally:
            con.close()
        return row[0] if row else 0.0

    def has_room(self) -> bool:
        return self.spent_today() < self.cap_usd

    def record(self, usd: float) -> None:
        con = self._connect()
        try:
            with con:  # one atomic upsert; commits on exit
                con.execute(
                    "INSERT INTO spend (day, usd, requests) VALUES (?, ?, 1) "
                    "ON CONFLICT(day) DO UPDATE SET usd = usd + excluded.usd, requests = requests + 1",
                    (self._today(), usd),
                )
        finally:
            con.close()
