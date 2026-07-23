from __future__ import annotations

import importlib
import ipaddress
import math
import queue
import socket
import threading
import time
from datetime import datetime
from typing import Callable, Optional

MAX_ERROR_DETAIL_LENGTH = 1000


def safe_error_detail(value: object) -> str:
    text = str(value).replace("\x00", "")
    text = " ".join(text.replace("\r", "\n").splitlines())
    return text[:MAX_ERROR_DETAIL_LENGTH]


from .models import (
    PingOutcome,
    PingRecord,
    QueueMessage,
    ResolvedTarget,
    ResultStatus,
    RunConfig,
)


class PingBackend:
    def __init__(self, ping_function: Optional[Callable] = None):
        self.import_error = ""
        self._ping = ping_function
        if self._ping is None:
            try:
                module = importlib.import_module("ping3")
                self._ping = module.ping
            except (ModuleNotFoundError, ImportError) as exc:
                self.import_error = safe_error_detail(f"{type(exc).__name__}: {exc}")
            except Exception as exc:  # dependency initialization can fail for other reasons
                self.import_error = safe_error_detail(f"{type(exc).__name__}: {exc}")

    @property
    def available(self) -> bool:
        return self._ping is not None

    def ping(
        self,
        host: str,
        *,
        timeout: float,
        size: int,
        sequence: int,
    ) -> PingOutcome:
        if self._ping is None:
            return PingOutcome(None, ResultStatus.MISSING_DEPENDENCY, self.import_error)
        try:
            value = self._ping(
                host,
                timeout=timeout,
                unit="ms",
                size=size,
                seq=sequence % 65536,
            )
            if value is False:
                return PingOutcome(
                    None,
                    ResultStatus.NETWORK_ERROR,
                    "ping3 returned False",
                )
            if value is None:
                return PingOutcome(None, ResultStatus.TIMEOUT, "timeout")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return PingOutcome(
                    None,
                    ResultStatus.INTERNAL_ERROR,
                    safe_error_detail(f"unexpected ping result: {value!r}"),
                )
            latency = float(value)
            if not math.isfinite(latency) or latency < 0:
                return PingOutcome(
                    None,
                    ResultStatus.INTERNAL_ERROR,
                    safe_error_detail(f"invalid latency: {value!r}"),
                )
            return PingOutcome(latency, ResultStatus.SUCCESS)
        except PermissionError as exc:
            return PingOutcome(None, ResultStatus.PERMISSION_ERROR, safe_error_detail(exc))
        except socket.gaierror as exc:
            return PingOutcome(None, ResultStatus.RESOLVE_ERROR, safe_error_detail(exc))
        except OSError as exc:
            text = str(exc).lower()
            if "permission" in text or "operation not permitted" in text:
                return PingOutcome(None, ResultStatus.PERMISSION_ERROR, safe_error_detail(exc))
            return PingOutcome(None, ResultStatus.NETWORK_ERROR, safe_error_detail(exc))
        except Exception as exc:
            name = type(exc).__name__.lower()
            text = str(exc).lower()
            if "timeout" in name or "timeout" in text:
                return PingOutcome(None, ResultStatus.TIMEOUT, safe_error_detail(exc))
            if "hostunknown" in name or "resolve" in text or "name or service" in text:
                return PingOutcome(None, ResultStatus.RESOLVE_ERROR, safe_error_detail(exc))
            if "permission" in name or "permission" in text:
                return PingOutcome(None, ResultStatus.PERMISSION_ERROR, safe_error_detail(exc))
            return PingOutcome(
                None,
                ResultStatus.INTERNAL_ERROR,
                safe_error_detail(f"{type(exc).__name__}: {exc}"),
            )


def resolve_host(host: str) -> ResolvedTarget:
    literal_family: int | None = None
    try:
        literal = ipaddress.ip_address(host.split("%", 1)[0])
        literal_family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
    except ValueError:
        pass

    infos = socket.getaddrinfo(
        host,
        None,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_DGRAM,
    )
    candidates: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        address = sockaddr[0]
        key = (family, address)
        if key not in seen:
            candidates.append(key)
            seen.add(key)
    if not candidates:
        raise socket.gaierror(f"No IPv4/IPv6 address found for {host}")

    if literal_family is not None:
        candidates.sort(key=lambda item: 0 if item[0] == literal_family else 1)
    else:
        # Prefer IPv4 for broad raw-ICMP compatibility, while retaining IPv6 support.
        candidates.sort(key=lambda item: (0 if item[0] == socket.AF_INET else 1, item[1]))
    family, address = candidates[0]
    return ResolvedTarget(host, address, family)


def _put_with_stop(
    out_queue: queue.Queue,
    message: QueueMessage,
    stop_event: threading.Event,
    *,
    force: bool = False,
) -> bool:
    deadline = time.monotonic() + 5.0 if force else None
    while force or not stop_event.is_set():
        try:
            out_queue.put(message, timeout=0.2)
            return True
        except queue.Full:
            if force and deadline is not None and time.monotonic() >= deadline:
                return False
    return False


def run_ping_session(
    config: RunConfig,
    backend: PingBackend,
    out_queue: queue.Queue,
    stop_event: threading.Event,
) -> None:
    reason = "completed"
    try:
        try:
            target = resolve_host(config.original_host)
        except socket.gaierror as exc:
            _put_with_stop(
                out_queue,
                QueueMessage(
                    config.session_id,
                    "resolve_failed",
                    safe_error_detail(f"{type(exc).__name__}: {exc}"),
                ),
                stop_event,
                force=True,
            )
            reason = "resolve_failed"
            return

        if not _put_with_stop(
            out_queue,
            QueueMessage(config.session_id, "resolved", target),
            stop_event,
        ):
            reason = "stopped"
            return

        start_mono = time.monotonic()
        deadline = (
            start_mono + config.duration_seconds
            if config.duration_seconds is not None
            else None
        )
        next_due = start_mono
        sequence = 0

        while not stop_event.is_set():
            now_mono = time.monotonic()
            if deadline is not None and now_mono >= deadline:
                break
            if config.count is not None and sequence >= config.count:
                break

            remaining = None if deadline is None else max(0.0, deadline - now_mono)
            if remaining is not None and remaining <= 0:
                break
            effective_timeout = config.timeout_seconds
            if remaining is not None:
                effective_timeout = max(0.001, min(effective_timeout, remaining))

            sequence += 1
            sent_at = datetime.now()
            elapsed = time.monotonic() - start_mono
            outcome = backend.ping(
                target.address,
                timeout=effective_timeout,
                size=config.packet_size,
                sequence=sequence - 1,
            )
            record = PingRecord(
                sequence=sequence,
                timestamp=sent_at,
                elapsed_seconds=elapsed,
                latency_ms=outcome.latency_ms,
                status=outcome.status,
                detail=outcome.detail,
            )
            if not _put_with_stop(
                out_queue,
                QueueMessage(config.session_id, "record", record),
                stop_event,
            ):
                reason = "stopped"
                break

            if config.count is not None and sequence >= config.count:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break

            next_due += config.interval_seconds
            wait_seconds = max(0.0, next_due - time.monotonic())
            if deadline is not None:
                wait_seconds = min(wait_seconds, max(0.0, deadline - time.monotonic()))
            if wait_seconds > 0 and stop_event.wait(wait_seconds):
                reason = "stopped"
                break

        if stop_event.is_set():
            reason = "stopped"
    except Exception as exc:
        reason = "internal_error"
        _put_with_stop(
            out_queue,
            QueueMessage(
                config.session_id,
                "worker_error",
                safe_error_detail(f"{type(exc).__name__}: {exc}"),
            ),
            stop_event,
            force=True,
        )
    finally:
        _put_with_stop(
            out_queue,
            QueueMessage(config.session_id, "finished", reason),
            stop_event,
            force=True,
        )
