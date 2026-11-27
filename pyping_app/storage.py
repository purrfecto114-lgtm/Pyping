from __future__ import annotations

import csv
import os
import sqlite3
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .events import FailureEvent, FailureEventRow
from .fileio import atomic_output_path, normalize_output_path, spreadsheet_safe_text
from .models import ChartSample, PingRecord, RangeStatistics, ResultStatus

MAX_CHART_POINTS = 50_000
MAX_RECORD_DETAIL_LENGTH = 1000
MAX_FAILURE_EVENTS = 100_000

_STATUS_VALUES = (
    "success",
    "timeout",
    "resolve_error",
    "permission_error",
    "network_error",
    "internal_error",
    "missing_dependency",
    "cancelled",
)


def _range_statistics_query() -> str:
    # Cancelled probes carry no network evidence: they are counted, but
    # excluded from the ICMP-unanswered / error buckets and from rates.
    return """
        SELECT
            COUNT(*),
            SUM(CASE WHEN status = ? THEN 1 ELSE 0 END),
            SUM(CASE WHEN status = ? THEN 1 ELSE 0 END),
            SUM(CASE WHEN status NOT IN (?, ?, ?) THEN 1 ELSE 0 END),
            SUM(CASE WHEN status = ? THEN 1 ELSE 0 END),
            AVG(CASE WHEN status = ? THEN latency END),
            MIN(CASE WHEN status = ? THEN latency END),
            MAX(CASE WHEN status = ? THEN latency END)
        FROM records
        WHERE wall_ts >= ? AND wall_ts < ?
    """


def _range_statistics_params(start_ts: float, end_ts: float) -> tuple:
    success = ResultStatus.SUCCESS.value
    timeout = ResultStatus.TIMEOUT.value
    cancelled = ResultStatus.CANCELLED.value
    return (
        success,
        timeout,
        success,
        timeout,
        cancelled,
        cancelled,
        success,
        success,
        success,
        start_ts,
        end_ts,
    )


def _range_statistics_from_row(row) -> RangeStatistics:
    return RangeStatistics(
        total=int(row[0] or 0),
        success=int(row[1] or 0),
        timeout=int(row[2] or 0),
        errors=int(row[3] or 0),
        cancelled=int(row[4] or 0),
        average_latency=float(row[5]) if row[5] is not None else None,
        min_latency=float(row[6]) if row[6] is not None else None,
        max_latency=float(row[7]) if row[7] is not None else None,
    )


def _chart_samples_on(
    connection: sqlite3.Connection,
    start_ts: float,
    end_ts: float,
    *,
    max_points: int,
) -> tuple[list[ChartSample], bool, int]:
    success = ResultStatus.SUCCESS.value
    timeout = ResultStatus.TIMEOUT.value
    cancelled = ResultStatus.CANCELLED.value
    count = int(
        connection.execute(
            "SELECT COUNT(*) FROM records WHERE wall_ts >= ? AND wall_ts < ?",
            (start_ts, end_ts),
        ).fetchone()[0]
    )
    if count <= max_points:
        rows = connection.execute(
            """
            SELECT wall_ts, latency, status
            FROM records
            WHERE wall_ts >= ? AND wall_ts < ?
            ORDER BY sequence
            """,
            (start_ts, end_ts),
        ).fetchall()
        samples = [
            ChartSample(
                timestamp=datetime.fromtimestamp(row[0]),
                latency_ms=float(row[1]) if row[1] is not None else None,
                status=ResultStatus(row[2]),
                timeout_count=1 if row[2] == timeout else 0,
                error_count=(
                    1
                    if row[2] not in (success, timeout, cancelled)
                    else 0
                ),
            )
            for row in rows
        ]
        return samples, False, count

    span = max(end_ts - start_ts, 0.001)
    bucket_width = span / max_points
    # Each bucket keeps the success-avg latency together with success min/max,
    # so long sessions with a few failures still expose the latency envelope
    # instead of hiding every mixed bucket.
    rows = connection.execute(
        """
        SELECT
            AVG(wall_ts) AS avg_ts,
            AVG(CASE WHEN status = ? THEN latency END) AS avg_latency,
            MIN(CASE WHEN status = ? THEN latency END) AS min_latency,
            MAX(CASE WHEN status = ? THEN latency END) AS max_latency,
            SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS timeout_count,
            SUM(CASE WHEN status NOT IN (?, ?, ?) THEN 1 ELSE 0 END) AS error_count,
            COUNT(*) AS sample_count
        FROM records
        WHERE wall_ts >= ? AND wall_ts < ?
        GROUP BY CAST(MIN((wall_ts - ?) / ?, ?) AS INTEGER)
        ORDER BY avg_ts
        """,
        (
            success,
            success,
            success,
            timeout,
            success,
            timeout,
            cancelled,
            start_ts,
            end_ts,
            start_ts,
            bucket_width,
            max_points - 1,
        ),
    ).fetchall()

    samples: list[ChartSample] = []
    for row in rows:
        (
            avg_ts,
            latency,
            latency_min,
            latency_max,
            timeout_count,
            error_count,
            sample_count,
        ) = row
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
                latency_min=float(latency_min) if latency_min is not None else None,
                latency_max=float(latency_max) if latency_max is not None else None,
            )
        )
    return samples, True, count


def _range_statistics_on(
    connection: sqlite3.Connection,
    start_ts: float,
    end_ts: float,
) -> RangeStatistics:
    row = connection.execute(
        _range_statistics_query(), _range_statistics_params(start_ts, end_ts)
    ).fetchone()
    return _range_statistics_from_row(row)


class SessionStore:
    def __init__(self) -> None:
        fd, self.path = tempfile.mkstemp(prefix="pyping_", suffix=".sqlite3")
        os.close(fd)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            f"""
            CREATE TABLE records (
                sequence INTEGER PRIMARY KEY CHECK(sequence > 0),
                wall_ts REAL NOT NULL CHECK(wall_ts > 0),
                elapsed REAL NOT NULL CHECK(elapsed >= 0),
                latency REAL CHECK(latency IS NULL OR latency >= 0),
                status TEXT NOT NULL CHECK(status IN (
                    'success', 'timeout', 'resolve_error', 'permission_error',
                    'network_error', 'internal_error', 'missing_dependency',
                    'cancelled'
                )),
                detail TEXT NOT NULL CHECK(length(detail) <= {MAX_RECORD_DETAIL_LENGTH}),
                CHECK(
                    (status = 'success' AND latency IS NOT NULL) OR
                    (status <> 'success' AND latency IS NULL)
                )
            )
            """
        )
        self._conn.execute("CREATE INDEX idx_records_wall_ts ON records(wall_ts)")
        self._conn.execute(
            f"""
            CREATE TABLE failure_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(kind IN ('network', 'local')),
                status TEXT NOT NULL CHECK(length(status) <= 32),
                first_failure_seq INTEGER NOT NULL,
                first_failure_ts REAL NOT NULL,
                last_failure_seq INTEGER NOT NULL,
                last_failure_ts REAL NOT NULL,
                first_recovery_seq INTEGER,
                first_recovery_ts REAL,
                timeout_count INTEGER NOT NULL DEFAULT 0 CHECK(timeout_count >= 0),
                error_count INTEGER NOT NULL DEFAULT 0 CHECK(error_count >= 0),
                recovered INTEGER NOT NULL DEFAULT 0 CHECK(recovered IN (0, 1))
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX idx_events_first_failure_ts ON failure_events(first_failure_ts)"
        )
        self._conn.commit()
        self._closed = False

    def insert_many(self, records: Iterable[PingRecord]) -> int:
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
            return 0
        with self._lock:
            try:
                self._conn.executemany(
                    "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)", rows
                )
                self._conn.commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise
        return len(rows)

    def insert_event(self, event: FailureEvent) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO failure_events (
                    kind, status, first_failure_seq, first_failure_ts,
                    last_failure_seq, last_failure_ts, first_recovery_seq,
                    first_recovery_ts, timeout_count, error_count, recovered
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.kind,
                    event.status,
                    event.first_failure_seq,
                    event.first_failure_ts.timestamp(),
                    event.last_failure_seq,
                    event.last_failure_ts.timestamp(),
                    event.first_recovery_seq,
                    (
                        event.first_recovery_ts.timestamp()
                        if event.first_recovery_ts is not None
                        else None
                    ),
                    event.timeout_count,
                    event.error_count,
                    1 if event.recovered else 0,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def failure_events(self, *, limit: int = 500) -> list[FailureEventRow]:
        """Newest failure events first, bounded so the UI never loads unbounded rows."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT event_id, kind, status, first_failure_seq, first_failure_ts,
                       last_failure_seq, last_failure_ts, first_recovery_seq,
                       first_recovery_ts, timeout_count, error_count, recovered
                FROM failure_events
                ORDER BY first_failure_ts DESC, event_id DESC
                LIMIT ?
                """,
                (max(1, min(limit, MAX_FAILURE_EVENTS)),),
            ).fetchall()
        return [
            FailureEventRow(
                event_id=int(row[0]),
                kind=str(row[1]),
                status=str(row[2]),
                first_failure_seq=int(row[3]),
                first_failure_ts=datetime.fromtimestamp(row[4]),
                last_failure_seq=int(row[5]),
                last_failure_ts=datetime.fromtimestamp(row[6]),
                first_recovery_seq=int(row[7]) if row[7] is not None else None,
                first_recovery_ts=(
                    datetime.fromtimestamp(row[8]) if row[8] is not None else None
                ),
                timeout_count=int(row[9]),
                error_count=int(row[10]),
                recovered=bool(row[11]),
            )
            for row in rows
        ]

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
        with self._lock:
            stats = _range_statistics_on(
                self._conn, start.timestamp(), end.timestamp()
            )
        return stats

    def chart_snapshot(
        self,
        start: datetime,
        end: datetime,
        *,
        max_points: int = 5000,
    ) -> tuple[RangeStatistics, list[ChartSample], bool, int]:
        """Take statistics, chart samples and counts from one read-only snapshot.

        A short-lived read-only connection keeps long aggregation queries off
        the GUI writer connection entirely: under WAL the writer stays
        responsive, and the snapshot is taken in a single transaction so
        statistics and samples always describe the same data.  The transaction
        is released immediately so WAL checkpoints are not blocked.
        """
        if not 1 <= max_points <= MAX_CHART_POINTS:
            raise ValueError(
                f"max_points must be between 1 and {MAX_CHART_POINTS}"
            )
        start_ts, end_ts = start.timestamp(), end.timestamp()
        database_uri = Path(self.path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(database_uri, uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            stats = _range_statistics_on(connection, start_ts, end_ts)
            samples, aggregated, count = _chart_samples_on(
                connection, start_ts, end_ts, max_points=max_points
            )
            connection.commit()
        finally:
            connection.close()
        return stats, samples, aggregated, count

    def chart_samples(
        self,
        start: datetime,
        end: datetime,
        *,
        max_points: int = 5000,
    ) -> tuple[list[ChartSample], bool, int]:
        if not 1 <= max_points <= MAX_CHART_POINTS:
            raise ValueError(
                f"max_points must be between 1 and {MAX_CHART_POINTS}"
            )
        with self._lock:
            samples, aggregated, count = _chart_samples_on(
                self._conn, start.timestamp(), end.timestamp(), max_points=max_points
            )
        return samples, aggregated, count

    def export_csv(
        self,
        path: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        batch_size: int = 5000,
    ) -> int:
        """Atomically export a committed, read-only snapshot.

        A separate read-only connection keeps the GUI writer responsive under WAL
        mode. The destination is replaced only after the complete CSV has been
        flushed, so a failed export cannot destroy an existing file or leave a
        misleading partial result.
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")

        target = normalize_output_path(path)
        protected = {
            Path(self.path).resolve(),
            Path(self.path + "-wal").resolve(),
            Path(self.path + "-shm").resolve(),
        }
        if target.resolve(strict=False) in protected:
            raise ValueError("CSV destination must not overwrite the active session database")

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
            clauses.append("wall_ts < ?")
            params.append(end.timestamp())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY sequence"

        database_uri = Path(self.path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(database_uri, uri=True)
        count = 0
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            cursor = connection.execute(query, params)
            with atomic_output_path(target, suffix=".csv.tmp") as temporary:
                with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
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
                                    datetime.fromtimestamp(wall_ts).isoformat(
                                        sep=" ", timespec="milliseconds"
                                    ),
                                    f"{float(elapsed):.6f}",
                                    "" if latency is None else f"{float(latency):.6f}",
                                    spreadsheet_safe_text(status),
                                    spreadsheet_safe_text(detail),
                                )
                            )
                        count += len(rows)
                    handle.flush()
                    os.fsync(handle.fileno())
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
