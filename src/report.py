from __future__ import annotations

import csv
import ipaddress
from datetime import date
from pathlib import Path

from db import Database

CSV_HEADER = ["ip_address", "last_seen_date", "days_unused", "status"]


def natural_ip_key(value: str) -> tuple[int, object]:
    try:
        return (0, int(ipaddress.IPv4Address(value)))
    except ValueError:
        return (1, value)


def write_unused_report(
    db: Database,
    report_dir: Path,
    cidr: str,
    scan_date: date,
    days: int,
) -> tuple[Path, int]:
    rows = db.fetch_unused(cidr, scan_date, days)
    records: list[tuple[str, str, str, str]] = []
    for row in rows:
        last_seen = row["last_seen_date"]
        if last_seen is None:
            days_unused = ""
            status = "从未活跃"
        else:
            days_unused = str(max((scan_date - date.fromisoformat(last_seen)).days, 0))
            status = "未使用"
        records.append((row["ip_address"], last_seen or "", days_unused, status))

    records.sort(key=lambda item: natural_ip_key(item[0]))

    report_dir.mkdir(parents=True, exist_ok=True)
    output = report_dir / f"IP未使用清单_{days}天_{scan_date.strftime('%Y%m%d')}.csv"
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        writer.writerows(records)
    return output, len(records)


def generate_reports(
    db: Database,
    report_dir: Path,
    cidr: str,
    scan_date: date,
    days_list: tuple[int, ...] | list[int],
) -> list[tuple[Path, int]]:
    return [
        write_unused_report(db, report_dir, cidr, scan_date, days)
        for days in days_list
    ]
