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


class StorageTests(unittest.TestCase):
    def make_records(self, count: int = 20) -> list[PingRecord]:
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

    def test_exact_range_statistics(self) -> None:
        with SessionStore() as store:
            records = self.make_records()
            self.assertEqual(store.insert_many(records), 20)
            stats = store.range_statistics(records[0].timestamp, records[-1].timestamp)
            self.assertEqual(stats.total, 20)
            self.assertEqual(stats.timeout, 3)
            self.assertEqual(stats.errors, 1)
            self.assertEqual(stats.success, 16)
            self.assertAlmostEqual(stats.failure_rate, 20.0)

    def test_duplicate_sequence_is_rejected_and_batch_is_rolled_back(self) -> None:
        with SessionStore() as store:
            records = self.make_records(2)
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
            records = self.make_records(200)
            store.insert_many(records)
            samples, aggregated, exact_count = store.chart_samples(
                records[0].timestamp,
                records[-1].timestamp,
                max_points=20,
            )
            self.assertTrue(aggregated)
            self.assertEqual(exact_count, 200)
            self.assertLessEqual(len(samples), 21)
            self.assertTrue(any(sample.has_timeout for sample in samples))

    def test_chart_samples_rejects_unsafe_limits(self) -> None:
        with SessionStore() as store:
            records = self.make_records(1)
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
            records = self.make_records(37)
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
            store.insert_many(self.make_records(1))
            with self.assertRaises(ValueError):
                store.export_csv(store.path)

    def test_failed_csv_export_preserves_existing_destination(self) -> None:
        with SessionStore() as store:
            store.insert_many(self.make_records(2))
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
