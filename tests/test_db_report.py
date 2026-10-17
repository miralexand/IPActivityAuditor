from __future__ import annotations

import csv
import io
import ipaddress
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from db import Database
from report import generate_reports, natural_ip_key


class DbReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db = Database(self.root / "data" / "test.db")
        self.cidr = "192.168.0.0/24"
        self.today = date(2026, 10, 17)
        self.ips = [str(ip) for ip in ipaddress.ip_network(self.cidr).hosts()]
        self.db.initialize_inventory(self.cidr, self.ips)

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def set_last_seen(self, ip: str, day: date) -> None:
        with self.db.conn:
            self.db.conn.execute(
                "UPDATE ip_inventory SET last_seen_date = ?, last_status = 'offline' WHERE ip_address = ?",
                (day.isoformat(), ip),
            )

    def test_init_creates_all_hosts_without_last_seen(self) -> None:
        rows = self.db.fetch_inventory(self.cidr)
        self.assertEqual(len(rows), len(self.ips))
        self.assertTrue(all(row["last_seen_date"] is None for row in rows))
        self.assertNotIn("192.168.0.0", {row["ip_address"] for row in rows})
        self.assertNotIn("192.168.0.255", {row["ip_address"] for row in rows})

    def test_successful_scan_updates_online_only(self) -> None:
        online = ["192.168.0.2", "192.168.0.10"]
        self.db.apply_scan_success(
            cidr=self.cidr,
            scan_date=self.today.isoformat(),
            online_ips=online,
            duration_ms=123,
        )
        rows = {row["ip_address"]: row for row in self.db.fetch_inventory(self.cidr)}

        for ip in online:
            self.assertEqual(rows[ip]["last_seen_date"], self.today.isoformat())
            self.assertEqual(rows[ip]["first_seen_date"], self.today.isoformat())
            self.assertEqual(rows[ip]["last_status"], "online")

        self.assertIsNone(rows["192.168.0.3"]["last_seen_date"])
        self.assertEqual(rows["192.168.0.3"]["last_status"], "offline")

        run = self.db.conn.execute("SELECT * FROM scan_runs").fetchone()
        self.assertEqual(run["status"], "success")
        self.assertEqual(run["online_count"], 2)
        daily = self.db.conn.execute("SELECT COUNT(*) FROM daily_online").fetchone()[0]
        self.assertEqual(daily, 2)

    def test_failed_scan_does_not_modify_last_seen(self) -> None:
        old_day = self.today - timedelta(days=10)
        self.set_last_seen("192.168.0.2", old_day)
        self.db.record_failed_run(
            cidr=self.cidr,
            scan_date=self.today.isoformat(),
            duration_ms=50,
            error="simulated failure",
        )
        row = self.db.conn.execute(
            "SELECT last_seen_date FROM ip_inventory WHERE ip_address = '192.168.0.2'"
        ).fetchone()
        self.assertEqual(row["last_seen_date"], old_day.isoformat())

        run = self.db.conn.execute("SELECT * FROM scan_runs").fetchone()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error"], "simulated failure")

    def test_seven_and_fifteen_day_report_rules(self) -> None:
        self.set_last_seen("192.168.0.3", self.today - timedelta(days=8))
        self.set_last_seen("192.168.0.4", self.today - timedelta(days=16))
        self.set_last_seen("192.168.0.5", self.today - timedelta(days=7))

        reports = generate_reports(
            self.db, self.root / "reports", self.cidr, self.today, (7, 15)
        )
        self.assertEqual(len(reports), 2)

        seven_path, seven_count = reports[0]
        fifteen_path, fifteen_count = reports[1]
        seven_text = seven_path.read_text(encoding="utf-8-sig")
        fifteen_text = fifteen_path.read_text(encoding="utf-8-sig")

        def read_rows(text: str) -> list[dict[str, str]]:
            return list(csv.DictReader(io.StringIO(text)))

        seven_rows = read_rows(seven_text)
        fifteen_rows = read_rows(fifteen_text)
        seven_ips = {row["ip_address"] for row in seven_rows}
        fifteen_ips = {row["ip_address"] for row in fifteen_rows}

        self.assertIn("192.168.0.3", seven_ips)
        self.assertIn("192.168.0.4", seven_ips)
        self.assertIn("192.168.0.5", seven_ips)
        self.assertIn("192.168.0.4", fifteen_ips)
        self.assertNotIn("192.168.0.3", fifteen_ips)
        self.assertNotIn("192.168.0.5", fifteen_ips)
        self.assertIn("192.168.0.254", seven_ips)

        never_seen = next(
            row for row in seven_rows if row["ip_address"] == "192.168.0.254"
        )
        self.assertEqual(never_seen["last_seen_date"], "")
        self.assertEqual(never_seen["days_unused"], "")
        self.assertEqual(never_seen["status"], "从未活跃")

        self.assertEqual(
            seven_text.splitlines()[0], "ip_address,last_seen_date,days_unused,status"
        )
        self.assertEqual(seven_count, len(seven_rows))
        self.assertEqual(fifteen_count, len(fifteen_rows))

        raw = seven_path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))

    def test_natural_ip_sorting(self) -> None:
        values = ["192.168.0.10", "192.168.0.2", "192.168.0.254", "192.168.0.1"]
        self.assertEqual(
            sorted(values, key=natural_ip_key),
            ["192.168.0.1", "192.168.0.2", "192.168.0.10", "192.168.0.254"],
        )

    def test_report_filename(self) -> None:
        reports = generate_reports(
            self.db, self.root / "reports", self.cidr, self.today, [7]
        )
        self.assertEqual(
            reports[0][0].name, "IP未使用清单_7天_20261017.csv"
        )


if __name__ == "__main__":
    unittest.main()
