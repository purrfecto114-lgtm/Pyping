from __future__ import annotations

import ast
import compileall
import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_RELEASE_FILES = (
    ".github/workflows/build-windows.yml",
    ".github/workflows/ci.yml",
    ".github/dependabot.yml",
    "SECURITY.md",
    "packaging/Pyping.spec",
    "packaging/Pyping-onefile.spec",
    "packaging/build_windows.ps1",
    "packaging/build_windows.bat",
    "packaging/build_portable_onefile.bat",
    "packaging/clean_windows.bat",
    "packaging/installer/Pyping.iss",
    "packaging/requirements-runtime.txt",
    "packaging/requirements-windows.lock",
    "packaging/windows_version_info.txt",
    "pyping_app/assets/pyping.png",
    "pyping_app/assets/pyping.ico",
    "tools/verify_release.py",
    "tools/verify_build_environment.py",
)

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EXACT_REQUIREMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+==[^=<>!~\s]+$")


def check_action_pins(path: pathlib.Path, problems: list[str]) -> None:
    source = path.read_text(encoding="utf-8")
    for line_number, line in enumerate(source.splitlines(), 1):
        match = re.match(r"^\s*uses:\s*([^\s#]+)", line)
        if not match:
            continue
        reference = match.group(1)
        if reference.startswith("./"):
            continue
        if "@" not in reference:
            problems.append(f"{path.relative_to(ROOT)}:{line_number}: action has no ref")
            continue
        _action, ref = reference.rsplit("@", 1)
        if not FULL_SHA_RE.fullmatch(ref):
            problems.append(
                f"{path.relative_to(ROOT)}:{line_number}: action is not pinned to a full commit SHA"
            )


def check_exact_requirements(path: pathlib.Path, problems: list[str]) -> None:
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not EXACT_REQUIREMENT_RE.fullmatch(line):
            problems.append(
                f"{path.relative_to(ROOT)}:{line_number}: build requirement must be an exact direct pin"
            )


def main() -> int:
    compile_targets = [ROOT / "PingTool.py", ROOT / "pyping_app", ROOT / "tests", ROOT / "tools"]
    compile_ok = True
    for target in compile_targets:
        if target.is_dir():
            compile_ok = compileall.compile_dir(target, quiet=1) and compile_ok
        else:
            compile_ok = compileall.compile_file(target, quiet=1) and compile_ok
    if not compile_ok:
        print("compileall: FAILED")
        return 1
    print("compileall: OK")

    problems: list[str] = []
    for relative in REQUIRED_RELEASE_FILES:
        if not (ROOT / relative).is_file():
            problems.append(f"missing release file: {relative}")

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = metadata["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", project_version):
        problems.append("pyproject.toml: project version is not x.y.z")
    i18n_source = (ROOT / "pyping_app" / "i18n.py").read_text(encoding="utf-8")
    if f'APP_VERSION = "v{project_version}"' not in i18n_source:
        problems.append("APP_VERSION and pyproject.toml version differ")

    from pyping_app.i18n import LANGUAGES

    language_sets = {name: set(values) for name, values in LANGUAGES.items()}
    first_keys = next(iter(language_sets.values()))
    for name, keys in language_sets.items():
        if keys != first_keys:
            problems.append(f"translation key mismatch: {name}")

    ignored_parts = {
        ".git", ".venv", ".venv-build", "wheel-test-venv", "build", "dist",
        "dist-wheel", "dist-onefile", "release", "site-packages", "__pycache__",
    }
    for path in ROOT.rglob("*.py"):
        if any(part in ignored_parts or part.endswith("-venv") for part in path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: bare except")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "gethostbyname":
                    problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: IPv4-only gethostbyname")
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and node.func.attr == "system":
                    problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: os.system call")
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess":
                    for keyword in node.keywords:
                        if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                            problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: subprocess shell=True")
            if isinstance(node, ast.Name) and node.id == "ImageGrab":
                problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: screen-capture export")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
                problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: unsafe {node.func.id} call")

    build_script_path = ROOT / "packaging" / "build_windows.ps1"
    build_script = build_script_path.read_text(encoding="utf-8")
    for expected in (
        'Mode = "release"',
        "Resolve-BasePython",
        "Test-BuildInterpreter",
        "recreating it",
        "Get-SafeProjectPath",
        "Invoke-Checked -FilePath",
        "if ($LASTEXITCODE -ne 0)",
        "--no-cache-dir",
        "--only-binary=:all:",
        "--require-hashes",
        "--force-reinstall",
        "verify_build_environment.py",
        "requirements-windows.lock",
        "Assert-VersionConsistency",
        "release-manifest.json",
        "source_commit",
        "GITHUB_SHA",
        "source_ref",
        "tools\\verify_release.py",
        "Build-OneDir",
        "Build-OneFile",
        "Build-Installer",
    ):
        if expected not in build_script:
            problems.append(f"packaging/build_windows.ps1: missing {expected}")
    for forbidden in ("--upgrade", "Pyping-v0.4.0-Windows", "py -3.12 -m venv"):
        if forbidden in build_script:
            problems.append(f"packaging/build_windows.ps1: forbidden stale/non-reproducible pattern {forbidden}")
    if build_script.count("{") != build_script.count("}"):
        problems.append("packaging/build_windows.ps1: unbalanced braces")
    if build_script.count("(") != build_script.count(")"):
        problems.append("packaging/build_windows.ps1: unbalanced parentheses")

    check_exact_requirements(ROOT / "packaging/requirements-runtime.txt", problems)
    if (ROOT / "packaging/requirements-build.txt").exists():
        problems.append("packaging/requirements-build.txt: obsolete duplicate of hashed lock")

    lock_path = ROOT / "packaging" / "requirements-windows.lock"
    if not lock_path.is_file():
        problems.append("missing release file: packaging/requirements-windows.lock")
    else:
        logical_lines: list[str] = []
        current = ""
        for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            current += (" " if current else "") + line.rstrip("\\").strip()
            if not line.endswith("\\"):
                logical_lines.append(current)
                current = ""
        if current:
            problems.append("packaging/requirements-windows.lock: dangling continuation")
        if len(logical_lines) < 9:
            problems.append("packaging/requirements-windows.lock: incomplete transitive dependency lock")
        for index, line in enumerate(logical_lines, 1):
            if not re.match(r"^[A-Za-z0-9_.-]+==[^\s]+ ", line):
                problems.append(f"packaging/requirements-windows.lock:{index}: requirement is not exact")
            if "--hash=sha256:" not in line:
                problems.append(f"packaging/requirements-windows.lock:{index}: missing SHA-256 hash")
            if "http://" in line or "https://" in line:
                problems.append(f"packaging/requirements-windows.lock:{index}: direct URL is forbidden")

    build_script = (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
    for expected in ("Clear-GeneratedChildren", '.EndsWith(".egg-info"', "ReparsePoint"):
        if expected not in build_script:
            problems.append(f"packaging/build_windows.ps1: missing safe cleanup marker {expected}")

    batch_script = (ROOT / "packaging" / "build_windows.bat").read_text(encoding="utf-8")
    if "exit /b %EXIT_CODE%" not in batch_script:
        problems.append("packaging/build_windows.bat: exit code is not propagated")

    installer_script = (ROOT / "packaging" / "installer" / "Pyping.iss").read_text(encoding="utf-8")
    for expected in (
        f'#define MyAppVersion "{project_version}"',
        "SourceDir={#ProjectRoot}",
        r'Source: "dist\Pyping\*"',
    ):
        if expected not in installer_script:
            problems.append(f"packaging/installer/Pyping.iss: missing {expected}")

    # ChineseSimplified.isl is not shipped with Inno Setup 6 (only Inno Setup 7+).
    # The windows-2022 GitHub Actions runner still provides Inno Setup 6, so the
    # language file must be vendored into the repository and referenced via a
    # project-relative path.
    if r'MessagesFile: "packaging\installer\Languages\ChineseSimplified.isl"' not in installer_script:
        problems.append('packaging/installer/Pyping.iss: missing vendored ChineseSimplified.isl MessagesFile reference')
    vendored_isl = ROOT / "packaging" / "installer" / "Languages" / "ChineseSimplified.isl"
    if not vendored_isl.is_file():
        problems.append("packaging/installer/Languages/ChineseSimplified.isl: vendored language file is missing")

    gui_source = (ROOT / "pyping_app" / "gui.py").read_text(encoding="utf-8")
    for expected in (
        "Section.TFrame", "compact_height", "MIN_LOG_HEIGHT", "OUTPUT_ROW_MIN_HEIGHT",
        "get_work_area", "atomic_write_text",
    ):
        if expected not in gui_source:
            problems.append(f"pyping_app/gui.py: missing GUI/safe-output marker {expected}")
    if "DEFAULT_WINDOW_HEIGHT * scale" in gui_source:
        problems.append("pyping_app/gui.py: fixed DPI-multiplied startup height remains")

    storage_source = (ROOT / "pyping_app" / "storage.py").read_text(encoding="utf-8")
    for expected in ("INSERT INTO records", "self._conn.rollback()", "mode=ro", "spreadsheet_safe_text", "atomic_output_path"):
        if expected not in storage_source:
            problems.append(f"pyping_app/storage.py: missing integrity marker {expected}")
    if "INSERT OR REPLACE" in storage_source:
        problems.append("pyping_app/storage.py: duplicate records may be silently replaced")
    if (
        "CHECK(sequence > 0)" not in storage_source
        or "status = 'success' AND latency IS NOT NULL" not in storage_source
        or "MAX_CHART_POINTS = 50_000" not in storage_source
        or "MAX_RECORD_DETAIL_LENGTH = 1000" not in storage_source
    ):
        problems.append("pyping_app/storage.py: database/input integrity constraints are incomplete")

    charting_source = (ROOT / "pyping_app" / "charting.py").read_text(encoding="utf-8")
    if "subprocess" in charting_source or "fc-match" in charting_source:
        problems.append("pyping_app/charting.py: font discovery must not execute PATH-controlled commands")
    if 'os.environ.get("WINDIR"' in charting_source or "recursive=True" in charting_source:
        problems.append("pyping_app/charting.py: font discovery trusts mutable environment or recursive traversal")
    if "Chart dimensions are outside the supported range" not in charting_source:
        problems.append("pyping_app/charting.py: image dimensions are not bounded")

    build_workflow = ROOT / ".github" / "workflows" / "build-windows.yml"
    ci_workflow = ROOT / ".github" / "workflows" / "ci.yml"
    for workflow_path in (build_workflow, ci_workflow):
        check_action_pins(workflow_path, problems)
        workflow = workflow_path.read_text(encoding="utf-8")
        for forbidden in (
            "pull_request_target", "softprops/action-gh-release", "choco install",
            "windows-latest", "ubuntu-latest", 'cache: "pip"', "release/*",
        ):
            if forbidden in workflow:
                problems.append(f"{workflow_path.relative_to(ROOT)}: forbidden workflow pattern {forbidden}")
        if "persist-credentials: false" not in workflow:
            problems.append(f"{workflow_path.relative_to(ROOT)}: checkout credentials are not disabled")

    release_workflow = build_workflow.read_text(encoding="utf-8")
    for expected in (
        "permissions:\n  contents: read",
        "Build without repository write access",
        "Publish prebuilt assets only",
        "environment: release",
        "contents: write",
        "actions/download-artifact@",
        "gh release create",
        "independent release verification: OK",
        "release-manifest.json",
        "fetch-depth: 0",
        "git merge-base --is-ancestor",
        "github.event.repository.default_branch",
        "manifest.get(\"source_commit\")",
    ):
        if expected not in release_workflow:
            problems.append(f".github/workflows/build-windows.yml: missing {expected}")
    build_section, separator, publish_section = release_workflow.partition("\n  publish:")
    if not separator:
        problems.append(".github/workflows/build-windows.yml: build/publish jobs are not separated")
    else:
        if "contents: write" in build_section:
            problems.append(".github/workflows/build-windows.yml: build job has write permission")
        if "actions/checkout@" in publish_section or "actions/setup-python@" in publish_section:
            problems.append(".github/workflows/build-windows.yml: publish job executes checked-out repository code")
        if publish_section.count("contents: write") != 1:
            problems.append(".github/workflows/build-windows.yml: publish write permission is not narrowly scoped")

    for spec_name in ("packaging/Pyping.spec", "packaging/Pyping-onefile.spec"):
        spec = (ROOT / spec_name).read_text(encoding="utf-8")
        for expected in ('"ping3"', "console=False", "uac_admin=False"):
            if expected not in spec:
                problems.append(f"{spec_name}: missing {expected}")

    if problems:
        print("static policy: FAILED")
        print("\n".join(problems))
        return 1
    print("static policy: OK")
    print(f"metadata: OK (v{project_version}, {len(first_keys)} translation keys)")
    print("release and workflow security files: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
