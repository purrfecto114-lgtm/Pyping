from __future__ import annotations

from dataclasses import dataclass
import socket
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class ResultStatus(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    RESOLVE_ERROR = "resolve_error"
    PERMISSION_ERROR = "permission_error"
    NETWORK_ERROR = "network_error"
    INTERNAL_ERROR = "internal_error"
    MISSING_DEPENDENCY = "missing_dependency"


@dataclass(frozen=True)
class PingOutcome:
    latency_ms: Optional[float]
    status: ResultStatus
    detail: str = ""


@dataclass(frozen=True)
class ResolvedTarget:
    original_host: str
    address: str
    family: int

    @property
    def family_name(self) -> str:
        return "IPv6" if self.family == socket.AF_INET6 else "IPv4"


@dataclass(frozen=True)
class RunConfig:
    session_id: int
    original_host: str
    packet_size: int
    interval_seconds: float
    timeout_seconds: float
    count: Optional[int]
    duration_seconds: Optional[float]
    started_at: datetime


@dataclass(frozen=True)
class PingRecord:
    sequence: int
    timestamp: datetime
    elapsed_seconds: float
    latency_ms: Optional[float]
    status: ResultStatus
    detail: str = ""


@dataclass(frozen=True)
class QueueMessage:
    session_id: int
    kind: str
    payload: Any = None


@dataclass
class RunStatistics:
    total: int = 0
    success: int = 0
    timeout: int = 0
    errors: int = 0
    latency_sum: float = 0.0
    min_latency: Optional[float] = None
    max_latency: Optional[float] = None

    def update(self, record: PingRecord) -> None:
        self.total += 1
        if record.status == ResultStatus.SUCCESS and record.latency_ms is not None:
            self.success += 1
            self.latency_sum += record.latency_ms
            self.min_latency = (
                record.latency_ms
                if self.min_latency is None
                else min(self.min_latency, record.latency_ms)
            )
            self.max_latency = (
                record.latency_ms
                if self.max_latency is None
                else max(self.max_latency, record.latency_ms)
            )
        elif record.status == ResultStatus.TIMEOUT:
            self.timeout += 1
        else:
            self.errors += 1

    @property
    def failed(self) -> int:
        return self.timeout + self.errors

    @property
    def failure_rate(self) -> float:
        return (self.failed / self.total * 100.0) if self.total else 0.0

    @property
    def average_latency(self) -> Optional[float]:
        return (self.latency_sum / self.success) if self.success else None


@dataclass(frozen=True)
class RangeStatistics:
    total: int
    success: int
    timeout: int
    errors: int
    average_latency: Optional[float]
    min_latency: Optional[float]
    max_latency: Optional[float]

    @property
    def failed(self) -> int:
        return self.timeout + self.errors

    @property
    def failure_rate(self) -> float:
        return (self.failed / self.total * 100.0) if self.total else 0.0


@dataclass(frozen=True)
class ChartSample:
    timestamp: datetime
    latency_ms: Optional[float]
    status: ResultStatus
    timeout_count: int = 0
    error_count: int = 0
    sample_count: int = 1

    @property
    def has_timeout(self) -> bool:
        return self.timeout_count > 0 or self.status == ResultStatus.TIMEOUT

    @property
    def has_error(self) -> bool:
        return self.error_count > 0 or self.status not in (
            ResultStatus.SUCCESS,
            ResultStatus.TIMEOUT,
        )
