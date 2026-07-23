from __future__ import annotations

import pathlib
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pyping_app.core import PingBackend
from pyping_app.gui import MIN_LOG_HEIGHT, ChartWindow, PingApp


def run_session_smoke() -> None:
    root = tk.Tk()
    root.withdraw()
    app = PingApp(root, lang="zh_CN")
    app.backend = PingBackend(lambda *args, **kwargs: 3.5)
    app.host_var.set("127.0.0.1")
    app.count_var.set("3")
    app.interval_var.set("0.1")
    app.start_ping()
    deadline = time.monotonic() + 3
    while app.running and time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
    root.update()
    assert not app.running
    assert app.current_store is not None
    assert app.current_store.count() == 3
    assert "disabled" not in app.chart_btn.state()

    bounds = app.current_store.bounds()
    assert bounds is not None
    stats = app.current_store.range_statistics(*bounds)
    samples, aggregated, _count = app.current_store.chart_samples(*bounds)
    chart = ChartWindow(
        root,
        samples=samples,
        stats=stats,
        host="127.0.0.1",
        start=bounds[0],
        end=bounds[1],
        translations=app.t,
        aggregated=aggregated,
        pillow_available=True,
        palette=app.palette,
        on_closed=lambda _window: None,
    )
    root.update()
    chart.draw_chart()
    assert len(chart.canvas.find_all()) > 0
    app.chart_windows.append(chart)

    app.change_language("en_US")
    root.update_idletasks()
    assert app.lang == "en_US"
    assert app.metric_vars["total_packets"].get() == "3"
    assert chart.winfo_exists(), "language switch destroyed an unsaved chart window"

    app.theme_var.set("dark")
    app.change_theme()
    root.update_idletasks()
    assert app.palette["window"] == "#202020"
    assert app.output.cget("background") == app.palette["control"]

    app.theme_var.set("light")
    app.change_theme()
    root.update_idletasks()
    assert app.palette["window"] == "#F3F3F3"
    style = ttk.Style(root)
    assert style.lookup("TFrame", "background") == app.palette["surface"]
    assert style.lookup("Root.TFrame", "background") == app.palette["window"]
    assert style.lookup("TEntry", "fieldbackground") == app.palette["control"]

    entry_heights = {
        widget.winfo_reqheight()
        for widget in (app.host_entry, app.size_entry, app.interval_entry, app.timeout_entry, app.count_entry)
    }
    assert len(entry_heights) == 1, entry_heights

    # Cancelling an unsaved-chart prompt must not partially stop the main app.
    app.running = True
    app.stop_event.clear()
    chart.dirty = True
    with mock.patch.object(messagebox, "askyesno", return_value=True), mock.patch.object(
        messagebox, "askyesnocancel", return_value=None
    ):
        app.on_close()
    root.update_idletasks()
    assert root.winfo_exists()
    assert not app.stop_event.is_set()
    assert app.status_after_id is not None
    assert chart.winfo_exists()

    app.running = False
    chart.dirty = False
    app.on_close()


def run_startup_layout_smoke() -> None:
    root = tk.Tk()
    app = PingApp(root, lang="zh_CN")
    root.update()
    root.update_idletasks()
    assert root.winfo_width() <= root.winfo_screenwidth()
    assert root.winfo_height() <= root.winfo_screenheight()
    expected_log_height = MIN_LOG_HEIGHT - 12 if root.winfo_screenheight() >= 700 else 56
    assert app.output.winfo_height() >= expected_log_height, app.output.winfo_height()
    assert app.output.winfo_ismapped()
    app.on_close()


if __name__ == "__main__":
    run_session_smoke()
    run_startup_layout_smoke()
    print("GUI smoke: OK")
