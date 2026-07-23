from __future__ import annotations

from datetime import datetime, timedelta
import os
import tempfile
import unittest
from unittest import mock

from pyping_app.charting import build_line_segments, calculate_layout, render_chart_png, sample_xy
from pyping_app.models import ChartSample, RangeStatistics, ResultStatus


class ChartingTests(unittest.TestCase):
    def test_failure_breaks_line_segment(self) -> None:
        start = datetime.now()
        samples = [
            ChartSample(start, 10.0, ResultStatus.SUCCESS),
            ChartSample(start + timedelta(seconds=1), None, ResultStatus.TIMEOUT, timeout_count=1),
            ChartSample(start + timedelta(seconds=2), 20.0, ResultStatus.SUCCESS),
        ]
        segments = build_line_segments(
            samples,
            lambda sample: None
            if sample.latency_ms is None
            else (sample.timestamp.timestamp(), sample.latency_ms),
        )
        self.assertEqual(len(segments), 2)
        self.assertEqual([len(segment) for segment in segments], [1, 1])

    def test_aggregated_bucket_with_failures_breaks_latency_line(self) -> None:
        start = datetime.now()
        samples = [
            ChartSample(start, 10.0, ResultStatus.SUCCESS),
            ChartSample(
                start + timedelta(seconds=1),
                15.0,
                ResultStatus.SUCCESS,
                timeout_count=1,
                sample_count=4,
            ),
            ChartSample(start + timedelta(seconds=2), 20.0, ResultStatus.SUCCESS),
        ]
        layout = calculate_layout(samples, 800, 500, left=50, right=50, top=50, bottom=50)
        segments = build_line_segments(samples, lambda sample: sample_xy(sample, layout))
        self.assertEqual([len(segment) for segment in segments], [1, 1])


class PngRenderTests(unittest.TestCase):
    def translations(self) -> dict[str, str]:
        return {
            "chart_title": "Ping Results",
            "chart_latency": "Latency (ms)",
            "chart_timeout": "Timeout",
            "chart_error": "Other error",
            "total_packets": "Total",
            "success_packets": "Success",
            "timeout_packets": "Timeout",
            "error_packets": "Errors",
            "failure_rate": "Failure rate",
            "avg_latency": "Average latency",
            "min_latency": "Minimum latency",
            "max_latency": "Maximum latency",
            "chart_aggregated": "Aggregated",
        }

    def render_arguments(self):
        start = datetime(2026, 7, 22, 10, 0, 0)
        samples = [
            ChartSample(start, 10.0, ResultStatus.SUCCESS),
            ChartSample(start + timedelta(seconds=1), None, ResultStatus.TIMEOUT, timeout_count=1),
            ChartSample(start + timedelta(seconds=2), 20.0, ResultStatus.SUCCESS),
        ]
        stats = RangeStatistics(3, 2, 1, 0, 15.0, 10.0, 20.0)
        return start, samples, stats

    def test_png_export_is_offscreen_and_valid(self) -> None:
        from PIL import Image

        start, samples, stats = self.render_arguments()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "chart.png")
            render_chart_png(
                path,
                samples=samples,
                stats=stats,
                host="127.0.0.1",
                start=start,
                end=start + timedelta(seconds=2),
                translations=self.translations(),
                aggregated=False,
                width=800,
                height=500,
            )
            with Image.open(path) as image:
                self.assertEqual(image.size, (800, 500))
                self.assertEqual(image.format, "PNG")

    def test_png_export_rejects_unsafe_dimensions(self) -> None:
        start, samples, stats = self.render_arguments()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "chart.png")
            for width, height in ((100, 500), (800, 100), (5000, 900), (4096, 4096)):
                with self.subTest(width=width, height=height):
                    with self.assertRaises(ValueError):
                        render_chart_png(
                            path, samples=samples, stats=stats, host="127.0.0.1",
                            start=start, end=start + timedelta(seconds=2),
                            translations=self.translations(), aggregated=False,
                            width=width, height=height,
                        )

    def test_failed_png_save_preserves_existing_file(self) -> None:
        from PIL import Image

        start, samples, stats = self.render_arguments()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "chart.png")
            with open(path, "wb") as handle:
                handle.write(b"ORIGINAL")
            with mock.patch.object(Image.Image, "save", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    render_chart_png(
                        path,
                        samples=samples,
                        stats=stats,
                        host="127.0.0.1",
                        start=start,
                        end=start + timedelta(seconds=2),
                        translations=self.translations(),
                        aggregated=False,
                        width=800,
                        height=500,
                    )
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), b"ORIGINAL")


if __name__ == "__main__":
    unittest.main()
