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
                self.assertEqual(db.scan_stats(cidr), (1, 1, 0))
                self.assertEqual(db.last_success_date(cidr), today.isoformat())

                db.record_failed_run(
                    cidr=cidr,
                    scan_date=(today + timedelta(days=1)).isoformat(),
                    duration_ms=50,
                    error="simulated",
                )
                self.assertEqual(db.scan_stats(cidr), (2, 1, 1))
                recent = db.recent_scans(cidr, limit=2)
                self.assertEqual(recent[0]["status"], "failed")
                self.assertEqual(recent[1]["status"], "success")

                counts = db.daily_online_counts(today, today)
                self.assertEqual(counts, [(today.isoformat(), 1)])
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
