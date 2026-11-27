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


class TimeRangeBoundaryTests(unittest.TestCase):
    """_confirm() boundary semantics without a display.

    The dialog instance is built via __new__ so no Tk widgets are required;
    only the pure decision logic (preset exactness, half-open end handling)
    is exercised.
    """

    BOUNDS = (
        datetime(2026, 7, 22, 12, 0, 0, 100000),
        datetime(2026, 7, 22, 12, 0, 0, 900000),
    )

    def make_dialog(self, preset_value: str, start_text: str, end_text: str):
        from types import SimpleNamespace

        dialog = TimeRangeDialog.__new__(TimeRangeDialog)
        dialog.t = {"error_title": "error", "range_invalid": "invalid"}
        dialog.bounds = self.BOUNDS
        dialog.dialog = None
        dialog.preset_var = SimpleNamespace(get=lambda: preset_value)
        dialog.start_var = SimpleNamespace(get=lambda: start_text)
        dialog.end_var = SimpleNamespace(get=lambda: end_text)
        dialog._preset_range = None
        dialog.result = None
        return dialog

    def test_preset_all_returns_exact_bounds_without_text_roundtrip(self) -> None:
        from types import SimpleNamespace
        from datetime import timedelta

        dialog = self.make_dialog("range_all", "", "")
        dialog._preset_range = (
            self.BOUNDS[0],
            self.BOUNDS[1] + timedelta(microseconds=1),
        )
        # Display strings are second-truncated; presets must ignore them.
        dialog.start_var = SimpleNamespace(get=lambda: "2026-07-22 12:00:00")
        dialog.end_var = SimpleNamespace(get=lambda: "2026-07-22 12:00:00")
        dialog._confirm()
        self.assertEqual(dialog.result, (
            self.BOUNDS[0],
            self.BOUNDS[1] + timedelta(microseconds=1),
        ))
        self.assertIsNotNone(dialog.result)

    def test_subsecond_custom_end_stays_exact(self) -> None:
        dialog = self.make_dialog(
            "range_custom",
            "2026-07-22 12:00:00.200000",
            "2026-07-22 12:00:00.300000",
        )
        dialog._confirm()
        self.assertEqual(
            dialog.result,
            (datetime(2026, 7, 22, 12, 0, 0, 200000), datetime(2026, 7, 22, 12, 0, 0, 300000)),
        )

    def test_whole_second_custom_end_covers_full_second(self) -> None:
        dialog = self.make_dialog(
            "range_custom",
            "2026-07-22 12:00:00",
            "2026-07-22 12:00:01",
        )
        dialog._confirm()
        from datetime import timedelta
        self.assertEqual(
            dialog.result,
            (
                datetime(2026, 7, 22, 12, 0, 0),
                datetime(2026, 7, 22, 12, 0, 1) + timedelta(seconds=0.999999),
            ),
        )

    def test_range_not_overlapping_data_is_rejected(self) -> None:
        from unittest import mock
        dialog = self.make_dialog(
            "range_custom",
            "2026-07-22 11:00:00",
            "2026-07-22 11:00:30",
        )
        with mock.patch("pyping_app.gui.messagebox.showerror") as show_error:
            dialog._confirm()
        show_error.assert_called_once()
        self.assertIsNone(dialog.result)

    def test_inverted_range_is_rejected(self) -> None:
        from unittest import mock
        dialog = self.make_dialog(
            "range_custom",
            "2026-07-22 12:00:10",
            "2026-07-22 12:00:00",
        )
        with mock.patch("pyping_app.gui.messagebox.showerror") as show_error:
            dialog._confirm()
        show_error.assert_called_once()
        self.assertIsNone(dialog.result)

