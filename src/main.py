from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

from apputil import (
    activate_existing_instance,
    app_base_dir,
    attach_parent_console,
    ensure_single_instance,
    setup_logging,
)
from config import AppConfig, ConfigError, load_config, parse_days
from db import Database
from report import generate_reports
from scanner import ScanError, is_admin, resolve_backend, scan
from version import APP_NAME, APP_VERSION

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_ADMIN = 2
EXIT_CONFIG = 3
EXIT_SCAN_FAILED = 4
EXIT_REPORT_FAILED = 5


def default_config_path(base_dir: Path) -> Path:
    local = base_dir / "config.ini"
    if local.exists():
        return local
    program_data = os.environ.get("PROGRAMDATA")
    if program_data:
        installed = Path(program_data) / "IPActivityAuditor" / "config.ini"
        if installed.exists():
            return installed
    return local


def _days_arg(value: str) -> tuple[int, ...]:
    try:
        return parse_days(value)
    except ConfigError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="IPActivityAuditor",
        description="内网 IP 活跃度审计工具（仅限授权网段使用）",
    )
    parser.add_argument("--config", default=None, help="配置文件路径，默认 exe 同目录 config.ini")
    parser.add_argument("--init", action="store_true", help="初始化库存表")
    parser.add_argument("--scan", action="store_true", help="执行一次 ARP 扫描")
    parser.add_argument("--report", action="store_true", help="生成未使用 IP 报表")
    parser.add_argument(
        "--days",
        type=_days_arg,
        default=None,
        help="报表天数，逗号分隔，如 7,15；默认读取配置",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="打开图形界面（不带任何参数启动时默认打开图形界面）",
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        help="启动后隐藏到系统托盘静默运行",
    )
    parser.add_argument(
        "--new-instance",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {APP_VERSION}",
    )
    return parser


def _record_failure(
    db: Database,
    cfg: AppConfig,
    scan_date: date,
    duration_ms: int,
    message: str,
    logger: logging.Logger,
) -> None:
    try:
        db.record_failed_run(
            cidr=cfg.scan.cidr,
            scan_date=scan_date.isoformat(),
            duration_ms=duration_ms,
            error=message,
        )
    except Exception:
        logger.exception("写入失败扫描记录时出错")


def _run_init(db: Database, cfg: AppConfig, logger: logging.Logger) -> None:
    inserted = 0
    for network in cfg.scan.networks:
        addresses = (str(ip) for ip in network.hosts())
        inserted += db.initialize_inventory(str(network), addresses)
    total = db.count_inventory(cfg.scan.cidrs)
    logger.info(
        "库存初始化完成：网段 %s，新增 %d 个 IP，当前共 %d 个",
        cfg.scan.cidr,
        inserted,
        total,
    )


def _run_scan(
    db: Database,
    cfg: AppConfig,
    scan_date: date,
    logger: logging.Logger,
) -> bool:
    started = time.perf_counter()
    logger.info(
        "开始扫描：%s（后端 %s，超时 %d ms，重试 %d 次）",
        cfg.scan.cidr,
        cfg.scan.backend,
        cfg.scan.timeout_ms,
        cfg.scan.retries,
    )

    try:
        result = scan(cfg.scan)
    except ScanError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        _record_failure(db, cfg, scan_date, duration_ms, str(exc), logger)
        logger.error("扫描失败，本次未更新任何 IP 的活跃日期：%s", exc)
        return False
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.exception("扫描出现未预期异常")
        _record_failure(db, cfg, scan_date, duration_ms, str(exc), logger)
        logger.error("扫描失败，本次未更新任何 IP 的活跃日期：%s", exc)
        return False

    try:
        groups: list[tuple[str, list[str]]] = []
        for network in cfg.scan.networks:
            cidr = str(network)
            db.initialize_inventory(
                cidr, (str(ip) for ip in network.hosts())
            )
            online = [
                ip
                for ip in result.online_ips
                if ipaddress.ip_address(ip) in network
            ]
            groups.append((cidr, online))
        db.apply_scan_success_multi(
            groups=groups,
            cidr_label=cfg.scan.cidr,
            scan_date=scan_date.isoformat(),
            duration_ms=result.duration_ms,
            mac_map=result.mac_map,
        )
    except Exception as exc:
        logger.exception("保存扫描结果失败")
        _record_failure(db, cfg, scan_date, result.duration_ms, f"保存扫描结果失败：{exc}", logger)
        return False

    logger.info(
        "扫描完成：后端 %s，网卡 %s，在线 %d 个 IP，扫描耗时 %d ms",
        result.backend,
        result.iface,
        len(result.online_ips),
        result.duration_ms,
    )
    if not result.online_ips:
        logger.warning("本次扫描没有任何 ARP 响应，请检查网卡、扫描后端与网段配置")
    return True


def _run_reports(
    db: Database,
    cfg: AppConfig,
    scan_date: date,
    days_list: tuple[int, ...],
    logger: logging.Logger,
) -> int:
    try:
        results = generate_reports(
            db, cfg.report_dir, cfg.scan.cidrs, scan_date, days_list
        )
    except Exception:
        logger.exception("生成报表失败")
        return EXIT_REPORT_FAILED

    for path, count in results:
        logger.info("报表已生成：%s（%d 条）", path, count)
    return EXIT_OK


def _execute(
    args: argparse.Namespace,
    actions: set[str],
    cfg: AppConfig,
    logger: logging.Logger,
) -> int:
    logger.info("配置文件：%s", cfg.path)
    logger.info(
        "扫描配置：cidr=%s，backend=%s，timeout_ms=%d，retries=%d",
        cfg.scan.cidr,
        cfg.scan.backend,
        cfg.scan.timeout_ms,
        cfg.scan.retries,
    )
    logger.info("数据库：%s", cfg.db_path)
    logger.info("报表目录：%s", cfg.report_dir)
    logger.info("执行动作：%s", ",".join(sorted(actions)))

    if (
        "scan" in actions
        and resolve_backend(cfg.scan) == "scapy"
        and not is_admin()
    ):
        logger.error("Scapy 扫描后端需要管理员权限，当前进程未提权")
        return EXIT_NO_ADMIN

    scan_date = date.today()
    days_list = args.days if args.days is not None else cfg.unused_days

    try:
        db = Database(cfg.db_path)
    except Exception:
        logger.exception("初始化数据库失败：%s", cfg.db_path)
        return EXIT_ERROR

    with db:
        try:
            if "init" in actions:
                _run_init(db, cfg, logger)

            if "scan" in actions and not _run_scan(db, cfg, scan_date, logger):
                return EXIT_SCAN_FAILED

            if "report" in actions:
                if "scan" not in actions and db.count_inventory(cfg.scan.cidrs) == 0:
                    logger.warning("库存为空，请先运行 --init 或 --scan")
                code = _run_reports(db, cfg, scan_date, days_list, logger)
                if code != EXIT_OK:
                    return code
        except Exception:
            logger.exception("执行过程中出现未处理异常")
            return EXIT_ERROR

    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and "--gui" not in raw_args:
        attach_parent_console()

    parser = build_parser()
    args = parser.parse_args(raw_args)

    base_dir = app_base_dir()
    config_path = (
        Path(args.config).expanduser()
        if args.config
        else default_config_path(base_dir)
    )

    if args.gui or args.tray or not raw_args:
        if args.new_instance:
            for _attempt in range(50):
                if ensure_single_instance():
                    break
                time.sleep(0.1)
        elif not ensure_single_instance():
            activate_existing_instance()
            return EXIT_OK
        try:
            from gui import run_gui
        except Exception as exc:
            print(f"无法启动图形界面：{exc}", file=sys.stderr)
            return EXIT_ERROR
        return run_gui(
            config_path, base_dir=base_dir, start_hidden=bool(args.tray)
        )

    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return EXIT_CONFIG

    try:
        logger = setup_logging(cfg.log_dir)
    except Exception as exc:
        print(f"初始化日志失败：{exc}", file=sys.stderr)
        return EXIT_ERROR

    actions: set[str] = set()
    if args.init:
        actions.add("init")
    if args.scan:
        actions.add("scan")
    if args.report:
        actions.add("report")
    if not actions:
        actions.update({"scan", "report"})

    logger.info("=== %s %s 启动 ===", APP_NAME, APP_VERSION)
    logger.info("程序目录：%s", base_dir)

    try:
        code = _execute(args, actions, cfg, logger)
    except Exception:
        logger.exception("程序出现未处理异常")
        code = EXIT_ERROR

    logger.info("=== %s 退出，退出码 %d ===", APP_NAME, code)
    return code


if __name__ == "__main__":
    sys.exit(main())
