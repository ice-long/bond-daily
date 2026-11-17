from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

import pandas as pd


class Store:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "bonds.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS catalog (
                    code TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, started_at TEXT NOT NULL, fetched_at TEXT,
                    finished_at TEXT, source_date TEXT, source_time TEXT,
                    status TEXT NOT NULL, error TEXT, raw_path TEXT,
                    raw_count INTEGER, target_count INTEGER, excluded_count INTEGER,
                    unknown_count INTEGER, missing_json TEXT, warnings_json TEXT
                );
                CREATE TABLE IF NOT EXISTS quotes (
                    run_id TEXT NOT NULL REFERENCES runs(id), code TEXT NOT NULL,
                    name TEXT NOT NULL, category TEXT NOT NULL, issuer TEXT,
                    term_raw TEXT, term_years REAL, net_price REAL,
                    latest_yield REAL, weighted_yield REAL, change_bp REAL, volume REAL,
                    record_time TEXT, quote_state TEXT NOT NULL,
                    PRIMARY KEY (run_id, code)
                );
                CREATE INDEX IF NOT EXISTS idx_quote_code ON quotes(code);
                CREATE TABLE IF NOT EXISTS unclassified (
                    run_id TEXT NOT NULL REFERENCES runs(id), code TEXT NOT NULL,
                    name TEXT, reason TEXT, raw_json TEXT,
                    PRIMARY KEY (run_id, code)
                );
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def catalog(self) -> tuple[dict[str, dict], str | None]:
        with self.connect() as conn:
            records = {row["code"]: json.loads(row["payload"])
                       for row in conn.execute("SELECT * FROM catalog")}
            stamp = conn.execute("SELECT value FROM metadata WHERE key='catalog_date'").fetchone()
        return records, stamp[0] if stamp else None

    def save_catalog(self, items: list[dict], date: str):
        with self.connect() as conn:
            conn.execute("DELETE FROM catalog")
            conn.executemany("INSERT INTO catalog VALUES (?,?)",
                             [(str(row["bondCode"]), json.dumps(row, ensure_ascii=False)) for row in items])
            conn.execute("INSERT OR REPLACE INTO metadata VALUES ('catalog_date', ?)", (date,))

    def start_run(self, run_id, started_at):
        with self.connect() as conn:
            conn.execute("INSERT INTO runs(id,started_at,status) VALUES (?,?,'running')", (run_id, started_at))

    def mark_raw(self, run_id, fetched_at, path):
        with self.connect() as conn:
            conn.execute("UPDATE runs SET fetched_at=?,raw_path=? WHERE id=?", (fetched_at, path, run_id))

    def fail_run(self, run_id, message, finished_at):
        with self.connect() as conn:
            conn.execute("UPDATE runs SET status='failed', error=?,finished_at=? WHERE id=?",
                         (message, finished_at, run_id))

    def recover_interrupted(self, finished_at):
        with self.connect() as conn:
            conn.execute("UPDATE runs SET status='failed',error='上次采集进程中断，未发布该批次',finished_at=? WHERE status='running'", (finished_at,))

    def finish_run(self, run_id, normalized: dict, warnings: list[str], finished_at: str):
        fields = ("code", "name", "category", "issuer", "term_raw", "term_years",
                  "net_price", "latest_yield", "weighted_yield", "change_bp", "volume",
                  "record_time", "quote_state")
        with self.connect() as conn:
            conn.executemany(f"INSERT INTO quotes (run_id,{','.join(fields)}) VALUES ({','.join(['?'] * 14)})",
                             [(run_id, *(row[field] for field in fields)) for row in normalized["records"]])
            conn.executemany("INSERT INTO unclassified VALUES (?,?,?,?,?)", [
                (run_id, row["code"], row["name"], row["reason"], json.dumps(row["raw"], ensure_ascii=False))
                for row in normalized["unknown"]])
            conn.execute("""UPDATE runs SET status='success',finished_at=?,source_date=?,source_time=?,
                         raw_count=?,target_count=?,excluded_count=?,unknown_count=?,missing_json=?,warnings_json=?
                         WHERE id=?""", (finished_at, normalized["source_date"], normalized["source_time"],
                         normalized["raw_count"], len(normalized["records"]), normalized["excluded_count"],
                         len(normalized["unknown"]), json.dumps(normalized["missing"]),
                         json.dumps(warnings, ensure_ascii=False), run_id))

    def dates(self) -> list[str]:
        with self.connect() as conn:
            return [row[0] for row in conn.execute("SELECT DISTINCT source_date FROM runs WHERE status='success' ORDER BY source_date DESC")]

    def daily_run(self, date: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE status='success' AND source_date=? ORDER BY source_time DESC, fetched_at DESC, rowid DESC LIMIT 1", (date,)).fetchone()
            return dict(row) if row else None

    def quotes(self, run_id: str) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query("""SELECT q.*,r.source_date,r.source_time,r.fetched_at
                                     FROM quotes q JOIN runs r ON q.run_id=r.id WHERE run_id=?
                                     ORDER BY q.category,q.code""", conn, params=(run_id,))

    def unknown(self, run_id: str) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query("SELECT code,name,reason FROM unclassified WHERE run_id=?", conn, params=(run_id,))

    def recent_runs(self, limit=30) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query("SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT ?", conn, params=(limit,))

    def history(self, code: str) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query("""
                WITH ranked AS (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY source_date ORDER BY source_time DESC,fetched_at DESC,rowid DESC) AS rank
                    FROM runs WHERE status='success'
                )
                SELECT r.source_date,r.source_time,r.fetched_at,q.* FROM ranked r
                LEFT JOIN quotes q ON q.run_id=r.id AND q.code=?
                WHERE r.rank=1 ORDER BY r.source_date
            """, conn, params=(code,))
