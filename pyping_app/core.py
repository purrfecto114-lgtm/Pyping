from __future__ import annotations

import importlib
import ipaddress
import math
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Callable, Optional

MAX_ERROR_DETAIL_LENGTH = 1000
SYSTEM_PING_OUTPUT_LIMIT = 4000


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
    """Primary ICMP backend built on ping3.

    ping3's module-level EXCEPTIONS flag is enabled exactly once, at adapter
    initialization, so concurrent probes never flip shared module state.  With
    exceptions enabled, ICMP type/code details and the responding source address
    survive into the stored record instead of being compressed to ``False``.
    """

    def __init__(self, ping_function: Optional[Callable] = None):
        self.import_error = ""
        self._ping = ping_function
        self._errors = None
        if self._ping is None:
            try:
                module = importlib.import_module("ping3")
                self._ping = module.ping
                self._errors = getattr(module, "errors", None)
                try:
                    module.EXCEPTIONS = True
                except Exception:
                    pass
            except (ModuleNotFoundError, ImportError) as exc:
                self.import_error = safe_error_detail(f"{type(exc).__name__}: {exc}")
            except Exception as exc:  # dependency initialization can fail for other reasons
                self.import_error = safe_error_detail(f"{type(exc).__name__}: {exc}")

    @property
    def available(self) -> bool:
        return self._ping is not None

    def _classify_ping_error(self, exc: Exception) -> PingOutcome:
        errors = self._errors
        if errors is not None:
            detail = safe_error_detail(f"{type(exc).__name__}: {exc}")
            # Order matters: subclasses must be tested before their bases.
            dest_unreachable = getattr(errors, "DestinationUnreachable", None)
            if dest_unreachable is not None and isinstance(exc, dest_unreachable):
                # The exception message embeds the ICMP source address when the
                # IP header is available, so structured information is kept.
                return PingOutcome(None, ResultStatus.NETWORK_ERROR, detail)
            timeout_cls = getattr(errors, "Timeout", None)
            if timeout_cls is not None and isinstance(exc, timeout_cls):
                return PingOutcome(None, ResultStatus.TIMEOUT, detail)
            for resolve_name in ("HostUnknown", "HostUnkown", "DestinationNotFound"):
                resolve_cls = getattr(errors, resolve_name, None)
                if resolve_cls is not None and isinstance(exc, resolve_cls):
                    return PingOutcome(None, ResultStatus.RESOLVE_ERROR, detail)
            ping_error = getattr(errors, "PingError", None)
            if ping_error is not None and isinstance(exc, ping_error):
                return PingOutcome(None, ResultStatus.NETWORK_ERROR, detail)
        return PingOutcome(
            None,
            ResultStatus.INTERNAL_ERROR,
            safe_error_detail(f"{type(exc).__name__}: {exc}"),
        )

    def ping(
        self,
        host: str,
        *,
        timeout: float,
        size: int,
        sequence: int,
        stop_event: Optional[threading.Event] = None,
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
            outcome = self._classify_ping_error(exc)
            if outcome.status is not ResultStatus.INTERNAL_ERROR:
                return outcome
            name = type(exc).__name__.lower()
            text = str(exc).lower()
            if "timeout" in name or "timeout" in text:
                return PingOutcome(None, ResultStatus.TIMEOUT, safe_error_detail(exc))
            if "hostunknown" in name or "resolve" in text or "name or service" in text:
                return PingOutcome(None, ResultStatus.RESOLVE_ERROR, safe_error_detail(exc))
            if "permission" in name or "permission" in text:
                return PingOutcome(None, ResultStatus.PERMISSION_ERROR, safe_error_detail(exc))
            return outcome


class SystemPingBackend:
    """Fallback backend that drives the operating system's ping utility.

    It is only selected when the ping3 backend cannot run at all (missing
    dependency, insufficient privileges).  Ordinary timeouts and
    unreachables are measurement results and must not trigger a backend
    switch, otherwise the sampling rhythm and loss statistics change.

    A single in-flight child process is tracked so ``cancel()`` can stop a
    pending probe promptly; the command always uses an argument array with
    ``shell=False`` semantics (no shell involved).
    """

    def __init__(self, family: Optional[int] = None, platform_name: Optional[str] = None):
        self.platform = platform_name if platform_name is not None else sys.platform
        self.family = family
        self.import_error = ""
        self._binary, self._binary_family = self._resolve_binary()
        self._proc_lock = threading.Lock()
        self._current_proc: Optional[subprocess.Popen] = None

    @property
    def available(self) -> bool:
        return self._binary is not None

    def _resolve_binary(self) -> tuple[Optional[str], Optional[int]]:
        if self.platform == "win32":
            system_root = os.environ.get("SystemRoot") or r"C:\Windows"
            path = os.path.join(system_root, "System32", "ping.exe")
            if not os.path.isfile(path):
                path = shutil.which("ping")
            return path, socket.AF_INET
        names: tuple[str, ...] = ("ping",)
        binary_family = socket.AF_INET
        if self.family == socket.AF_INET6:
            # Apple ships a dedicated ping6; on Linux iputils ping handles
            # both families.  Try ping6 first so BusyBox-style setups that
            # lack -6 support still work where ping6 exists.
            names = ("ping6", "ping")
        for name in names:
            path = shutil.which(name)
            if path:
                return path, socket.AF_INET6 if name == "ping6" else binary_family
        return None, None

    def _build_command(self, address: str, *, timeout: float, size: int) -> list[str]:
        assert self._binary is not None
        timeout_ms = max(1, int(round(timeout * 1000)))
        if self.platform == "win32":
            family_flag = "-6" if self.family == socket.AF_INET6 else "-4"
            return [
                self._binary,
                family_flag,
                "-n", "1",
                "-w", str(timeout_ms),
                "-l", str(size),
                address,
            ]
        if self.platform == "darwin":
            if self.family == socket.AF_INET6:
                # Apple ping6's -W is a node-information option, not a wait
                # timeout, so it must not be copied from the IPv4 form.  The
                # application-level deadline below is the real backstop.
                return [self._binary, "-n", "-c", "1", "-s", str(size), address]
            return [
                self._binary,
                "-n", "-c", "1",
                "-W", str(timeout_ms),
                "-s", str(size),
                address,
            ]
        # Linux and other POSIX (iputils style); -W accepts fractional seconds.
        wait = f"{max(0.05, timeout):.2f}"
        if self.family == socket.AF_INET6 and self._binary_family != socket.AF_INET6:
            return [
                self._binary,
                "-6",
                "-n", "-c", "1",
                "-W", wait,
                "-s", str(size),
                address,
            ]
        return [
            self._binary,
            "-n", "-c", "1",
            "-W", wait,
            "-s", str(size),
            address,
        ]

    def _child_environment(self) -> dict[str, str]:
        # PyInstaller changes library search paths; child processes inherit
        # them and may load incompatible bundled libraries (see the official
        # "launching external programs" pitfalls).  Strip them for frozen apps.
        env = os.environ.copy()
        if getattr(sys, "frozen", False):
            for key in (
                "LD_LIBRARY_PATH",
                "DYLD_LIBRARY_PATH",
                "DYLD_FALLBACK_LIBRARY_PATH",
            ):
                env.pop(key, None)
        return env

    def ping(
        self,
        host: str,
        *,
        timeout: float,
        size: int,
        sequence: int,
        stop_event: Optional[threading.Event] = None,
    ) -> PingOutcome:
        if self._binary is None:
            return PingOutcome(
                None,
                ResultStatus.MISSING_DEPENDENCY,
                self.import_error or "system ping utility not found",
            )
        command = self._build_command(host, timeout=timeout, size=size)
        process_deadline = max(timeout + 1.0, timeout * 1.5 + 0.5)
        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                errors="replace",
                env=self._child_environment(),
            )
        except PermissionError as exc:
            return PingOutcome(None, ResultStatus.PERMISSION_ERROR, safe_error_detail(exc))
        except FileNotFoundError as exc:
            return PingOutcome(
                None,
                ResultStatus.INTERNAL_ERROR,
                safe_error_detail(f"system ping not executable: {exc}"),
            )
        except OSError as exc:
            return PingOutcome(None, ResultStatus.PERMISSION_ERROR, safe_error_detail(exc))
        with self._proc_lock:
            self._current_proc = proc
        stdout_text = ""
        stderr_text = ""
        try:
            deadline = time.monotonic() + process_deadline
            while True:
                try:
                    stdout_text, stderr_text = proc.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    if stop_event is not None and stop_event.is_set():
                        proc.kill()
                        proc.communicate()
                        return PingOutcome(
                            None,
                            ResultStatus.CANCELLED,
                            "probe cancelled before an ICMP reply arrived",
                        )
                    if time.monotonic() >= deadline:
                        proc.kill()
                        proc.communicate()
                        return PingOutcome(
                            None,
                            ResultStatus.TIMEOUT,
                            "no ICMP reply within the configured timeout",
                        )
        except OSError as exc:
            return PingOutcome(None, ResultStatus.INTERNAL_ERROR, safe_error_detail(exc))
        finally:
            with self._proc_lock:
                self._current_proc = None
        return parse_system_ping_output(stdout_text, stderr_text, proc.returncode)

    def cancel(self) -> None:
        with self._proc_lock:
            proc = self._current_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass


RTT_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)
RTT_ZH_RE = re.compile(r"时间[=<]\s*([\d.]+)\s*ms")
UNREACHABLE_RE = re.compile(
    r"unreachable|not reachable|无法访问|不可达|找不到主机|不能访问",
    re.IGNORECASE,
)
FULL_LOSS_RE = re.compile(r"100\.?\d*\s*%\s*(packet\s*)?loss|100\.?\d*\s*%\s*丢包", re.IGNORECASE)
TIMEOUT_TEXT_RE = re.compile(
    r"timed? ?out|timeout|超时|请求超时|request timed out",
    re.IGNORECASE,
)


def parse_system_ping_output(
    stdout: str,
    stderr: str,
    returncode: Optional[int],
) -> PingOutcome:
    """Classify one system ping run from its captured output.

    Success requires a real echo-reply evidence chain: a zero exit code from
    the system utility plus a parsable per-packet time field.  Arbitrary
    output containing the word "time" is never treated as a reply.  Text must
    come from the documented fixed samples (English, Chinese, ``<1ms``);
    localized variants we cannot parse are reported honestly instead of
    guessed.
    """
    combined = "\n".join(part for part in (stdout or "", stderr or "") if part)
    detail = safe_error_detail(combined[:SYSTEM_PING_OUTPUT_LIMIT])
    rtt: Optional[float] = None
    for regex in (RTT_RE, RTT_ZH_RE):
        match = regex.search(stdout or "")
        if match:
            try:
                rtt = float(match.group(1))
            except ValueError:
                rtt = None
            else:
                break
    # Failure indicators are checked BEFORE the exit code: Windows ping.exe
    # can exit with code 0 even when every request timed out, so the exit
    # code alone is not echo-reply evidence.
    if UNREACHABLE_RE.search(combined):
        return PingOutcome(None, ResultStatus.NETWORK_ERROR, detail or "destination unreachable")
    if FULL_LOSS_RE.search(combined) or TIMEOUT_TEXT_RE.search(combined):
        return PingOutcome(
            None,
            ResultStatus.TIMEOUT,
            "no ICMP reply within the configured timeout",
        )
    if returncode == 0:
        if rtt is not None and rtt >= 0:
            return PingOutcome(rtt, ResultStatus.SUCCESS)
        # Exit code 0 with no failure indicators on every supported platform
        # means at least one echo reply arrived; the RTT itself is not
        # parsable in this locale.
        return PingOutcome(
            0.0,
            ResultStatus.SUCCESS,
            "echo reply confirmed by exit code; RTT not parsable from system locale",
        )
    return PingOutcome(
        None,
        ResultStatus.NETWORK_ERROR,
        safe_error_detail(f"system ping exited with {returncode}: {detail}"),
    )


def system_ping_supported() -> bool:
    """Whether a system ping utility exists for the current platform."""
    return SystemPingBackend().available


def resolve_host(host: str) -> ResolvedTarget:
    literal_family: int | None = None
    literal_scope = ""
    try:
        head = host.split("%", 1)[0]
        literal = ipaddress.ip_address(head)
        literal_family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        if literal.version == 6 and "%" in host:
            candidate_scope = host.split("%", 1)[1].strip()
            if candidate_scope:
                literal_scope = candidate_scope
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
    if family == socket.AF_INET6:
        # Keep the interface scope of link-local addresses (fe80::1%7).  Both
        # the user-provided scope and any scope already returned by the
        # resolver must survive into the backend call.
        if literal_scope and "%" not in address:
            address = f"{address}%{literal_scope}"
    return ResolvedTarget(host, address, family)


def _advance_to_next_slot(grid_start: float, interval: float, now: float) -> float:
    """First scheduled slot strictly after ``now`` on the grid anchored at ``grid_start``.

    Missed slots are skipped, never replayed: a slow probe must not turn the
    next interval into an immediate catch-up burst.
    """
    if interval <= 0:
        return now
    steps = math.floor((now - grid_start) / interval) + 1
    if steps < 1:
        steps = 1
    return grid_start + steps * interval


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
    current_backend: PingBackend | SystemPingBackend = backend
    system_fallback: SystemPingBackend | None = None
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
        sequence = 0
        switch_logged = False

        def ensure_system_fallback() -> SystemPingBackend:
            nonlocal system_fallback
            if system_fallback is None:
                system_fallback = SystemPingBackend(family=target.family)
            return system_fallback

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
            outcome = current_backend.ping(
                target.address,
                timeout=effective_timeout,
                size=config.packet_size,
                sequence=sequence - 1,
                stop_event=stop_event,
            )
            # Switch backends only when the primary backend cannot work at all
            # (missing dependency / no permission).  Ordinary timeouts and
            # unreachables stay measurements on the same backend.  The failed
            # ping3 attempt never left the machine (socket creation failed), so
            # retrying this slot with the system backend counts exactly one
            # real send.
            if (
                outcome.status in (ResultStatus.PERMISSION_ERROR, ResultStatus.MISSING_DEPENDENCY)
                and isinstance(current_backend, PingBackend)
            ):
                fallback = ensure_system_fallback()
                if fallback.available:
                    if not switch_logged:
                        switch_logged = True
                        _put_with_stop(
                            out_queue,
                            QueueMessage(
                                config.session_id,
                                "backend_switch",
                                {
                                    "reason": outcome.detail,
                                    "backend": "system-ping",
                                    "family": target.family_name,
                                },
                            ),
                            stop_event,
                            force=True,
                        )
                    current_backend = fallback
                    outcome = fallback.ping(
                        target.address,
                        timeout=effective_timeout,
                        size=config.packet_size,
                        sequence=sequence - 1,
                        stop_event=stop_event,
                    )
            record = PingRecord(
                sequence=sequence,
                timestamp=sent_at,
                elapsed_seconds=elapsed,
                latency_ms=outcome.latency_ms,
                status=outcome.status,
                detail=outcome.detail,
            )
            # The probe already ran to completion: its result must survive a
            # concurrent stop request instead of being dropped silently.
            if not _put_with_stop(
                out_queue,
                QueueMessage(config.session_id, "record", record),
                stop_event,
                force=True,
            ):
                reason = "stopped"
                break

            if config.count is not None and sequence >= config.count:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break

            # Skip missed grid slots; never replay them as a burst.
            now_after = time.monotonic()
            next_due = _advance_to_next_slot(start_mono, config.interval_seconds, now_after)
            wait_seconds = max(0.0, next_due - now_after)
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
        if system_fallback is not None:
            system_fallback.cancel()
        _put_with_stop(
            out_queue,
            QueueMessage(config.session_id, "finished", reason),
            stop_event,
            force=True,
        )
