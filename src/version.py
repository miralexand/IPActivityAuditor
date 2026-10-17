from __future__ import annotations

APP_NAME = "IPActivityAuditor"
APP_DISPLAY_NAME = "IP 活跃度审计"
APP_VERSION = "0.5.0"
APP_PUBLISHER = "IP Activity Auditor"
APP_DESCRIPTION = "内网 IP 活跃度审计工具"


def version_tuple() -> tuple[int, int, int, int]:
    parts = [int(part) for part in APP_VERSION.split(".")]
    return tuple((parts + [0, 0, 0])[:4])  # type: ignore[return-value]
