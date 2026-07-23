from __future__ import annotations

from datetime import datetime
import unittest

from pyping_app.gui import TimeRangeDialog


class TimeRangeLogicTests(unittest.TestCase):
    def test_local_datetime_is_accepted(self) -> None:
        self.assertEqual(
            TimeRangeDialog._parse_datetime("2026-07-22 12:34:56"),
            datetime(2026, 7, 22, 12, 34, 56),
        )

    def test_timezone_aware_datetime_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TimeRangeDialog._parse_datetime("2026-07-22T12:34:56+00:00")


if __name__ == "__main__":
    unittest.main()
