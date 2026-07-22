import unittest
from datetime import datetime, timedelta

from pyping_app.charting import build_line_segments
from pyping_app.models import ChartSample, ResultStatus


class ChartingTests(unittest.TestCase):
    def test_failure_breaks_line_segment(self):
        start = datetime.now()
        samples = [
            ChartSample(start, 10.0, ResultStatus.SUCCESS),
            ChartSample(start + timedelta(seconds=1), None, ResultStatus.TIMEOUT, timeout_count=1),
            ChartSample(start + timedelta(seconds=2), 20.0, ResultStatus.SUCCESS),
        ]
        segments = build_line_segments(
            samples,
            lambda sample: None if sample.latency_ms is None else (sample.timestamp.timestamp(), sample.latency_ms),
        )
        self.assertEqual(len(segments), 2)
        self.assertEqual([len(segment) for segment in segments], [1, 1])


if __name__ == "__main__":
    unittest.main()

class PngRenderTests(unittest.TestCase):
    def test_png_export_is_offscreen_and_valid(self):
        import os
        import tempfile
        from PIL import Image
        from pyping_app.charting import render_chart_png
        from pyping_app.models import RangeStatistics

        start = datetime(2026, 7, 22, 10, 0, 0)
        samples = [
            ChartSample(start, 10.0, ResultStatus.SUCCESS),
            ChartSample(start + timedelta(seconds=1), None, ResultStatus.TIMEOUT, timeout_count=1),
            ChartSample(start + timedelta(seconds=2), 20.0, ResultStatus.SUCCESS),
        ]
        stats = RangeStatistics(3, 2, 1, 0, 15.0, 10.0, 20.0)
        translations = {
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
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            render_chart_png(
                path,
                samples=samples,
                stats=stats,
                host="127.0.0.1",
                start=start,
                end=start + timedelta(seconds=2),
                translations=translations,
                aggregated=False,
                width=800,
                height=500,
            )
            with Image.open(path) as image:
                self.assertEqual(image.size, (800, 500))
                self.assertEqual(image.format, "PNG")
        finally:
            os.remove(path)
