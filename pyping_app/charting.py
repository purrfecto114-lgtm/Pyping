from __future__ import annotations

import glob
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from .models import ChartSample, RangeStatistics


@dataclass(frozen=True)
class ChartLayout:
    x0: float
    y0: float
    x1: float
    y1: float
    max_latency: float
    start_ts: float
    end_ts: float


def calculate_layout(
    samples: list[ChartSample],
    width: int,
    height: int,
    *,
    left: int,
    right: int,
    top: int,
    bottom: int,
) -> ChartLayout:
    valid = [s.latency_ms for s in samples if s.latency_ms is not None]
    max_latency = max(valid) * 1.2 if valid else 1.0
    max_latency = max(max_latency, 1.0)
    if samples:
        start_ts = samples[0].timestamp.timestamp()
        end_ts = samples[-1].timestamp.timestamp()
    else:
        now = datetime.now().timestamp()
        start_ts = end_ts = now
    if end_ts <= start_ts:
        end_ts = start_ts + 1.0
    return ChartLayout(
        x0=float(left),
        y0=float(height - bottom),
        x1=float(width - right),
        y1=float(top),
        max_latency=max_latency,
        start_ts=start_ts,
        end_ts=end_ts,
    )


def sample_xy(sample: ChartSample, layout: ChartLayout) -> tuple[float, float] | None:
    if sample.latency_ms is None:
        return None
    ratio_x = (sample.timestamp.timestamp() - layout.start_ts) / (
        layout.end_ts - layout.start_ts
    )
    ratio_y = sample.latency_ms / layout.max_latency
    x = layout.x0 + ratio_x * (layout.x1 - layout.x0)
    y = layout.y0 - ratio_y * (layout.y0 - layout.y1)
    return x, y


def build_line_segments(
    samples: Iterable[ChartSample],
    point_mapper: Callable[[ChartSample], tuple[float, float] | None],
) -> list[list[tuple[float, float]]]:
    segments: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for sample in samples:
        point = point_mapper(sample)
        if point is None:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(point)
    if current:
        segments.append(current)
    return segments


def format_latency(value: float | None) -> str:
    return "--" if value is None else f"{value:.1f} ms"


def _font_candidates() -> list[str]:
    candidates: list[str] = []
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        candidates.extend(
            [
                os.path.join(windir, "Fonts", "msyh.ttc"),
                os.path.join(windir, "Fonts", "msyhbd.ttc"),
                os.path.join(windir, "Fonts", "segoeui.ttf"),
                os.path.join(windir, "Fonts", "arial.ttf"),
            ]
        )
    elif sys.platform == "darwin":
        candidates.extend(
            [
                "/System/Library/Fonts/PingFang.ttc",
                "/System/Library/Fonts/STHeiti Light.ttc",
                "/System/Library/Fonts/Helvetica.ttc",
                "/Library/Fonts/Arial Unicode.ttf",
            ]
        )
    else:
        try:
            result = subprocess.run(
                ["fc-match", "-f", "%{file}", "sans:lang=zh-cn"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if result.stdout.strip():
                candidates.append(result.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
        candidates.extend(
            [
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
                "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            ]
        )
        candidates.extend(glob.glob("/usr/share/fonts/**/*NotoSans*CJK*.*", recursive=True))
    return [path for path in candidates if path and os.path.isfile(path)]


def find_pillow_font() -> str | None:
    candidates = _font_candidates()
    return candidates[0] if candidates else None


def render_chart_png(
    path: str,
    *,
    samples: list[ChartSample],
    stats: RangeStatistics,
    host: str,
    start: datetime,
    end: datetime,
    translations: dict[str, str],
    aggregated: bool,
    width: int = 1600,
    height: int = 900,
) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font_path = find_pillow_font()

    def font(size: int, bold: bool = False):
        if font_path:
            try:
                return ImageFont.truetype(font_path, size=size)
            except OSError:
                pass
        return ImageFont.load_default()

    title_font = font(34, True)
    axis_font = font(20)
    tick_font = font(17)
    stats_font = font(20, True)
    note_font = font(17)

    left, right, top, bottom = 125, 250, 100, 145
    layout = calculate_layout(
        samples,
        width,
        height,
        left=left,
        right=right,
        top=top,
        bottom=bottom,
    )

    title = f"{translations['chart_title']} - {host}"
    title_box = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((width - (title_box[2] - title_box[0])) / 2, 30), title, fill="#111111", font=title_font)

    draw.line((layout.x0, layout.y1, layout.x0, layout.y0), fill="#111111", width=3)
    draw.line((layout.x0, layout.y0, layout.x1, layout.y0), fill="#111111", width=3)

    for i in range(6):
        value = layout.max_latency * i / 5
        y = layout.y0 - (layout.y0 - layout.y1) * i / 5
        if i > 0:
            draw.line((layout.x0, y, layout.x1, y), fill="#E5E5E5", width=1)
        label = f"{value:.1f}"
        box = draw.textbbox((0, 0), label, font=tick_font)
        draw.text((layout.x0 - 14 - (box[2] - box[0]), y - 10), label, fill="#333333", font=tick_font)

    y_label = translations["chart_latency"]
    draw.text((20, (layout.y0 + layout.y1) / 2), y_label, fill="#222222", font=axis_font)

    mapper = lambda sample: sample_xy(sample, layout)
    for segment in build_line_segments(samples, mapper):
        if len(segment) >= 2:
            draw.line(segment, fill="#1976D2", width=3)

    for sample in samples:
        point = mapper(sample)
        if point is not None:
            x, y = point
            latency = sample.latency_ms or 0.0
            color = "#2196F3" if latency < 100 else "#FF9800" if latency < 300 else "#F44336"
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=color, outline=color)
        x_ratio = (sample.timestamp.timestamp() - layout.start_ts) / (
            layout.end_ts - layout.start_ts
        )
        x = layout.x0 + x_ratio * (layout.x1 - layout.x0)
        if sample.has_timeout:
            draw.text((x - 6, layout.y0 - 34), "X", fill="#D32F2F", font=axis_font)
        if sample.has_error:
            draw.text((x - 4, layout.y0 - 62), "!", fill="#7B1FA2", font=axis_font)

    for i in range(5):
        ratio = i / 4
        ts = layout.start_ts + ratio * (layout.end_ts - layout.start_ts)
        x = layout.x0 + ratio * (layout.x1 - layout.x0)
        label = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        box = draw.textbbox((0, 0), label, font=tick_font)
        draw.text((x - (box[2] - box[0]) / 2, layout.y0 + 18), label, fill="#333333", font=tick_font)

    legend_x = layout.x1 + 35
    legend_y = layout.y1 + 15
    draw.ellipse((legend_x, legend_y, legend_x + 14, legend_y + 14), fill="#2196F3")
    draw.text((legend_x + 24, legend_y - 5), translations["chart_latency"], fill="#222222", font=axis_font)
    draw.text((legend_x, legend_y + 48), "X", fill="#D32F2F", font=axis_font)
    draw.text((legend_x + 24, legend_y + 44), translations["chart_timeout"], fill="#D32F2F", font=axis_font)
    draw.text((legend_x, legend_y + 95), "!", fill="#7B1FA2", font=axis_font)
    draw.text((legend_x + 24, legend_y + 91), translations["chart_error"], fill="#7B1FA2", font=axis_font)

    range_text = f"{start:%Y-%m-%d %H:%M:%S}  —  {end:%Y-%m-%d %H:%M:%S}"
    draw.text((left, height - 105), range_text, fill="#444444", font=note_font)
    stats_text = (
        f"{translations['total_packets']}: {stats.total} | "
        f"{translations['success_packets']}: {stats.success} | "
        f"{translations['timeout_packets']}: {stats.timeout} | "
        f"{translations['error_packets']}: {stats.errors} | "
        f"{translations['failure_rate']}: {stats.failure_rate:.2f}% | "
        f"{translations['avg_latency']}: {format_latency(stats.average_latency)} | "
        f"{translations['min_latency']}: {format_latency(stats.min_latency)} | "
        f"{translations['max_latency']}: {format_latency(stats.max_latency)}"
    )
    draw.text((left, height - 72), stats_text, fill="#222222", font=stats_font)
    if aggregated:
        draw.text((left, height - 38), translations["chart_aggregated"], fill="#8A5A00", font=note_font)

    image.save(path, "PNG")
