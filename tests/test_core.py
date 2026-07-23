import queue
import threading
import time
import unittest
from datetime import datetime

from pyping_app.core import PingBackend, run_ping_session
from pyping_app.models import ResultStatus, RunConfig


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
