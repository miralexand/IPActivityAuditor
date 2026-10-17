from __future__ import annotations

import csv
import ipaddress
import locale
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import ttk
from typing import Callable

import customtkinter as ctk
from PIL import Image

try:
    import pystray
except Exception:
    pystray = None

if sys.platform == "win32":
    import winreg
else:
    winreg = None

from apputil import (
    app_base_dir,
    format_bytes,
    is_frozen,
    open_path,
    relaunch_as_admin,
    resource_path,
    setup_logging,
)
from config import (
    AppConfig,
    ConfigError,
    load_config,
    parse_days,
    save_config,
    write_default_config,
)
from db import Database
from report import generate_reports, natural_ip_key
from scanner import ScanError, backend_status, is_admin, npcap_present, resolve_backend, scan
from version import APP_DISPLAY_NAME, APP_NAME, APP_VERSION

FONT = "Microsoft YaHei UI"
TASK_NAME = "IPActivityAuditor"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "IPActivityAuditor"

C = {
    "bg": "#F4F6FB",
    "sidebar": "#FFFFFF",
    "surface": "#F2F5FA",
    "card": "#FFFFFF",
    "card_hover": "#FFF4EA",
    "line": "#E1E6EF",
    "line_soft": "#EDF0F6",
    "text": "#1B2433",
    "text_soft": "#46556C",
    "muted": "#66758A",
    "accent": "#EA580C",
    "accent_hover": "#C2410C",
    "accent_soft": "#FFF0E4",
    "green": "#15803D",
    "green_soft": "#E9F8EF",
    "amber": "#B45309",
    "amber_soft": "#FFF5E5",
    "red": "#DC2626",
    "red_soft": "#FDECEC",
    "teal": "#0F766E",
    "purple": "#7C3AED",
}

BACKEND_OPTIONS = {
    "自动（推荐）": "auto",
    "SendARP（无需 Npcap）": "sendarp",
    "Scapy（需要 Npcap）": "scapy",
}
BACKEND_LABELS = {value: key for key, value in BACKEND_OPTIONS.items()}

WEEKDAYS = "一二三四五六日"


def _font(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT, size=round(size * 1.4), weight=weight)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _format_duration(ms: int | None) -> str:
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{ms} ms"
    return f"{ms / 1000:.1f} s"


def _ellipsize(value: str, length: int = 42) -> str:
    return value if len(value) <= length else value[: length - 1] + "…"


def _no_window_flags() -> int:
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _autostart_command(config_path: Path) -> str:
    if is_frozen():
        executable = str(Path(sys.executable).resolve())
        return f'"{executable}" --tray --config "{config_path}"'
    script = Path(__file__).resolve().parents[1] / "src" / "main.py"
    return f'"{sys.executable}" "{script}" --tray --config "{config_path}"'


def autostart_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ
        ) as key:
            value, _value_type = winreg.QueryValueEx(key, AUTOSTART_NAME)
            return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool, config_path: Path) -> None:
    if winreg is None:
        raise RuntimeError("开机自启动仅支持 Windows")
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(
                key,
                AUTOSTART_NAME,
                0,
                winreg.REG_SZ,
                _autostart_command(config_path),
            )
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
            except FileNotFoundError:
                pass


class StatCard(ctk.CTkFrame):
    def __init__(
        self,
        master,
        title: str,
        accent: str,
        hint: str = "",
        value: str = "—",
        compact: bool = False,
    ) -> None:
        super().__init__(
            master,
            fg_color=C["card"],
            corner_radius=16,
            border_width=1,
            border_color=C["line_soft"],
        )
        self.grid_columnconfigure(0, weight=1)
        self._accent = accent
        pad = 14 if compact else 18
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=pad, pady=(pad, 0))
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkFrame(
            top, width=30, height=4, corner_radius=2, fg_color=accent
        ).grid(row=0, column=0, sticky="w")
        self.title_label = ctk.CTkLabel(
            top,
            text=title,
            font=_font(11 if compact else 12),
            text_color=C["text_soft"],
            anchor="w",
        )
        self.title_label.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.value_label = ctk.CTkLabel(
            self,
            text=value,
            font=_font(24 if compact else 30, "bold"),
            text_color=C["text"],
            anchor="w",
        )
        self.value_label.grid(
            row=1, column=0, sticky="w", padx=pad, pady=(6 if compact else 10, 0)
        )
        self.hint_label = ctk.CTkLabel(
            self,
            text=hint,
            font=_font(10 if compact else 11),
            text_color=C["muted"],
            anchor="w",
        )
        self.hint_label.grid(
            row=2, column=0, sticky="w", padx=pad, pady=(2, pad if compact else 16)
        )

    def set_value(self, value: str, hint: str | None = None) -> None:
        self.value_label.configure(text=value)
        if hint is not None:
            self.hint_label.configure(text=hint)


class DonutChart(tk.Canvas):
    def __init__(self, master, size: int = 190, **kwargs) -> None:
        super().__init__(
            master,
            width=size,
            height=size,
            bg=C["card"],
            bd=0,
            highlightthickness=0,
            **kwargs,
        )
        self._size = size
        self._online = 0
        self._total = 0

    def update_data(self, online: int, total: int) -> None:
        self._online = max(online, 0)
        self._total = max(total, 0)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        size = self._size
        pad = 16
        width = 20
        box = (pad, pad, size - pad, size - pad)
        self.create_oval(
            *box,
            outline=C["line_soft"],
            width=width,
        )
        ratio = 0.0
        if self._total > 0:
            ratio = min(self._online / self._total, 1.0)
        if ratio > 0:
            extent = -359.9 * ratio
            self.create_arc(
                *box,
                start=90,
                extent=extent,
                style="arc",
                outline=C["green"],
                width=width,
            )
            self.create_arc(
                *box,
                start=90,
                extent=max(extent + 0.1, -0.1),
                style="arc",
                outline=C["teal"],
                width=width,
            )
        self.create_text(
            size / 2,
            size / 2 - 12,
            text=f"{ratio * 100:.0f}%",
            fill=C["text"],
            font=(FONT, 42, "bold"),
        )
        self.create_text(
            size / 2,
            size / 2 + 22,
            text="当前在线率",
            fill=C["muted"],
            font=(FONT, 14),
        )


class BarChart(tk.Canvas):
    def __init__(self, master, height: int = 170, **kwargs) -> None:
        super().__init__(
            master,
            height=height,
            bg=C["card"],
            bd=0,
            highlightthickness=0,
            **kwargs,
        )
        self._data: list[tuple[str, int]] = []
        self._height = height
        self.bind("<Configure>", lambda _event: self._draw())

    def update_data(self, data: list[tuple[str, int]]) -> None:
        self._data = data
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 320)
        height = self._height
        left, right, top, bottom = 46, 16, 18, 32
        plot_width = width - left - right
        plot_height = height - top - bottom
        if plot_width <= 10:
            return

        maximum = max((value for _day, value in self._data), default=0)
        maximum = max(maximum, 1)
        grid_color = C["line_soft"]
        for step in range(4):
            y = top + plot_height * step / 3
            self.create_line(left, y, width - right, y, fill=grid_color, width=1)
            self.create_text(
                left - 8,
                y,
                text=str(round(maximum * (3 - step) / 3)),
                fill=C["muted"],
                font=(FONT, 12),
                anchor="e",
            )

        count = max(len(self._data), 1)
        slot = plot_width / count
        bar_width = min(max(slot * 0.52, 6), 30)
        for index, (day, value) in enumerate(self._data):
            center = left + slot * index + slot / 2
            bar_height = plot_height * value / maximum if maximum else 0
            x0 = center - bar_width / 2
            x1 = center + bar_width / 2
            y0 = top + plot_height - bar_height
            y1 = top + plot_height
            color = C["accent"] if value else C["line_soft"]
            if value == maximum and value > 0:
                color = C["teal"]
            if value > 0:
                self.create_rectangle(
                    x0, y0, x1, y1, fill=color, outline="", width=0
                )
                if count <= 16:
                    self.create_text(
                        center,
                        y0 - 8,
                        text=str(value),
                        fill=C["text_soft"],
                        font=(FONT, 12),
                    )
            else:
                self.create_rectangle(
                    x0, y1 - 3, x1, y1, fill=color, outline="", width=0
                )
            if count <= 16 or index % 2 == 0:
                label = day[5:] if len(day) >= 10 else day
                self.create_text(
                    center,
                    height - 12,
                    text=label,
                    fill=C["muted"],
                    font=(FONT, 12),
                )


def confirm_dialog(
    parent: ctk.CTk,
    title: str,
    message: str,
    ok_text: str = "确定",
    cancel_text: str = "取消",
    danger: bool = False,
) -> bool:
    result = {"ok": False}
    dialog = ctk.CTkToplevel(parent)
    dialog.title(title)
    dialog.configure(fg_color=C["surface"])
    dialog.resizable(False, False)
    dialog.transient(parent)
    dialog.grab_set()

    frame = ctk.CTkFrame(dialog, fg_color="transparent")
    frame.pack(fill="both", expand=True, padx=26, pady=24)
    ctk.CTkLabel(
        frame, text=title, font=_font(16, "bold"), text_color=C["text"], anchor="w"
    ).pack(fill="x")
    ctk.CTkLabel(
        frame,
        text=message,
        font=_font(11),
        text_color=C["text_soft"],
        justify="left",
        anchor="w",
        wraplength=400,
    ).pack(fill="x", pady=(12, 22))

    buttons = ctk.CTkFrame(frame, fg_color="transparent")
    buttons.pack(fill="x")

    def close(value: bool) -> None:
        result["ok"] = value
        dialog.destroy()

    ctk.CTkButton(
        buttons,
        text=cancel_text,
        width=96,
        height=42,
        corner_radius=10,
        fg_color="transparent",
        border_width=1,
        border_color=C["line"],
        hover_color=C["card_hover"],
        text_color=C["text_soft"],
        font=_font(11),
        command=lambda: close(False),
    ).pack(side="right")
    ctk.CTkButton(
        buttons,
        text=ok_text,
        width=110,
        height=42,
        corner_radius=10,
        fg_color=C["red"] if danger else C["accent"],
        hover_color="#DC2626" if danger else C["accent_hover"],
        text_color="#FFFFFF",
        font=_font(11, "bold"),
        command=lambda: close(True),
    ).pack(side="right", padx=(0, 10))

    dialog.update_idletasks()
    width = max(dialog.winfo_reqwidth(), 460)
    height = max(dialog.winfo_reqheight(), 210)
    parent_x = parent.winfo_rootx()
    parent_y = parent.winfo_rooty()
    parent_w = parent.winfo_width()
    parent_h = parent.winfo_height()
    x = parent_x + (parent_w - width) // 2
    y = parent_y + (parent_h - height) // 2
    dialog.geometry(f"{width}x{height}+{x}+{y}")
    dialog.protocol("WM_DELETE_WINDOW", lambda: close(False))
    dialog.wait_window()
    return result["ok"]


class IPActivityAuditorApp(ctk.CTk):
    def __init__(
        self,
        config_path: Path,
        base_dir: Path | None = None,
        start_hidden: bool = False,
    ) -> None:
        ctk.set_appearance_mode("light")
        super().__init__()
        self.title(f"{APP_DISPLAY_NAME} · IP Activity Auditor")
        self.configure(fg_color=C["bg"])
        self.minsize(1080, 760)
        self.geometry(self._center_geometry(1240, 840))
        self._start_hidden = start_hidden
        if start_hidden:
            self.withdraw()
        self._set_icon()

        self.config_path = Path(config_path).expanduser().resolve()
        self.base_dir = base_dir or app_base_dir()
        self.cfg: AppConfig | None = None
        self.cfg_error: str | None = None
        self.logger = setup_logging(self.base_dir / "logs")

        self._busy = False
        self._closing = False
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._pages: dict[str, ctk.CTkFrame] = {}
        self._action_buttons: list[ctk.CTkButton] = []
        self._inventory_rows: list[dict[str, object]] = []
        self._search_after_id: str | None = None
        self._tick_after_id: str | None = None
        self._action_started = 0.0
        self._toast: ctk.CTkFrame | None = None
        self._preview_path: Path | None = None
        self._settings_populated = False
        self._current_page = "dashboard"
        self._config_error_banner: ctk.CTkFrame | None = None
        self._tray_icon = None
        self._tray_thread: threading.Thread | None = None
        self._hidden_to_tray = False
        self._tray_notice_shown = False
        self.report_callback_exception = self._on_tk_exception

        self._load_configuration()
        self._build_style()
        self._build_layout()
        self._show_page("dashboard")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<F5>", lambda _event: self.refresh_all())
        self._start_tray()
        self.after(60, self._drain_ui_queue)
        self.after(200, self._initial_refresh)

    # ------------------------------------------------------------------ setup

    def _center_geometry(self, width: int, height: int) -> str:
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max((screen_w - width) // 2, 0)
        y = max((screen_h - height) // 2 - 20, 0)
        return f"{width}x{height}+{x}+{y}"

    def _set_icon(self) -> None:
        try:
            icon = resource_path("assets/icon.ico")
            if icon.exists():
                self.iconbitmap(str(icon))
        except Exception:
            pass

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "IPAA.Treeview",
            background=C["card"],
            fieldbackground=C["card"],
            foreground=C["text_soft"],
            borderwidth=0,
            relief="flat",
            rowheight=44,
            font=(FONT, 14),
        )
        style.configure(
            "IPAA.Treeview.Heading",
            background=C["surface"],
            foreground=C["muted"],
            relief="flat",
            borderwidth=0,
            font=(FONT, 13, "bold"),
            padding=(13, 11),
        )
        style.map(
            "IPAA.Treeview",
            background=[("selected", C["accent_soft"])],
            foreground=[("selected", C["text"])],
        )
        style.map(
            "IPAA.Treeview.Heading",
            background=[("active", C["surface"])],
        )

    def _load_configuration(self) -> None:
        self.cfg_error = None
        self.cfg = None
        try:
            if not self.config_path.exists():
                write_default_config(self.config_path)
            self.cfg = load_config(self.config_path)
            self.logger = setup_logging(self.cfg.log_dir)
            self.logger.info("图形界面启动，配置：%s", self.config_path)
        except Exception as exc:
            self.cfg_error = str(exc)
            self.logger.exception("加载配置失败")

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0, minsize=252)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()

        self.main = ctk.CTkFrame(self, fg_color=C["bg"], corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(1, weight=1)

        self._build_header()

        self.container = ctk.CTkFrame(self.main, fg_color=C["bg"], corner_radius=0)
        self.container.grid(row=1, column=0, sticky="nsew")
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self._build_dashboard_page()
        self._build_inventory_page()
        self._build_history_page()
        self._build_reports_page()
        self._build_logs_page()
        self._build_settings_page()

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, fg_color=C["sidebar"], corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(2, weight=1)
        sidebar.grid_columnconfigure(0, weight=1)

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=22, pady=(26, 26))
        brand.grid_columnconfigure(1, weight=1)

        logo_path = resource_path("assets/logo.png")
        if logo_path.exists():
            image = ctk.CTkImage(
                light_image=Image.open(logo_path),
                dark_image=Image.open(logo_path),
                size=(42, 42),
            )
            self._logo_image = image
            ctk.CTkLabel(brand, image=image, text="").grid(
                row=0, column=0, rowspan=2, sticky="w"
            )
        else:
            ctk.CTkLabel(
                brand,
                text="IA",
                width=42,
                height=42,
                corner_radius=12,
                fg_color=C["accent"],
                text_color="#FFFFFF",
                font=_font(15, "bold"),
            ).grid(row=0, column=0, rowspan=2, sticky="w")

        ctk.CTkLabel(
            brand,
            text=APP_DISPLAY_NAME,
            font=_font(15, "bold"),
            text_color=C["text"],
            anchor="w",
        ).grid(row=0, column=1, sticky="sw", padx=(12, 0))
        ctk.CTkLabel(
            brand,
            text="IP ACTIVITY AUDITOR",
            font=_font(8),
            text_color=C["muted"],
            anchor="w",
        ).grid(row=1, column=1, sticky="nw", padx=(12, 0))

        nav = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="new", padx=14)
        nav.grid_columnconfigure(0, weight=1)

        items = [
            ("dashboard", "概览", "掌握内网活跃态势"),
            ("inventory", "IP 库存", "全部地址与活跃状态"),
            ("history", "扫描记录", "历次扫描审计轨迹"),
            ("reports", "报表中心", "未使用 IP 清单"),
            ("logs", "日志", "运行日志查询"),
            ("settings", "设置", "参数与系统集成"),
        ]
        for index, (key, label, _hint) in enumerate(items):
            button = ctk.CTkButton(
                nav,
                text=f"  {label}",
                anchor="w",
                height=48,
                corner_radius=11,
                fg_color="transparent",
                hover_color=C["card_hover"],
                text_color=C["text_soft"],
                font=_font(12),
                command=lambda page=key: self._show_page(page),
            )
            button.grid(row=index, column=0, sticky="ew", pady=3)
            self._nav_buttons[key] = button

        footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="sew", padx=22, pady=(18, 22))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            footer,
            text="本地运行 · 数据不出内网",
            font=_font(9),
            text_color=C["muted"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            footer,
            text=f"Version {APP_VERSION}",
            font=_font(9),
            text_color=C["muted"],
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self.main, fg_color=C["bg"], corner_radius=0)
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 0))
        header.grid_columnconfigure(0, weight=1)

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        self.header_title = ctk.CTkLabel(
            left, text="概览", font=_font(24, "bold"), text_color=C["text"], anchor="w"
        )
        self.header_title.pack(anchor="w")
        self.header_sub = ctk.CTkLabel(
            left,
            text="实时掌握内网 IP 活跃态势",
            font=_font(10),
            text_color=C["muted"],
            anchor="w",
        )
        self.header_sub.pack(anchor="w", pady=(3, 0))

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")

        today = date.today()
        ctk.CTkLabel(
            right,
            text=f"{today.year}年{today.month}月{today.day}日  星期{WEEKDAYS[today.weekday()]}",
            font=_font(10),
            text_color=C["muted"],
        ).pack(anchor="e", pady=(0, 7))

        chips = ctk.CTkFrame(right, fg_color="transparent")
        chips.pack(anchor="e")
        self.admin_dot, self.admin_chip, self.admin_label = self._status_chip(
            chips, "管理员权限"
        )
        self.admin_chip.pack(side="left", padx=(0, 8))
        self.backend_dot, self.backend_chip, self.backend_label = self._status_chip(
            chips, "扫描后端"
        )
        self.backend_chip.pack(side="left")
        self._refresh_system_chips()

    def _status_chip(
        self, master, text: str
    ) -> tuple[ctk.CTkLabel, ctk.CTkFrame, ctk.CTkLabel]:
        frame = ctk.CTkFrame(
            master,
            fg_color=C["card"],
            corner_radius=16,
            border_width=1,
            border_color=C["line_soft"],
        )
        dot = ctk.CTkLabel(
            frame, text="●", font=_font(8), text_color=C["green"], width=10
        )
        dot.pack(side="left", padx=(11, 5), pady=5)
        label = ctk.CTkLabel(
            frame, text=text, font=_font(9), text_color=C["text_soft"]
        )
        label.pack(side="left", padx=(0, 11), pady=5)
        return dot, frame, label

    # ----------------------------------------------------------------- widgets

    def _card(self, master, **kwargs) -> ctk.CTkFrame:
        options = dict(
            fg_color=C["card"],
            corner_radius=16,
            border_width=1,
            border_color=C["line_soft"],
        )
        options.update(kwargs)
        return ctk.CTkFrame(master, **options)

    def _section_title(self, master, title: str, subtitle: str = "") -> None:
        ctk.CTkLabel(
            master,
            text=title,
            font=_font(13, "bold"),
            text_color=C["text"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        if subtitle:
            ctk.CTkLabel(
                master,
                text=subtitle,
                font=_font(9),
                text_color=C["muted"],
                anchor="w",
            ).grid(row=1, column=0, sticky="w", pady=(2, 0))

    def _tree(
        self,
        master,
        columns: list[str],
        headings: list[str],
        widths: list[int],
        height: int = 10,
    ) -> ttk.Treeview:
        tree = ttk.Treeview(
            master,
            columns=columns,
            show="headings",
            style="IPAA.Treeview",
            height=height,
            selectmode="browse",
        )
        for column, heading, width in zip(columns, headings, widths):
            tree.heading(column, text=heading)
            tree.column(column, width=width, minwidth=60, anchor="w")
        scrollbar = ctk.CTkScrollbar(
            master,
            command=tree.yview,
            width=10,
            fg_color="transparent",
            button_color=C["line"],
            button_hover_color=C["muted"],
        )
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        master.grid_rowconfigure(0, weight=1)
        master.grid_columnconfigure(0, weight=1)
        return tree

    # --------------------------------------------------------------- dashboard

    def _build_dashboard_page(self) -> None:
        page = ctk.CTkScrollableFrame(
            self.container,
            fg_color=C["bg"],
            corner_radius=0,
            scrollbar_button_color=C["line"],
            scrollbar_button_hover_color=C["muted"],
        )
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        self._pages["dashboard"] = page

        stats = ctk.CTkFrame(page, fg_color="transparent")
        stats.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 0))
        for column in range(4):
            stats.grid_columnconfigure(column, weight=1, uniform="stat")

        self.stat_total = StatCard(stats, "库存 IP", C["accent"], "尚未初始化")
        self.stat_total.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self.stat_online = StatCard(stats, "当前在线", C["green"], "等待首次扫描")
        self.stat_online.grid(row=0, column=1, sticky="nsew", padx=7)
        self.stat_unused_a = StatCard(stats, "7 天未使用", C["amber"], "含从未活跃")
        self.stat_unused_a.grid(row=0, column=2, sticky="nsew", padx=7)
        self.stat_unused_b = StatCard(stats, "15 天未使用", C["red"], "含从未活跃")
        self.stat_unused_b.grid(row=0, column=3, sticky="nsew", padx=(7, 0))

        middle = ctk.CTkFrame(page, fg_color="transparent")
        middle.grid(row=1, column=0, sticky="ew", padx=28, pady=(16, 0))
        middle.grid_columnconfigure(0, weight=5)
        middle.grid_columnconfigure(1, weight=4)

        chart_card = self._card(middle)
        chart_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        chart_card.grid_columnconfigure(1, weight=1)
        chart_card.grid_rowconfigure(1, weight=1)
        title_box = ctk.CTkFrame(chart_card, fg_color="transparent")
        title_box.grid(row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(18, 0))
        self._section_title(title_box, "在线态势", "基于最近一次成功扫描的库存状态")

        self.donut = DonutChart(chart_card, size=200)
        self.donut.grid(row=1, column=0, padx=(16, 8), pady=(4, 16))

        legend = ctk.CTkFrame(chart_card, fg_color="transparent")
        legend.grid(row=1, column=1, sticky="nsew", padx=(4, 18), pady=(8, 16))
        legend.grid_columnconfigure(1, weight=1)
        self.legend_online = self._legend_row(legend, 0, C["green"], "在线")
        self.legend_offline = self._legend_row(legend, 1, C["muted"], "离线")
        self.legend_unused = self._legend_row(legend, 2, C["amber"], "从未活跃")

        ctk.CTkLabel(
            legend,
            text="最后成功扫描",
            font=_font(9),
            text_color=C["muted"],
            anchor="w",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(16, 2))
        self.last_success_label = ctk.CTkLabel(
            legend,
            text="—",
            font=_font(11, "bold"),
            text_color=C["text"],
            anchor="w",
        )
        self.last_success_label.grid(row=4, column=0, columnspan=2, sticky="w")

        action_card = self._card(middle)
        action_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        action_card.grid_columnconfigure(0, weight=1)
        action_box = ctk.CTkFrame(action_card, fg_color="transparent")
        action_box.grid(row=0, column=0, sticky="nsew", padx=20, pady=18)
        action_box.grid_columnconfigure(0, weight=1)
        action_box.grid_columnconfigure(1, weight=1)
        self._section_title(action_box, "快捷操作", "扫描与报表将自动写入本地数据库")

        self.scan_button = ctk.CTkButton(
            action_box,
            text="开始扫描",
            height=48,
            corner_radius=11,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color="#FFFFFF",
            font=_font(12, "bold"),
            command=self.start_scan,
        )
        self.scan_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(16, 8))
        self._action_buttons.append(self.scan_button)

        self.report_button = ctk.CTkButton(
            action_box,
            text="生成报表",
            height=48,
            corner_radius=10,
            fg_color="transparent",
            border_width=1,
            border_color=C["line"],
            hover_color=C["card_hover"],
            text_color=C["text_soft"],
            font=_font(11),
            command=self.generate_reports_from_config,
        )
        self.report_button.grid(row=2, column=0, sticky="ew", padx=(0, 4), pady=(0, 8))
        self._action_buttons.append(self.report_button)

        self.init_button = ctk.CTkButton(
            action_box,
            text="初始化库存",
            height=48,
            corner_radius=10,
            fg_color="transparent",
            border_width=1,
            border_color=C["line"],
            hover_color=C["card_hover"],
            text_color=C["text_soft"],
            font=_font(11),
            command=self.initialize_inventory,
        )
        self.init_button.grid(row=2, column=1, sticky="ew", padx=(4, 0), pady=(0, 8))
        self._action_buttons.append(self.init_button)

        self.auto_report_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            action_box,
            text="扫描完成后自动生成报表",
            variable=self.auto_report_var,
            font=_font(9),
            text_color=C["text_soft"],
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            border_color=C["line"],
            checkmark_color="#FFFFFF",
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=5,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self.progress = ctk.CTkProgressBar(
            action_box,
            height=7,
            corner_radius=4,
            fg_color=C["line_soft"],
            progress_color=C["accent"],
            mode="determinate",
        )
        self.progress.set(0)
        self.progress.grid(row=4, column=0, columnspan=2, sticky="ew")

        self.action_status = ctk.CTkLabel(
            action_box,
            text="就绪",
            font=_font(9),
            text_color=C["muted"],
            anchor="w",
        )
        self.action_status.grid(row=5, column=0, columnspan=2, sticky="w", pady=(7, 0))

        recent_card = self._card(page)
        recent_card.grid(row=2, column=0, sticky="ew", padx=28, pady=(16, 28))
        recent_card.grid_columnconfigure(0, weight=1)
        recent_head = ctk.CTkFrame(recent_card, fg_color="transparent")
        recent_head.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        recent_head.grid_columnconfigure(0, weight=1)
        self._section_title(recent_head, "最近扫描", "最新的扫描结果与耗时")
        ctk.CTkButton(
            recent_head,
            text="查看全部",
            width=76,
            height=28,
            corner_radius=8,
            fg_color="transparent",
            hover_color=C["card_hover"],
            text_color=C["accent"],
            font=_font(9),
            command=lambda: self._show_page("history"),
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        header_row = ctk.CTkFrame(recent_card, fg_color="transparent")
        header_row.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 2))
        self._recent_header(header_row)
        ctk.CTkFrame(
            recent_card, height=1, fg_color=C["line_soft"]
        ).grid(row=2, column=0, sticky="ew", padx=20)

        self.recent_rows = ctk.CTkFrame(recent_card, fg_color="transparent")
        self.recent_rows.grid(row=3, column=0, sticky="ew", padx=20, pady=(2, 14))
        self.recent_rows.grid_columnconfigure(0, weight=1)

    def _legend_row(self, master, row: int, color: str, label: str) -> ctk.CTkLabel:
        dot = ctk.CTkLabel(
            master, text="●", font=_font(11), text_color=color, width=14
        )
        dot.grid(row=row, column=0, sticky="w", pady=3)
        text = ctk.CTkLabel(
            master,
            text=label,
            font=_font(10),
            text_color=C["text_soft"],
            anchor="w",
        )
        text.grid(row=row, column=1, sticky="w")
        value = ctk.CTkLabel(
            master,
            text="0",
            font=_font(11, "bold"),
            text_color=C["text"],
            anchor="e",
        )
        value.grid(row=row, column=2, sticky="e", padx=(10, 0))
        return value

    def _recent_header(self, master) -> None:
        columns = ((0, 2), (1, 1), (2, 1), (3, 1), (4, 3))
        labels = ["扫描日期", "状态", "在线 IP", "耗时", "说明"]
        for column, weight in columns:
            master.grid_columnconfigure(column, weight=weight)
            ctk.CTkLabel(
                master,
                text=labels[column],
                font=_font(9, "bold"),
                text_color=C["muted"],
                anchor="w",
            ).grid(row=0, column=column, sticky="w", padx=(0, 8))

    # --------------------------------------------------------------- inventory

    def _build_inventory_page(self) -> None:
        page = ctk.CTkFrame(self.container, fg_color=C["bg"], corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        self._pages["inventory"] = page

        toolbar = ctk.CTkFrame(page, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=28, pady=(18, 12))
        toolbar.grid_columnconfigure(1, weight=1)

        self.search_entry = ctk.CTkEntry(
            toolbar,
            width=260,
            height=42,
            corner_radius=10,
            fg_color=C["card"],
            border_color=C["line"],
            border_width=1,
            placeholder_text="搜索 IP 地址…",
            placeholder_text_color=C["muted"],
            text_color=C["text"],
            font=_font(10),
        )
        self.search_entry.grid(row=0, column=0, sticky="w")
        self.search_entry.bind("<KeyRelease>", lambda _event: self._schedule_inventory_filter())

        self.filter_segment = ctk.CTkSegmentedButton(
            toolbar,
            values=["全部", "在线", "离线", "从未活跃"],
            width=300,
            height=42,
            corner_radius=10,
            fg_color=C["card"],
            selected_color=C["accent"],
            selected_hover_color=C["accent_hover"],
            unselected_color=C["card"],
            unselected_hover_color=C["card_hover"],
            text_color=C["text_soft"],
            font=_font(10),
            command=lambda _value: self._render_inventory(),
        )
        self.filter_segment.set("全部")
        self.filter_segment.grid(row=0, column=2, sticky="e", padx=(0, 8))

        ctk.CTkButton(
            toolbar,
            text="刷新",
            width=72,
            height=42,
            corner_radius=10,
            fg_color="transparent",
            border_width=1,
            border_color=C["line"],
            hover_color=C["card_hover"],
            text_color=C["text_soft"],
            font=_font(10),
            command=self.refresh_inventory,
        ).grid(row=0, column=3, padx=(0, 8))

        ctk.CTkButton(
            toolbar,
            text="导出 CSV",
            width=90,
            height=42,
            corner_radius=10,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color="#FFFFFF",
            font=_font(10, "bold"),
            command=self.export_inventory,
        ).grid(row=0, column=4)

        card = self._card(page)
        card.grid(row=1, column=0, sticky="nsew", padx=28, pady=(0, 26))
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)

        tree_holder = ctk.CTkFrame(card, fg_color="transparent")
        tree_holder.grid(row=0, column=0, sticky="nsew", padx=14, pady=(14, 0))
        self.inventory_tree = self._tree(
            tree_holder,
            ["ip", "status", "mac", "last", "days", "updated"],
            ["IP 地址", "状态", "MAC 地址", "最后活跃", "未使用天数", "更新时间"],
            [140, 90, 150, 120, 110, 160],
            height=8,
        )
        self.inventory_tree.tag_configure("online", foreground=C["green"])
        self.inventory_tree.tag_configure("offline", foreground=C["text_soft"])
        self.inventory_tree.tag_configure("never", foreground=C["amber"])

        footer = ctk.CTkFrame(card, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", padx=20, pady=(8, 12))
        footer.grid_columnconfigure(0, weight=1)
        self.inventory_count = ctk.CTkLabel(
            footer,
            text="共 0 个 IP",
            font=_font(9),
            text_color=C["muted"],
            anchor="w",
        )
        self.inventory_count.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            footer,
            text="状态基于最近一次成功扫描；未使用天数含从未活跃",
            font=_font(9),
            text_color=C["muted"],
            anchor="e",
        ).grid(row=0, column=1, sticky="e")

    # ----------------------------------------------------------------- history

    def _build_history_page(self) -> None:
        page = ctk.CTkFrame(self.container, fg_color=C["bg"], corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)
        self._pages["history"] = page

        stats = ctk.CTkFrame(page, fg_color="transparent")
        stats.grid(row=0, column=0, sticky="ew", padx=28, pady=(18, 0))
        for column in range(4):
            stats.grid_columnconfigure(column, weight=1, uniform="hist")
        self.hist_total = StatCard(stats, "扫描总次数", C["accent"], "成功 + 失败", compact=True)
        self.hist_total.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.hist_success = StatCard(stats, "成功", C["green"], "可用于未使用判定", compact=True)
        self.hist_success.grid(row=0, column=1, sticky="nsew", padx=6)
        self.hist_failed = StatCard(stats, "失败", C["red"], "失败不会更新活跃日期", compact=True)
        self.hist_failed.grid(row=0, column=2, sticky="nsew", padx=6)
        self.hist_last = StatCard(stats, "最近成功", C["teal"], "最后有效扫描日期", compact=True)
        self.hist_last.grid(row=0, column=3, sticky="nsew", padx=(6, 0))

        chart_card = self._card(page)
        chart_card.grid(row=1, column=0, sticky="ew", padx=28, pady=(14, 0))
        chart_card.grid_columnconfigure(0, weight=1)
        chart_head = ctk.CTkFrame(chart_card, fg_color="transparent")
        chart_head.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 0))
        self._section_title(chart_head, "近 14 天在线 IP 数量", "每日成功扫描记录到的在线设备数")
        self.history_chart = BarChart(chart_card, height=150)
        self.history_chart.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        table_card = self._card(page)
        table_card.grid(row=2, column=0, sticky="nsew", padx=28, pady=(14, 26))
        table_card.grid_rowconfigure(0, weight=1)
        table_card.grid_columnconfigure(0, weight=1)
        holder = ctk.CTkFrame(table_card, fg_color="transparent")
        holder.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)
        self.history_tree = self._tree(
            holder,
            ["date", "status", "online", "duration", "error"],
            ["扫描日期", "状态", "在线 IP", "耗时", "错误 / 备注"],
            [130, 90, 90, 100, 380],
            height=2,
        )
        self.history_tree.tag_configure("success", foreground=C["green"])
        self.history_tree.tag_configure("failed", foreground=C["red"])

    # ----------------------------------------------------------------- reports

    def _build_reports_page(self) -> None:
        page = ctk.CTkFrame(self.container, fg_color=C["bg"], corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        self._pages["reports"] = page

        action = self._card(page)
        action.grid(row=0, column=0, sticky="ew", padx=28, pady=(18, 12))
        action.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            action,
            text="报表天数",
            font=_font(10, "bold"),
            text_color=C["text_soft"],
        ).grid(row=0, column=0, padx=(20, 10), pady=18, sticky="w")
        self.report_days_entry = ctk.CTkEntry(
            action,
            width=150,
            height=42,
            corner_radius=10,
            fg_color=C["surface"],
            border_color=C["line"],
            border_width=1,
            text_color=C["text"],
            font=_font(10),
            placeholder_text="如 7,15",
        )
        self.report_days_entry.grid(row=0, column=1, sticky="w")
        ctk.CTkButton(
            action,
            text="生成报表",
            width=110,
            height=42,
            corner_radius=10,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color="#FFFFFF",
            font=_font(10, "bold"),
            command=self.generate_reports_from_input,
        ).grid(row=0, column=2, padx=8)
        ctk.CTkButton(
            action,
            text="打开报表目录",
            width=116,
            height=42,
            corner_radius=10,
            fg_color="transparent",
            border_width=1,
            border_color=C["line"],
            hover_color=C["card_hover"],
            text_color=C["text_soft"],
            font=_font(10),
            command=self.open_report_dir,
        ).grid(row=0, column=3, padx=(0, 20))

        split = ctk.CTkFrame(page, fg_color="transparent")
        split.grid(row=1, column=0, sticky="nsew", padx=28, pady=(0, 26))
        split.grid_columnconfigure(0, weight=4)
        split.grid_columnconfigure(1, weight=6)
        split.grid_rowconfigure(0, weight=1)

        list_card = self._card(split)
        list_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        list_card.grid_rowconfigure(1, weight=1)
        list_card.grid_columnconfigure(0, weight=1)
        list_head = ctk.CTkFrame(list_card, fg_color="transparent")
        list_head.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
        list_head.grid_columnconfigure(0, weight=1)
        self._section_title(list_head, "报表文件", "双击文件可预览，生成后自动刷新")
        self.report_list = ctk.CTkScrollableFrame(
            list_card,
            fg_color="transparent",
            scrollbar_button_color=C["line"],
            scrollbar_button_hover_color=C["muted"],
        )
        self.report_list.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 12))
        self.report_list.grid_columnconfigure(0, weight=1)

        preview_card = self._card(split)
        preview_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        preview_card.grid_rowconfigure(1, weight=1)
        preview_card.grid_columnconfigure(0, weight=1)
        preview_head = ctk.CTkFrame(preview_card, fg_color="transparent")
        preview_head.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
        preview_head.grid_columnconfigure(0, weight=1)
        self.preview_title = ctk.CTkLabel(
            preview_head,
            text="未选择报表",
            font=_font(13, "bold"),
            text_color=C["text"],
            anchor="w",
        )
        self.preview_title.grid(row=0, column=0, sticky="w")
        self.preview_meta = ctk.CTkLabel(
            preview_head,
            text="从左侧选择报表查看前 200 行",
            font=_font(9),
            text_color=C["muted"],
            anchor="w",
        )
        self.preview_meta.grid(row=1, column=0, sticky="w", pady=(2, 0))
        ctk.CTkButton(
            preview_head,
            text="打开文件",
            width=84,
            height=30,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color=C["line"],
            hover_color=C["card_hover"],
            text_color=C["text_soft"],
            font=_font(9),
            command=self.open_preview_file,
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        preview_holder = ctk.CTkFrame(preview_card, fg_color="transparent")
        preview_holder.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.preview_tree = self._tree(
            preview_holder,
            ["c0", "c1", "c2", "c3"],
            ["ip_address", "last_seen_date", "days_unused", "status"],
            [160, 140, 120, 120],
            height=8,
        )

    # ---------------------------------------------------------------- settings

    def _build_settings_page(self) -> None:
        page = ctk.CTkScrollableFrame(
            self.container,
            fg_color=C["bg"],
            corner_radius=0,
            scrollbar_button_color=C["line"],
            scrollbar_button_hover_color=C["muted"],
        )
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        self._pages["settings"] = page

        if self.cfg_error:
            banner = ctk.CTkFrame(
                page,
                fg_color=C["red_soft"],
                corner_radius=14,
                border_width=1,
                border_color="#F3C7C7",
            )
            self._config_error_banner = banner
            banner.grid(row=0, column=0, sticky="ew", padx=28, pady=(20, 0))
            banner.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                banner,
                text="配置读取失败",
                font=_font(12, "bold"),
                text_color=C["red"],
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 2))
            ctk.CTkLabel(
                banner,
                text=self.cfg_error,
                font=_font(9),
                text_color=C["text_soft"],
                anchor="w",
                wraplength=760,
                justify="left",
            ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 14))
            ctk.CTkButton(
                banner,
                text="写入默认配置",
                width=120,
                height=32,
                corner_radius=8,
                fg_color=C["red"],
                hover_color="#DC2626",
                font=_font(9, "bold"),
                command=self.write_default_and_reload,
            ).grid(row=0, column=1, rowspan=2, padx=18)

        form = self._card(page)
        form.grid(row=1, column=0, sticky="ew", padx=28, pady=(16, 0))
        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, weight=1)
        head = ctk.CTkFrame(form, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(18, 4))
        self._section_title(head, "扫描参数", "仅允许扫描此处配置的 CIDR，不会自动扩展网段")

        self.entries: dict[str, ctk.CTkEntry] = {}
        self.options: dict[str, ctk.CTkOptionMenu] = {}
        self._cidr_field(form, 1)
        self._option_field(
            form,
            "backend",
            "扫描后端",
            list(BACKEND_OPTIONS.keys()),
            2,
            0,
            2,
        )
        self._field(form, "timeout_ms", "Scapy 超时（毫秒）", 3, 0)
        self._field(form, "retries", "重试次数", 3, 1)

        output_head = ctk.CTkFrame(form, fg_color="transparent")
        output_head.grid(row=4, column=0, columnspan=2, sticky="ew", padx=20, pady=(14, 0))
        self._section_title(output_head, "存储与输出", "相对路径以配置文件所在目录为基准")
        self._field(form, "db_path", "数据库路径", 5, 0, 2)
        self._field(form, "report_dir", "报表目录", 6, 0)
        self._field(form, "unused_days", "报表天数（逗号分隔）", 6, 1)
        self._field(form, "log_dir", "日志目录", 7, 0, 2)

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=8, column=0, columnspan=2, sticky="ew", padx=20, pady=(16, 18))
        actions.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(
            actions,
            text="保存配置",
            width=112,
            height=42,
            corner_radius=10,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color="#FFFFFF",
            font=_font(10, "bold"),
            command=self.save_settings,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            actions,
            text="打开配置文件",
            width=116,
            height=42,
            corner_radius=10,
            fg_color="transparent",
            border_width=1,
            border_color=C["line"],
            hover_color=C["card_hover"],
            text_color=C["text_soft"],
            font=_font(10),
            command=self.open_config_file,
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))
        ctk.CTkButton(
            actions,
            text="打开数据目录",
            width=116,
            height=42,
            corner_radius=10,
            fg_color="transparent",
            border_width=1,
            border_color=C["line"],
            hover_color=C["card_hover"],
            text_color=C["text_soft"],
            font=_font(10),
            command=self.open_data_dir,
        ).grid(row=0, column=2, sticky="w", padx=(10, 0))

        system = self._card(page)
        system.grid(row=2, column=0, sticky="ew", padx=28, pady=(14, 0))
        system.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(system, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 6))
        self._section_title(
            head,
            "系统与权限",
            "默认使用 Windows 原生 SendARP，无需 Npcap；Scapy 后端可选",
        )
        status = ctk.CTkFrame(system, fg_color="transparent")
        status.grid(row=1, column=0, sticky="ew", padx=20)
        status.grid_columnconfigure(1, weight=1)
        self.settings_admin = self._settings_status_row(status, 0, "管理员权限")
        self.settings_backend = self._settings_status_row(status, 1, "扫描后端")
        self.settings_task = self._settings_status_row(status, 2, "每日任务计划")

        buttons = ctk.CTkFrame(system, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=20, pady=(14, 18))
        ctk.CTkButton(
            buttons,
            text="以管理员身份重启",
            width=142,
            height=40,
            corner_radius=10,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color="#FFFFFF",
            font=_font(10, "bold"),
            command=self.restart_as_admin,
        ).pack(side="left")
        ctk.CTkButton(
            buttons,
            text="打开日志目录",
            width=116,
            height=40,
            corner_radius=10,
            fg_color="transparent",
            border_width=1,
            border_color=C["line"],
            hover_color=C["card_hover"],
            text_color=C["text_soft"],
            font=_font(10),
            command=self.open_log_dir,
        ).pack(side="left", padx=(10, 0))

        tray_card = self._card(page)
        tray_card.grid(row=3, column=0, sticky="ew", padx=28, pady=(14, 0))
        tray_card.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(tray_card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 6))
        self._section_title(
            head,
            "托盘与启动",
            "关闭窗口后程序继续在系统托盘静默运行，可从托盘菜单扫描、打开或退出",
        )
        tray_body = ctk.CTkFrame(tray_card, fg_color="transparent")
        tray_body.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 18))
        tray_body.grid_columnconfigure(1, weight=1)
        self.autostart_var = ctk.BooleanVar(value=autostart_enabled())
        self.autostart_check = ctk.CTkCheckBox(
            tray_body,
            text="开机自动启动（静默到托盘）",
            variable=self.autostart_var,
            command=self.toggle_autostart,
            font=_font(10),
            text_color=C["text_soft"],
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            border_color=C["line"],
            checkmark_color="#FFFFFF",
            checkbox_width=20,
            checkbox_height=20,
            corner_radius=5,
        )
        self.autostart_check.grid(row=0, column=0, sticky="w")
        self.tray_status = ctk.CTkLabel(
            tray_body,
            text="正在检查托盘状态…",
            font=_font(9),
            text_color=C["muted"],
            anchor="e",
        )
        self.tray_status.grid(row=0, column=1, sticky="e", padx=(12, 0))
        tray_buttons = ctk.CTkFrame(tray_body, fg_color="transparent")
        tray_buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(14, 0))
        ctk.CTkButton(
            tray_buttons,
            text="隐藏到托盘",
            width=120,
            height=40,
            corner_radius=10,
            fg_color=C["accent_soft"],
            hover_color=C["card_hover"],
            text_color=C["accent"],
            font=_font(10, "bold"),
            command=self.hide_window_to_tray,
        ).pack(side="left")
        ctk.CTkButton(
            tray_buttons,
            text="退出程序",
            width=106,
            height=40,
            corner_radius=10,
            fg_color="transparent",
            border_width=1,
            border_color=C["line"],
            hover_color=C["red_soft"],
            text_color=C["red"],
            font=_font(10),
            command=self.exit_from_settings,
        ).pack(side="left", padx=(10, 0))

        task_card = self._card(page)
        task_card.grid(row=4, column=0, sticky="ew", padx=28, pady=(14, 0))
        task_card.grid_columnconfigure(1, weight=1)
        head = ctk.CTkFrame(task_card, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=3, sticky="ew", padx=20, pady=(18, 8))
        self._section_title(head, "每日自动扫描", "通过 Windows 任务计划以最高权限无人值守运行")
        ctk.CTkLabel(
            task_card,
            text="执行时间",
            font=_font(10, "bold"),
            text_color=C["text_soft"],
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 16))
        self.task_time_entry = ctk.CTkEntry(
            task_card,
            width=100,
            height=40,
            corner_radius=9,
            fg_color=C["surface"],
            border_color=C["line"],
            border_width=1,
            text_color=C["text"],
            font=_font(10),
        )
        self.task_time_entry.insert(0, "23:30")
        self.task_time_entry.grid(row=1, column=1, sticky="w", pady=(0, 16))
        task_buttons = ctk.CTkFrame(task_card, fg_color="transparent")
        task_buttons.grid(row=1, column=2, sticky="e", padx=(0, 20), pady=(0, 16))
        ctk.CTkButton(
            task_buttons,
            text="创建 / 更新任务",
            width=126,
            height=40,
            corner_radius=9,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color="#FFFFFF",
            font=_font(10, "bold"),
            command=self.create_scheduled_task,
        ).pack(side="left")
        ctk.CTkButton(
            task_buttons,
            text="删除任务",
            width=92,
            height=40,
            corner_radius=9,
            fg_color="transparent",
            border_width=1,
            border_color=C["line"],
            hover_color=C["card_hover"],
            text_color=C["text_soft"],
            font=_font(10),
            command=self.delete_scheduled_task,
        ).pack(side="left", padx=(8, 0))

        about = self._card(page)
        about.grid(row=5, column=0, sticky="ew", padx=28, pady=(14, 28))
        about.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            about,
            text=f"{APP_DISPLAY_NAME}  {APP_VERSION}",
            font=_font(13, "bold"),
            text_color=C["text"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(
            about,
            text=(
                "仅可用于已获得明确授权的内网。程序只扫描配置中的 CIDR，"
                "扫描失败时不会更新任何 IP 的活跃日期。所有数据保存在本机。"
            ),
            font=_font(9),
            text_color=C["muted"],
            anchor="w",
            justify="left",
            wraplength=820,
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 16))

    def _field(
        self,
        master,
        key: str,
        label: str,
        row: int,
        column: int,
        colspan: int = 1,
    ) -> None:
        holder = ctk.CTkFrame(master, fg_color="transparent")
        holder.grid(
            row=row,
            column=column,
            columnspan=colspan,
            sticky="ew",
            padx=(20 if column == 0 else 10, 20 if column + colspan >= 2 else 10),
            pady=(10, 0),
        )
        holder.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            holder,
            text=label,
            font=_font(9, "bold"),
            text_color=C["muted"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        entry = ctk.CTkEntry(
            holder,
            height=42,
            corner_radius=9,
            fg_color=C["surface"],
            border_color=C["line"],
            border_width=1,
            text_color=C["text"],
            font=_font(10),
        )
        entry.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self.entries[key] = entry

    def _cidr_field(self, master, row: int) -> None:
        holder = ctk.CTkFrame(master, fg_color="transparent")
        holder.grid(
            row=row,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=20,
            pady=(10, 0),
        )
        holder.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            holder,
            text="扫描网段（CIDR，可填写多个）",
            font=_font(9, "bold"),
            text_color=C["muted"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.cidr_textbox = ctk.CTkTextbox(
            holder,
            height=84,
            corner_radius=9,
            fg_color=C["surface"],
            border_color=C["line"],
            border_width=1,
            text_color=C["text"],
            font=_font(10),
            wrap="none",
        )
        self.cidr_textbox.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        ctk.CTkLabel(
            holder,
            text="多个网段请用换行、逗号或分号分隔；网段之间不能重叠",
            font=_font(9),
            text_color=C["muted"],
            anchor="w",
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))

    def _option_field(
        self,
        master,
        key: str,
        label: str,
        values: list[str],
        row: int,
        column: int,
        colspan: int = 1,
    ) -> None:
        holder = ctk.CTkFrame(master, fg_color="transparent")
        holder.grid(
            row=row,
            column=column,
            columnspan=colspan,
            sticky="ew",
            padx=(20 if column == 0 else 10, 20 if column + colspan >= 2 else 10),
            pady=(10, 0),
        )
        holder.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            holder,
            text=label,
            font=_font(9, "bold"),
            text_color=C["muted"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        menu = ctk.CTkOptionMenu(
            holder,
            values=values,
            height=42,
            corner_radius=9,
            fg_color=C["surface"],
            button_color=C["accent"],
            button_hover_color=C["accent_hover"],
            text_color=C["text"],
            dropdown_fg_color=C["card"],
            dropdown_hover_color=C["accent_soft"],
            dropdown_text_color=C["text"],
            font=_font(10),
            dropdown_font=_font(10),
            anchor="w",
        )
        menu.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self.options[key] = menu

    def _settings_status_row(self, master, row: int, label: str) -> ctk.CTkLabel:
        ctk.CTkLabel(
            master,
            text=label,
            font=_font(10),
            text_color=C["text_soft"],
            anchor="w",
        ).grid(row=row, column=0, sticky="w", pady=4)
        value = ctk.CTkLabel(
            master,
            text="—",
            font=_font(10, "bold"),
            text_color=C["text"],
            anchor="w",
        )
        value.grid(row=row, column=1, sticky="w", padx=(18, 0), pady=4)
        return value

    # ------------------------------------------------------------------ actions

    def _initial_refresh(self) -> None:
        self._populate_settings()
        self._report_days_default()
        self.refresh_all()
        if self.cfg_error:
            self._show_page("settings")

    def _report_days_default(self) -> None:
        if self.cfg:
            self.report_days_entry.delete(0, "end")
            self.report_days_entry.insert(
                0, ",".join(str(day) for day in self.cfg.unused_days)
            )

    def _show_page(self, key: str) -> None:
        page = self._pages.get(key)
        if page is None:
            return
        for page_key, candidate in self._pages.items():
            if page_key == key:
                candidate.grid(row=0, column=0, sticky="nsew")
            else:
                candidate.grid_remove()
        self._current_page = key
        titles = {
            "dashboard": ("概览", "实时掌握内网 IP 活跃态势"),
            "inventory": ("IP 库存", "全部地址、在线状态与未使用天数"),
            "history": ("扫描记录", "历次扫描审计轨迹与每日在线趋势"),
            "reports": ("报表中心", "生成、在软件内查看与打开未使用 IP 清单"),
            "logs": ("日志", "查询运行、扫描与错误日志"),
            "settings": ("设置", "扫描参数、系统权限与每日任务"),
        }
        title, subtitle = titles[key]
        self.header_title.configure(text=title)
        self.header_sub.configure(text=subtitle)
        for nav_key, button in self._nav_buttons.items():
            active = nav_key == key
            button.configure(
                fg_color=C["accent_soft"] if active else "transparent",
                text_color=C["accent"] if active else C["text_soft"],
                font=_font(12, "bold") if active else _font(12),
            )
        if key == "inventory":
            self.refresh_inventory()
        elif key == "history":
            self.refresh_history()
        elif key == "reports":
            self.refresh_report_files()
            self._report_days_default()
        elif key == "logs":
            self.refresh_logs()
        elif key == "settings":
            self._populate_settings()
            self._refresh_system_settings()
        else:
            self.refresh_dashboard()

    def refresh_all(self) -> None:
        self._refresh_system_chips()
        page = self._current_page
        if page == "inventory":
            self.refresh_inventory()
        elif page == "history":
            self.refresh_history()
        elif page == "reports":
            self.refresh_report_files()
        elif page == "logs":
            self.refresh_logs()
        elif page == "settings":
            self._refresh_system_settings()
        else:
            self.refresh_dashboard()

    def _snapshot(self) -> dict[str, object] | None:
        if not self.cfg:
            return None
        snapshot: dict[str, object] = {}
        try:
            with Database(self.cfg.db_path) as db:
                today = date.today()
                cidrs = self.cfg.scan.cidrs
                snapshot["total"] = db.count_inventory(cidrs)
                snapshot["online"] = db.count_online(cidrs)
                snapshot["days"] = {
                    day: len(db.fetch_unused(cidrs, today, day))
                    for day in self.cfg.unused_days
                }
                snapshot["last"] = db.last_scan()
                snapshot["recent"] = db.recent_scans(6)
                snapshot["stats"] = db.scan_stats()
                snapshot["last_success"] = db.last_success_date()
                start = today - timedelta(days=13)
                counts = dict(db.daily_online_counts(start, today))
                snapshot["daily"] = [
                    (
                        (start + timedelta(days=offset)).isoformat(),
                        counts.get((start + timedelta(days=offset)).isoformat(), 0),
                    )
                    for offset in range(14)
                ]
        except Exception as exc:
            self.logger.exception("读取统计数据失败")
            self.toast(f"读取数据失败：{exc}", "error")
            return None
        return snapshot

    def refresh_dashboard(self) -> None:
        snapshot = self._snapshot()
        if not snapshot:
            self.stat_total.set_value("—", "配置或数据库不可用")
            self.stat_online.set_value("—", "配置或数据库不可用")
            self.stat_unused_a.set_value("—", "配置或数据库不可用")
            self.stat_unused_b.set_value("—", "配置或数据库不可用")
            self._clear_recent_rows()
            self.donut.update_data(0, 0)
            return

        total = int(snapshot["total"])  # type: ignore[arg-type]
        online = int(snapshot["online"])  # type: ignore[arg-type]
        days_counts: dict[int, int] = snapshot["days"]  # type: ignore[assignment]
        cfg_days = list(self.cfg.unused_days) if self.cfg else [7, 15]
        first_day = cfg_days[0]
        second_day = cfg_days[1] if len(cfg_days) > 1 else cfg_days[0]
        total_hint = ""
        if self.cfg:
            total_hint = (
                self.cfg.scan.cidr
                if len(self.cfg.scan.cidrs) == 1
                else f"{len(self.cfg.scan.cidrs)} 个网段"
            )
        self.stat_total.set_value(str(total), total_hint)
        last = snapshot["last"]
        if last is not None:
            last_status = str(last["status"])
            hint = (
                f"{last['scan_date']} · {'成功' if last_status == 'success' else '失败'}"
            )
            self.stat_online.set_value(str(online), hint)
        else:
            self.stat_online.set_value(str(online), "尚无扫描记录")
        self.stat_unused_a.title_label.configure(text=f"{first_day} 天未使用")
        self.stat_unused_a.set_value(
            str(days_counts.get(first_day, 0)), "含从未活跃"
        )
        self.stat_unused_b.title_label.configure(
            text=f"{second_day} 天未使用" if len(cfg_days) > 1 else "未使用（扩展）"
        )
        self.stat_unused_b.set_value(
            str(days_counts.get(second_day, 0)), "含从未活跃"
        )

        never = 0
        try:
            with Database(self.cfg.db_path) as db:
                never = sum(
                    1
                    for row in db.fetch_inventory(self.cfg.scan.cidrs)
                    if not row["last_seen_date"]
                )
        except Exception:
            pass
        self.donut.update_data(online, total)
        self.legend_online.configure(text=str(online))
        self.legend_offline.configure(text=str(max(total - online, 0)))
        self.legend_unused.configure(text=str(never))

        self.last_success_label.configure(
            text=str(snapshot["last_success"] or "尚无成功记录")
        )
        self._fill_recent_rows(snapshot["recent"])  # type: ignore[arg-type]

    def _clear_recent_rows(self) -> None:
        for child in self.recent_rows.winfo_children():
            child.destroy()

    def _fill_recent_rows(self, rows) -> None:
        self._clear_recent_rows()
        if not rows:
            ctk.CTkLabel(
                self.recent_rows,
                text="暂无扫描记录，点击“开始扫描”创建第一条记录。",
                font=_font(9),
                text_color=C["muted"],
                anchor="w",
            ).grid(row=0, column=0, sticky="w", pady=14)
            return
        for index, row in enumerate(rows):
            frame = ctk.CTkFrame(
                self.recent_rows,
                fg_color="transparent",
                height=40,
            )
            frame.grid(row=index, column=0, sticky="ew")
            for column, weight in ((0, 2), (1, 1), (2, 1), (3, 1), (4, 3)):
                frame.grid_columnconfigure(column, weight=weight)
            values = (
                (0, 2, str(row["scan_date"]), C["text_soft"]),
                (1, 1, "", C["text_soft"]),
                (2, 1, str(row["online_count"]), C["text_soft"]),
                (3, 1, _format_duration(row["duration_ms"]), C["text_soft"]),
                (4, 3, _ellipsize(str(row["error"] or "—"), 56), C["muted"]),
            )
            for column, weight, value, color in values:
                if column == 1:
                    status = str(row["status"])
                    success = status == "success"
                    ctk.CTkLabel(
                        frame,
                        text="成功" if success else "失败",
                        font=_font(8, "bold"),
                        text_color=C["green"] if success else C["red"],
                        fg_color=C["green_soft"] if success else C["red_soft"],
                        corner_radius=6,
                        width=42,
                        height=20,
                    ).grid(row=0, column=column, sticky="w", padx=(0, 8), pady=7)
                    continue
                ctk.CTkLabel(
                    frame,
                    text=value,
                    font=_font(9),
                    text_color=color,
                    anchor="w",
                ).grid(
                    row=0,
                    column=column,
                    sticky="w",
                    padx=(0, 8),
                    pady=7,
                )

    def refresh_inventory(self) -> None:
        if not self.cfg:
            self._inventory_rows = []
            self._render_inventory()
            return
        try:
            with Database(self.cfg.db_path) as db:
                rows = db.fetch_inventory(self.cfg.scan.cidrs)
        except Exception as exc:
            self.logger.exception("读取库存失败")
            self.toast(f"读取库存失败：{exc}", "error")
            return
        today = date.today()
        data: list[dict[str, object]] = []
        for row in rows:
            last_seen = row["last_seen_date"]
            last_date = _parse_date(last_seen)
            if last_seen is None:
                status = "never"
                status_text = "从未活跃"
                days_unused = "-"
            elif row["last_status"] == "online":
                status = "online"
                status_text = "在线"
                days_unused = str(max((today - last_date).days, 0)) if last_date else "-"
            else:
                status = "offline"
                status_text = "离线"
                days_unused = str(max((today - last_date).days, 0)) if last_date else "-"
            data.append(
                {
                    "ip": str(row["ip_address"]),
                    "status": status,
                    "status_text": status_text,
                    "last": last_seen or "—",
                    "days": days_unused,
                    "mac": row["mac_address"] or "—",
                    "first": row["first_seen_date"] or "—",
                    "updated": row["updated_at"] or "—",
                }
            )
        data.sort(key=lambda item: natural_ip_key(str(item["ip"])))
        self._inventory_rows = data
        self._render_inventory()

    def _schedule_inventory_filter(self) -> None:
        if self._search_after_id:
            try:
                self.after_cancel(self._search_after_id)
            except Exception:
                pass
        self._search_after_id = self.after(180, self._render_inventory)

    def _render_inventory(self) -> None:
        if not hasattr(self, "inventory_tree"):
            return
        tree = self.inventory_tree
        for item in tree.get_children():
            tree.delete(item)
        query = self.search_entry.get().strip().lower()
        selected = self.filter_segment.get()
        shown = 0
        for row in self._inventory_rows:
            if query:
                haystack = f"{row['ip']} {row['mac']}".lower()
                if query not in haystack:
                    continue
            status = str(row["status"])
            if selected == "在线" and status != "online":
                continue
            if selected == "离线" and status != "offline":
                continue
            if selected == "从未活跃" and status != "never":
                continue
            tree.insert(
                "",
                "end",
                values=(
                    row["ip"],
                    row["status_text"],
                    row["mac"],
                    row["last"],
                    row["days"],
                    row["updated"],
                ),
                tags=(status,),
            )
            shown += 1
        self.inventory_count.configure(
            text=f"共 {len(self._inventory_rows)} 个 IP · 当前显示 {shown} 个"
        )

    def refresh_history(self) -> None:
        snapshot = self._snapshot()
        if not snapshot:
            self.hist_total.set_value("—")
            self.hist_success.set_value("—")
            self.hist_failed.set_value("—")
            self.hist_last.set_value("—")
            return
        total, success, failed = snapshot["stats"]  # type: ignore[misc]
        self.hist_total.set_value(str(total))
        self.hist_success.set_value(str(success))
        self.hist_failed.set_value(str(failed))
        self.hist_last.set_value(str(snapshot["last_success"] or "—"))
        self.history_chart.update_data(snapshot["daily"])  # type: ignore[arg-type]

        tree = self.history_tree
        for item in tree.get_children():
            tree.delete(item)
        for row in snapshot["recent"]:  # type: ignore[union-attr]
            status = str(row["status"])
            tree.insert(
                "",
                "end",
                values=(
                    row["scan_date"],
                    "成功" if status == "success" else "失败",
                    row["online_count"],
                    _format_duration(row["duration_ms"]),
                    _ellipsize(str(row["error"] or "—"), 90),
                ),
                tags=(status,),
            )

    def refresh_report_files(self) -> None:
        if not hasattr(self, "report_list"):
            return
        for child in self.report_list.winfo_children():
            child.destroy()
        files: list[Path] = []
        if self.cfg:
            folder = self.cfg.report_dir
            if folder.exists():
                files = sorted(
                    folder.glob("*.csv"),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
        if not files:
            ctk.CTkLabel(
                self.report_list,
                text="暂无报表，点击上方“生成报表”创建。",
                font=_font(9),
                text_color=C["muted"],
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=8, pady=16)
            if self._preview_path is None:
                self._clear_preview()
            return
        for index, path in enumerate(files):
            row = ctk.CTkFrame(
                self.report_list,
                fg_color=C["surface"] if index % 2 == 0 else "transparent",
                corner_radius=9,
            )
            row.grid(row=index, column=0, sticky="ew", padx=2, pady=2)
            row.grid_columnconfigure(0, weight=1)
            name = path.name
            ctk.CTkLabel(
                row,
                text=_ellipsize(name, 30),
                font=_font(9, "bold"),
                text_color=C["text_soft"],
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=12, pady=(7, 0))
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            ctk.CTkLabel(
                row,
                text=f"{format_bytes(path.stat().st_size)} · {modified:%m-%d %H:%M}",
                font=_font(8),
                text_color=C["muted"],
                anchor="w",
            ).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 7))
            ctk.CTkButton(
                row,
                text="查看",
                width=52,
                height=26,
                corner_radius=7,
                fg_color=C["accent_soft"],
                hover_color=C["accent"],
                text_color=C["text"],
                font=_font(8),
                command=lambda target=path: self.preview_report(target),
            ).grid(row=0, column=1, rowspan=2, padx=(4, 4))
            ctk.CTkButton(
                row,
                text="打开",
                width=52,
                height=26,
                corner_radius=7,
                fg_color="transparent",
                border_width=1,
                border_color=C["line"],
                hover_color=C["card_hover"],
                text_color=C["text_soft"],
                font=_font(8),
                command=lambda target=path: self.open_file(target),
            ).grid(row=0, column=2, rowspan=2, padx=(0, 10))
            row.bind("<Double-Button-1>", lambda _event, target=path: self.preview_report(target))
        if self._preview_path is None or not self._preview_path.exists():
            self.preview_report(files[0])

    def _clear_preview(self) -> None:
        self.preview_title.configure(text="未选择报表")
        self.preview_meta.configure(text="从左侧选择报表即可在软件内查看")
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)
        headings = [
            "ip_address",
            "last_seen_date",
            "days_unused",
            "mac_address",
            "never_active",
            "status",
        ]
        columns = [f"c{index}" for index in range(len(headings))]
        self.preview_tree.configure(columns=columns, displaycolumns=columns)
        for index, heading in enumerate(headings):
            column = f"c{index}"
            self.preview_tree.heading(column, text=heading)

    def preview_report(self, path: Path) -> None:
        self._preview_path = path
        self.preview_title.configure(text=path.name)
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader, [])
                all_rows = [row for row in reader]
        except Exception as exc:
            self.preview_meta.configure(text=f"无法读取：{exc}")
            return
        rows = all_rows[:200]
        never_count = 0
        if "never_active" in header:
            index = header.index("never_active")
            never_count = sum(
                1
                for row in all_rows
                if len(row) > index and row[index] == "是"
            )
        column_count = max(len(header), 1)
        columns = [f"c{index}" for index in range(column_count)]
        self.preview_tree.configure(columns=columns, displaycolumns=columns)
        for index, heading in enumerate(header):
            column = f"c{index}"
            width = 150 if index == 0 else 120
            self.preview_tree.heading(column, text=heading)
            self.preview_tree.column(column, width=width, anchor="w")
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)
        for row in rows:
            padded = list(row) + [""] * (column_count - len(row))
            self.preview_tree.insert("", "end", values=padded[:column_count])
        self.preview_meta.configure(
            text=(
                f"共 {len(all_rows)} 条 · 从未活跃 {never_count} 条 · "
                f"当前显示 {len(rows)} 行 · UTF-8-BOM"
            )
        )

    def open_preview_file(self) -> None:
        if self._preview_path and self._preview_path.exists():
            self.open_file(self._preview_path)
        else:
            self.toast("请先选择一份报表", "info")

    # --------------------------------------------------------------------- logs

    def _build_logs_page(self) -> None:
        page = ctk.CTkFrame(self.container, fg_color=C["bg"], corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        self._pages["logs"] = page

        toolbar = ctk.CTkFrame(page, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=28, pady=(18, 12))
        toolbar.grid_columnconfigure(1, weight=1)

        self.log_search_entry = ctk.CTkEntry(
            toolbar,
            width=300,
            height=42,
            corner_radius=10,
            fg_color=C["card"],
            border_color=C["line"],
            border_width=1,
            placeholder_text="搜索日志内容…",
            placeholder_text_color=C["muted"],
            text_color=C["text"],
            font=_font(10),
        )
        self.log_search_entry.grid(row=0, column=0, sticky="w")
        self.log_search_entry.bind("<KeyRelease>", lambda _event: self.refresh_logs())

        self.log_level_segment = ctk.CTkSegmentedButton(
            toolbar,
            values=["全部", "INFO", "WARNING", "ERROR"],
            width=300,
            height=42,
            corner_radius=10,
            fg_color=C["card"],
            selected_color=C["accent"],
            selected_hover_color=C["accent_hover"],
            unselected_color=C["card"],
            unselected_hover_color=C["card_hover"],
            text_color=C["text"],
            font=_font(10),
            command=lambda _value: self.refresh_logs(),
        )
        self.log_level_segment.set("全部")
        self.log_level_segment.grid(row=0, column=2, sticky="e", padx=(0, 8))

        ctk.CTkButton(
            toolbar,
            text="刷新",
            width=72,
            height=42,
            corner_radius=10,
            fg_color="transparent",
            border_width=1,
            border_color=C["line"],
            hover_color=C["card_hover"],
            text_color=C["text_soft"],
            font=_font(10),
            command=self.refresh_logs,
        ).grid(row=0, column=3, padx=(0, 8))

        ctk.CTkButton(
            toolbar,
            text="打开日志目录",
            width=116,
            height=42,
            corner_radius=10,
            fg_color=C["accent"],
            hover_color=C["accent_hover"],
            text_color="#FFFFFF",
            font=_font(10, "bold"),
            command=self.open_log_dir,
        ).grid(row=0, column=4)

        card = self._card(page)
        card.grid(row=1, column=0, sticky="nsew", padx=28, pady=(0, 26))
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)
        holder = ctk.CTkFrame(card, fg_color="transparent")
        holder.grid(row=0, column=0, sticky="nsew", padx=14, pady=(14, 0))
        self.log_tree = self._tree(
            holder,
            ["time", "level", "message"],
            ["时间", "级别", "内容"],
            [170, 110, 600],
            height=10,
        )
        self.log_tree.tag_configure("ERROR", foreground=C["red"])
        self.log_tree.tag_configure("WARNING", foreground=C["amber"])
        self.log_tree.tag_configure("INFO", foreground=C["text_soft"])
        footer = ctk.CTkFrame(card, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", padx=20, pady=(8, 12))
        self.log_count = ctk.CTkLabel(
            footer,
            text="尚未读取日志",
            font=_font(9),
            text_color=C["muted"],
            anchor="w",
        )
        self.log_count.pack(side="left")

    def refresh_logs(self) -> None:
        if not hasattr(self, "log_tree"):
            return
        tree = self.log_tree
        for item in tree.get_children():
            tree.delete(item)
        if not self.cfg:
            self.log_count.configure(text="配置不可用，无法读取日志")
            return
        log_path = self.cfg.log_dir / "ip_activity_auditor.log"
        lines: list[str] = []
        if log_path.exists():
            try:
                with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                    lines = handle.readlines()[-2000:]
            except Exception as exc:
                self.log_count.configure(text=f"读取日志失败：{exc}")
                return
        else:
            self.log_count.configure(text=f"日志文件不存在：{log_path}")
            return

        pattern = re.compile(
            r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] (.*)$"
        )
        level_filter = self.log_level_segment.get()
        query = self.log_search_entry.get().strip().lower()
        shown = 0
        for line in reversed(lines):
            text = line.rstrip("\r\n")
            match = pattern.match(text)
            if match:
                timestamp, level, message = match.groups()
            else:
                timestamp, level, message = "", "INFO", text
            if level_filter != "全部" and level != level_filter:
                continue
            if query and query not in text.lower():
                continue
            tree.insert(
                "",
                "end",
                values=(timestamp, level, message),
                tags=(level,),
            )
            shown += 1
        self.log_count.configure(
            text=f"最近 {len(lines)} 行 · 当前显示 {shown} 条 · {log_path}"
        )

    # ------------------------------------------------------------------ workers

    def _post(self, callback: Callable[[], None]) -> None:
        self._ui_queue.put(callback)

    def _drain_ui_queue(self) -> None:
        if self._closing:
            return
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                try:
                    callback()
                except Exception:
                    self.logger.exception("界面回调执行失败")
        except queue.Empty:
            pass
        self.after(60, self._drain_ui_queue)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        for button in self._action_buttons:
            button.configure(state="disabled" if busy else "normal")
        if busy:
            self._action_started = time.perf_counter()
            self.progress.configure(mode="indeterminate")
            self.progress.start()
            self.action_status.configure(text=message)
            self._start_ticker(message)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(0)
            if self._tick_after_id:
                try:
                    self.after_cancel(self._tick_after_id)
                except Exception:
                    pass
                self._tick_after_id = None
            self.action_status.configure(text=message or "就绪")

    def _start_ticker(self, base: str) -> None:
        if self._tick_after_id:
            try:
                self.after_cancel(self._tick_after_id)
            except Exception:
                pass

        def tick() -> None:
            if not self._busy:
                return
            elapsed = time.perf_counter() - self._action_started
            self.action_status.configure(text=f"{base} · 已用时 {elapsed:.1f}s")
            self._tick_after_id = self.after(400, tick)

        tick()

    def _require_config(self) -> AppConfig | None:
        if self.cfg:
            return self.cfg
        self.toast("配置不可用，请先在设置页修复配置", "error")
        self._show_page("settings")
        return None

    def start_scan(self) -> None:
        cfg = self._require_config()
        if not cfg or self._busy:
            return
        backend = resolve_backend(cfg.scan)
        if backend == "scapy" and not npcap_present():
            self.toast(
                "Scapy 后端需要 Npcap。请安装 Npcap，或在设置中改用 SendARP/自动后端",
                "error",
            )
            return
        if backend == "scapy" and not is_admin():
            accepted = confirm_dialog(
                self,
                "需要管理员权限",
                "ARP 扫描需要管理员权限。是否以管理员身份重新启动程序？\n\n"
                "重新启动后，请在概览页再次点击“开始扫描”。",
                ok_text="以管理员重启",
            )
            if accepted:
                if not relaunch_as_admin(self.config_path):
                    self.toast("未能获取管理员权限（UAC 可能已取消）", "error")
                else:
                    self._quit_app()
            return
        self._set_busy(True, "正在扫描网段")
        threading.Thread(
            target=self._scan_worker,
            args=(cfg, self.auto_report_var.get()),
            daemon=True,
        ).start()

    def _scan_worker(self, cfg: AppConfig, auto_report: bool) -> None:
        db: Database | None = None
        started = time.perf_counter()
        scan_succeeded = False
        self.logger.info(
            "GUI 开始扫描：%s，后端 %s", cfg.scan.cidr, resolve_backend(cfg.scan)
        )
        try:
            db = Database(cfg.db_path)
            for network in cfg.scan.networks:
                db.initialize_inventory(
                    str(network), (str(ip) for ip in network.hosts())
                )
            try:
                result = scan(cfg.scan)
            except ScanError as exc:
                duration = int((time.perf_counter() - started) * 1000)
                db.record_failed_run(
                    cidr=cfg.scan.cidr,
                    scan_date=date.today().isoformat(),
                    duration_ms=duration,
                    error=str(exc),
                )
                self.logger.error("GUI 扫描失败：%s", exc)
                self._post(
                    lambda message=str(exc): self._finish_action(
                        False, f"扫描失败：{message}", error=True
                    )
                )
                return
            groups: list[tuple[str, list[str]]] = []
            for network in cfg.scan.networks:
                groups.append(
                    (
                        str(network),
                        [
                            ip
                            for ip in result.online_ips
                            if ipaddress.ip_address(ip) in network
                        ],
                    )
                )
            db.apply_scan_success_multi(
                groups=groups,
                cidr_label=cfg.scan.cidr,
                scan_date=date.today().isoformat(),
                duration_ms=result.duration_ms,
                mac_map=result.mac_map,
            )
            scan_succeeded = True
            report_files: list[tuple[Path, int]] = []
            report_error: str | None = None
            if auto_report:
                try:
                    report_files = generate_reports(
                        db,
                        cfg.report_dir,
                        cfg.scan.cidrs,
                        date.today(),
                        cfg.unused_days,
                    )
                except Exception as exc:
                    report_error = str(exc)
                    self.logger.exception("扫描成功，但自动生成报表失败")
            backend_label = "SendARP" if result.backend == "sendarp" else "Scapy"
            self.logger.info(
                "GUI 扫描完成：后端 %s，在线 %d，耗时 %d ms",
                backend_label,
                len(result.online_ips),
                result.duration_ms,
            )
            message = (
                f"扫描完成（{backend_label}，{len(cfg.scan.cidrs)} 个网段）："
                f"在线 {len(result.online_ips)} 个 IP，"
                f"耗时 {_format_duration(result.duration_ms)}"
            )
            if report_error:
                message += "；自动报表生成失败"
            self._post(
                lambda message=message, count=len(report_files): self._finish_action(
                    True,
                    message,
                    report_count=count,
                )
            )
        except Exception as exc:
            self.logger.exception("GUI 扫描异常")
            message = str(exc)
            if db is not None and not scan_succeeded:
                try:
                    db.record_failed_run(
                        cidr=cfg.scan.cidr,
                        scan_date=date.today().isoformat(),
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        error=message,
                    )
                except Exception:
                    self.logger.exception("写入失败扫描记录出错")
            prefix = "保存扫描结果失败" if scan_succeeded else "扫描失败"
            self._post(
                lambda message=message, prefix=prefix: self._finish_action(
                    False, f"{prefix}：{message}", error=True
                )
            )
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    def initialize_inventory(self) -> None:
        cfg = self._require_config()
        if not cfg or self._busy:
            return
        self._set_busy(True, "正在初始化库存")
        threading.Thread(target=self._init_worker, args=(cfg,), daemon=True).start()

    def _init_worker(self, cfg: AppConfig) -> None:
        db: Database | None = None
        try:
            db = Database(cfg.db_path)
            inserted = 0
            for network in cfg.scan.networks:
                inserted += db.initialize_inventory(
                    str(network), (str(ip) for ip in network.hosts())
                )
            total = db.count_inventory(cfg.scan.cidrs)
            self.logger.info("GUI 初始化库存：%s，新增 %d，总计 %d", cfg.scan.cidr, inserted, total)
            self._post(
                lambda: self._finish_action(
                    True, f"库存初始化完成：共 {total} 个 IP（新增 {inserted}）"
                )
            )
        except Exception as exc:
            self.logger.exception("GUI 初始化库存失败")
            self._post(
                lambda message=str(exc): self._finish_action(
                    False, f"初始化失败：{message}", error=True
                )
            )
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    def generate_reports_from_config(self) -> None:
        self._start_report_worker(None)

    def generate_reports_from_input(self) -> None:
        raw = self.report_days_entry.get().strip()
        try:
            days = parse_days(raw)
        except ConfigError as exc:
            self.toast(str(exc), "error")
            return
        self._start_report_worker(days)

    def _start_report_worker(self, days: tuple[int, ...] | None) -> None:
        cfg = self._require_config()
        if not cfg or self._busy:
            return
        self._set_busy(True, "正在生成报表")
        threading.Thread(
            target=self._report_worker, args=(cfg, days or cfg.unused_days), daemon=True
        ).start()

    def _report_worker(self, cfg: AppConfig, days: tuple[int, ...]) -> None:
        db: Database | None = None
        try:
            db = Database(cfg.db_path)
            if db.count_inventory(cfg.scan.cidrs) == 0:
                self._post(
                    lambda: self._finish_action(
                        False, "库存为空，请先执行扫描或初始化库存", error=True
                    )
                )
                return
            files = generate_reports(
                db, cfg.report_dir, cfg.scan.cidrs, date.today(), days
            )
            self.logger.info("GUI 生成报表：%s", files)
            self._post(
                lambda: self._finish_action(
                    True,
                    f"已生成 {len(files)} 份报表",
                    refresh_reports=True,
                )
            )
        except Exception as exc:
            self.logger.exception("GUI 生成报表失败")
            self._post(
                lambda message=str(exc): self._finish_action(
                    False, f"报表生成失败：{message}", error=True
                )
            )
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    def _finish_action(
        self,
        success: bool,
        message: str,
        *,
        error: bool = False,
        report_count: int = 0,
        refresh_reports: bool = False,
    ) -> None:
        if self._closing:
            return
        self._set_busy(False, message)
        self.toast(message, "success" if success else "error")
        self.refresh_all()
        if refresh_reports or report_count:
            self.refresh_report_files()
            if report_count:
                self._report_days_default()
        if self._is_window_hidden():
            self._tray_notify(message)

    def export_inventory(self) -> None:
        cfg = self._require_config()
        if not cfg:
            return
        if not self._inventory_rows:
            self.refresh_inventory()
        if not self._inventory_rows:
            self.toast("没有可导出的库存数据", "info")
            return
        output = cfg.report_dir / f"IP库存_{datetime.now():%Y%m%d_%H%M%S}.csv"
        try:
            cfg.report_dir.mkdir(parents=True, exist_ok=True)
            with output.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "ip_address",
                        "status",
                        "mac_address",
                        "last_seen_date",
                        "days_unused",
                        "first_seen_date",
                        "updated_at",
                    ]
                )
                for row in self._inventory_rows:
                    writer.writerow(
                        [
                            row["ip"],
                            row["status_text"],
                            row["mac"],
                            row["last"],
                            row["days"],
                            row["first"],
                            row["updated"],
                        ]
                    )
            self.logger.info("导出库存：%s", output)
            self.toast(f"库存已导出：{output.name}", "success")
        except Exception as exc:
            self.logger.exception("导出库存失败")
            self.toast(f"导出失败：{exc}", "error")

    # ----------------------------------------------------------------- settings

    def _populate_settings(self) -> None:
        if not hasattr(self, "entries"):
            return
        if self._settings_populated and self.cfg:
            return
        values = {
            "cidr": "192.168.0.0/24",
            "backend": "auto",
            "timeout_ms": "1500",
            "retries": "1",
            "db_path": "data/ip_activity.db",
            "report_dir": "reports",
            "unused_days": "7,15",
            "log_dir": "logs",
        }
        if self.cfg:
            values = {
                "cidr": "\n".join(self.cfg.scan.cidrs),
                "backend": self.cfg.scan.backend,
                "timeout_ms": str(self.cfg.scan.timeout_ms),
                "retries": str(self.cfg.scan.retries),
                "db_path": str(self.cfg.db_path),
                "report_dir": str(self.cfg.report_dir),
                "unused_days": ",".join(str(day) for day in self.cfg.unused_days),
                "log_dir": str(self.cfg.log_dir),
            }
        for key, entry in self.entries.items():
            entry.delete(0, "end")
            entry.insert(0, values.get(key, ""))
        self.cidr_textbox.delete("1.0", "end")
        self.cidr_textbox.insert("1.0", values.get("cidr", ""))
        backend_menu = self.options.get("backend")
        if backend_menu is not None:
            backend_menu.set(BACKEND_LABELS.get(values["backend"], "自动（推荐）"))
        self._settings_populated = True
        self._refresh_system_settings()

    def _refresh_system_settings(self) -> None:
        if not hasattr(self, "settings_admin"):
            return
        admin = is_admin()
        backend_name, backend_ok, backend_detail = backend_status(
            self.cfg.scan if self.cfg else None
        )
        if admin:
            admin_text = "已启用（管理员）"
        elif backend_name == "SendARP":
            admin_text = "普通权限（SendARP 可直接扫描）"
        else:
            admin_text = "普通权限（Scapy 扫描前需提权）"
        self.settings_admin.configure(
            text=admin_text,
            text_color=C["green"] if admin else C["amber"],
        )
        self.settings_backend.configure(
            text=f"{backend_name} · {backend_detail}",
            text_color=C["green"] if backend_ok else C["red"],
        )
        exists = self._scheduled_task_exists()
        self.settings_task.configure(
            text=f"已创建（{TASK_NAME}）" if exists else "未创建",
            text_color=C["green"] if exists else C["muted"],
        )
        if hasattr(self, "tray_status"):
            tray_running = self._tray_icon is not None
            self.tray_status.configure(
                text="托盘运行中" if tray_running else "系统托盘不可用",
                text_color=C["green"] if tray_running else C["red"],
            )
        if hasattr(self, "autostart_var"):
            self.autostart_var.set(autostart_enabled())

    def toggle_autostart(self) -> None:
        enabled = bool(self.autostart_var.get())
        try:
            set_autostart(enabled, self.config_path)
        except Exception as exc:
            self.autostart_var.set(autostart_enabled())
            self.logger.exception("设置开机自启动失败")
            self.toast(f"设置开机自启动失败：{exc}", "error")
            return
        if enabled:
            self.logger.info("已启用开机自启动：%s", self.config_path)
            self.toast("已启用开机自启动，将以静默托盘模式运行", "success")
        else:
            self.logger.info("已关闭开机自启动")
            self.toast("已关闭开机自启动", "info")

    def hide_window_to_tray(self) -> None:
        if self.hide_to_tray():
            self.logger.info("用户从设置页隐藏到系统托盘")
        else:
            self.toast("系统托盘不可用，无法隐藏窗口", "error")

    def exit_from_settings(self) -> None:
        accepted = confirm_dialog(
            self,
            "退出程序",
            "退出后，正在运行的后台扫描或报表任务将终止。确定退出吗？",
            ok_text="退出",
            danger=True,
        )
        if accepted:
            self._quit_app()

    def save_settings(self) -> None:
        values = {key: entry.get().strip() for key, entry in self.entries.items()}
        cidr_text = self.cidr_textbox.get("1.0", "end-1c").strip()
        backend_menu = self.options.get("backend")
        backend = (
            BACKEND_OPTIONS.get(backend_menu.get(), "auto")
            if backend_menu is not None
            else "auto"
        )
        try:
            cfg = save_config(
                self.config_path,
                cidr=cidr_text,
                timeout_ms=values["timeout_ms"],
                retries=values["retries"],
                db_path=values["db_path"],
                report_dir=values["report_dir"],
                unused_days=values["unused_days"],
                log_dir=values["log_dir"],
                backend=backend,
            )
        except ConfigError as exc:
            self.toast(f"配置无效：{exc}", "error")
            return
        except Exception as exc:
            self.logger.exception("保存配置失败")
            self.toast(f"保存失败：{exc}", "error")
            return
        self.cfg = cfg
        self.cfg_error = None
        self._settings_populated = False
        self._hide_config_error_banner()
        self.logger = setup_logging(cfg.log_dir)
        self.logger.info("配置已保存：%s", self.config_path)
        self._report_days_default()
        self._populate_settings()
        self.refresh_all()
        self.toast("配置已保存并生效", "success")

    def write_default_and_reload(self) -> None:
        try:
            write_default_config(self.config_path, overwrite=True)
            self.cfg = load_config(self.config_path)
            self.cfg_error = None
            self._settings_populated = False
            self._hide_config_error_banner()
            self.logger = setup_logging(self.cfg.log_dir)
            self._populate_settings()
            self.refresh_all()
            self.toast("已写入默认配置", "success")
        except Exception as exc:
            self.toast(f"写入默认配置失败：{exc}", "error")

    def open_config_file(self) -> None:
        if self.config_path.exists():
            self.open_file(self.config_path)
        else:
            self.toast("配置文件不存在", "error")

    def open_data_dir(self) -> None:
        if self.cfg:
            self.cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
            open_path(self.cfg.db_path.parent)
        else:
            open_path(self.base_dir)

    def open_log_dir(self) -> None:
        folder = self.cfg.log_dir if self.cfg else self.base_dir / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        open_path(folder)

    def open_report_dir(self) -> None:
        if self.cfg:
            self.cfg.report_dir.mkdir(parents=True, exist_ok=True)
            open_path(self.cfg.report_dir)
        else:
            self.toast("配置不可用", "error")

    def open_file(self, path: Path) -> None:
        try:
            open_path(path)
        except Exception as exc:
            self.toast(f"无法打开文件：{exc}", "error")

    def restart_as_admin(self) -> None:
        if is_admin():
            self.toast("当前已经是管理员权限", "info")
            return
        accepted = confirm_dialog(
            self,
            "以管理员身份重启",
            "重新启动后将关闭当前窗口，并在新窗口中继续操作。",
            ok_text="重启",
        )
        if not accepted:
            return
        if relaunch_as_admin(self.config_path):
            self._quit_app()
        else:
            self.toast("未能获取管理员权限（UAC 可能已取消）", "error")

    # -------------------------------------------------------------- task plan

    def _scheduled_task_exists(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", TASK_NAME],
                capture_output=True,
                text=True,
                encoding=locale.getpreferredencoding(False),
                errors="ignore",
                creationflags=_no_window_flags(),
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _prepare_task_launcher(self) -> Path:
        if is_frozen():
            command = f'"{Path(sys.executable).resolve()}"'
        else:
            command = (
                f'"{sys.executable}" '
                f'"{Path(self.base_dir) / "src" / "main.py"}"'
            )
        command += f' --config "{self.config_path}"'
        launcher = self.config_path.parent / "run_scan.cmd"
        content = (
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            f"{command}\r\n"
            "exit /b %ERRORLEVEL%\r\n"
        )
        launcher.write_text(content, encoding="utf-8", newline="")
        return launcher

    def create_scheduled_task(self) -> None:
        if sys.platform != "win32":
            self.toast("仅支持 Windows 任务计划", "error")
            return
        if not is_admin():
            accepted = confirm_dialog(
                self,
                "需要管理员权限",
                "创建每日任务需要管理员权限。是否以管理员身份重新启动程序？\n\n"
                "重启后请在设置页再次点击“创建 / 更新任务”。",
                ok_text="以管理员重启",
            )
            if accepted:
                if relaunch_as_admin(self.config_path):
                    self._quit_app()
                else:
                    self.toast("未能获取管理员权限", "error")
            return
        time_text = self.task_time_entry.get().strip()
        try:
            datetime.strptime(time_text, "%H:%M")
        except ValueError:
            self.toast("时间格式应为 HH:MM，例如 23:30", "error")
            return
        accepted = confirm_dialog(
            self,
            "创建每日任务",
            f"将创建/更新任务计划“{TASK_NAME}”，每天 {time_text} 以 SYSTEM 最高权限"
            "运行扫描并生成报表。是否继续？",
            ok_text="创建任务",
        )
        if not accepted:
            return
        try:
            launcher = self._prepare_task_launcher()
        except Exception as exc:
            self.toast(f"无法创建任务启动脚本：{exc}", "error")
            return
        try:
            result = subprocess.run(
                [
                    "schtasks",
                    "/Create",
                    "/TN",
                    TASK_NAME,
                    "/TR",
                    str(launcher),
                    "/SC",
                    "DAILY",
                    "/ST",
                    time_text,
                    "/RL",
                    "HIGHEST",
                    "/RU",
                    "SYSTEM",
                    "/F",
                ],
                capture_output=True,
                text=True,
                encoding=locale.getpreferredencoding(False),
                errors="ignore",
                creationflags=_no_window_flags(),
                timeout=30,
            )
        except Exception as exc:
            self.logger.exception("创建任务计划失败")
            self.toast(f"创建任务失败：{exc}", "error")
            return
        if result.returncode == 0:
            self.logger.info("已创建任务计划：%s", launcher)
            self.toast(f"任务计划已创建，每天 {time_text} 运行", "success")
        else:
            message = (result.stderr or result.stdout or "未知错误").strip()
            self.logger.error("创建任务计划失败：%s", message)
            self.toast(f"创建任务失败：{_ellipsize(message, 80)}", "error")
        self._refresh_system_settings()

    def delete_scheduled_task(self) -> None:
        if sys.platform != "win32":
            return
        if not self._scheduled_task_exists():
            self.toast("任务计划不存在", "info")
            return
        if not is_admin():
            self.toast("删除任务需要管理员权限，请先以管理员身份重启", "error")
            return
        accepted = confirm_dialog(
            self,
            "删除每日任务",
            f"确定删除任务计划“{TASK_NAME}”吗？不会删除任何历史数据。",
            ok_text="删除",
            danger=True,
        )
        if not accepted:
            return
        try:
            result = subprocess.run(
                ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                capture_output=True,
                text=True,
                encoding=locale.getpreferredencoding(False),
                errors="ignore",
                creationflags=_no_window_flags(),
                timeout=20,
            )
            if result.returncode == 0:
                self.logger.info("已删除任务计划 %s", TASK_NAME)
                self.toast("任务计划已删除", "success")
            else:
                message = (result.stderr or result.stdout or "未知错误").strip()
                self.toast(f"删除失败：{_ellipsize(message, 80)}", "error")
        except Exception as exc:
            self.logger.exception("删除任务计划失败")
            self.toast(f"删除失败：{exc}", "error")
        self._refresh_system_settings()

    # --------------------------------------------------------------------- UI

    def _refresh_system_chips(self) -> None:
        if not hasattr(self, "admin_dot"):
            return
        admin = is_admin()
        self.admin_label.configure(text="管理员权限" if admin else "普通权限")
        self.admin_dot.configure(text_color=C["green"] if admin else C["amber"])
        self.admin_chip.configure(
            border_color=C["line_soft"] if admin else "#F6DFC0",
        )
        backend_name, backend_ok, _detail = backend_status(
            self.cfg.scan if self.cfg else None
        )
        self.backend_label.configure(text=backend_name)
        self.backend_dot.configure(text_color=C["green"] if backend_ok else C["red"])
        self.backend_chip.configure(
            border_color=C["line_soft"] if backend_ok else "#F3C7C7"
        )
        self._refresh_system_settings()

    def toast(self, message: str, kind: str = "info") -> None:
        if self._closing:
            return
        if self._toast is not None:
            try:
                self._toast.destroy()
            except Exception:
                pass
            self._toast = None
        colors = {
            "success": (C["green_soft"], C["green"]),
            "error": (C["red_soft"], C["red"]),
            "info": (C["accent_soft"], C["accent"]),
        }
        background, accent = colors.get(kind, colors["info"])
        frame = ctk.CTkFrame(
            self,
            fg_color=background,
            corner_radius=12,
            border_width=1,
            border_color=accent,
        )
        frame.place(relx=1.0, rely=1.0, x=-26, y=-24, anchor="se")
        ctk.CTkLabel(
            frame,
            text="●",
            font=_font(8),
            text_color=accent,
            width=10,
        ).pack(side="left", padx=(13, 6), pady=10)
        ctk.CTkLabel(
            frame,
            text=_ellipsize(message, 90),
            font=_font(9),
            text_color=C["text"],
        ).pack(side="left", padx=(0, 15), pady=10)
        self._toast = frame
        frame.after(4200, lambda: self._dismiss_toast(frame))

    def _dismiss_toast(self, frame: ctk.CTkFrame) -> None:
        try:
            if self._toast is frame:
                self._toast = None
            frame.destroy()
        except Exception:
            pass

    def _hide_config_error_banner(self) -> None:
        if self._config_error_banner is not None:
            try:
                self._config_error_banner.destroy()
            except Exception:
                pass
            self._config_error_banner = None

    def _on_tk_exception(self, exc_type, exc_value, exc_traceback) -> None:
        try:
            self.logger.error(
                "界面异常",
                exc_info=(exc_type, exc_value, exc_traceback),
            )
        except Exception:
            pass
        self.toast(f"界面操作失败：{exc_value}", "error")

    def _start_tray(self) -> None:
        if pystray is None:
            self.logger.warning("pystray 不可用，系统托盘功能已禁用")
            if self._start_hidden:
                self.show_window()
            return
        try:
            image = Image.open(resource_path("assets/logo.png")).convert("RGBA")
            menu = pystray.Menu(
                pystray.MenuItem("打开主界面", self._tray_show, default=True),
                pystray.MenuItem("立即扫描", self._tray_scan),
                pystray.MenuItem("生成报表", self._tray_report),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", self._tray_exit),
            )
            icon = pystray.Icon(
                "IPActivityAuditor", image, "IP 活跃度审计", menu
            )
            self._tray_icon = icon
            self._tray_thread = threading.Thread(
                target=self._tray_run, name="ipaa-tray", daemon=True
            )
            self._tray_thread.start()
            self.logger.info("系统托盘已启动")
        except Exception:
            self.logger.exception("启动系统托盘失败")
            self._tray_icon = None
            if self._start_hidden:
                self.show_window()

    def _tray_run(self) -> None:
        try:
            if self._tray_icon is not None:
                self._tray_icon.run()
        except Exception:
            self.logger.exception("系统托盘线程异常退出")
        finally:
            if not self._closing:
                self._tray_icon = None
                self._post(self._on_tray_stopped)

    def _on_tray_stopped(self) -> None:
        if self._closing:
            return
        if self._is_window_hidden():
            self.show_window()
            self.toast("系统托盘不可用，已恢复主窗口", "error")

    def _tray_show(self, _icon=None, _item=None) -> None:
        self._post(self.show_window)

    def _tray_scan(self, _icon=None, _item=None) -> None:
        self._post(self._tray_scan_ui)

    def _tray_report(self, _icon=None, _item=None) -> None:
        self._post(self._tray_report_ui)

    def _tray_exit(self, _icon=None, _item=None) -> None:
        self._post(self._quit_app)

    def _tray_scan_ui(self) -> None:
        if not self.cfg:
            self.show_window()
            self.toast("配置不可用，请先修复配置", "error")
            return
        backend = resolve_backend(self.cfg.scan)
        if backend == "scapy" and (not is_admin() or not npcap_present()):
            self.show_window()
        self.start_scan()

    def _tray_report_ui(self) -> None:
        if not self.cfg:
            self.show_window()
            self.toast("配置不可用，请先修复配置", "error")
            return
        self.generate_reports_from_config()

    def show_window(self) -> None:
        if self._closing:
            return
        try:
            self._hidden_to_tray = False
            self.deiconify()
            self.update_idletasks()
            self.lift()
            self.focus_force()
            self.attributes("-topmost", True)
            self.after(300, self._clear_topmost)
        except Exception:
            self.logger.exception("显示主界面失败")

    def _clear_topmost(self) -> None:
        try:
            self.attributes("-topmost", False)
        except Exception:
            pass

    def hide_to_tray(self, show_notice: bool = True) -> bool:
        if self._tray_icon is None:
            return False
        self._hidden_to_tray = True
        self.withdraw()
        if show_notice and not self._tray_notice_shown:
            self._tray_notice_shown = True
            self._tray_notify(
                "程序已隐藏到系统托盘，扫描与报表仍会在后台运行。",
            )
        return True

    def _tray_notify(self, message: str) -> None:
        if self._tray_icon is None:
            return
        try:
            self._tray_icon.notify(message, APP_DISPLAY_NAME)
        except Exception:
            self.logger.debug("发送托盘通知失败", exc_info=True)

    def _is_window_hidden(self) -> bool:
        try:
            return self.state() == "withdrawn"
        except Exception:
            return self._hidden_to_tray

    def _quit_app(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            if self._tray_icon is not None:
                self._tray_icon.stop()
                self._tray_icon = None
        except Exception:
            self.logger.exception("停止系统托盘失败")
        self.destroy()

    def _on_close(self) -> None:
        if self._tray_icon is not None:
            try:
                if self._busy:
                    self.logger.info("窗口关闭，后台任务继续运行并隐藏到托盘")
            except Exception:
                pass
            self.hide_to_tray()
            return
        self._quit_app()


def run_gui(
    config_path: str | Path,
    base_dir: Path | None = None,
    start_hidden: bool = False,
) -> int:
    try:
        app = IPActivityAuditorApp(
            Path(config_path), base_dir=base_dir, start_hidden=start_hidden
        )
    except Exception as exc:
        import tkinter.messagebox as messagebox

        messagebox.showerror(
            APP_DISPLAY_NAME,
            f"图形界面启动失败：\n{exc}\n\n请检查日志或改用命令行模式运行。",
        )
        return 1
    app.mainloop()
    return 0
