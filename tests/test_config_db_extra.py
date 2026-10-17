from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import ConfigError, load_config, save_config, write_default_config
from db import Database


class ConfigTests(unittest.TestCase):
    def test_write_and_reload_default_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.ini"
            write_default_config(path)
            cfg = load_config(path)
            self.assertEqual(cfg.scan.cidr, "192.168.0.0/24")
            self.assertEqual(cfg.scan.backend, "auto")
            self.assertEqual(cfg.unused_days, (7, 15))

    def test_save_config_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.ini"
            write_default_config(path)
            cfg = save_config(
                path,
                cidr="10.10.0.0/24",
                timeout_ms="900",
                retries="2",
                db_path="store/data.db",
                report_dir="out",
                unused_days="3,7,30",
                log_dir="var/log",
                backend="sendarp",
            )
            self.assertEqual(cfg.scan.cidr, "10.10.0.0/24")
            self.assertEqual(cfg.scan.backend, "sendarp")
            self.assertEqual(cfg.scan.timeout_ms, 900)
            self.assertEqual(cfg.scan.retries, 2)
            self.assertEqual(cfg.unused_days, (3, 7, 30))
            self.assertEqual(
                cfg.db_path, (path.parent / "store" / "data.db").resolve()
            )

    def test_save_config_multiple_cidrs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.ini"
            write_default_config(path)
            cfg = save_config(
                path,
                cidr="10.10.0.0/24\n10.10.1.0/24, 10.10.2.0/24",
                timeout_ms="900",
                retries="1",
                db_path="data/db",
                report_dir="reports",
                unused_days="7,15",
                log_dir="logs",
            )
            self.assertEqual(
                cfg.scan.cidrs,
                ("10.10.0.0/24", "10.10.1.0/24", "10.10.2.0/24"),
            )
            self.assertEqual(len(cfg.scan.networks), 3)
            loaded = load_config(path)
            self.assertEqual(loaded.scan.cidrs, cfg.scan.cidrs)

    def test_save_config_rejects_overlapping_cidrs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.ini"
            write_default_config(path)
            with self.assertRaises(ConfigError):
                save_config(
                    path,
                    cidr="10.10.0.0/24, 10.10.0.0/25",
                    timeout_ms="1000",
                    retries="0",
                    db_path="data/db",
                    report_dir="reports",
                    unused_days="7",
                    log_dir="logs",
                )

    def test_save_config_rejects_bad_cidr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.ini"
            with self.assertRaises(ConfigError):
                save_config(
                    path,
                    cidr="10.10.0.1/24",
                    timeout_ms="1000",
                    retries="0",
                    db_path="data/db",
                    report_dir="reports",
                    unused_days="7",
                    log_dir="logs",
                    backend="auto",
                )


class DatabaseQueryTests(unittest.TestCase):
    def test_dashboard_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            today = date(2026, 10, 17)
            db = Database(root / "data" / "test.db")
            try:
                cidr = "192.168.0.0/24"
                db.initialize_inventory(cidr, ["192.168.0.2", "192.168.0.3"])
                db.apply_scan_success(
                    cidr=cidr,
                    scan_date=today.isoformat(),
                    online_ips=["192.168.0.2"],
                    duration_ms=120,
                    scanned_at=f"{today.isoformat()} 23:30:00",
                )
                self.assertEqual(db.count_online(cidr), 1)
                self.assertEqual(db.scan_stats(), (1, 1, 0))
                self.assertEqual(db.last_success_date(), today.isoformat())

                db.record_failed_run(
                    cidr=cidr,
                    scan_date=(today + timedelta(days=1)).isoformat(),
                    duration_ms=50,
                    error="simulated",
                )
                self.assertEqual(db.scan_stats(), (2, 1, 1))
                recent = db.recent_scans(limit=2)
                self.assertEqual(recent[0]["status"], "failed")
                self.assertEqual(recent[1]["status"], "success")

                counts = db.daily_online_counts(today, today)
                self.assertEqual(counts, [(today.isoformat(), 1)])
            finally:
                db.close()

    def test_multi_cidr_and_mac(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            today = date(2026, 10, 17)
            db = Database(root / "data" / "test.db")
            try:
                cidr_a = "192.168.0.0/24"
                cidr_b = "192.168.1.0/24"
                db.initialize_inventory(cidr_a, ["192.168.0.2", "192.168.0.3"])
                db.initialize_inventory(cidr_b, ["192.168.1.2", "192.168.1.3"])
                db.apply_scan_success_multi(
                    groups=[
                        (cidr_a, ["192.168.0.2"]),
                        (cidr_b, ["192.168.1.3"]),
                    ],
                    cidr_label=f"{cidr_a}, {cidr_b}",
                    scan_date=today.isoformat(),
                    duration_ms=200,
                    mac_map={
                        "192.168.0.2": "AA:BB:CC:DD:EE:01",
                        "192.168.1.3": "AA:BB:CC:DD:EE:02",
                    },
                )
                self.assertEqual(db.count_inventory([cidr_a, cidr_b]), 4)
                self.assertEqual(db.count_online([cidr_a, cidr_b]), 2)
                rows = {
                    row["ip_address"]: row
                    for row in db.fetch_inventory([cidr_a, cidr_b])
                }
                self.assertEqual(rows["192.168.0.2"]["mac_address"], "AA:BB:CC:DD:EE:01")
                self.assertEqual(rows["192.168.1.3"]["mac_address"], "AA:BB:CC:DD:EE:02")
                self.assertIsNone(rows["192.168.0.3"]["mac_address"])

                run = db.last_scan()
                self.assertEqual(run["online_count"], 2)
                self.assertEqual(run["cidr"], f"{cidr_a}, {cidr_b}")

                unused = db.fetch_unused([cidr_a, cidr_b], today, 7)
                self.assertEqual(
                    {row["ip_address"] for row in unused},
                    {"192.168.0.3", "192.168.1.2"},
                )
            finally:
                db.close()

    def test_old_database_gets_mac_column(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "data" / "old.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path)
            conn.execute(
                """
                CREATE TABLE ip_inventory (
                    ip_address TEXT PRIMARY KEY,
                    cidr TEXT,
                    first_seen_date TEXT,
                    last_seen_date TEXT,
                    last_status TEXT,
                    updated_at TEXT
                )
                """
            )
            conn.commit()
            conn.close()

            db = Database(path)
            try:
                columns = {
                    row["name"]
                    for row in db.conn.execute("PRAGMA table_info(ip_inventory)")
                }
                self.assertIn("mac_address", columns)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
