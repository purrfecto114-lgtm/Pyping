from __future__ import annotations

import csv
import os
import sqlite3
import tempfile
import threading
from datetime import datetime
from typing import Iterable

from .models import ChartSample, PingRecord, RangeStatistics, ResultStatus


class SessionStore:
    def __init__(self) -> None:
        fd, self.path = tempfile.mkstemp(prefix="pyping_", suffix=".sqlite3")
        os.close(fd)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE records (
                sequence INTEGER PRIMARY KEY,
                wall_ts REAL NOT NULL,
                elapsed REAL NOT NULL,
                latency REAL,
                status TEXT NOT NULL,
                detail TEXT NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX idx_records_wall_ts ON records(wall_ts)")
        self._conn.commit()
        self._closed = False

    def insert_many(self, records: Iterable[PingRecord]) -> None:
        rows = [
            (
                record.sequence,
                record.timestamp.timestamp(),
                record.elapsed_seconds,
                record.latency_ms,
                record.status.value,
                record.detail,
            )
            for record in records
        ]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?, ?, ?)", rows
            )
            self._conn.commit()

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM records").fetchone()
        return int(row[0])

    def bounds(self) -> tuple[datetime, datetime] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(wall_ts), MAX(wall_ts) FROM records"
            ).fetchone()
        if not row or row[0] is None:
            return None
        return datetime.fromtimestamp(row[0]), datetime.fromtimestamp(row[1])

    def range_statistics(self, start: datetime, end: datetime) -> RangeStatistics:
        start_ts, end_ts = start.timestamp(), end.timestamp()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    COUNT(*),
                    SUM(CASE WHEN status = ? THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status = ? THEN 1 ELSE 0 END),
                    SUM(CASE WHEN status NOT IN (?, ?) THEN 1 ELSE 0 END),
                    AVG(CASE WHEN status = ? THEN latency END),
                    MIN(CASE WHEN status = ? THEN latency END),
                    MAX(CASE WHEN status = ? THEN latency END)
                FROM records
                WHERE wall_ts BETWEEN ? AND ?
                """,
                (
                    ResultStatus.SUCCESS.value,
                    ResultStatus.TIMEOUT.value,
                    ResultStatus.SUCCESS.value,
                    ResultStatus.TIMEOUT.value,
                    ResultStatus.SUCCESS.value,
                    ResultStatus.SUCCESS.value,
                    ResultStatus.SUCCESS.value,
                    start_ts,
                    end_ts,
                ),
            ).fetchone()
        return RangeStatistics(
            total=int(row[0] or 0),
            success=int(row[1] or 0),
            timeout=int(row[2] or 0),
            errors=int(row[3] or 0),
            average_latency=float(row[4]) if row[4] is not None else None,
            min_latency=float(row[5]) if row[5] is not None else None,
            max_latency=float(row[6]) if row[6] is not None else None,
        )

    def chart_samples(
        self,
        start: datetime,
        end: datetime,
        *,
        max_points: int = 5000,
    ) -> tuple[list[ChartSample], bool, int]:
        start_ts, end_ts = start.timestamp(), end.timestamp()
        with self._lock:
            count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM records WHERE wall_ts BETWEEN ? AND ?",
                    (start_ts, end_ts),
                ).fetchone()[0]
            )
            if count <= max_points:
                rows = self._conn.execute(
                    """
                    SELECT wall_ts, latency, status
                    FROM records
                    WHERE wall_ts BETWEEN ? AND ?
                    ORDER BY sequence
                    """,
                    (start_ts, end_ts),
                ).fetchall()
                samples = [
                    ChartSample(
                        timestamp=datetime.fromtimestamp(row[0]),
                        latency_ms=float(row[1]) if row[1] is not None else None,
                        status=ResultStatus(row[2]),
                        timeout_count=1 if row[2] == ResultStatus.TIMEOUT.value else 0,
                        error_count=(
                            1
                            if row[2]
                            not in (
                                ResultStatus.SUCCESS.value,
                                ResultStatus.TIMEOUT.value,
                            )
                            else 0
                        ),
                    )
                    for row in rows
                ]
                return samples, False, count

            span = max(end_ts - start_ts, 0.001)
            bucket_width = span / max_points
            rows = self._conn.execute(
                """
                SELECT
                    AVG(wall_ts) AS avg_ts,
                    AVG(CASE WHEN status = ? THEN latency END) AS avg_latency,
                    SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS timeout_count,
                    SUM(CASE WHEN status NOT IN (?, ?) THEN 1 ELSE 0 END) AS error_count,
                    COUNT(*) AS sample_count
                FROM records
                WHERE wall_ts BETWEEN ? AND ?
                GROUP BY CAST(MIN((wall_ts - ?) / ?, ?) AS INTEGER)
                ORDER BY avg_ts
                """,
                (
                    ResultStatus.SUCCESS.value,
                    ResultStatus.TIMEOUT.value,
                    ResultStatus.SUCCESS.value,
                    ResultStatus.TIMEOUT.value,
                    start_ts,
                    end_ts,
                    start_ts,
                    bucket_width,
                    max_points - 1,
                ),
            ).fetchall()

        samples: list[ChartSample] = []
        for avg_ts, latency, timeout_count, error_count, sample_count in rows:
            timeout_count = int(timeout_count or 0)
            error_count = int(error_count or 0)
            if latency is not None:
                status = ResultStatus.SUCCESS
            elif timeout_count:
                status = ResultStatus.TIMEOUT
            else:
                status = ResultStatus.NETWORK_ERROR
            samples.append(
                ChartSample(
                    timestamp=datetime.fromtimestamp(avg_ts),
                    latency_ms=float(latency) if latency is not None else None,
                    status=status,
                    timeout_count=timeout_count,
                    error_count=error_count,
                    sample_count=int(sample_count),
                )
            )
        return samples, True, count

    def export_csv(
        self,
        path: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        batch_size: int = 5000,
    ) -> int:
        """Export a committed snapshot through a separate read connection.

        A separate connection keeps the GUI writer responsive under WAL mode and
        avoids loading long sessions into memory.
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        query = (
            "SELECT sequence, wall_ts, elapsed, latency, status, detail "
            "FROM records"
        )
        params: list[float] = []
        clauses: list[str] = []
        if start is not None:
            clauses.append("wall_ts >= ?")
            params.append(start.timestamp())
        if end is not None:
            clauses.append("wall_ts <= ?")
            params.append(end.timestamp())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY sequence"

        connection = sqlite3.connect(self.path)
        count = 0
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            cursor = connection.execute(query, params)
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    (
                        "sequence",
                        "timestamp",
                        "elapsed_seconds",
                        "latency_ms",
                        "status",
                        "detail",
                    )
                )
                while True:
                    rows = cursor.fetchmany(batch_size)
                    if not rows:
                        break
                    for sequence, wall_ts, elapsed, latency, status, detail in rows:
                        writer.writerow(
                            (
                                sequence,
                                datetime.fromtimestamp(wall_ts).isoformat(sep=" ", timespec="milliseconds"),
                                f"{float(elapsed):.6f}",
                                "" if latency is None else f"{float(latency):.6f}",
                                status,
                                detail,
                            )
                        )
                    count += len(rows)
            connection.commit()
        finally:
            connection.close()
        return count

    def close(self, *, delete: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._conn.close()
            finally:
                if delete:
                    for candidate in (self.path, self.path + "-wal", self.path + "-shm"):
                        try:
                            os.remove(candidate)
                        except FileNotFoundError:
                            pass

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()
