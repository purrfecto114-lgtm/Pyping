from __future__ import annotations

import csv
from datetime import datetime, timedelta
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from pyping_app.models import PingRecord, ResultStatus
from pyping_app.storage import SessionStore


def make_records(count: int = 20) -> list[PingRecord]:
    start = datetime(2026, 7, 22, 10, 0, 0)
    records: list[PingRecord] = []
    for i in range(count):
        if i % 7 == 0:
            status, latency = ResultStatus.TIMEOUT, None
        elif i % 11 == 0:
            status, latency = ResultStatus.NETWORK_ERROR, None
        else:
            status, latency = ResultStatus.SUCCESS, float(i + 1)
        records.append(
            PingRecord(i + 1, start + timedelta(seconds=i), float(i), latency, status)
        )
    return records


class StorageTests(unittest.TestCase):
    def test_exact_range_statistics(self) -> None:
        with SessionStore() as store:
            records = make_records()
            self.assertEqual(store.insert_many(records), 20)
            # Range queries are half-open [start, end): the end boundary is
            # exclusive, so the exact "all data" end must be extended past the
            # final record by one microsecond.
            exclusive_end = records[-1].timestamp + timedelta(microseconds=1)
            stats = store.range_statistics(records[0].timestamp, exclusive_end)
            self.assertEqual(stats.total, 20)
            self.assertEqual(stats.timeout, 3)
            self.assertEqual(stats.errors, 1)
            self.assertEqual(stats.success, 16)
            self.assertAlmostEqual(stats.failure_rate, 20.0)
            truncated = store.range_statistics(records[0].timestamp, records[-1].timestamp)
            self.assertEqual(truncated.total, 19)

    def test_duplicate_sequence_is_rejected_and_batch_is_rolled_back(self) -> None:
        with SessionStore() as store:
            records = make_records(2)
            store.insert_many(records)
            duplicate = PingRecord(
                2,
                records[-1].timestamp + timedelta(seconds=1),
                2.0,
                999.0,
                ResultStatus.SUCCESS,
            )
            new_record = PingRecord(
                3,
                records[-1].timestamp + timedelta(seconds=2),
                3.0,
                30.0,
                ResultStatus.SUCCESS,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                store.insert_many([new_record, duplicate])
            self.assertEqual(store.count(), 2)

    def test_large_range_is_aggregated_without_losing_exact_count(self) -> None:
        with SessionStore() as store:
            records = make_records(200)
            store.insert_many(records)
            samples, aggregated, exact_count = store.chart_samples(
                records[0].timestamp,
                records[-1].timestamp + timedelta(microseconds=1),
                max_points=20,
            )
            self.assertTrue(aggregated)
            self.assertEqual(exact_count, 200)
            self.assertLessEqual(len(samples), 21)
            self.assertTrue(any(sample.has_timeout for sample in samples))

    def test_chart_samples_rejects_unsafe_limits(self) -> None:
        with SessionStore() as store:
            records = make_records(1)
            store.insert_many(records)
            for limit in (0, -1, 50_001):
                with self.subTest(limit=limit):
                    with self.assertRaisesRegex(ValueError, "between 1 and 50000"):
                        store.chart_samples(
                            records[0].timestamp, records[0].timestamp, max_points=limit
                        )

    def test_database_constraints_reject_invalid_record(self) -> None:
        with SessionStore() as store:
            invalid = PingRecord(
                0, datetime(2026, 7, 22, 10, 0, 0), 0.0, 1.0, ResultStatus.SUCCESS
            )
            with self.assertRaises(sqlite3.IntegrityError):
                store.insert_many([invalid])
            self.assertEqual(store.count(), 0)

    def test_database_rejects_oversized_detail(self) -> None:
        with SessionStore() as store:
            invalid = PingRecord(
                1,
                datetime(2026, 7, 22, 10, 0, 0),
                0.0,
                None,
                ResultStatus.NETWORK_ERROR,
                "x" * 1001,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                store.insert_many([invalid])
            self.assertEqual(store.count(), 0)

    def test_database_rejects_status_latency_mismatch(self) -> None:
        with SessionStore() as store:
            invalid = PingRecord(
                1, datetime(2026, 7, 22, 10, 0, 0), 0.0, None, ResultStatus.SUCCESS
            )
            with self.assertRaises(sqlite3.IntegrityError):
                store.insert_many([invalid])
            self.assertEqual(store.count(), 0)

    def test_csv_export_streams_all_records_and_neutralizes_formulas(self) -> None:
        with SessionStore() as store:
            records = make_records(37)
            records[1] = PingRecord(
                records[1].sequence,
                records[1].timestamp,
                records[1].elapsed_seconds,
                records[1].latency_ms,
                records[1].status,
                "=HYPERLINK(\"https://example.invalid\",\"click\")",
            )
            store.insert_many(records)
            with tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "results.csv")
                exported = store.export_csv(path, batch_size=7)
                self.assertEqual(exported, 37)
                with open(path, encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(len(rows), 37)
                self.assertEqual(rows[0]["sequence"], "1")
                self.assertEqual(rows[-1]["sequence"], "37")
                self.assertEqual(rows[0]["status"], ResultStatus.TIMEOUT.value)
                self.assertTrue(rows[1]["detail"].startswith("'="))

    def test_csv_export_refuses_to_overwrite_active_database(self) -> None:
        with SessionStore() as store:
            store.insert_many(make_records(1))
            with self.assertRaises(ValueError):
                store.export_csv(store.path)

    def test_failed_csv_export_preserves_existing_destination(self) -> None:
        with SessionStore() as store:
            store.insert_many(make_records(2))
            with tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "existing.csv")
                with open(path, "wb") as handle:
                    handle.write(b"ORIGINAL")
                with mock.patch("pyping_app.storage.csv.writer", side_effect=OSError("boom")):
                    with self.assertRaises(OSError):
                        store.export_csv(path)
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), b"ORIGINAL")
                leftovers = [name for name in os.listdir(directory) if name != "existing.csv"]
                self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()

class FailureEventStorageTests(unittest.TestCase):
    def test_event_roundtrip_is_newest_first(self) -> None:
        from pyping_app.events import FailureEvent

        with SessionStore() as store:
            base = datetime(2026, 11, 27, 12, 0, 0)
            first = FailureEvent(
                kind="network", status="timeout",
                first_failure_seq=1, first_failure_ts=base,
                last_failure_seq=2, last_failure_ts=base + timedelta(seconds=1),
                first_recovery_seq=3, first_recovery_ts=base + timedelta(seconds=2),
                timeout_count=2, error_count=0, recovered=True,
            )
            second = FailureEvent(
                kind="local", status="permission_error",
                first_failure_seq=4, first_failure_ts=base + timedelta(seconds=5),
                last_failure_seq=4, last_failure_ts=base + timedelta(seconds=5),
                first_recovery_seq=5, first_recovery_ts=base + timedelta(seconds=6),
                timeout_count=0, error_count=1, recovered=True,
            )
            store.insert_event(first)
            store.insert_event(second)
            rows = store.failure_events()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].event_id, second.first_failure_seq and rows[0].event_id)
            self.assertEqual(rows[0].kind, "local")
            self.assertEqual(rows[1].kind, "network")
            self.assertTrue(rows[1].recovered)
            self.assertAlmostEqual(rows[1].observation_span_seconds, 2.0)

    def test_chart_snapshot_matches_writer_queries(self) -> None:
        from pyping_app.events import FailureEvent

        with SessionStore() as store:
            records = make_records(50)
            store.insert_many(records)
            event = FailureEvent(
                kind="network", status="timeout",
                first_failure_seq=1, first_failure_ts=records[0].timestamp,
                last_failure_seq=1, last_failure_ts=records[0].timestamp,
                first_recovery_seq=2, first_recovery_ts=records[1].timestamp,
                timeout_count=1, error_count=0, recovered=True,
            )
            store.insert_event(event)
            start = records[0].timestamp
            end = records[-1].timestamp + timedelta(microseconds=1)
            stats, samples, aggregated, count = store.chart_snapshot(start, end, max_points=10)
            direct_stats = store.range_statistics(start, end)
            direct_samples, direct_aggregated, direct_count = store.chart_samples(start, end, max_points=10)
            self.assertEqual(stats.total, direct_stats.total)
            self.assertEqual(stats.timeout, direct_stats.timeout)
            self.assertEqual(count, direct_count)
            self.assertEqual(aggregated, direct_aggregated)
            self.assertEqual(len(samples), len(direct_samples))
            self.assertEqual(len(store.failure_events()), 1)

    def test_cancelled_records_are_excluded_from_failure_metrics(self) -> None:
        with SessionStore() as store:
            base = datetime(2026, 11, 27, 13, 0, 0)
            records = [
                PingRecord(1, base, 0.0, 10.0, ResultStatus.SUCCESS),
                PingRecord(2, base + timedelta(seconds=1), 1.0, None, ResultStatus.TIMEOUT),
                PingRecord(3, base + timedelta(seconds=2), 2.0, None, ResultStatus.CANCELLED),
            ]
            store.insert_many(records)
            stats = store.range_statistics(base, base + timedelta(seconds=3, microseconds=1))
            self.assertEqual(stats.total, 3)
            self.assertEqual(stats.cancelled, 1)
            self.assertEqual(stats.errors, 0)
            self.assertEqual(stats.timeout, 1)
            # 1 failure out of 2 measured probes: cancelled probes carry no
            # evidence and must not dilute or inflate the rate.
            self.assertAlmostEqual(stats.failure_rate, 50.0)
            stats, samples, aggregated, count = store.chart_snapshot(
                base, base + timedelta(seconds=3, microseconds=1), max_points=1
            )
            self.assertTrue(aggregated)
            self.assertEqual(samples[0].error_count, 0)
            self.assertEqual(samples[0].timeout_count, 1)
            self.assertAlmostEqual(samples[0].latency_ms, 10.0)

