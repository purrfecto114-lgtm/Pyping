from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_windows_build_script_is_portable_and_checks_exit_codes(self) -> None:
        source = (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
        self.assertIn('Mode = "release"', source)
        self.assertIn("Resolve-BasePython", source)
        self.assertIn("Invoke-Checked -FilePath", source)
        self.assertIn("if ($LASTEXITCODE -ne 0)", source)
        self.assertNotIn("py -3.12 -m venv", source)
        self.assertEqual(source.count("{"), source.count("}"))
        self.assertEqual(source.count("("), source.count(")"))

    def test_batch_wrapper_propagates_failures(self) -> None:
        source = (ROOT / "packaging" / "build_windows.bat").read_text(encoding="utf-8")
        self.assertIn("exit /b %EXIT_CODE%", source)

    def test_installer_uses_project_root_as_source_directory(self) -> None:
        source = (ROOT / "packaging" / "installer" / "Pyping.iss").read_text(encoding="utf-8")
        self.assertIn('SourceDir={#ProjectRoot}', source)
        self.assertIn('Source: "dist\\Pyping\\*"', source)
        self.assertIn('#define MyAppVersion "0.4.0"', source)


if __name__ == "__main__":
    unittest.main()
