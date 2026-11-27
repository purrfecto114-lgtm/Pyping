from __future__ import annotations

from datetime import datetime, timedelta
import unittest

from pyping_app.events import FailureEventTracker, event_window
from pyping_app.models import PingRecord, ResultStatus


def make_record(seq: int, ts: datetime, status: ResultStatus) -> PingRecord:
    return PingRecord(seq, ts, float(seq), None if status != ResultStatus.SUCCESS else 5.0, status)


class FailureEventTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = datetime(2026, 11, 27, 12, 0, 0)

    def ts(self, seconds: float) -> datetime:
        return self.base + timedelta(seconds=seconds)

    def test_single_timeout_is_kept_and_recovered(self) -> None:
        tracker = FailureEventTracker()
        self.assertIsNone(tracker.process(make_record(1, self.ts(0), ResultStatus.SUCCESS)))
        event = tracker.process(make_record(2, self.ts(1), ResultStatus.TIMEOUT))
        self.assertIsNone(event)
        event = tracker.process(make_record(3, self.ts(2), ResultStatus.SUCCESS))
        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "network")
        self.assertEqual(event.status, "timeout")
        self.assertEqual(event.first_failure_seq, 2)
        self.assertEqual(event.first_recovery_seq, 3)
        self.assertTrue(event.recovered)
        self.assertEqual(event.failure_count, 1)

    def test_consecutive_failures_merge_into_one_event(self) -> None:
        tracker = FailureEventTracker()
        tracker.process(make_record(1, self.ts(0), ResultStatus.TIMEOUT))
        tracker.process(make_record(2, self.ts(1), ResultStatus.NETWORK_ERROR))
        tracker.process(make_record(3, self.ts(2), ResultStatus.TIMEOUT))
        event = tracker.process(make_record(4, self.ts(3), ResultStatus.SUCCESS))
        self.assertEqual(event.timeout_count, 2)
        self.assertEqual(event.error_count, 1)
        self.assertEqual(event.failure_count, 3)
        self.assertEqual(event.observation_span_seconds, 3.0)

    def test_local_errors_are_separate_events(self) -> None:
        tracker = FailureEventTracker()
        tracker.process(make_record(1, self.ts(0), ResultStatus.TIMEOUT))
        # The next failure is a local tool error: it must close the network
        # event and never be merged into the network chain.
        closed = tracker.process(make_record(2, self.ts(1), ResultStatus.PERMISSION_ERROR))
        self.assertIsNotNone(closed)
        self.assertEqual(closed.kind, "network")
        self.assertFalse(closed.recovered)
        closed = tracker.process(make_record(3, self.ts(2), ResultStatus.SUCCESS))
        self.assertIsNotNone(closed)
        self.assertEqual(closed.kind, "local")
        self.assertEqual(closed.status, "permission_error")
        self.assertTrue(closed.recovered)

    def test_cancelled_probe_closes_event_as_unrecovered(self) -> None:
        tracker = FailureEventTracker()
        tracker.process(make_record(1, self.ts(0), ResultStatus.TIMEOUT))
        event = tracker.process(make_record(2, self.ts(1), ResultStatus.CANCELLED))
        self.assertIsNotNone(event)
        self.assertFalse(event.recovered)
        # The cancelled probe itself is not evidence: a success afterwards
        # opens nothing, and no phantom recovery is recorded.
        self.assertIsNone(tracker.process(make_record(3, self.ts(2), ResultStatus.SUCCESS)))

    def test_flush_marks_unrecovered(self) -> None:
        tracker = FailureEventTracker()
        tracker.process(make_record(1, self.ts(0), ResultStatus.TIMEOUT))
        event = tracker.flush()
        self.assertIsNotNone(event)
        self.assertFalse(event.recovered)
        self.assertIsNone(tracker.flush())

    def test_mark_boundary_closes_open_event(self) -> None:
        tracker = FailureEventTracker()
        tracker.process(make_record(1, self.ts(0), ResultStatus.NETWORK_ERROR))
        event = tracker.mark_boundary()
        self.assertIsNotNone(event)
        self.assertFalse(event.recovered)
        tracker.process(make_record(2, self.ts(1), ResultStatus.NETWORK_ERROR))
        event = tracker.process(make_record(3, self.ts(2), ResultStatus.SUCCESS))
        self.assertEqual(event.failure_count, 1)


class EventWindowTests(unittest.TestCase):
    def test_window_is_symmetric_and_clamped(self) -> None:
        from pyping_app.events import FailureEventRow

        base = datetime(2026, 11, 27, 12, 0, 0)
        bounds = (base, base + timedelta(seconds=40))
        row = FailureEventRow(
            event_id=1,
            kind="network",
            status="timeout",
            first_failure_seq=2,
            first_failure_ts=base + timedelta(seconds=12.5),
            last_failure_seq=4,
            last_failure_ts=base + timedelta(seconds=12.7),
            first_recovery_seq=5,
            first_recovery_ts=base + timedelta(seconds=12.8),
            timeout_count=3,
            error_count=0,
            recovered=True,
        )
        start, end = event_window(row, bounds, 30)
        self.assertEqual(start, base)  # clamped to session start
        self.assertEqual(end, base + timedelta(seconds=40) + timedelta(microseconds=1))
        # Report-style example: unclamped window covers failure ±30 s.
        wide_bounds = (base - timedelta(seconds=100), base + timedelta(seconds=100))
        start, end = event_window(row, wide_bounds, 30)
        self.assertEqual(start, base + timedelta(seconds=12.5) - timedelta(seconds=30))
        self.assertEqual(end, base + timedelta(seconds=12.8) + timedelta(seconds=30, microseconds=1))

    def test_unrecovered_event_uses_last_failure_as_center_end(self) -> None:
        from pyping_app.events import FailureEventRow

        base = datetime(2026, 11, 27, 12, 0, 0)
        row = FailureEventRow(
            event_id=2,
            kind="network",
            status="timeout",
            first_failure_seq=2,
            first_failure_ts=base + timedelta(seconds=10),
            last_failure_seq=4,
            last_failure_ts=base + timedelta(seconds=10.2),
            first_recovery_seq=None,
            first_recovery_ts=None,
            timeout_count=3,
            error_count=0,
            recovered=False,
        )
        bounds = (base - timedelta(seconds=100), base + timedelta(seconds=100))
        start, end = event_window(row, bounds, 30)
        self.assertEqual(start, base + timedelta(seconds=10) - timedelta(seconds=30))
        self.assertEqual(
            end,
            base + timedelta(seconds=10.2) + timedelta(seconds=30, microseconds=1),
        )


if __name__ == "__main__":
    unittest.main()
