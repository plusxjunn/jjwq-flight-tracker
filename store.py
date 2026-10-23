"""SQLite storage, API-call counter, run log and CSV export."""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SGT = timezone(timedelta(hours=8))

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    slot TEXT,
    status TEXT,              -- running | ok | partial | api_error | quota_skip | error
    calls INTEGER DEFAULT 0,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    month TEXT NOT NULL,      -- YYYY-MM (SGT)
    run_id INTEGER,
    search_id TEXT,
    ok INTEGER
);
CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY,
    run_id INTEGER,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,        -- YYYY-MM-DD (SGT)
    search_id TEXT NOT NULL,
    lowest_price REAL,
    price_level TEXT,
    typical_low REAL,
    typical_high REAL,
    url TEXT
);
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY,
    search_row INTEGER NOT NULL,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    search_id TEXT NOT NULL,
    airline TEXT NOT NULL,
    price REAL NOT NULL,
    flights TEXT,
    depart TEXT,
    arrive TEXT,
    preferred INTEGER NOT NULL DEFAULT 0,   -- 1 = cheapest within the after-work time window
    is_overall_min INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS quotes_lookup ON quotes (search_id, airline, preferred, ts);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,       -- deal | climbing | watch
    search_id TEXT,
    airline TEXT,
    price REAL,
    reasons TEXT
);
"""


def now_sgt() -> datetime:
    return datetime.now(SGT).replace(microsecond=0)


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.commit()
        self.db.close()

    # ---- runs & quota ---------------------------------------------------
    def start_run(self, slot: str, ts: datetime) -> int:
        cur = self.db.execute("INSERT INTO runs (started_at, slot, status) VALUES (?,?,?)",
                              (ts.isoformat(), slot, "running"))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, status: str, calls: int, detail: str = ""):
        self.db.execute("UPDATE runs SET finished_at=?, status=?, calls=?, detail=? WHERE id=?",
                        (now_sgt().isoformat(), status, calls, detail[:2000], run_id))
        self.db.commit()

    def log_call(self, run_id: int, search_id: str, ok: bool, ts: datetime):
        self.db.execute("INSERT INTO api_calls (ts, month, run_id, search_id, ok) VALUES (?,?,?,?,?)",
                        (ts.isoformat(), ts.strftime("%Y-%m"), run_id, search_id, int(ok)))
        self.db.commit()

    def slot_done(self, slot: str, day) -> bool:
        """Has this slot already run successfully (or been quota-skipped) on this SGT day?"""
        return self.db.execute(
            "SELECT 1 FROM runs WHERE slot=? AND substr(started_at, 1, 10)=? AND status IN ('ok', 'partial', 'quota_skip')",
            (slot, day.isoformat())).fetchone() is not None

    def calls_this_month(self, ts: datetime) -> int:
        return self.db.execute("SELECT COUNT(*) FROM api_calls WHERE month=?",
                               (ts.strftime("%Y-%m"),)).fetchone()[0]

    # ---- prices ---------------------------------------------------------
    def save_search(self, run_id: int, ts: datetime, result, quotes: list[dict]) -> int:
        """quotes: dicts with airline, price, flights, depart, arrive, preferred, is_overall_min."""
        cur = self.db.execute(
            "INSERT INTO searches (run_id, ts, day, search_id, lowest_price, price_level,"
            " typical_low, typical_high, url) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, ts.isoformat(), ts.date().isoformat(), result.spec.id, result.lowest_price,
             result.price_level, result.typical_low, result.typical_high, result.url))
        row = cur.lastrowid
        self.db.executemany(
            "INSERT INTO quotes (search_row, ts, day, search_id, airline, price, flights, depart,"
            " arrive, preferred, is_overall_min) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(row, ts.isoformat(), ts.date().isoformat(), result.spec.id, q["airline"], q["price"],
              q["flights"], q["depart"], q["arrive"], int(q["preferred"]), int(q["is_overall_min"]))
             for q in quotes])
        self.db.commit()
        return row

    def quote_history(self, search_id: str, airline: str | None, before: datetime | None = None,
                      preferred: int = 0) -> list[sqlite3.Row]:
        """All quotes for a search (+airline, or overall-cheapest if airline is None), oldest first."""
        sql = "SELECT * FROM quotes WHERE search_id=? AND preferred=?"
        args: list = [search_id, preferred]
        if airline is None:
            sql += " AND is_overall_min=1"
        else:
            sql += " AND airline=?"
            args.append(airline)
        if before is not None:
            sql += " AND ts < ?"
            args.append(before.isoformat())
        return self.db.execute(sql + " ORDER BY ts", args).fetchall()

    def latest_quotes(self, search_id: str, preferred: int = 0, at_or_before: datetime | None = None):
        """Quotes from the most recent search for this id (optionally as of a time)."""
        sql = "SELECT id FROM searches WHERE search_id=?"
        args: list = [search_id]
        if at_or_before is not None:
            sql += " AND ts <= ?"
            args.append(at_or_before.isoformat())
        row = self.db.execute(sql + " ORDER BY ts DESC LIMIT 1", args).fetchone()
        if not row:
            return []
        return self.db.execute("SELECT * FROM quotes WHERE search_row=? AND preferred=? ORDER BY price",
                               (row["id"], preferred)).fetchall()

    def latest_search(self, search_id: str):
        return self.db.execute("SELECT * FROM searches WHERE search_id=? ORDER BY ts DESC LIMIT 1",
                               (search_id,)).fetchone()

    # ---- alerts ---------------------------------------------------------
    def last_alert(self, kind: str, search_id: str | None, airline: str | None):
        return self.db.execute(
            "SELECT * FROM alerts WHERE kind=? AND search_id IS ? AND airline IS ? ORDER BY ts DESC LIMIT 1",
            (kind, search_id, airline)).fetchone()

    def save_alert(self, ts: datetime, kind: str, search_id, airline, price, reasons: str):
        self.db.execute("INSERT INTO alerts (ts, kind, search_id, airline, price, reasons) VALUES (?,?,?,?,?,?)",
                        (ts.isoformat(), kind, search_id, airline, price, reasons))
        self.db.commit()

    def alerts(self, search_id: str | None = None):
        if search_id is None:
            return self.db.execute("SELECT * FROM alerts ORDER BY ts").fetchall()
        return self.db.execute("SELECT * FROM alerts WHERE search_id=? ORDER BY ts", (search_id,)).fetchall()

    # ---- export ---------------------------------------------------------
    def export_csv(self, folder: str | Path):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        exports = {
            "prices.csv": "SELECT q.ts, q.day, q.search_id, q.airline, q.price, q.flights, q.depart,"
                          " q.arrive, q.preferred, q.is_overall_min, s.price_level, s.typical_low,"
                          " s.typical_high FROM quotes q JOIN searches s ON s.id = q.search_row ORDER BY q.ts, q.search_id, q.preferred, q.price",
            "runs.csv": "SELECT * FROM runs ORDER BY id",
            "alerts.csv": "SELECT * FROM alerts ORDER BY id",
        }
        for name, sql in exports.items():
            cur = self.db.execute(sql)
            with open(folder / name, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow([d[0] for d in cur.description])
                w.writerows(cur.fetchall())
