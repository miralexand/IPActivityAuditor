from __future__ import annotations

import configparser
import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class ConfigError(ValueError):
    pass


BACKENDS = ("auto", "sendarp", "scapy")


DEFAULT_CONFIG_TEXT = """; IP Activity Auditor 配置文件
; 相对路径以本配置文件所在目录为基准
; 仅允许扫描本文件中的 CIDR，请确保已获得网络扫描授权
; 可填写多个网段，使用逗号、分号或换行分隔

[scan]
cidr = 192.168.0.0/24
backend = auto
timeout_ms = 1500
retries = 1

[storage]
db_path = data/ip_activity.db

[report]
report_dir = reports
unused_days = 7,15

[log]
log_dir = logs
"""


@dataclass(frozen=True)
class ScanConfig:
    cidrs: tuple[str, ...]
    networks: tuple[ipaddress.IPv4Network, ...]
    timeout_ms: int
    retries: int
    backend: str = "auto"

    @property
    def cidr(self) -> str:
        return ", ".join(self.cidrs)

    @property
    def network(self) -> ipaddress.IPv4Network:
        return self.networks[0]


@dataclass(frozen=True)
class AppConfig:
    path: Path
    scan: ScanConfig
    db_path: Path
    report_dir: Path
    unused_days: tuple[int, ...]
    log_dir: Path


def parse_days(value: str) -> tuple[int, ...]:
    days: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            day = int(part)
        except ValueError as exc:
            raise ConfigError(f"无效的天数：{part!r}") from exc
        if day <= 0:
            raise ConfigError(f"天数必须为正整数：{part!r}")
        if day not in days:
            days.append(day)
    if not days:
        raise ConfigError("未使用天数不能为空")
    return tuple(days)


def _required(parser: configparser.ConfigParser, section: str, option: str) -> str:
    if not parser.has_option(section, option):
        raise ConfigError(f"缺少配置项 [{section}] {option}")
    value = parser.get(section, option).strip()
    if not value:
        raise ConfigError(f"配置项 [{section}] {option} 不能为空")
    return value


def _int_required(
    parser: configparser.ConfigParser,
    section: str,
    option: str,
    minimum: int,
) -> int:
    raw = _required(parser, section, option)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"配置项 [{section}] {option} 必须是整数：{raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"配置项 [{section}] {option} 不能小于 {minimum}")
    return value


def _resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def parse_cidrs(value: str) -> tuple[ipaddress.IPv4Network, ...]:
    parts = [part for part in re.split(r"[,;\s]+", value.strip()) if part]
    if not parts:
        raise ConfigError("扫描网段不能为空")
    networks: list[ipaddress.IPv4Network] = []
    for part in parts:
        try:
            network = ipaddress.ip_network(part, strict=True)
        except ValueError as exc:
            raise ConfigError(
                f"cidr 不是合法网段（不得包含主机位）：{part!r}"
            ) from exc
        if network.version != 4:
            raise ConfigError(f"cidr 仅支持 IPv4：{part!r}")
        if network in networks:
            continue
        for existing in networks:
            if network.overlaps(existing):
                raise ConfigError(
                    f"扫描网段不能重叠：{existing} 与 {network}"
                )
        networks.append(network)
    return tuple(networks)


def write_default_config(path: str | Path, *, overwrite: bool = False) -> Path:
    config_path = Path(path).expanduser().resolve()
    if config_path.exists() and not overwrite:
        return config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8", newline="\n")
    return config_path


def save_config(
    path: str | Path,
    *,
    cidr: str,
    timeout_ms: str | int,
    retries: str | int,
    db_path: str,
    report_dir: str,
    unused_days: str,
    log_dir: str,
    backend: str = "auto",
) -> AppConfig:
    config_path = Path(path).expanduser().resolve()

    networks = parse_cidrs(cidr)
    cidr_text = ", ".join(str(network) for network in networks)

    backend = str(backend).strip().lower() or "auto"
    if backend not in BACKENDS:
        raise ConfigError(f"backend 必须是 auto、sendarp 或 scapy：{backend!r}")

    try:
        timeout = int(str(timeout_ms).strip())
    except ValueError as exc:
        raise ConfigError(f"timeout_ms 必须是整数：{timeout_ms!r}") from exc
    if timeout < 1:
        raise ConfigError("timeout_ms 不能小于 1")

    try:
        retry_count = int(str(retries).strip())
    except ValueError as exc:
        raise ConfigError(f"retries 必须是整数：{retries!r}") from exc
    if retry_count < 0:
        raise ConfigError("retries 不能为负数")

    days = parse_days(str(unused_days))
    for name, value in (
        ("db_path", db_path),
        ("report_dir", report_dir),
        ("log_dir", log_dir),
    ):
        if not str(value).strip():
            raise ConfigError(f"{name} 不能为空")

    text = f"""; IP Activity Auditor 配置文件
; 相对路径以本配置文件所在目录为基准
; 仅允许扫描本文件中的 CIDR，请确保已获得网络扫描授权
; 最后保存时间：{datetime.now():%Y-%m-%d %H:%M:%S}

[scan]
cidr = {cidr_text}
backend = {backend}
timeout_ms = {timeout}
retries = {retry_count}

[storage]
db_path = {str(db_path).strip()}

[report]
report_dir = {str(report_dir).strip()}
unused_days = {",".join(str(day) for day in days)}

[log]
log_dir = {str(log_dir).strip()}
"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8", newline="\n")
    return load_config(config_path)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"配置文件不存在：{config_path}")

    parser = configparser.ConfigParser(interpolation=None)
    try:
        read_files = parser.read(config_path, encoding="utf-8-sig")
    except (configparser.Error, OSError, UnicodeError) as exc:
        raise ConfigError(f"读取配置文件失败：{exc}") from exc
    if not read_files:
        raise ConfigError(f"配置文件无法读取：{config_path}")

    for section in ("scan", "storage", "report", "log"):
        if not parser.has_section(section):
            raise ConfigError(f"缺少配置节 [{section}]")

    cidr = _required(parser, "scan", "cidr")
    try:
        networks = parse_cidrs(cidr)
    except ConfigError as exc:
        raise ConfigError(f"[scan] {exc}") from exc

    backend = parser.get("scan", "backend", fallback="auto").strip().lower() or "auto"
    if backend not in BACKENDS:
        raise ConfigError(f"[scan] backend 必须是 auto、sendarp 或 scapy：{backend!r}")

    timeout_ms = _int_required(parser, "scan", "timeout_ms", 1)
    retries = _int_required(parser, "scan", "retries", 0)
    unused_days = parse_days(_required(parser, "report", "unused_days"))

    base_dir = config_path.parent
    return AppConfig(
        path=config_path,
        scan=ScanConfig(
            cidrs=tuple(str(network) for network in networks),
            networks=networks,
            timeout_ms=timeout_ms,
            retries=retries,
            backend=backend,
        ),
        db_path=_resolve_path(_required(parser, "storage", "db_path"), base_dir),
        report_dir=_resolve_path(_required(parser, "report", "report_dir"), base_dir),
        unused_days=unused_days,
        log_dir=_resolve_path(_required(parser, "log", "log_dir"), base_dir),
    )
