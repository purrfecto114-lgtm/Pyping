from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import re
import sysconfig

NAME_NORMALIZER = re.compile(r"[-_.]+")


def normalize_name(value: str) -> str:
    return NAME_NORMALIZER.sub("-", value).lower()


def parse_hashed_lock(path: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    current = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        current += (" " if current else "") + line.rstrip("\\").strip()
        if line.endswith("\\"):
            continue
        requirement = current.split()[0]
        current = ""
        if "==" not in requirement:
            raise ValueError(f"Lock requirement is not exact: {requirement}")
        name, version = requirement.split("==", 1)
        normalized = normalize_name(name)
        if normalized in expected:
            raise ValueError(f"Duplicate lock requirement: {name}")
        expected[normalized] = version
    if current:
        raise ValueError("Lock file ends with a dangling continuation")
    return expected


def installed_distributions() -> dict[str, str]:
    search_paths = {
        Path(sysconfig.get_path("purelib")).resolve(),
        Path(sysconfig.get_path("platlib")).resolve(),
    }
    result: dict[str, str] = {}
    for distribution in metadata.distributions(path=[str(path) for path in search_paths]):
        name = distribution.metadata.get("Name")
        if not name:
            continue
        result[normalize_name(name)] = distribution.version
    return result


def verify_environment(lock_path: Path) -> None:
    expected = parse_hashed_lock(lock_path)
    installed = installed_distributions()
    allowed = set(expected) | {"pip"}
    unexpected = sorted(set(installed) - allowed)
    missing = sorted(set(expected) - set(installed))
    mismatched = sorted(
        f"{name}: expected {expected[name]}, installed {installed[name]}"
        for name in expected.keys() & installed.keys()
        if installed[name] != expected[name]
    )
    if unexpected or missing or mismatched:
        details = []
        if unexpected:
            details.append(f"unexpected distributions: {unexpected}")
        if missing:
            details.append(f"missing distributions: {missing}")
        if mismatched:
            details.append(f"version mismatches: {mismatched}")
        raise ValueError("; ".join(details))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the isolated Windows build environment")
    parser.add_argument("--lock", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        verify_environment(arguments.lock.resolve(strict=True))
    except (OSError, ValueError) as exc:
        print(f"build environment verification: FAILED: {exc}")
        return 1
    print("build environment verification: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
