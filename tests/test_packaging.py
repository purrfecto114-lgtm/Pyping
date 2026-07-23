from __future__ import annotations

import hashlib
import json
import pathlib
import re
import tempfile
import unittest

from tools.verify_build_environment import parse_hashed_lock
from tools.verify_release import verify_release

ROOT = pathlib.Path(__file__).resolve().parents[1]
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class PackagingTests(unittest.TestCase):
    def test_windows_build_script_is_portable_and_checks_exit_codes(self) -> None:
        source = (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
        for marker in (
            'Mode = "release"',
            "Resolve-BasePython",
            "Test-BuildInterpreter",
            "recreating it",
            "Get-SafeProjectPath",
            "Clear-GeneratedChildren",
            '.EndsWith(".egg-info"',
            "Invoke-Checked -FilePath",
            "if ($LASTEXITCODE -ne 0)",
            "--no-cache-dir",
            "--only-binary=:all:",
            "--require-hashes",
            "--force-reinstall",
            "verify_build_environment.py",
            "requirements-windows.lock",
            "release-manifest.json",
            "source_commit",
            "GITHUB_SHA",
            "source_ref",
            "tools\\verify_release.py",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("--upgrade", source)
        self.assertNotIn("Pyping-v0.4.0-Windows", source)
        self.assertNotIn("py -3.12 -m venv", source)
        self.assertEqual(source.count("{"), source.count("}"))
        self.assertEqual(source.count("("), source.count(")"))

    def test_batch_wrapper_propagates_failures(self) -> None:
        source = (ROOT / "packaging" / "build_windows.bat").read_text(encoding="utf-8")
        self.assertIn("exit /b %EXIT_CODE%", source)

    def test_installer_uses_project_root_as_source_directory(self) -> None:
        source = (ROOT / "packaging" / "installer" / "Pyping.iss").read_text(encoding="utf-8")
        self.assertIn("SourceDir={#ProjectRoot}", source)
        self.assertIn('Source: "dist\\Pyping\\*"', source)
        self.assertIn('#define MyAppVersion "0.4.0"', source)
        self.assertIn('MessagesFile: "compiler:Languages\\ChineseSimplified.isl"', source)
        self.assertFalse((ROOT / "packaging" / "installer" / "Languages").exists())

    def test_release_workflow_uses_read_only_build_and_sha_pins(self) -> None:
        path = ROOT / ".github" / "workflows" / "build-windows.yml"
        source = path.read_text(encoding="utf-8")
        build_section, separator, publish_section = source.partition("\n  publish:")
        self.assertTrue(separator)
        self.assertNotIn("contents: write", build_section)
        self.assertEqual(publish_section.count("contents: write"), 1)
        self.assertNotIn("actions/checkout@", publish_section)
        self.assertNotIn("softprops/action-gh-release", source)
        self.assertNotIn("choco install", source)
        self.assertNotIn("release/*", source)
        self.assertIn("persist-credentials: false", source)
        self.assertIn("fetch-depth: 0", source)
        self.assertIn("git merge-base --is-ancestor", source)
        self.assertIn("github.event.repository.default_branch", source)
        self.assertIn("manifest.get(\"source_commit\")", source)
        self.assertIn("environment: release", source)
        self.assertIn("independent release verification: OK", source)
        for line in source.splitlines():
            match = re.match(r"^\s*uses:\s*([^\s#]+)", line)
            if not match:
                continue
            reference = match.group(1)
            self.assertIn("@", reference)
            self.assertRegex(reference.rsplit("@", 1)[1], FULL_SHA_RE)

    def test_validation_workflow_is_read_only_and_does_not_cache_dependencies(self) -> None:
        source = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("contents: write", source)
        self.assertNotIn("cache:", source)
        self.assertNotIn("pull_request_target", source)
        self.assertIn("permissions:\n  contents: read", source)

    def test_ci_runtime_requirements_are_exact_direct_pins(self) -> None:
        relative = "packaging/requirements-runtime.txt"
        for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                self.assertRegex(line, r"^[A-Za-z0-9_.-]+==[^=<>!~\s]+$")
        self.assertFalse((ROOT / "packaging" / "requirements-build.txt").exists())

    def test_windows_lock_parses_to_complete_exact_set(self) -> None:
        values = parse_hashed_lock(ROOT / "packaging" / "requirements-windows.lock")
        self.assertEqual(values["ping3"], "5.1.5")
        self.assertEqual(values["pillow"], "12.3.0")
        self.assertEqual(values["pyinstaller"], "6.21.0")
        self.assertGreaterEqual(len(values), 9)

    def test_windows_lock_has_hashes_for_every_requirement(self) -> None:
        source = (ROOT / "packaging" / "requirements-windows.lock").read_text(encoding="utf-8")
        logical_lines: list[str] = []
        current = ""
        for raw in source.splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            current += (" " if current else "") + stripped.rstrip("\\").strip()
            if not stripped.endswith("\\"):
                logical_lines.append(current)
                current = ""
        self.assertFalse(current)
        self.assertGreaterEqual(len(logical_lines), 9)
        for line in logical_lines:
            self.assertRegex(line, r"^[A-Za-z0-9_.-]+==[^\s]+ ")
            self.assertIn("--hash=sha256:", line)
            self.assertNotIn("http://", line)
            self.assertNotIn("https://", line)

    def test_release_verifier_accepts_exact_payload_set(self) -> None:
        version = "0.4.0"
        with tempfile.TemporaryDirectory() as directory_name:
            directory = pathlib.Path(directory_name)
            payload_names = {
                f"Pyping-v{version}-Windows-x64-portable.zip",
                f"Pyping-v{version}-Windows-x64-onefile.exe",
                f"Pyping-Setup-{version}-x64.exe",
            }
            entries = []
            for index, name in enumerate(sorted(payload_names), 1):
                content = f"payload-{index}".encode()
                (directory / name).write_bytes(content)
                entries.append(
                    {
                        "name": name,
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
            manifest = {
                "schema": 1,
                "application": "Pyping GUI",
                "version": version,
                "platform": "windows-x64",
                "source_commit": "local",
                "source_ref": "local",
                "built_at_utc": "2026-07-22T00:00:00Z",
                "files": entries,
            }
            manifest_path = directory / "release-manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            checksum_names = payload_names | {manifest_path.name}
            lines = [
                f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}"
                for name in sorted(checksum_names)
            ]
            (directory / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="ascii")
            verify_release(directory, version, require_installer=True)

    def test_release_verifier_rejects_boolean_manifest_size(self) -> None:
        version = "0.4.0"
        with tempfile.TemporaryDirectory() as directory_name:
            directory = pathlib.Path(directory_name)
            names = {
                f"Pyping-v{version}-Windows-x64-portable.zip",
                f"Pyping-v{version}-Windows-x64-onefile.exe",
            }
            entries = []
            for name in sorted(names):
                (directory / name).write_bytes(b"x")
                entries.append(
                    {"name": name, "size": True, "sha256": hashlib.sha256(b"x").hexdigest()}
                )
            manifest = {
                "schema": 1, "application": "Pyping GUI", "version": version,
                "platform": "windows-x64", "source_commit": "local",
                "source_ref": "local", "files": entries,
            }
            manifest_path = directory / "release-manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            checksum_names = names | {manifest_path.name}
            (directory / "SHA256SUMS.txt").write_text(
                "".join(
                    f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
                    for name in sorted(checksum_names)
                ), encoding="ascii"
            )
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                verify_release(directory, version, require_installer=False)

    def test_release_verifier_rejects_source_commit_mismatch(self) -> None:
        version = "0.4.0"
        with tempfile.TemporaryDirectory() as directory_name:
            directory = pathlib.Path(directory_name)
            payload_names = {
                f"Pyping-v{version}-Windows-x64-portable.zip",
                f"Pyping-v{version}-Windows-x64-onefile.exe",
            }
            entries = []
            for name in sorted(payload_names):
                content = name.encode()
                (directory / name).write_bytes(content)
                entries.append(
                    {
                        "name": name,
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
            manifest = {
                "schema": 1,
                "application": "Pyping GUI",
                "version": version,
                "platform": "windows-x64",
                "source_commit": "a" * 40,
                "source_ref": "refs/tags/v0.4.0",
                "files": entries,
            }
            manifest_path = directory / "release-manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            checksum_names = payload_names | {manifest_path.name}
            (directory / "SHA256SUMS.txt").write_text(
                "".join(
                    f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
                    for name in sorted(checksum_names)
                ),
                encoding="ascii",
            )
            with self.assertRaisesRegex(ValueError, "source commit mismatch"):
                verify_release(
                    directory,
                    version,
                    require_installer=False,
                    source_commit="b" * 40,
                    source_ref="refs/tags/v0.4.0",
                )

    def test_release_verifier_rejects_unexpected_file(self) -> None:
        version = "0.4.0"
        with tempfile.TemporaryDirectory() as directory_name:
            directory = pathlib.Path(directory_name)
            (directory / "unexpected.exe").write_bytes(b"x")
            (directory / "release-manifest.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "application": "Pyping GUI",
                        "version": version,
                        "platform": "windows-x64",
                        "source_commit": "local",
                        "source_ref": "local",
                        "files": [],
                    }
                ),
                encoding="utf-8",
            )
            (directory / "SHA256SUMS.txt").write_text("", encoding="ascii")
            with self.assertRaises(ValueError):
                verify_release(directory, version, require_installer=False)


if __name__ == "__main__":
    unittest.main()
