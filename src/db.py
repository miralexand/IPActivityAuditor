from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

_MAX_ERROR_LEN = 2000

SCHEMA = """
CREATE TABLE IF NOT EXISTS ip_inventory (
    ip_address TEXT PRIMARY KEY,
    cidr TEXT,
    first_seen_date TEXT,
    last_seen_date TEXT,
    last_status TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ip_inventory_cidr ON ip_inventory(cidr);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date TEXT NOT NULL,
    cidr TEXT NOT NULL,
    online_count INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS daily_online (
    scan_date TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    scanned_at TEXT NOT NULL,
    PRIMARY KEY (scan_date, ip_address)
);
"""

_ONLINE_UPSERT = """
INSERT INTO ip_inventory (
    ip_address, cidr, first_seen_date, last_seen_date, last_status, updated_at
) VALUES (?, ?, ?, ?, 'online', ?)
ON CONFLICT(ip_address) DO UPDATE SET
    cidr = excluded.cidr,
    last_seen_date = excluded.last_seen_date,
    first_seen_date = COALESCE(ip_inventory.first_seen_date, excluded.first_seen_date),
    last_status = 'online',
    updated_at = excluded.updated_at
"""


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.init_schema()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(SCHEMA)

    def initialize_inventory(
        self,
        cidr: str,
        ip_addresses: Iterable[str],
        updated_at: str | None = None,
    ) -> int:
        updated_at = updated_at or _now_text()
        rows = [(ip, cidr, updated_at) for ip in ip_addresses]
        before = self.conn.total_changes
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO ip_inventory (
                    ip_address, cidr, first_seen_date, last_seen_date, last_status, updated_at
                ) VALUES (?, ?, NULL, NULL, NULL, ?)
                ON CONFLICT(ip_address) DO UPDATE SET cidr = excluded.cidr
                """,
                rows,
            )
        return self.conn.total_changes - before

    def apply_scan_success(
        self,
        *,
        cidr: str,
        scan_date: str,
        online_ips: Sequence[str],
        duration_ms: int,
        scanned_at: str | None = None,
    ) -> None:
        scanned_at = scanned_at or _now_text()
        online = sorted(set(online_ips))
        with self.conn:
            self.conn.execute(
                """
                UPDATE ip_inventory
                SET last_status = 'offline', updated_at = ?
                WHERE cidr = ? AND (last_status IS NULL OR last_status <> 'offline')
                """,
                (scanned_at, cidr),
            )
            self.conn.executemany(
                _ONLINE_UPSERT,
                [(ip, cidr, scan_date, scan_date, scanned_at) for ip in online],
            )
            self.conn.executemany(
                "INSERT OR REPLACE INTO daily_online (scan_date, ip_address, scanned_at) VALUES (?, ?, ?)",
                [(scan_date, ip, scanned_at) for ip in online],
            )
            self.conn.execute(
                """
                INSERT INTO scan_runs (scan_date, cidr, online_count, duration_ms, status, error)
                VALUES (?, ?, ?, ?, 'success', NULL)
                """,
                (scan_date, cidr, len(online), duration_ms),
            )

    def record_failed_run(
        self,
        *,
        cidr: str,
        scan_date: str,
        duration_ms: int,
        error: str,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO scan_runs (scan_date, cidr, online_count, duration_ms, status, error)
                VALUES (?, ?, 0, ?, 'failed', ?)
                """,
                (scan_date, cidr, duration_ms, (error or "")[:_MAX_ERROR_LEN]),
            )

    def fetch_unused(self, cidr: str, scan_date: date, days: int) -> list[sqlite3.Row]:
        cutoff = (scan_date - timedelta(days=days)).isoformat()
        cursor = self.conn.execute(
            """
            SELECT ip_address, last_seen_date
            FROM ip_inventory
            WHERE cidr = ? AND (last_seen_date IS NULL OR last_seen_date <= ?)
            ORDER BY ip_address
            """,
            (cidr, cutoff),
        )
        return cursor.fetchall()

    def fetch_inventory(self, cidr: str) -> list[sqlite3.Row]:
        cursor = self.conn.execute(
            """
            SELECT ip_address, cidr, first_seen_date, last_seen_date, last_status, updated_at
            FROM ip_inventory
            WHERE cidr = ?
            ORDER BY ip_address
            """,
            (cidr,),
        )
        return cursor.fetchall()

    def count_inventory(self, cidr: str) -> int:
        cursor = self.conn.execute(
            "SELECT COUNT(*) FROM ip_inventory WHERE cidr = ?", (cidr,)
        )
        return int(cursor.fetchone()[0])

    def count_online(self, cidr: str) -> int:
        cursor = self.conn.execute(
            "SELECT COUNT(*) FROM ip_inventory WHERE cidr = ? AND last_status = 'online'",
            (cidr,),
        )
        return int(cursor.fetchone()[0])

    def recent_scans(
        self, cidr: str | None = None, limit: int = 50
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM scan_runs"
        params: list[object] = []
        if cidr:
            sql += " WHERE cidr = ?"
            params.append(cidr)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(int(limit), 1))
        return self.conn.execute(sql, params).fetchall()

    def last_scan(self, cidr: str | None = None) -> sqlite3.Row | None:
        rows = self.recent_scans(cidr=cidr, limit=1)
        return rows[0] if rows else None

    def last_success_date(self, cidr: str | None = None) -> str | None:
        if cidr:
            row = self.conn.execute(
                "SELECT scan_date FROM scan_runs WHERE status = 'success' AND cidr = ? "
                "ORDER BY id DESC LIMIT 1",
                (cidr,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT scan_date FROM scan_runs WHERE status = 'success' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return str(row["scan_date"]) if row else None

    def scan_stats(self, cidr: str | None = None) -> tuple[int, int, int]:
        sql = "SELECT status, COUNT(*) AS amount FROM scan_runs"
        params: tuple[object, ...] = ()
        if cidr:
            sql += " WHERE cidr = ?"
            params = (cidr,)
        sql += " GROUP BY status"
        counts = {
            str(row["status"]): int(row["amount"])
            for row in self.conn.execute(sql, params).fetchall()
        }
        success = counts.get("success", 0)
        failed = counts.get("failed", 0)
        return success + failed, success, failed

    def daily_online_counts(
        self, start_date: date, end_date: date
    ) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            """
            SELECT scan_date, COUNT(*) AS amount
            FROM daily_online
            WHERE scan_date >= ? AND scan_date <= ?
            GROUP BY scan_date
            ORDER BY scan_date
            """,
            (start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
        return [(str(row["scan_date"]), int(row["amount"])) for row in rows]
