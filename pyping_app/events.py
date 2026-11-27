"""Incremental failure-event detection over the stream of ping records.

The tracker consumes each record exactly once while it flows through the GUI
queue, so opening the event list never rescans the whole database.  Raw
single-packet failures are always kept in the records table; the event table
merges consecutive failures into observables for the failure-event browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import PingRecord, ResultStatus

NETWORK_FAILURE_STATUSES = frozenset({ResultStatus.TIMEOUT, ResultStatus.NETWORK_ERROR})


def event_window(
    row: "FailureEventRow | FailureEvent",
    bounds: tuple[datetime, datetime],
    margin_seconds: int,
) -> tuple[datetime, datetime]:
    """Chart window around one failure event, clamped to the session bounds.

    The window is symmetric around the *observed* span (first failure to
    first recovery), clamped to the real session boundaries.  The end is
    exclusive ([start, end)) and extended by one microsecond so the recovery
    record itself is included.
    """
    bounds_start, bounds_end = bounds
    center_start = row.first_failure_ts
    center_end = row.first_recovery_ts or row.last_failure_ts
    start = max(bounds_start, center_start - timedelta(seconds=margin_seconds))
    end = min(
        center_end + timedelta(seconds=margin_seconds, microseconds=1),
        bounds_end + timedelta(microseconds=1),
    )
    if end <= start:
        end = start + timedelta(microseconds=1)
    return start, end


@dataclass
class FailureEvent:
    """An in-memory failure event produced by :class:`FailureEventTracker`."""

    kind: str  # "network" | "local"
    status: str  # category of the first failure in the chain
    first_failure_seq: int
    first_failure_ts: datetime
    last_failure_seq: int
    last_failure_ts: datetime
    first_recovery_seq: int | None = None
    first_recovery_ts: datetime | None = None
    timeout_count: int = 0
    error_count: int = 0
    recovered: bool = False

    @property
    def failure_count(self) -> int:
        return self.timeout_count + self.error_count

    @property
    def observation_span_seconds(self) -> float:
        """Observed span from first failure to first recovery.

        This is a sampling observation, not the exact outage duration: the
        real break may have started between the last success and the first
        failure.
        """
        end = self.first_recovery_ts or self.last_failure_ts
        return max(0.0, (end - self.first_failure_ts).total_seconds())


@dataclass(frozen=True)
class FailureEventRow:
    """A failure event read back from storage."""

    event_id: int
    kind: str
    status: str
    first_failure_seq: int
    first_failure_ts: datetime
    last_failure_seq: int
    last_failure_ts: datetime
    first_recovery_seq: int | None
    first_recovery_ts: datetime | None
    timeout_count: int
    error_count: int
    recovered: bool

    @property
    def failure_count(self) -> int:
        return self.timeout_count + self.error_count

    @property
    def observation_span_seconds(self) -> float:
        end = self.first_recovery_ts or self.last_failure_ts
        return max(0.0, (end - self.first_failure_ts).total_seconds())


class FailureEventTracker:
    """Single-pass state machine over ping records.

    States follow the diagnostic plan:

    - timeout / network_error extend (or open) a network observation event;
    - resolve / permission / dependency / internal errors extend (or open) a
      separate local-tool event, never merged into the network chain;
    - the first success closes the open event and records recovery;
    - cancelled probes and backend changes close the open event as
      unrecovered, recording an observation boundary instead of pretending
      the outage ended exactly at that instant.
    """

    def __init__(self) -> None:
        self._open: FailureEvent | None = None

    def _close_open(self, *, recovered: bool) -> FailureEvent | None:
        event = self._open
        self._open = None
        if event is not None and recovered:
            event.recovered = True
        return event

    def process(self, record: PingRecord) -> FailureEvent | None:
        """Consume one record; return a finalized event if one just closed."""
        status = record.status
        if status == ResultStatus.CANCELLED:
            # The observation is interrupted: the previous event stays
            # unrecovered and the cancelled probe itself is not evidence.
            return self._close_open(recovered=False)
        if status == ResultStatus.SUCCESS:
            event = self._open
            self._open = None
            if event is not None:
                event.first_recovery_seq = record.sequence
                event.first_recovery_ts = record.timestamp
                event.recovered = True
                return event
            return None
        kind = "network" if status in NETWORK_FAILURE_STATUSES else "local"
        closed: FailureEvent | None = None
        if self._open is not None and self._open.kind != kind:
            # Local errors must not masquerade as network events: close the
            # current chain and start a fresh one for the new kind.
            closed = self._close_open(recovered=False)
        if self._open is None:
            self._open = FailureEvent(
                kind=kind,
                status=status.value,
                first_failure_seq=record.sequence,
                first_failure_ts=record.timestamp,
                last_failure_seq=record.sequence,
                last_failure_ts=record.timestamp,
            )
        else:
            self._open.last_failure_seq = record.sequence
            self._open.last_failure_ts = record.timestamp
        if status == ResultStatus.TIMEOUT:
            self._open.timeout_count += 1
        else:
            self._open.error_count += 1
        return closed

    def mark_boundary(self) -> FailureEvent | None:
        """Close the open event without marking recovery.

        Used when the backend, target address or address family changes: the
        chain must not silently continue across the boundary.
        """
        return self._close_open(recovered=False)

    def flush(self) -> FailureEvent | None:
        """Close the open event at session end as unrecovered."""
        return self._close_open(recovered=False)
