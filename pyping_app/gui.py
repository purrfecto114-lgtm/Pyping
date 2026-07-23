from __future__ import annotations

import locale
import os
import queue
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, scrolledtext, ttk

from .charting import (
    build_line_segments,
    calculate_layout,
    format_latency,
    render_chart_png,
    sample_xy,
)
from .core import PingBackend, run_ping_session
from .fileio import atomic_write_text
from .i18n import LANGUAGES, LANGUAGE_NAMES
from .models import (
    ChartSample,
    QueueMessage,
    RangeStatistics,
    ResultStatus,
    RunConfig,
    RunStatistics,
)
from .storage import SessionStore
from .validators import (
    ValidationError,
    parse_count,
    parse_duration,
    parse_host,
    parse_interval,
    parse_packet_size,
    parse_timeout,
)

DEFAULT_WINDOW_WIDTH = 1080
DEFAULT_WINDOW_HEIGHT = 820
MIN_WINDOW_WIDTH = 820
MIN_WINDOW_HEIGHT = 620
MIN_LOG_HEIGHT = 150
OUTPUT_ROW_MIN_HEIGHT = 222
WORK_AREA_MARGIN = 12
WINDOW_CHROME_ALLOWANCE = 40
QUEUE_POLL_MS = 100
MAX_QUEUE_MESSAGES_PER_TICK = 300
QUEUE_MAXSIZE = 10000
MAX_LOG_LINES = 5000
MAX_CHART_POINTS = 5000
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_high_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def configure_tkinter_dpi(root: tk.Tk) -> None:
    try:
        current = float(root.tk.call("tk", "scaling"))
        if current <= 0:
            root.tk.call("tk", "scaling", 1.0)
    except (tk.TclError, TypeError, ValueError):
        pass


def get_dpi_scale(root: tk.Misc) -> float:
    try:
        return max(1.0, root.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        return 1.0


def choose_ui_font(root: tk.Misc) -> str:
    try:
        available = set(tkfont.families(root))
    except tk.TclError:
        available = set()
    if sys.platform == "win32":
        candidates = (
            "Segoe UI Variable Text",
            "Segoe UI Variable",
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "Segoe UI",
            "Arial",
        )
    elif sys.platform == "darwin":
        candidates = ("PingFang SC", "SF Pro Text", "Helvetica", "Arial")
    else:
        candidates = (
            "Noto Sans CJK SC",
            "Noto Sans SC",
            "WenQuanYi Micro Hei",
            "Noto Sans",
            "DejaVu Sans",
        )
    return next((name for name in candidates if name in available), "TkDefaultFont")


def choose_monospace_font(root: tk.Misc) -> str:
    try:
        available = set(tkfont.families(root))
    except tk.TclError:
        available = set()
    if sys.platform == "win32":
        candidates = ("Cascadia Mono", "Cascadia Code", "Consolas", "Courier New")
    elif sys.platform == "darwin":
        candidates = ("SF Mono", "Menlo", "Monaco")
    else:
        candidates = ("Noto Sans Mono CJK SC", "Noto Sans Mono", "DejaVu Sans Mono", "Liberation Mono")
    return next((name for name in candidates if name in available), "TkFixedFont")


def detect_system_dark_theme() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except (OSError, ValueError, TypeError):
        return False


def get_work_area(root: tk.Misc) -> tuple[int, int, int, int]:
    """Return the usable desktop rectangle, excluding the Windows taskbar when possible."""
    if sys.platform == "win32":
        try:
            import ctypes

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        except (AttributeError, OSError, ValueError):
            pass
    return 0, 0, max(1, root.winfo_screenwidth()), max(1, root.winfo_screenheight())


def resource_path(relative_path: str) -> str:
    if hasattr(sys, "_MEIPASS"):
        frozen_root = getattr(sys, "_MEIPASS")
        candidates = (
            os.path.join(frozen_root, relative_path),
            os.path.join(frozen_root, "pyping_app", relative_path),
        )
    else:
        candidates = (os.path.join(os.path.dirname(__file__), relative_path),)
    return next((path for path in candidates if os.path.exists(path)), candidates[0])

def detect_default_language() -> str:
    candidates: list[str] = []
    try:
        current = locale.getlocale()[0]
        if current:
            candidates.append(current)
    except (ValueError, TypeError):
        pass
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(key)
        if value:
            candidates.append(value)
    for value in candidates:
        normalized = value.replace("-", "_").lower()
        if normalized.startswith("en"):
            return "en_US"
        if normalized.startswith("zh"):
            return "zh_CN"
    return "zh_CN"


class TimeRangeDialog:
    PRESETS = (
        ("range_all", None),
        ("range_last_1m", 60),
        ("range_last_5m", 300),
        ("range_last_15m", 900),
        ("range_last_1h", 3600),
        ("range_last_24h", 86400),
        ("range_custom", "custom"),
    )

    def __init__(
        self,
        parent: tk.Misc,
        translations: dict[str, str],
        bounds: tuple[datetime, datetime],
        count: int,
        ui_font: str,
        palette: dict[str, str],
    ) -> None:
        self.parent = parent
        self.t = translations
        self.bounds = bounds
        self.count = count
        self.ui_font = ui_font
        self.palette = palette
        self.result: tuple[datetime, datetime] | None = None
        self.dialog: tk.Toplevel | None = None
        self.preset_var = tk.StringVar(value="range_all")
        self.start_var = tk.StringVar(value=bounds[0].strftime(TIME_FORMAT))
        self.end_var = tk.StringVar(value=bounds[1].strftime(TIME_FORMAT))

    def show(self) -> tuple[datetime, datetime] | None:
        dialog = tk.Toplevel(self.parent)
        self.dialog = dialog
        dialog.title(self.t["range_title"])
        dialog.configure(background=self.palette["window"])
        dialog.transient(self.parent)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", self._cancel)

        frame = ttk.Frame(dialog, padding=20, style="Dialog.TFrame")
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=self.t["range_info"].format(
                count=self.count,
                start=self.bounds[0].strftime(TIME_FORMAT),
                end=self.bounds[1].strftime(TIME_FORMAT),
            ),
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Label(frame, text=self.t["range_preset"]).grid(row=1, column=0, sticky="w", padx=(0, 10), pady=6)
        values = [self.t[key] for key, _seconds in self.PRESETS]
        self._label_to_key = {self.t[key]: key for key, _seconds in self.PRESETS}
        self._key_to_seconds = {key: seconds for key, seconds in self.PRESETS}
        combo = ttk.Combobox(frame, state="readonly", values=values, width=24)
        combo.set(self.t["range_all"])
        combo.grid(row=1, column=1, sticky="ew", pady=6)
        combo.bind("<<ComboboxSelected>>", lambda _event: self._preset_changed(combo.get()))

        ttk.Label(frame, text=self.t["range_start"]).grid(row=2, column=0, sticky="w", padx=(0, 10), pady=6)
        self.start_entry = ttk.Entry(frame, textvariable=self.start_var, width=26)
        self.start_entry.grid(row=2, column=1, sticky="ew", pady=6)

        ttk.Label(frame, text=self.t["range_end"]).grid(row=3, column=0, sticky="w", padx=(0, 10), pady=6)
        self.end_entry = ttk.Entry(frame, textvariable=self.end_var, width=26)
        self.end_entry.grid(row=3, column=1, sticky="ew", pady=6)

        ttk.Label(frame, text=self.t["range_format"], style="Muted.TLabel").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(2, 12)
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text=self.t["range_confirm"], command=self._confirm, style="Primary.TButton").pack(side="left", padx=5)
        ttk.Button(buttons, text=self.t["range_cancel"], command=self._cancel).pack(side="left", padx=5)

        frame.columnconfigure(1, weight=1)
        self._set_custom_state(False)
        dialog.update_idletasks()
        x = self.parent.winfo_rootx() + max(0, (self.parent.winfo_width() - dialog.winfo_width()) // 2)
        y = self.parent.winfo_rooty() + max(0, (self.parent.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.wait_window()
        return self.result

    def _preset_changed(self, label: str) -> None:
        key = self._label_to_key[label]
        self.preset_var.set(key)
        seconds = self._key_to_seconds[key]
        if seconds == "custom":
            self._set_custom_state(True)
            return
        self._set_custom_state(False)
        start_bound, end_bound = self.bounds
        if seconds is None:
            start = start_bound
        else:
            start = max(start_bound, end_bound - timedelta(seconds=int(seconds)))
        self.start_var.set(start.strftime(TIME_FORMAT))
        self.end_var.set(end_bound.strftime(TIME_FORMAT))

    def _set_custom_state(self, enabled: bool) -> None:
        state = "normal" if enabled else "readonly"
        self.start_entry.configure(state=state)
        self.end_entry.configure(state=state)

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        text = value.strip()
        try:
            result = datetime.strptime(text, TIME_FORMAT)
        except ValueError:
            result = datetime.fromisoformat(text)
        if result.tzinfo is not None:
            raise ValueError("Time-range values must use local time without a UTC offset")
        return result

    def _confirm(self) -> None:
        try:
            start = self._parse_datetime(self.start_var.get())
            end = self._parse_datetime(self.end_var.get())
        except ValueError:
            messagebox.showerror(self.t["error_title"], self.t["range_invalid"], parent=self.dialog)
            return
        bounds_start, bounds_end = self.bounds
        if start > end or end < bounds_start or start > bounds_end:
            messagebox.showerror(self.t["error_title"], self.t["range_invalid"], parent=self.dialog)
            return
        start = max(start, bounds_start)
        end = min(end, bounds_end)
        if end < bounds_end:
            try:
                end = min(end + timedelta(seconds=0.999999), bounds_end)
            except OverflowError:
                end = bounds_end
        self.result = (start, end)
        self.dialog.destroy()

    def _cancel(self) -> None:
        self.result = None
        if self.dialog is not None:
            self.dialog.destroy()


class ChartWindow(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        samples: list[ChartSample],
        stats: RangeStatistics,
        host: str,
        start: datetime,
        end: datetime,
        translations: dict[str, str],
        aggregated: bool,
        pillow_available: bool,
        palette: dict[str, str] | None = None,
        on_closed=None,
    ) -> None:
        super().__init__(parent)
        self.samples = samples
        self.stats = stats
        self.host = host
        self.start = start
        self.end = end
        self.t = translations.copy()
        self.aggregated = aggregated
        self.pillow_available = pillow_available
        self.palette = palette or {
            "window": "#F3F3F3",
            "surface": "#FFFFFF",
            "text": "#1A1A1A",
            "muted": "#616161",
            "border": "#D6D6D6",
            "accent": "#0067C0",
            "warning": "#9D5D00",
            "danger": "#C42B1C",
            "grid": "#E5E5E5",
        }
        self.on_closed = on_closed or (lambda _window: None)
        self.ui_font = choose_ui_font(self)
        self.dpi_scale = get_dpi_scale(self)
        self.dirty = True
        self._draw_after_id: str | None = None

        self.title(f"{self.t['chart_title']} - {host}")
        self.configure(background=self.palette["window"])
        work_x, work_y, work_width, work_height = get_work_area(self)
        width = min(1120, max(760, work_width - WORK_AREA_MARGIN * 2))
        height = min(700, max(500, work_height - WORK_AREA_MARGIN * 2))
        x = work_x + max(0, (work_width - width) // 2)
        y = work_y + max(0, (work_height - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(780, width), min(500, height))
        self.protocol("WM_DELETE_WINDOW", self.close_window)

        self.canvas = tk.Canvas(self, bg=self.palette["surface"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        self.canvas.bind("<Configure>", self._schedule_draw)

        buttons = ttk.Frame(self, style="Root.TFrame")
        buttons.pack(fill="x", padx=10, pady=(4, 10))
        self.save_btn = ttk.Button(buttons, text=self.t["save_chart"], command=self.save_chart)
        self.save_btn.pack(side="right", padx=5)
        ttk.Button(buttons, text=self.t["close"], command=self.close_window).pack(side="right", padx=5)
        if not pillow_available:
            self.save_btn.configure(state="disabled")

        self._draw_after_id = self.after_idle(self.draw_chart)

    def _cancel_pending_draw(self) -> None:
        if self._draw_after_id is None:
            return
        try:
            self.after_cancel(self._draw_after_id)
        except tk.TclError:
            pass
        self._draw_after_id = None

    def _schedule_draw(self, _event=None) -> None:
        if self._draw_after_id is not None:
            try:
                self.after_cancel(self._draw_after_id)
            except tk.TclError:
                pass
        self._draw_after_id = self.after(120, self.draw_chart)

    def _fit_font(self, text: str, desired: int, minimum: int, max_width: int, weight: str = "normal") -> int:
        for size in range(desired, minimum - 1, -1):
            item = self.canvas.create_text(-1000, -1000, text=text, font=(self.ui_font, size, weight))
            box = self.canvas.bbox(item)
            self.canvas.delete(item)
            if box and box[2] - box[0] <= max_width:
                return size
        return minimum

    def draw_chart(self) -> None:
        # This method is also called directly after theme changes.  Cancel any
        # queued resize draw first so it cannot outlive the Toplevel command.
        self._cancel_pending_draw()
        if not self.winfo_exists():
            return
        self.canvas.delete("all")
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width < 400 or height < 300:
            return

        left, right, top, bottom = 92, 190, 80, 110
        layout = calculate_layout(
            self.samples,
            width,
            height,
            left=left,
            right=right,
            top=top,
            bottom=bottom,
        )
        title = f"{self.t['chart_title']} - {self.host}"
        title_size = self._fit_font(title, 16, 10, int(width * 0.75), "bold")
        text_color = self.palette["text"]
        muted_color = self.palette["muted"]
        grid_color = self.palette.get("grid", self.palette["border"])
        self.canvas.create_text(width / 2, 28, text=title, font=(self.ui_font, title_size, "bold"), fill=text_color)

        self.canvas.create_line(layout.x0, layout.y1, layout.x0, layout.y0, width=2, fill=text_color)
        self.canvas.create_line(layout.x0, layout.y0, layout.x1, layout.y0, width=2, fill=text_color)
        for i in range(6):
            value = layout.max_latency * i / 5
            y = layout.y0 - (layout.y0 - layout.y1) * i / 5
            self.canvas.create_text(layout.x0 - 10, y, text=f"{value:.1f}", anchor="e", font=(self.ui_font, 9), fill=muted_color)
            if i > 0:
                self.canvas.create_line(layout.x0, y, layout.x1, y, fill=grid_color)
        self.canvas.create_text(24, (layout.y0 + layout.y1) / 2, text=self.t["chart_latency"], angle=90, font=(self.ui_font, 10), fill=text_color)

        mapper = lambda sample: sample_xy(sample, layout)
        for segment in build_line_segments(self.samples, mapper):
            if len(segment) >= 2:
                flattened = [coord for point in segment for coord in point]
                self.canvas.create_line(*flattened, fill=self.palette["accent"], width=2)

        for sample in self.samples:
            point = mapper(sample)
            if point is not None:
                x, y = point
                latency = sample.latency_ms or 0.0
                color = "#2196F3" if latency < 100 else "#FF9800" if latency < 300 else "#F44336"
                self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline=color)
            x_ratio = (sample.timestamp.timestamp() - layout.start_ts) / (layout.end_ts - layout.start_ts)
            x = layout.x0 + x_ratio * (layout.x1 - layout.x0)
            if sample.has_timeout:
                self.canvas.create_text(x, layout.y0 - 18, text="X", fill="#D32F2F", font=(self.ui_font, 10, "bold"))
            if sample.has_error:
                self.canvas.create_text(x, layout.y0 - 37, text="!", fill="#7B1FA2", font=(self.ui_font, 10, "bold"))

        for i in range(5):
            ratio = i / 4
            ts = layout.start_ts + ratio * (layout.end_ts - layout.start_ts)
            x = layout.x0 + ratio * (layout.x1 - layout.x0)
            self.canvas.create_text(x, layout.y0 + 20, text=datetime.fromtimestamp(ts).strftime("%H:%M:%S"), font=(self.ui_font, 9), fill=muted_color)

        legend_x = layout.x1 + 18
        legend_y = layout.y1 + 10
        self.canvas.create_oval(legend_x, legend_y, legend_x + 10, legend_y + 10, fill="#2196F3", outline="#2196F3")
        self.canvas.create_text(legend_x + 18, legend_y + 5, text=self.t["chart_latency"], anchor="w", font=(self.ui_font, 9), fill=text_color)
        self.canvas.create_text(legend_x + 5, legend_y + 38, text="X", fill="#D32F2F", font=(self.ui_font, 10, "bold"))
        self.canvas.create_text(legend_x + 18, legend_y + 38, text=self.t["chart_timeout"], anchor="w", fill="#D32F2F", font=(self.ui_font, 9))
        self.canvas.create_text(legend_x + 5, legend_y + 72, text="!", fill="#7B1FA2", font=(self.ui_font, 10, "bold"))
        self.canvas.create_text(legend_x + 18, legend_y + 72, text=self.t["chart_error"], anchor="w", fill="#7B1FA2", font=(self.ui_font, 9))

        stats_text = (
            f"{self.t['total_packets']}: {self.stats.total} | "
            f"{self.t['success_packets']}: {self.stats.success} | "
            f"{self.t['timeout_packets']}: {self.stats.timeout} | "
            f"{self.t['error_packets']}: {self.stats.errors} | "
            f"{self.t['failure_rate']}: {self.stats.failure_rate:.2f}% | "
            f"{self.t['avg_latency']}: {format_latency(self.stats.average_latency)} | "
            f"{self.t['min_latency']}: {format_latency(self.stats.min_latency)} | "
            f"{self.t['max_latency']}: {format_latency(self.stats.max_latency)}"
        )
        stats_size = self._fit_font(stats_text, 10, 7, int(width * 0.96), "bold")
        range_text = f"{self.start:%Y-%m-%d %H:%M:%S} — {self.end:%Y-%m-%d %H:%M:%S}"
        self.canvas.create_text(width / 2, height - 57, text=range_text, font=(self.ui_font, 9), fill=muted_color)
        self.canvas.create_text(width / 2, height - 34, text=stats_text, font=(self.ui_font, stats_size, "bold"), fill=text_color)
        if self.aggregated:
            self.canvas.create_text(width / 2, height - 13, text=self.t["chart_aggregated"], font=(self.ui_font, 8), fill=self.palette["warning"])

    def save_chart(self) -> str:
        if not self.pillow_available:
            messagebox.showerror(self.t["error_title"], self.t["save_missing_pillow"], parent=self)
            return "failed"
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("All files", "*.*")],
            initialfile=f"ping_{self.start:%Y%m%d_%H%M%S}_{self.end:%H%M%S}.png",
        )
        if not path:
            return "cancelled"
        root, extension = os.path.splitext(path)
        if extension.lower() != ".png":
            path = root + ".png"
        try:
            render_chart_png(
                path,
                samples=self.samples,
                stats=self.stats,
                host=self.host,
                start=self.start,
                end=self.end,
                translations=self.t,
                aggregated=self.aggregated,
            )
        except Exception as exc:
            messagebox.showerror(self.t["error_title"], f"{self.t['save_failed']}\n{type(exc).__name__}: {exc}", parent=self)
            return "failed"
        self.dirty = False
        messagebox.showinfo(self.t["save_success"], f"{self.t['save_path']}\n{path}", parent=self)
        return "saved"

    def close_window(self) -> None:
        if self.dirty:
            answer = messagebox.askyesnocancel(self.t["unsaved_title"], self.t["unsaved_message"], parent=self)
            if answer is None:
                return
            if answer and self.save_chart() != "saved":
                return
        self._cancel_pending_draw()
        try:
            self.on_closed(self)
        finally:
            self.destroy()


class PingApp:
    def __init__(self, root: tk.Tk, lang: str = "zh_CN") -> None:
        self.root = root
        self._initial_root_state = root.state()
        if self._initial_root_state != "withdrawn":
            self.root.withdraw()
        self.lang = lang if lang in LANGUAGES else "zh_CN"
        self.t = LANGUAGES[self.lang]
        self.ui_font = choose_ui_font(root)
        self.mono_font = choose_monospace_font(root)
        self.dpi_scale = get_dpi_scale(root)
        self.initial_work_area = get_work_area(root)
        self.compact_height = self.initial_work_area[3] < 700
        self.backend = PingBackend()
        try:
            import PIL  # noqa: F401

            self.pillow_available = True
        except (ModuleNotFoundError, ImportError):
            self.pillow_available = False

        self.host_var = tk.StringVar(value="www.bing.com")
        self.size_var = tk.StringVar(value="56")
        self.interval_var = tk.StringVar(value="1.0")
        self.timeout_var = tk.StringVar(value="2.0")
        self.mode_var = tk.StringVar(value="count")
        self.count_var = tk.StringVar(value="30")
        self.duration_var = tk.StringVar(value="")
        self.menu_lang_var = tk.StringVar(value=self.lang)
        self.theme_var = tk.StringVar(value="system")
        self.auto_scroll_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar()
        self.runtime_var = tk.StringVar()
        self.records_var = tk.StringVar()
        self.queue_var = tk.StringVar()
        self.metric_vars = {
            key: tk.StringVar(value="0")
            for key in (
                "total_packets",
                "success_packets",
                "timeout_packets",
                "error_packets",
                "failure_rate",
                "avg_latency",
                "min_latency",
                "max_latency",
            )
        }

        self.session_id = 0
        self.current_config: RunConfig | None = None
        self.current_resolved_address = ""
        self.current_store: SessionStore | None = None
        self.persisted_count = 0
        self.statistics = RunStatistics()
        self.data_queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAXSIZE)
        self.stop_event = threading.Event()
        self.ping_thread: threading.Thread | None = None
        self.export_thread: threading.Thread | None = None
        self.export_result_queue: queue.Queue = queue.Queue(maxsize=1)
        self.after_id: str | None = None
        self.status_after_id: str | None = None
        self.running = False
        self.export_in_progress = False
        self.run_started_monotonic: float | None = None
        self.last_elapsed_seconds = 0.0
        self.status_key = "state_idle"
        self.chart_windows: list[ChartWindow] = []

        self.root.title(self.t["app_title"])
        try:
            icon_path = resource_path(os.path.join("assets", "pyping.png"))
            if os.path.isfile(icon_path):
                self._icon_image = tk.PhotoImage(file=icon_path)
                self.root.iconphoto(True, self._icon_image)
        except (tk.TclError, OSError):
            self._icon_image = None
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._setup_style()
        self.create_widgets()
        self._configure_initial_window()
        self.create_menu()
        self._bind_shortcuts()
        self.update_stats_display()
        self._update_status_display()
        self._schedule_status_tick()
        self.dependency_after_id: str | None = None
        if self._initial_root_state != "withdrawn":
            self.root.after_idle(self.root.deiconify)
            self.dependency_after_id = self.root.after(300, self.show_dependency_warnings)

    def _s(self, key: str) -> str:
        return self.t.get(key, key)

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        # The native Vista theme ignores several field/background options.  Clam
        # gives us one predictable rendering path for light, dark and system mode.
        if "clam" in style.theme_names():
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass

        selected = self.theme_var.get()
        dark = selected == "dark" or (selected == "system" and detect_system_dark_theme())
        if dark:
            palette = {
                "window": "#202020",
                "surface": "#2C2C2C",
                "surface_alt": "#333333",
                "control": "#2B2B2B",
                "control_hover": "#3A3A3A",
                "control_pressed": "#454545",
                "text": "#F5F5F5",
                "muted": "#C2C2C2",
                "disabled": "#777777",
                "border": "#4A4A4A",
                "accent": "#60CDFF",
                "accent_hover": "#77D6FF",
                "accent_pressed": "#4AB7E8",
                "accent_text": "#111111",
                "success": "#6CCB9F",
                "warning": "#F3C781",
                "danger": "#FF8A80",
                "danger_fill": "#C42B1C",
                "selection": "#005A9E",
                "grid": "#414141",
            }
        else:
            palette = {
                "window": "#F3F3F3",
                "surface": "#FFFFFF",
                "surface_alt": "#F8F8F8",
                "control": "#FFFFFF",
                "control_hover": "#F6F6F6",
                "control_pressed": "#EAEAEA",
                "text": "#1A1A1A",
                "muted": "#616161",
                "disabled": "#9A9A9A",
                "border": "#D6D6D6",
                "accent": "#0067C0",
                "accent_hover": "#1975C5",
                "accent_pressed": "#005A9E",
                "accent_text": "#FFFFFF",
                "success": "#0F7B56",
                "warning": "#9D5D00",
                "danger": "#C42B1C",
                "danger_fill": "#C42B1C",
                "selection": "#CCE8FF",
                "grid": "#E5E5E5",
            }
        # Backward-compatible alias used by tests and older extensions.
        palette["bg"] = palette["window"]
        palette["entry"] = palette["control"]
        self.palette = palette
        self.root.configure(background=palette["window"])

        body_size = 10
        caption_size = 9
        metric_size = 12 if self.compact_height else 13
        entry_vpad = 5 if self.compact_height else 7
        button_vpad = 6 if self.compact_height else 8
        style.configure(".", font=(self.ui_font, body_size))
        style.configure("TFrame", background=palette["surface"])
        style.configure("Root.TFrame", background=palette["window"])
        style.configure("Dialog.TFrame", background=palette["surface"])
        style.configure(
            "Metric.TFrame",
            background=palette["surface_alt"],
            bordercolor=palette["border"],
            lightcolor=palette["border"],
            darkcolor=palette["border"],
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "Section.TFrame",
            background=palette["surface"],
            bordercolor=palette["border"],
            lightcolor=palette["border"],
            darkcolor=palette["border"],
            borderwidth=1,
            relief="solid",
        )
        style.configure("SectionBody.TFrame", background=palette["surface"])
        style.configure("SectionTitle.TLabel", background=palette["surface"], foreground=palette["text"], font=(self.ui_font, body_size, "bold"))
        style.configure("TLabel", background=palette["surface"], foreground=palette["text"], font=(self.ui_font, body_size))
        style.configure("Root.TLabel", background=palette["window"], foreground=palette["text"])
        style.configure("Muted.TLabel", background=palette["surface"], foreground=palette["muted"], font=(self.ui_font, caption_size))
        style.configure("MetricName.TLabel", font=(self.ui_font, caption_size), foreground=palette["muted"], background=palette["surface_alt"])
        style.configure("MetricValue.TLabel", font=(self.ui_font, metric_size, "bold"), foreground=palette["text"], background=palette["surface_alt"])
        style.configure("Status.TLabel", font=(self.ui_font, caption_size), foreground=palette["muted"], background=palette["window"])
        style.configure(
            "TLabelFrame",
            background=palette["surface"],
            foreground=palette["text"],
            bordercolor=palette["border"],
            lightcolor=palette["border"],
            darkcolor=palette["border"],
            borderwidth=1,
            relief="solid",
        )
        style.configure("TLabelFrame.Label", background=palette["surface"], foreground=palette["text"], font=(self.ui_font, body_size, "bold"))
        style.configure(
            "TEntry",
            font=(self.ui_font, body_size),
            padding=(10, entry_vpad),
            fieldbackground=palette["control"],
            foreground=palette["text"],
            insertcolor=palette["text"],
            bordercolor=palette["border"],
            lightcolor=palette["border"],
            darkcolor=palette["border"],
            selectbackground=palette["selection"],
            selectforeground=palette["text"],
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", palette["accent"]), ("disabled", palette["border"])],
            fieldbackground=[("disabled", palette["surface_alt"])],
            foreground=[("disabled", palette["disabled"])],
        )
        style.configure(
            "TCombobox",
            font=(self.ui_font, body_size),
            padding=(10, entry_vpad),
            fieldbackground=palette["control"],
            background=palette["control"],
            foreground=palette["text"],
            arrowcolor=palette["text"],
            bordercolor=palette["border"],
            lightcolor=palette["border"],
            darkcolor=palette["border"],
            selectbackground=palette["selection"],
            selectforeground=palette["text"],
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", palette["control"]), ("disabled", palette["surface_alt"])],
            foreground=[("readonly", palette["text"]), ("disabled", palette["disabled"])],
            bordercolor=[("focus", palette["accent"])],
        )
        style.configure("TCheckbutton", background=palette["surface"], foreground=palette["text"], font=(self.ui_font, body_size))
        style.configure("TRadiobutton", background=palette["surface"], foreground=palette["text"], font=(self.ui_font, body_size))
        for widget_style in ("TCheckbutton", "TRadiobutton"):
            style.map(widget_style, background=[("active", palette["surface"])], foreground=[("disabled", palette["disabled"])])

        style.configure(
            "TButton",
            font=(self.ui_font, body_size),
            padding=(12, button_vpad),
            background=palette["control"],
            foreground=palette["text"],
            bordercolor=palette["border"],
            lightcolor=palette["border"],
            darkcolor=palette["border"],
            relief="flat",
        )
        style.map(
            "TButton",
            background=[("pressed", palette["control_pressed"]), ("active", palette["control_hover"]), ("disabled", palette["surface_alt"])],
            foreground=[("disabled", palette["disabled"])],
            bordercolor=[("focus", palette["accent"]), ("disabled", palette["border"])],
        )
        style.configure("Compact.TButton", font=(self.ui_font, caption_size), padding=(8, 4))
        style.configure("Compact.TCheckbutton", font=(self.ui_font, caption_size), padding=(0, 2))
        for name in ("Primary.TButton", "Accent.TButton"):
            style.configure(
                name,
                font=(self.ui_font, body_size, "bold"),
                padding=(12, button_vpad),
                background=palette["accent"],
                foreground=palette["accent_text"],
                bordercolor=palette["accent"],
                lightcolor=palette["accent"],
                darkcolor=palette["accent"],
                relief="flat",
            )
            style.map(
                name,
                background=[("pressed", palette["accent_pressed"]), ("active", palette["accent_hover"]), ("disabled", palette["surface_alt"])],
                foreground=[("disabled", palette["disabled"])],
                bordercolor=[("disabled", palette["border"])],
            )
        style.configure(
            "Danger.TButton",
            font=(self.ui_font, body_size, "bold"),
            padding=(12, button_vpad),
            background=palette["danger_fill"],
            foreground="#FFFFFF",
            bordercolor=palette["danger_fill"],
            lightcolor=palette["danger_fill"],
            darkcolor=palette["danger_fill"],
            relief="flat",
        )
        style.map(
            "Danger.TButton",
            background=[("pressed", "#8E1E14"), ("active", "#D13B2B"), ("disabled", palette["surface_alt"])],
            foreground=[("disabled", palette["disabled"])],
            bordercolor=[("disabled", palette["border"])],
        )
        self._apply_text_theme()

    def _apply_text_theme(self) -> None:
        if not hasattr(self, "output"):
            return
        try:
            if not self.output.winfo_exists():
                return
        except tk.TclError:
            return
        p = self.palette
        self.output.configure(
            background=p["entry"],
            foreground=p["text"],
            insertbackground=p["text"],
            selectbackground=p["selection"],
            selectforeground=p["text"],
            relief="flat",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=p["border"],
            highlightcolor=p["accent"],
        )
        self.output.tag_configure("success", foreground=p["success"])
        self.output.tag_configure("timeout", foreground=p["warning"])
        self.output.tag_configure("error", foreground=p["danger"])
        self.output.tag_configure("info", foreground=p["accent"])
        self.output.tag_configure("muted", foreground=p["muted"])

    def _configure_initial_window(self) -> None:
        """Size and center the window while reserving usable space for the log."""
        self.root.update_idletasks()
        work_x, work_y, work_width, work_height = get_work_area(self.root)
        max_width = max(640, work_width - WORK_AREA_MARGIN * 2)
        max_height = max(500, work_height - WORK_AREA_MARGIN * 2 - WINDOW_CHROME_ALLOWANCE)
        output_row_min = OUTPUT_ROW_MIN_HEIGHT if work_height >= 700 else 150
        self.root.grid_rowconfigure(3, minsize=output_row_min)
        self.root.update_idletasks()
        preferred_width = min(DEFAULT_WINDOW_WIDTH, int(work_width * 0.90))
        preferred_height = min(DEFAULT_WINDOW_HEIGHT, int(work_height * 0.90))
        requested_width = max(preferred_width, self.root.winfo_reqwidth())
        requested_height = max(preferred_height, self.root.winfo_reqheight())
        width = min(requested_width, max_width)
        height = min(requested_height, max_height)
        min_width = min(MIN_WINDOW_WIDTH, max_width)
        min_height = min(MIN_WINDOW_HEIGHT, max_height)
        self.root.minsize(min_width, min_height)
        x = work_x + max(0, (work_width - width) // 2)
        y = work_y + max(0, (work_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.update_idletasks()
        self.root.after_idle(self._ensure_log_visible)

    def _ensure_log_visible(self) -> None:
        if not hasattr(self, "output") or not self.root.winfo_exists():
            return
        self.root.update_idletasks()
        current = self.output.winfo_height()
        if current >= MIN_LOG_HEIGHT:
            return
        work_x, work_y, work_width, work_height = get_work_area(self.root)
        available_height = max(500, work_height - WORK_AREA_MARGIN * 2 - WINDOW_CHROME_ALLOWANCE)
        grow_by = MIN_LOG_HEIGHT - current
        new_height = min(available_height, self.root.winfo_height() + grow_by)
        if new_height > self.root.winfo_height():
            x = max(work_x, self.root.winfo_x())
            y = max(work_y, min(self.root.winfo_y(), work_y + work_height - new_height))
            self.root.geometry(f"{self.root.winfo_width()}x{new_height}+{x}+{y}")
            self.root.update_idletasks()

    def create_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label=self._s("export_csv"), command=self.export_csv, accelerator="Ctrl+Shift+S")
        file_menu.add_command(label=self._s("export_log"), command=self.export_log)
        file_menu.add_separator()
        file_menu.add_command(label=self._s("menu_exit"), command=self.on_close)
        menubar.add_cascade(label=self._s("menu_file"), menu=file_menu)

        language_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label=self._s("menu_language"), menu=language_menu)
        for code, label in LANGUAGE_NAMES.items():
            language_menu.add_radiobutton(
                label=label,
                value=code,
                variable=self.menu_lang_var,
                command=lambda selected=code: self.change_language(selected),
            )

        theme_menu = tk.Menu(menubar, tearoff=0)
        for value, key in (("system", "theme_system"), ("light", "theme_light"), ("dark", "theme_dark")):
            theme_menu.add_radiobutton(
                label=self._s(key),
                value=value,
                variable=self.theme_var,
                command=self.change_theme,
            )
        menubar.add_cascade(label=self._s("menu_theme"), menu=theme_menu)
        menubar.add_command(label=self._s("menu_about"), command=self.show_about)
        self.root.configure(menu=menubar)

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<Control-Return>", lambda _event: self.start_ping())
        self.root.bind_all("<Escape>", lambda _event: self.stop_ping())
        self.root.bind_all("<Control-g>", lambda _event: self.generate_chart())
        self.root.bind_all("<Control-G>", lambda _event: self.generate_chart())
        self.root.bind_all("<Control-Shift-s>", lambda _event: self.export_csv())
        self.root.bind_all("<Control-Shift-S>", lambda _event: self.export_csv())

    def create_widgets(self) -> None:
        for row in range(5):
            self.root.grid_rowconfigure(row, weight=1 if row == 3 else 0, minsize=OUTPUT_ROW_MIN_HEIGHT if row == 3 else 0)
        self.root.grid_columnconfigure(0, weight=1)
        pad_x = 16
        pad_y = 8

        input_section = ttk.Frame(self.root, style="Section.TFrame", padding=1)
        input_section.grid(row=0, column=0, sticky="ew", padx=pad_x, pady=(pad_y, 4))
        input_section.columnconfigure(0, weight=1)
        input_header = ttk.Frame(input_section, style="SectionBody.TFrame")
        input_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(5 if self.compact_height else 7, 1))
        ttk.Label(input_header, text=self._s("param_frame"), style="SectionTitle.TLabel").pack(side="left")
        input_frame = ttk.Frame(input_section, style="SectionBody.TFrame", padding=(14, 3 if self.compact_height else 5))
        input_frame.grid(row=1, column=0, sticky="ew")
        input_frame.columnconfigure(1, weight=1, uniform="input_values")
        input_frame.columnconfigure(3, weight=1, uniform="input_values")

        ttk.Label(input_frame, text=self._s("target_host")).grid(row=0, column=0, sticky="w", padx=(4, 12), pady=4)
        self.host_entry = ttk.Entry(input_frame, textvariable=self.host_var)
        self.host_entry.grid(row=0, column=1, columnspan=3, sticky="ew", padx=4, pady=4)

        ttk.Label(input_frame, text=self._s("packet_size")).grid(row=1, column=0, sticky="w", padx=(4, 12), pady=4)
        self.size_entry = ttk.Entry(input_frame, textvariable=self.size_var)
        self.size_entry.grid(row=1, column=1, sticky="ew", padx=(4, 14), pady=4)
        ttk.Label(input_frame, text=self._s("interval")).grid(row=1, column=2, sticky="w", padx=(4, 12), pady=4)
        self.interval_entry = ttk.Entry(input_frame, textvariable=self.interval_var)
        self.interval_entry.grid(row=1, column=3, sticky="ew", padx=4, pady=4)

        ttk.Label(input_frame, text=self._s("timeout")).grid(row=2, column=0, sticky="w", padx=(4, 12), pady=4)
        self.timeout_entry = ttk.Entry(input_frame, textvariable=self.timeout_var)
        self.timeout_entry.grid(row=2, column=1, sticky="ew", padx=(4, 14), pady=4)
        self.count_label = ttk.Label(input_frame, text=self._s("ping_count"))
        self.count_label.grid(row=2, column=2, sticky="w", padx=(4, 12), pady=4)
        self.count_entry = ttk.Entry(input_frame, textvariable=self.count_var)
        self.count_entry.grid(row=2, column=3, sticky="ew", padx=4, pady=4)
        self.duration_label = ttk.Label(input_frame, text=self._s("duration"))
        self.duration_entry = ttk.Entry(input_frame, textvariable=self.duration_var)

        mode_parent = input_header if self.compact_height else input_frame
        mode_frame = ttk.Frame(mode_parent)
        if self.compact_height:
            mode_frame.pack(side="right")
        else:
            mode_frame.grid(row=3, column=0, columnspan=4, sticky="ew", padx=4, pady=(4, 1))
        ttk.Label(mode_frame, text=f"{self._s('mode_frame')}:", style="Muted.TLabel").pack(side="left", padx=(0, 12))
        self.count_radio = ttk.Radiobutton(
            mode_frame,
            text=self._s("count_mode"),
            variable=self.mode_var,
            value="count",
            command=self.on_mode_change,
        )
        self.count_radio.pack(side="left", padx=(4, 18))
        self.duration_radio = ttk.Radiobutton(
            mode_frame,
            text=self._s("duration_mode"),
            variable=self.mode_var,
            value="duration",
            command=self.on_mode_change,
        )
        self.duration_radio.pack(side="left", padx=4)
        self.on_mode_change()

        button_frame = ttk.Frame(self.root, style="Root.TFrame")
        button_frame.grid(row=1, column=0, sticky="ew", padx=pad_x, pady=4)
        for column in range(5):
            button_frame.columnconfigure(column, weight=1, uniform="actions")
        self.start_btn = ttk.Button(button_frame, text=self._s("start_btn"), command=self.start_ping, style="Primary.TButton")
        self.stop_btn = ttk.Button(button_frame, text=self._s("stop_btn"), command=self.stop_ping, style="Danger.TButton", state="disabled")
        self.chart_btn = ttk.Button(button_frame, text=self._s("generate_chart"), command=self.generate_chart, style="Accent.TButton", state="disabled")
        self.csv_btn = ttk.Button(button_frame, text=self._s("export_csv"), command=self.export_csv, state="disabled")
        self.clear_data_btn = ttk.Button(button_frame, text=self._s("clear_data"), command=self.clear_session_data, state="disabled")
        for column, button in enumerate((self.start_btn, self.stop_btn, self.chart_btn, self.csv_btn, self.clear_data_btn)):
            button.grid(row=0, column=column, sticky="ew", padx=4)

        stats_section = ttk.Frame(self.root, style="Section.TFrame", padding=1)
        stats_section.grid(row=2, column=0, sticky="ew", padx=pad_x, pady=4)
        stats_section.columnconfigure(0, weight=1)
        ttk.Label(stats_section, text=self._s("stats_frame"), style="SectionTitle.TLabel").grid(row=0, column=0, sticky="w", padx=12, pady=(7, 1))
        stats_frame = ttk.Frame(stats_section, style="SectionBody.TFrame", padding=(5, 2 if self.compact_height else 4))
        stats_frame.grid(row=1, column=0, sticky="ew")
        metric_keys = (
            "total_packets",
            "success_packets",
            "timeout_packets",
            "error_packets",
            "failure_rate",
            "avg_latency",
            "min_latency",
            "max_latency",
        )
        for column in range(8):
            stats_frame.columnconfigure(column, weight=1, uniform="metrics")
        for index, key in enumerate(metric_keys):
            card = ttk.Frame(stats_frame, style="Metric.TFrame", padding=(7, 2 if self.compact_height else 4))
            card.grid(row=0, column=index, sticky="nsew", padx=2, pady=2)
            ttk.Label(card, text=self._s(key), style="MetricName.TLabel").pack(anchor="w")
            ttk.Label(card, textvariable=self.metric_vars[key], style="MetricValue.TLabel").pack(anchor="w", pady=(1, 0))

        output_section = ttk.Frame(self.root, style="Section.TFrame", padding=1)
        output_section.grid(row=3, column=0, sticky="nsew", padx=pad_x, pady=4)
        output_section.grid_rowconfigure(1, weight=1)
        output_section.grid_columnconfigure(0, weight=1)
        ttk.Label(output_section, text=self._s("output_frame"), style="SectionTitle.TLabel").grid(row=0, column=0, sticky="w", padx=12, pady=(7, 1))
        output_frame = ttk.Frame(output_section, style="SectionBody.TFrame", padding=(8, 5))
        output_frame.grid(row=1, column=0, sticky="nsew")
        output_frame.grid_rowconfigure(1, weight=1)
        output_frame.grid_columnconfigure(0, weight=1)
        log_toolbar = ttk.Frame(output_frame)
        log_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Checkbutton(log_toolbar, text=self._s("auto_scroll"), variable=self.auto_scroll_var, style="Compact.TCheckbutton").pack(side="left")
        ttk.Button(log_toolbar, text=self._s("copy_log"), command=self.copy_log, style="Compact.TButton").pack(side="right", padx=(4, 0))
        ttk.Button(log_toolbar, text=self._s("clear_log"), command=self.clear_log, style="Compact.TButton").pack(side="right", padx=(4, 0))
        ttk.Button(log_toolbar, text=self._s("export_log"), command=self.export_log, style="Compact.TButton").pack(side="right", padx=(4, 0))

        self.output = scrolledtext.ScrolledText(output_frame, wrap="word", height=8, font=(self.mono_font, 10), undo=False)
        self.output.grid(row=1, column=0, sticky="nsew")
        self._apply_text_theme()

        status_frame = ttk.Frame(self.root, style="Root.TFrame")
        status_frame.grid(row=4, column=0, sticky="ew", padx=pad_x, pady=(3, pad_y))
        for column in range(4):
            status_frame.columnconfigure(column, weight=1)
        ttk.Label(status_frame, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.runtime_var, style="Status.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(status_frame, textvariable=self.records_var, style="Status.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Label(status_frame, textvariable=self.queue_var, style="Status.TLabel").grid(row=0, column=3, sticky="e")

        self._set_inputs_enabled(not self.running)
        self._refresh_button_states()
        self.host_entry.focus_set()

    def on_mode_change(self) -> None:
        if not hasattr(self, "count_label"):
            return
        if self.mode_var.get() == "count":
            self.count_label.configure(text=self._s("ping_count"))
            self.count_label.grid(row=2, column=2, sticky="w", padx=(4, 12), pady=4)
            self.count_entry.grid(row=2, column=3, sticky="ew", padx=4, pady=4)
            self.duration_label.grid_remove()
            self.duration_entry.grid_remove()
        else:
            self.count_label.grid_remove()
            self.count_entry.grid_remove()
            self.duration_label.grid(row=2, column=2, sticky="w", padx=(4, 12), pady=4)
            self.duration_entry.grid(row=2, column=3, sticky="ew", padx=4, pady=4)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget_name in (
            "host_entry",
            "size_entry",
            "interval_entry",
            "timeout_entry",
            "count_entry",
            "duration_entry",
            "count_radio",
            "duration_radio",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.configure(state=state)

    def _refresh_button_states(self) -> None:
        if not hasattr(self, "start_btn"):
            return
        has_data = self.current_store is not None and self.persisted_count > 0
        self.start_btn.configure(state="disabled" if self.running or self.export_in_progress else "normal")
        self.stop_btn.configure(state="normal" if self.running else "disabled")
        self.chart_btn.configure(state="normal" if has_data else "disabled")
        self.csv_btn.configure(state="normal" if has_data and not self.export_in_progress else "disabled")
        self.clear_data_btn.configure(state="normal" if has_data and not self.running and not self.export_in_progress else "disabled")

    def validate_inputs(self) -> tuple[str, int, float, float, int | None, float | None] | None:
        try:
            host = parse_host(self.host_var.get())
            packet_size = parse_packet_size(self.size_var.get())
            interval = parse_interval(self.interval_var.get())
            timeout = parse_timeout(self.timeout_var.get())
            if self.mode_var.get() == "count":
                count = parse_count(self.count_var.get())
                duration = None
            else:
                count = None
                duration = parse_duration(self.duration_var.get())
        except ValidationError as exc:
            messagebox.showerror(self._s("error_title"), self._s(exc.key), parent=self.root)
            return None
        return host, packet_size, interval, timeout, count, duration

    def start_ping(self) -> None:
        if self.running or self.export_in_progress:
            return
        params = self.validate_inputs()
        if params is None:
            return
        if not self.backend.available:
            self.show_dependency_warnings(force_ping3=True)
            return

        host, packet_size, interval, timeout, count, duration = params
        self._close_current_store()
        self.current_store = SessionStore()
        self.persisted_count = 0
        self.statistics = RunStatistics()
        self.current_resolved_address = ""
        self.session_id += 1
        self.current_config = RunConfig(
            session_id=self.session_id,
            original_host=host,
            packet_size=packet_size,
            interval_seconds=interval,
            timeout_seconds=timeout,
            count=count,
            duration_seconds=duration,
            started_at=datetime.now(),
        )
        self.data_queue = queue.Queue(maxsize=QUEUE_MAXSIZE)
        self.stop_event = threading.Event()
        self.running = True
        self.status_key = "state_running"
        self.run_started_monotonic = time.monotonic()
        self.last_elapsed_seconds = 0.0
        self.output.delete("1.0", tk.END)
        self._append_log(self._s("run_started") + "\n", "info")
        self.update_stats_display()
        self._set_inputs_enabled(False)
        self._refresh_button_states()
        self._update_status_display()

        self.ping_thread = threading.Thread(
            target=run_ping_session,
            args=(self.current_config, self.backend, self.data_queue, self.stop_event),
            name=f"PingSession-{self.session_id}",
            daemon=True,
        )
        self.ping_thread.start()
        self._schedule_poll(0)

    def _schedule_poll(self, delay_ms: int = QUEUE_POLL_MS) -> None:
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except tk.TclError:
                pass
        self.after_id = self.root.after(delay_ms, self.poll_queue)

    def poll_queue(self) -> None:
        self.after_id = None
        processed = 0
        records = []
        log_items: list[tuple[str, str]] = []
        finished_reason: str | None = None
        while processed < MAX_QUEUE_MESSAGES_PER_TICK:
            try:
                message: QueueMessage = self.data_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            if message.session_id != self.session_id:
                continue
            if message.kind == "record":
                records.append(message.payload)
            elif message.kind == "resolved":
                target = message.payload
                self.current_resolved_address = target.address
                log_items.append((self._s("resolved").format(address=target.address, family=target.family_name) + "\n", "info"))
            elif message.kind == "resolve_failed":
                log_items.append((self._s("run_resolve_failed").format(detail=message.payload) + "\n", "error"))
            elif message.kind == "worker_error":
                log_items.append((self._s("run_worker_error").format(detail=message.payload) + "\n", "error"))
            elif message.kind == "finished":
                finished_reason = str(message.payload)

        if records and self.current_store is not None:
            try:
                inserted = self.current_store.insert_many(records)
                self.persisted_count += inserted
                for record in records:
                    self.statistics.update(record)
                    log_items.append((self._format_record(record), self._record_tag(record.status)))
            except (sqlite3.Error, OSError) as exc:
                self.stop_event.set()
                log_items.append((f"{self._s('run_worker_error').format(detail=exc)}\n", "error"))
                finished_reason = "internal_error"
        for text, tag in log_items:
            self._append_log(text, tag)
        if records:
            self.update_stats_display()
            self._refresh_button_states()

        if finished_reason is not None:
            self._finish_session(finished_reason)
            return

        thread_alive = self.ping_thread is not None and self.ping_thread.is_alive()
        if thread_alive or not self.data_queue.empty():
            self._schedule_poll(1 if processed >= MAX_QUEUE_MESSAGES_PER_TICK else QUEUE_POLL_MS)
        elif self.running:
            self._finish_session("internal_error")

    @staticmethod
    def _record_tag(status: ResultStatus) -> str:
        if status == ResultStatus.SUCCESS:
            return "success"
        if status == ResultStatus.TIMEOUT:
            return "timeout"
        return "error"

    def _format_record(self, record) -> str:
        prefix = f"{record.timestamp:%Y-%m-%d %H:%M:%S} #{record.sequence}"
        if record.status == ResultStatus.SUCCESS and record.latency_ms is not None:
            status = f"{record.latency_ms:.2f} ms"
        else:
            key = f"status_{record.status.value}"
            status = self._s(key)
            if record.detail:
                status += f" ({record.detail})"
        return f"{prefix} - {status}\n"

    def _append_log(self, text: str, tag: str = "muted") -> None:
        if not hasattr(self, "output"):
            return
        self.output.insert(tk.END, text, tag)
        try:
            line_count = int(self.output.index("end-1c").split(".")[0])
            if line_count > MAX_LOG_LINES:
                self.output.delete("1.0", f"{line_count - MAX_LOG_LINES + 1}.0")
        except (tk.TclError, ValueError):
            pass
        if self.auto_scroll_var.get():
            self.output.see(tk.END)

    def copy_log(self) -> None:
        text = self.output.get("1.0", "end-1c")
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_key = "state_log_copied"
        self._update_status_display()

    def clear_log(self) -> None:
        self.output.delete("1.0", tk.END)

    def export_log(self) -> None:
        text = self.output.get("1.0", "end-1c")
        if not text:
            messagebox.showinfo(self._s("app_title"), self._s("no_log_data"), parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All files", "*.*")],
            initialfile=f"pyping_log_{datetime.now():%Y%m%d_%H%M%S}.txt",
        )
        if not path:
            return
        try:
            atomic_write_text(path, text, encoding="utf-8", newline="")
        except OSError as exc:
            messagebox.showerror(self._s("error_title"), f"{self._s('export_failed')}\n{exc}", parent=self.root)
            return
        messagebox.showinfo(self._s("export_success"), f"{self._s('export_path')}\n{path}", parent=self.root)

    def update_stats_display(self) -> None:
        values = {
            "total_packets": str(self.statistics.total),
            "success_packets": str(self.statistics.success),
            "timeout_packets": str(self.statistics.timeout),
            "error_packets": str(self.statistics.errors),
            "failure_rate": f"{self.statistics.failure_rate:.2f}%",
            "avg_latency": format_latency(self.statistics.average_latency),
            "min_latency": format_latency(self.statistics.min_latency),
            "max_latency": format_latency(self.statistics.max_latency),
        }
        for key, value in values.items():
            self.metric_vars[key].set(value)

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        total = max(0, int(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _schedule_status_tick(self) -> None:
        if self.status_after_id is not None:
            try:
                self.root.after_cancel(self.status_after_id)
            except tk.TclError:
                pass
        self.status_after_id = self.root.after(500, self._status_tick)

    def _status_tick(self) -> None:
        self.status_after_id = None
        self._poll_export_result()
        if self.running and self.run_started_monotonic is not None:
            self.last_elapsed_seconds = time.monotonic() - self.run_started_monotonic
        self._update_status_display()
        if self.root.winfo_exists():
            self._schedule_status_tick()

    def _poll_export_result(self) -> None:
        try:
            path, count, error = self.export_result_queue.get_nowait()
        except queue.Empty:
            return
        self._finish_export(path, count, error)

    def _update_status_display(self) -> None:
        self.status_var.set(f"{self._s('status_label')}: {self._s(self.status_key)}")
        self.runtime_var.set(f"{self._s('runtime_label')}: {self._format_elapsed(self.last_elapsed_seconds)}")
        record_count = self.persisted_count if self.current_store is not None else 0
        self.records_var.set(f"{self._s('records_label')}: {record_count}")
        try:
            backlog = self.data_queue.qsize()
        except NotImplementedError:
            backlog = 0
        self.queue_var.set(f"{self._s('queue_label')}: {backlog}")

    def _finish_session(self, reason: str) -> None:
        if not self.running:
            return
        if self.run_started_monotonic is not None:
            self.last_elapsed_seconds = time.monotonic() - self.run_started_monotonic
        self.running = False
        self.status_key = "state_stopped" if reason == "stopped" else "state_finished" if reason == "completed" else "state_error"
        self._set_inputs_enabled(True)
        self._refresh_button_states()
        if reason == "stopped":
            message, tag = self._s("run_stopped"), "info"
        elif reason == "completed":
            message, tag = self._s("run_finished"), "info"
        else:
            message, tag = self._s("run_failed"), "error"
        self._append_log(message + "\n", tag)
        self._update_status_display()

    def stop_ping(self) -> None:
        if self.running:
            self.stop_event.set()
            self.status_key = "state_stopping"
            self.stop_btn.configure(state="disabled")
            self._update_status_display()

    def generate_chart(self) -> None:
        if self.current_store is None:
            messagebox.showinfo(self._s("app_title"), self._s("no_chart_data"), parent=self.root)
            return
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        self.poll_queue()
        bounds = self.current_store.bounds()
        count = self.current_store.count()
        if bounds is None or count == 0:
            messagebox.showinfo(self._s("app_title"), self._s("no_chart_data"), parent=self.root)
            return
        selected = TimeRangeDialog(self.root, self.t, bounds, count, self.ui_font, self.palette).show()
        if selected is None:
            return
        start, end = selected
        stats = self.current_store.range_statistics(start, end)
        if stats.total == 0:
            messagebox.showinfo(self._s("app_title"), self._s("range_empty"), parent=self.root)
            return
        samples, aggregated, _exact_count = self.current_store.chart_samples(start, end, max_points=MAX_CHART_POINTS)
        host_label = self.current_config.original_host if self.current_config else self.host_var.get().strip()
        if self.current_resolved_address:
            host_label = f"{host_label} ({self.current_resolved_address})"
        window = ChartWindow(
            self.root,
            samples=samples,
            stats=stats,
            host=host_label,
            start=start,
            end=end,
            translations=self.t,
            aggregated=aggregated,
            pillow_available=self.pillow_available,
            palette=self.palette.copy(),
            on_closed=self._chart_closed,
        )
        self.chart_windows.append(window)

    def export_csv(self) -> None:
        if self.current_store is None or self.persisted_count == 0 or self.export_in_progress:
            if self.current_store is None or self.persisted_count == 0:
                messagebox.showinfo(self._s("app_title"), self._s("no_chart_data"), parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            initialfile=f"pyping_{datetime.now():%Y%m%d_%H%M%S}.csv",
        )
        if not path:
            return
        store = self.current_store
        self.export_in_progress = True
        self.status_key = "state_exporting"
        self._refresh_button_states()
        self._update_status_display()

        def worker() -> None:
            try:
                count = store.export_csv(path)
                result = (path, count, None)
            except Exception as exc:
                result = (path, None, exc)
            try:
                self.export_result_queue.put_nowait(result)
            except queue.Full:
                pass

        self.export_result_queue = queue.Queue(maxsize=1)
        self.export_thread = threading.Thread(target=worker, name="PypingCsvExport", daemon=True)
        self.export_thread.start()

    def _finish_export(self, path: str, count: int | None, error: Exception | None) -> None:
        self.export_in_progress = False
        if self.running:
            self.status_key = "state_running"
        elif error is None:
            self.status_key = "state_exported"
        else:
            self.status_key = "state_error"
        self._refresh_button_states()
        self._update_status_display()
        if error is not None:
            messagebox.showerror(self._s("error_title"), f"{self._s('export_failed')}\n{type(error).__name__}: {error}", parent=self.root)
        else:
            messagebox.showinfo(self._s("export_success"), f"{self._s('export_path')}\n{path}\n\n{self._s('export_rows').format(count=count)}", parent=self.root)

    def clear_session_data(self) -> None:
        if self.running or self.export_in_progress or self.current_store is None:
            return
        if not messagebox.askyesno(self._s("clear_data_title"), self._s("clear_data_confirm"), parent=self.root):
            return
        self._close_current_store()
        self.current_config = None
        self.current_resolved_address = ""
        self.persisted_count = 0
        self.statistics = RunStatistics()
        self.status_key = "state_idle"
        self.last_elapsed_seconds = 0.0
        self.update_stats_display()
        self._refresh_button_states()
        self._update_status_display()

    def _chart_closed(self, window: ChartWindow) -> None:
        try:
            self.chart_windows.remove(window)
        except ValueError:
            pass

    def change_language(self, lang: str) -> None:
        if lang == self.lang or lang not in LANGUAGES:
            return
        log_text = self.output.get("1.0", "end-1c") if hasattr(self, "output") else ""
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        self.lang = lang
        self.t = LANGUAGES[lang]
        self.menu_lang_var.set(lang)
        self.root.title(self.t["app_title"])
        for child in self.root.winfo_children():
            if isinstance(child, ChartWindow):
                continue
            child.destroy()
        self._setup_style()
        self.create_widgets()
        self.create_menu()
        if log_text:
            self.output.insert("1.0", log_text)
            self.output.see(tk.END)
        self.update_stats_display()
        self._set_inputs_enabled(not self.running)
        self._refresh_button_states()
        self._update_status_display()
        if self.running or (self.ping_thread is not None and self.ping_thread.is_alive()):
            self._schedule_poll(QUEUE_POLL_MS)

    def change_theme(self) -> None:
        self._setup_style()
        self._apply_text_theme()
        for chart in list(self.chart_windows):
            try:
                if chart.winfo_exists():
                    chart.palette = self.palette.copy()
                    chart.configure(background=self.palette["window"])
                    chart.canvas.configure(background=self.palette["surface"])
                    chart.draw_chart()
            except tk.TclError:
                continue

    def show_about(self) -> None:
        messagebox.showinfo(self._s("about_title"), self._s("about_message"), parent=self.root)

    def show_dependency_warnings(self, force_ping3: bool = False) -> None:
        messages: list[str] = []
        if force_ping3 or not self.backend.available:
            messages.append(self._s("dependency_ping3").format(detail=self.backend.import_error or "unknown"))
        if not self.pillow_available:
            messages.append(self._s("dependency_pillow"))
        if messages:
            messagebox.showwarning(self._s("dependency_title"), "\n\n".join(messages), parent=self.root)

    def _close_current_store(self) -> None:
        if self.current_store is not None:
            self.current_store.close()
            self.current_store = None
        self.persisted_count = 0

    def on_close(self) -> None:
        if self.export_in_progress:
            messagebox.showwarning(self._s("export_busy_title"), self._s("export_busy_message"), parent=self.root)
            return
        if self.running and not messagebox.askyesno(
            self._s("confirm_close_title"), self._s("confirm_close"), parent=self.root
        ):
            return

        # Resolve all unsaved chart prompts before changing the active session.
        # Cancelling a chart close must leave polling and the running Ping intact.
        for chart in list(self.chart_windows):
            try:
                chart.close_window()
                still_exists = bool(chart.winfo_exists())
            except tk.TclError:
                still_exists = False
            if still_exists:
                return

        if self.running:
            self.stop_event.set()
        for after_name in ("after_id", "status_after_id", "dependency_after_id"):
            after_value = getattr(self, after_name)
            if after_value is not None:
                try:
                    self.root.after_cancel(after_value)
                except tk.TclError:
                    pass
                setattr(self, after_name, None)
        self._close_current_store()
        self.root.destroy()

def main() -> None:
    setup_high_dpi()
    root = tk.Tk()
    configure_tkinter_dpi(root)
    PingApp(root, lang=detect_default_language())
    root.mainloop()
