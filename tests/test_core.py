import queue
import threading
import os
import socket
import time
import unittest
from datetime import datetime

from pyping_app.core import (
    PingBackend,
    SystemPingBackend,
    parse_system_ping_output,
    resolve_host,
    run_ping_session,
)
from pyping_app.models import PingOutcome, ResultStatus, RunConfig


class CoreTests(unittest.TestCase):
    def test_false_is_network_error_not_zero_latency(self):
        backend = PingBackend(lambda *args, **kwargs: False)
        outcome = backend.ping("127.0.0.1", timeout=1, size=56, sequence=0)
        self.assertEqual(outcome.status, ResultStatus.NETWORK_ERROR)
        self.assertIsNone(outcome.latency_ms)

    def test_error_details_are_single_line_and_bounded(self):
        def failed(*args, **kwargs):
            raise RuntimeError("first line\nsecond line" + "x" * 2000)

        outcome = PingBackend(failed).ping(
            "127.0.0.1", timeout=1, size=56, sequence=0
        )
        self.assertEqual(outcome.status, ResultStatus.INTERNAL_ERROR)
        self.assertNotIn("\n", outcome.detail)
        self.assertNotIn("\r", outcome.detail)
        self.assertLessEqual(len(outcome.detail), 1000)

    def test_invalid_numeric_result_is_internal_error(self):
        backend = PingBackend(lambda *args, **kwargs: float("nan"))
        outcome = backend.ping("127.0.0.1", timeout=1, size=56, sequence=0)
        self.assertEqual(outcome.status, ResultStatus.INTERNAL_ERROR)

    def test_single_count_does_not_sleep_after_last_ping(self):
        calls = []

        def fake_ping(*args, **kwargs):
            calls.append(kwargs["seq"])
            return 12.5

        config = RunConfig(1, "127.0.0.1", 56, 5.0, 1.0, 1, None, datetime.now())
        out = queue.Queue(maxsize=100)
        stop = threading.Event()
        started = time.monotonic()
        run_ping_session(config, PingBackend(fake_ping), out, stop)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0)
        self.assertEqual(calls, [0])
        messages = []
        while not out.empty():
            messages.append(out.get_nowait())
        records = [m.payload for m in messages if m.kind == "record"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].latency_ms, 12.5)
        self.assertEqual(messages[-1].kind, "finished")

    def test_sequence_increments(self):
        seen = []

        def fake_ping(*args, **kwargs):
            seen.append(kwargs["seq"])
            return 1.0

        config = RunConfig(2, "127.0.0.1", 56, 0.1, 1.0, 3, None, datetime.now())
        out = queue.Queue(maxsize=100)
        run_ping_session(config, PingBackend(fake_ping), out, threading.Event())
        self.assertEqual(seen, [0, 1, 2])

    def test_stop_wait_is_interruptible(self):
        backend = PingBackend(lambda *args, **kwargs: 1.0)
        config = RunConfig(3, "127.0.0.1", 56, 10.0, 1.0, None, None, datetime.now())
        out = queue.Queue(maxsize=100)
        stop = threading.Event()
        thread = threading.Thread(target=run_ping_session, args=(config, backend, out, stop))
        thread.start()
        time.sleep(0.15)
        stop.set()
        thread.join(1.0)
        self.assertFalse(thread.is_alive())




class SchedulingTests(unittest.TestCase):
    def test_slow_probe_does_not_cause_catchup_burst(self):
        # Interval 0.1 s; the first probe occupies 0.25 s.  Missed slots must
        # be skipped, never replayed as an immediate burst.
        def fake_ping(*args, **kwargs):
            if len(send_times) == 0:
                time.sleep(0.25)
            return 5.0

        send_times = []
        original = time.time
        config = RunConfig(7, "127.0.0.1", 56, 0.1, 1.0, 5, None, datetime.now())
        out = queue.Queue(maxsize=100)
        run_ping_session(config, PingBackend(fake_ping), out, threading.Event())
        while not out.empty():
            message = out.get_nowait()
            if message.kind == "record":
                send_times.append(message.payload.timestamp)
        self.assertEqual(len(send_times), 5)
        gaps = [
            (later - earlier).total_seconds()
            for earlier, later in zip(send_times, send_times[1:])
        ]
        self.assertTrue(all(gap >= 0.08 for gap in gaps), gaps)

    def test_stop_preserves_last_completed_record(self):
        probe_count = {"n": 0}

        def fake_ping(*args, **kwargs):
            probe_count["n"] += 1
            time.sleep(0.25)
            return 8.0

        config = RunConfig(8, "127.0.0.1", 56, 0.4, 1.0, None, None, datetime.now())
        out = queue.Queue(maxsize=100)
        stop = threading.Event()
        thread = threading.Thread(target=run_ping_session, args=(config, PingBackend(fake_ping), out, stop))
        thread.start()
        time.sleep(0.45)  # first probe completed, second probe in flight
        stop.set()
        thread.join(3.0)
        self.assertFalse(thread.is_alive())
        records = []
        while not out.empty():
            message = out.get_nowait()
            if message.kind == "record":
                records.append(message.payload)
        # Both the completed probe AND the probe that was in flight when stop
        # was pressed must appear; the in-flight result is not dropped.
        self.assertEqual(len(records), 2)
        self.assertEqual(records[-1].status, ResultStatus.SUCCESS)

    def test_resolve_host_preserves_ipv6_scope(self):
        original_getaddrinfo = socket.getaddrinfo

        def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            return [
                (socket.AF_INET6, socket.SOCK_DGRAM, 0, "", ("fe80::1", 0, 0, 7)),
            ]

        socket.getaddrinfo = fake_getaddrinfo
        try:
            target = resolve_host("fe80::1%7")
        finally:
            socket.getaddrinfo = original_getaddrinfo
        self.assertEqual(target.family, socket.AF_INET6)
        self.assertEqual(target.address, "fe80::1%7")

    def test_resolve_host_keeps_plain_address_unchanged(self):
        original_getaddrinfo = socket.getaddrinfo

        def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            return [
                (socket.AF_INET6, socket.SOCK_DGRAM, 0, "", ("2001:db8::1", 0, 0, 0)),
            ]

        socket.getaddrinfo = fake_getaddrinfo
        try:
            target = resolve_host("example.invalid")
        finally:
            socket.getaddrinfo = original_getaddrinfo
        self.assertEqual(target.address, "2001:db8::1")


class Ping3ExceptionClassificationTests(unittest.TestCase):
    class FakeErrors:
        class PingError(Exception):
            pass

        class Timeout(PingError):
            pass

        class DestinationUnreachable(PingError):
            pass

        class DestinationHostUnreachable(DestinationUnreachable):
            pass

        class HostUnknown(PingError):
            pass

    def make_backend(self, raiser) -> PingBackend:
        backend = PingBackend(raiser)
        backend._errors = self.FakeErrors
        return backend

    def test_destination_host_unreachable_keeps_source_address(self):
        def raiser(*args, **kwargs):
            raise self.FakeErrors.DestinationHostUnreachable(
                "Destination unreachable: Host unreachable. (Host='192.0.2.1')"
            )
        outcome = self.make_backend(raiser).ping("192.0.2.1", timeout=1, size=56, sequence=0)
        self.assertEqual(outcome.status, ResultStatus.NETWORK_ERROR)
        self.assertIn("192.0.2.1", outcome.detail)

    def test_timeout_exception_maps_to_timeout(self):
        def raiser(*args, **kwargs):
            raise self.FakeErrors.Timeout("Request timeout for ICMP packet.")
        outcome = self.make_backend(raiser).ping("192.0.2.1", timeout=1, size=56, sequence=0)
        self.assertEqual(outcome.status, ResultStatus.TIMEOUT)

    def test_host_unknown_maps_to_resolve_error(self):
        def raiser(*args, **kwargs):
            raise self.FakeErrors.HostUnknown("Cannot resolve: Unknown host.")
        outcome = self.make_backend(raiser).ping("no-such-host.invalid", timeout=1, size=56, sequence=0)
        self.assertEqual(outcome.status, ResultStatus.RESOLVE_ERROR)

    def test_subclass_is_matched_before_base(self):
        def raiser(*args, **kwargs):
            raise self.FakeErrors.DestinationHostUnreachable("unreachable (Host='192.0.2.1')")
        # DestinationHostUnreachable is both a PingError and a
        # DestinationUnreachable; the most specific mapping must win.
        outcome = self.make_backend(raiser).ping("192.0.2.1", timeout=1, size=56, sequence=0)
        self.assertEqual(outcome.status, ResultStatus.NETWORK_ERROR)


class SystemPingParserTests(unittest.TestCase):
    def test_linux_success_output(self):
        stdout = (
            "PING 127.0.0.1 (127.0.0.1) 56(84) bytes of data.\n"
            "64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time=0.123 ms\n\n"
            "--- 127.0.0.1 ping statistics ---\n"
            "1 packets transmitted, 1 received, 0% packet loss\n"
        )
        outcome = parse_system_ping_output(stdout, "", 0)
        self.assertEqual(outcome.status, ResultStatus.SUCCESS)
        self.assertAlmostEqual(outcome.latency_ms, 0.123)

    def test_linux_full_loss_is_timeout(self):
        stdout = "1 packets transmitted, 0 received, 100% packet loss, time 0ms"
        outcome = parse_system_ping_output(stdout, "", 1)
        self.assertEqual(outcome.status, ResultStatus.TIMEOUT)

    def test_windows_request_timed_out_with_zero_exit_code(self):
        # Windows ping.exe exits 0 even when every request timed out; the
        # output text is the real evidence.
        stdout = (
            "Pinging 192.0.2.1 with 56 bytes of data:\n"
            "Request timed out.\n\n"
            "Ping statistics for 192.0.2.1:\n"
            "    Packets: Sent = 1, Received = 0, Lost = 1 (100% loss),\n"
        )
        outcome = parse_system_ping_output(stdout, "", 0)
        self.assertEqual(outcome.status, ResultStatus.TIMEOUT)

    def test_windows_success_time_below_1ms(self):
        stdout = "Reply from 127.0.0.1: bytes=56 time<1ms TTL=128"
        outcome = parse_system_ping_output(stdout, "", 0)
        self.assertEqual(outcome.status, ResultStatus.SUCCESS)
        self.assertAlmostEqual(outcome.latency_ms, 1.0)

    def test_chinese_success_output(self):
        stdout = "来自 127.0.0.1 的回复: 字节=56 时间=3ms TTL=64"
        outcome = parse_system_ping_output(stdout, "", 0)
        self.assertEqual(outcome.status, ResultStatus.SUCCESS)
        self.assertAlmostEqual(outcome.latency_ms, 3.0)

    def test_chinese_timeout_output(self):
        stdout = "请求超时。\n数据包: 已发送 = 1，已接收 = 0，丢失 = 1 (100% 丢失)"
        outcome = parse_system_ping_output(stdout, "", 0)
        self.assertEqual(outcome.status, ResultStatus.TIMEOUT)

    def test_destination_unreachable_is_network_error(self):
        stdout = "From 192.0.2.1 icmp_seq=1 Destination Host Unreachable"
        outcome = parse_system_ping_output(stdout, "", 1)
        self.assertEqual(outcome.status, ResultStatus.NETWORK_ERROR)
        self.assertIn("192.0.2.1", outcome.detail)

    def test_zero_exit_code_without_rtt_is_honest_success(self):
        outcome = parse_system_ping_output("unparsable localized reply text", "", 0)
        self.assertEqual(outcome.status, ResultStatus.SUCCESS)
        self.assertEqual(outcome.latency_ms, 0.0)
        self.assertIn("RTT not parsable", outcome.detail)

    def test_nonzero_exit_without_indicators_is_network_error(self):
        outcome = parse_system_ping_output("bad usage", "ping: invalid option", 64)
        self.assertEqual(outcome.status, ResultStatus.NETWORK_ERROR)


class SystemPingCommandTests(unittest.TestCase):
    def make_backend(self, platform_name, family) -> SystemPingBackend:
        backend = SystemPingBackend(family=family, platform_name=platform_name)
        backend._binary = "/sbin/ping" if platform_name != "win32" else r"C:\Windows\System32\ping.exe"
        return backend

    def test_windows_command_uses_numeric_flags(self):
        backend = self.make_backend("win32", socket.AF_INET)
        command = backend._build_command("1.2.3.4", timeout=1.5, size=56)
        self.assertEqual(command[1], "-4")
        self.assertEqual(command[2:4], ["-n", "1"])
        self.assertEqual(command[4:6], ["-w", "1500"])
        self.assertEqual(command[6:8], ["-l", "56"])
        self.assertEqual(command[8], "1.2.3.4")

    def test_macos_ipv4_uses_millisecond_wait(self):
        backend = self.make_backend("darwin", socket.AF_INET)
        command = backend._build_command("1.2.3.4", timeout=2.0, size=56)
        self.assertIn("-W", command)
        self.assertEqual(command[command.index("-W") + 1], "2000")

    def test_macos_ipv6_does_not_copy_ipv4_wait_flag(self):
        # Apple ping6's -W is a node-information option, not a wait timeout.
        backend = self.make_backend("darwin", socket.AF_INET6)
        backend._binary = "/sbin/ping6"
        command = backend._build_command("fe80::1%7", timeout=2.0, size=56)
        self.assertNotIn("-W", command)
        self.assertEqual(command[0], "/sbin/ping6")

    def test_linux_ipv6_uses_seconds(self):
        backend = self.make_backend("linux", socket.AF_INET6)
        backend._binary = "/usr/bin/ping"
        backend._binary_family = socket.AF_INET
        command = backend._build_command("fe80::1%7", timeout=1.0, size=56)
        self.assertIn("-6", command)
        self.assertEqual(command[command.index("-W") + 1], "1.00")

    def test_child_environment_strips_library_paths_when_frozen(self):
        backend = SystemPingBackend(platform_name="linux")
        self.assertEqual(backend._child_environment(), dict(os.environ))


class BackendSwitchTests(unittest.TestCase):
    def test_permission_error_switches_to_system_ping_once(self):
        import pyping_app.core as core

        class FakeFallback(SystemPingBackend):
            instances: list["FakeFallback"] = []

            def __init__(self, family=None, platform_name=None):
                super().__init__(family=family, platform_name=platform_name)
                self.calls = []
                self.import_error = ""
                FakeFallback.instances.append(self)

            @property
            def available(self):
                return True

            def ping(self, host, *, timeout, size, sequence, stop_event=None):
                self.calls.append((host, size, timeout))
                return PingOutcome(5.0, ResultStatus.SUCCESS)

        def denied(*args, **kwargs):
            raise PermissionError("raw socket requires privileges")

        original = core.SystemPingBackend
        core.SystemPingBackend = FakeFallback
        try:
            config = RunConfig(9, "127.0.0.1", 56, 1.0, 1.0, 2, None, datetime.now())
            out = queue.Queue(maxsize=100)
            run_ping_session(config, PingBackend(denied), out, threading.Event())
        finally:
            core.SystemPingBackend = original

        messages = []
        while not out.empty():
            messages.append(out.get_nowait())
        switches = [m for m in messages if m.kind == "backend_switch"]
        records = [m for m in messages if m.kind == "record"]
        self.assertEqual(len(switches), 1, "backend switch must be logged once per session")
        self.assertEqual(switches[0].payload["backend"], "system-ping")
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r.payload.status == ResultStatus.SUCCESS for r in records))
        # Every real send happens exactly once, on the fallback backend.
        self.assertEqual(len(FakeFallback.instances[0].calls), 2)

    def test_timeout_does_not_trigger_backend_switch(self):
        import pyping_app.core as core

        def timing_out(*args, **kwargs):
            return None  # ping3 timeout

        original = core.SystemPingBackend
        created = []
        core.SystemPingBackend = lambda family=None, platform_name=None: created.append(1) or (_ for _ in ()).throw(AssertionError("system backend must not be created"))
        try:
            config = RunConfig(10, "127.0.0.1", 56, 1.0, 1.0, 1, None, datetime.now())
            out = queue.Queue(maxsize=100)
            run_ping_session(config, PingBackend(timing_out), out, threading.Event())
        finally:
            core.SystemPingBackend = original
        messages = []
        while not out.empty():
            messages.append(out.get_nowait())
        self.assertFalse(any(m.kind == "backend_switch" for m in messages))
        timeouts = [m for m in messages if m.kind == "record"]
        self.assertEqual(len(timeouts), 1)
        self.assertEqual(timeouts[0].payload.status, ResultStatus.TIMEOUT)


if __name__ == "__main__":
    unittest.main()

class BackendClassificationTests(unittest.TestCase):
    def test_permission_error_classification(self):
        def denied(*args, **kwargs):
            raise PermissionError("denied")
        outcome = PingBackend(denied).ping("127.0.0.1", timeout=1, size=56, sequence=0)
        self.assertEqual(outcome.status, ResultStatus.PERMISSION_ERROR)

    def test_timeout_exception_classification(self):
        class FakeTimeout(Exception):
            pass
        def timed_out(*args, **kwargs):
            raise FakeTimeout("request timeout")
        outcome = PingBackend(timed_out).ping("127.0.0.1", timeout=1, size=56, sequence=0)
        self.assertEqual(outcome.status, ResultStatus.TIMEOUT)
