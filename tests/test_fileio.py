from __future__ import annotations

import os
import tempfile
import unittest

from pyping_app.fileio import atomic_output_path, atomic_write_text, spreadsheet_safe_text


class FileIoTests(unittest.TestCase):
    def test_spreadsheet_formula_prefixes_are_neutralized(self) -> None:
        for value in ("=1+1", "+cmd", "-2+3", "@SUM(A1:A2)", " \t=1", "\n=1", "\r=1", "\u00a0=1"):
            with self.subTest(value=value):
                self.assertTrue(spreadsheet_safe_text(value).startswith("'"))
        self.assertEqual(spreadsheet_safe_text("normal"), "normal")

    def test_atomic_write_replaces_complete_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "output.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("old")
            atomic_write_text(path, "new", encoding="utf-8")
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "new")

    def test_atomic_output_failure_preserves_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "output.bin")
            with open(path, "wb") as handle:
                handle.write(b"old")
            with self.assertRaises(RuntimeError):
                with atomic_output_path(path) as temporary:
                    temporary.write_bytes(b"partial")
                    raise RuntimeError("stop")
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), b"old")
            self.assertEqual(os.listdir(directory), ["output.bin"])


if __name__ == "__main__":
    unittest.main()
