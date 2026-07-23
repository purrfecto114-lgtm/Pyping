from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_METADATA_BYTES = 1024 * 1024
COMMIT_RE = re.compile(r"^(?:local|[0-9a-f]{40})$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_payload_names(version: str, require_installer: bool) -> set[str]:
    names = {
        f"Pyping-v{version}-Windows-x64-portable.zip",
        f"Pyping-v{version}-Windows-x64-onefile.exe",
    }
    if require_installer:
        names.add(f"Pyping-Setup-{version}-x64.exe")
    return names


def parse_checksums(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        if not raw_line.strip():
            continue
        parts = raw_line.split("  ", 1)
        if len(parts) != 2 or not SHA256_RE.fullmatch(parts[0]):
            raise ValueError(f"Invalid SHA256SUMS.txt line {line_number}")
        name = parts[1]
        if Path(name).name != name or name in values:
            raise ValueError(f"Unsafe or duplicate checksum name: {name!r}")
        values[name] = parts[0]
    return values


def verify_release(
    directory: Path,
    version: str,
    require_installer: bool,
    source_commit: str | None = None,
    source_ref: str | None = None,
) -> None:
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"Invalid version: {version!r}")
    directory = directory.resolve(strict=True)
    if not directory.is_dir():
        raise ValueError(f"Release path is not a directory: {directory}")

    for item in directory.iterdir():
        if item.is_symlink():
            raise ValueError(f"Release output must not contain symlinks: {item.name}")
        if not item.is_file():
            raise ValueError(f"Release output must contain files only: {item.name}")

    manifest_path = directory / "release-manifest.json"
    checksums_path = directory / "SHA256SUMS.txt"
    for metadata_path in (manifest_path, checksums_path):
        if metadata_path.stat().st_size > MAX_METADATA_BYTES:
            raise ValueError(f"Release metadata is unexpectedly large: {metadata_path.name}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1:
        raise ValueError("Unsupported release manifest schema")
    if manifest.get("version") != version:
        raise ValueError("Release manifest version mismatch")
    if manifest.get("application") != "Pyping GUI" or manifest.get("platform") != "windows-x64":
        raise ValueError("Release manifest identity mismatch")
    manifest_commit = manifest.get("source_commit")
    if not isinstance(manifest_commit, str) or not COMMIT_RE.fullmatch(manifest_commit):
        raise ValueError("Release manifest source commit is invalid")
    if source_commit is not None and manifest_commit != source_commit:
        raise ValueError("Release manifest source commit mismatch")
    manifest_ref = manifest.get("source_ref")
    if not isinstance(manifest_ref, str) or not manifest_ref or any(ord(ch) < 32 for ch in manifest_ref):
        raise ValueError("Release manifest source ref is invalid")
    if source_ref is not None and manifest_ref != source_ref:
        raise ValueError("Release manifest source ref mismatch")

    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise ValueError("Manifest files must be a list")
    manifest_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid release manifest file entry")
        name = entry.get("name")
        if not isinstance(name, str) or Path(name).name != name or name in manifest_names:
            raise ValueError(f"Unsafe or duplicate manifest file name: {name!r}")
        manifest_names.add(name)
        path = directory / name
        if not path.is_file():
            raise ValueError(f"Manifest payload is missing: {name}")
        expected_size = entry.get("size")
        if (
            isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
            or expected_size < 0
            or expected_size != path.stat().st_size
        ):
            raise ValueError(f"Manifest size mismatch: {name}")
        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
            raise ValueError(f"Invalid manifest hash: {name}")
        if sha256_file(path) != expected_hash:
            raise ValueError(f"Manifest hash mismatch: {name}")

    mandatory = expected_payload_names(version, require_installer)
    optional_installer = f"Pyping-Setup-{version}-x64.exe"
    allowed_payloads = set(mandatory)
    if not require_installer:
        allowed_payloads.add(optional_installer)
    if not mandatory.issubset(manifest_names) or not manifest_names.issubset(allowed_payloads):
        raise ValueError(
            f"Unexpected manifest payload set: {sorted(manifest_names)}; "
            f"required: {sorted(mandatory)}"
        )

    checksums = parse_checksums(checksums_path)
    expected_checksum_names = manifest_names | {"release-manifest.json"}
    if set(checksums) != expected_checksum_names:
        raise ValueError("SHA256SUMS.txt contains missing or unexpected names")
    for name, expected_hash in checksums.items():
        if sha256_file(directory / name) != expected_hash:
            raise ValueError(f"Checksum mismatch: {name}")

    expected_directory_names = expected_checksum_names | {"SHA256SUMS.txt"}
    actual_directory_names = {item.name for item in directory.iterdir()}
    if actual_directory_names != expected_directory_names:
        raise ValueError(
            f"Release directory contains unexpected files: "
            f"{sorted(actual_directory_names - expected_directory_names)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify exact Pyping release artifacts")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--require-installer", action="store_true")
    parser.add_argument("--source-commit")
    parser.add_argument("--source-ref")
    arguments = parser.parse_args()
    try:
        verify_release(
            arguments.directory,
            arguments.version,
            arguments.require_installer,
            arguments.source_commit,
            arguments.source_ref,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"release verification: FAILED: {exc}", file=sys.stderr)
        return 1
    print("release verification: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
