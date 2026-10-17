from __future__ import annotations

import ctypes
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from version import APP_DISPLAY_NAME


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def app_base_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return project_root()


def resource_path(relative: str) -> Path:
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / relative
    return project_root() / relative


def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    file_handler = RotatingFileHandler(
        log_dir / "ip_activity_auditor.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if sys.stdout is not None:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


def attach_parent_console() -> bool:
    """Reconnect stdio to the launching console for windowed builds."""
    if sys.platform != "win32" or not is_frozen():
        return False
    if sys.stdout is not None and sys.stderr is not None:
        return True
    try:
        if not ctypes.windll.kernel32.AttachConsole(-1):
            return False
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        try:
            sys.stdin = open("CONIN$", "r", encoding="utf-8")
        except OSError:
            pass
        return True
    except Exception:
        return False


def open_path(path: Path | str) -> None:
    target = str(path)
    if sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]
    else:
        import subprocess

        subprocess.Popen(["xdg-open", target])


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


_INSTANCE_MUTEX = None
_INSTANCE_MUTEX_NAME = "Local\\IPActivityAuditor-SingleInstance"
_WINDOW_TITLE = f"{APP_DISPLAY_NAME} · IP Activity Auditor"


def ensure_single_instance() -> bool:
    """Return False when another GUI instance is already running."""
    global _INSTANCE_MUTEX
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, _INSTANCE_MUTEX_NAME)
        if not handle:
            return True
        already_exists = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
        if already_exists:
            kernel32.CloseHandle(handle)
            return False
        _INSTANCE_MUTEX = handle
        return True
    except Exception:
        return True


def activate_existing_instance() -> bool:
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = 0
        for _attempt in range(12):
            hwnd = user32.FindWindowW(None, _WINDOW_TITLE)
            if hwnd:
                break
            import time

            time.sleep(0.1)
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def relaunch_as_admin(config_path: Path | None = None) -> bool:
    """Restart the current application elevated. Returns True if UAC accepted."""
    if sys.platform != "win32":
        return False

    if is_frozen():
        executable = str(Path(sys.executable).resolve())
        arguments = ["--gui", "--new-instance"]
        working_dir = str(Path(sys.executable).resolve().parent)
    else:
        executable = sys.executable
        script = project_root() / "src" / "main.py"
        arguments = [str(script), "--gui", "--new-instance"]
        working_dir = str(project_root())

    if config_path is not None:
        arguments.extend(["--config", str(config_path)])

    params = " ".join(f'"{arg}"' if " " in arg else arg for arg in arguments)
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, params, working_dir, 1
        )
        return int(result) > 32
    except Exception:
        return False
