import unittest
from datetime import datetime, timedelta

from pyping_app.models import PingRecord, ResultStatus
from pyping_app.storage import SessionStore


class StorageTests(unittest.TestCase):
    def make_records(self, count=20):
        start = datetime(2026, 7, 22, 10, 0, 0)
        records = []
        for i in range(count):
            if i % 7 == 0:
                status, latency = ResultStatus.TIMEOUT, None
            elif i % 11 == 0:
                status, latency = ResultStatus.NETWORK_ERROR, None
            else:
                status, latency = ResultStatus.SUCCESS, float(i + 1)
            records.append(PingRecord(i + 1, start + timedelta(seconds=i), float(i), latency, status))
        return records

    def test_exact_range_statistics(self):
        with SessionStore() as store:
            records = self.make_records()
            store.insert_many(records)
            stats = store.range_statistics(records[0].timestamp, records[-1].timestamp)
            self.assertEqual(stats.total, 20)
            self.assertEqual(stats.timeout, 3)
            self.assertEqual(stats.errors, 1)
            self.assertEqual(stats.success, 16)
            self.assertAlmostEqual(stats.failure_rate, 20.0)

    def test_large_range_is_aggregated_without_losing_exact_count(self):
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

    def test_csv_export_streams_all_records(self):
        import csv
        import os
        import tempfile

        with SessionStore() as store:
            records = self.make_records(37)
            store.insert_many(records)
            fd, path = tempfile.mkstemp(suffix=".csv")
            os.close(fd)
            try:
                exported = store.export_csv(path, batch_size=7)
                self.assertEqual(exported, 37)
                with open(path, encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(len(rows), 37)
                self.assertEqual(rows[0]["sequence"], "1")
                self.assertEqual(rows[-1]["sequence"], "37")
                self.assertEqual(rows[0]["status"], ResultStatus.TIMEOUT.value)
            finally:
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
