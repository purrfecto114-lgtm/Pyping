from __future__ import annotations

import ast
import compileall
import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_RELEASE_FILES = (
    "packaging/Pyping.spec",
    "packaging/Pyping-onefile.spec",
    "packaging/build_windows.ps1",
    "packaging/build_windows.bat",
    "packaging/build_portable_onefile.bat",
    "packaging/clean_windows.bat",
    "packaging/installer/Pyping.iss",
    "packaging/windows_version_info.txt",
    ".github/workflows/build-windows.yml",
    "pyping_app/assets/pyping.png",
    "pyping_app/assets/pyping.ico",
)


def main() -> int:
    compile_targets = [
        ROOT / "PingTool.py",
        ROOT / "pyping_app",
        ROOT / "tests",
        ROOT / "tools",
    ]
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
    i18n_source = (ROOT / "pyping_app" / "i18n.py").read_text(encoding="utf-8")
    if f'APP_VERSION = "v{project_version}"' not in i18n_source:
        problems.append("APP_VERSION and pyproject.toml version differ")

    from pyping_app.i18n import LANGUAGES

    language_sets = {name: set(values) for name, values in LANGUAGES.items()}
    first_keys = next(iter(language_sets.values()))
    for name, keys in language_sets.items():
        if keys != first_keys:
            problems.append(f"translation key mismatch: {name}")

    for path in ROOT.rglob("*.py"):
        if any(
            part in {".venv", ".venv-build", "wheel-test-venv", "build", "dist", "dist-wheel", "site-packages"}
            or part.endswith("-venv")
            for part in path.parts
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: bare except")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "gethostbyname":
                    problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: IPv4-only gethostbyname")
            if isinstance(node, ast.Name) and node.id == "ImageGrab":
                problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: screen-capture export")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"eval", "exec"}:
                    problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: unsafe {node.func.id} call")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and node.func.attr == "system":
                    problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: os.system call")
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess":
                    for keyword in node.keywords:
                        if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                            problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: subprocess shell=True")


    build_script = (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
    for expected in (
        'Mode = "release"',
        "Resolve-BasePython",
        "Invoke-Checked -FilePath",
        "if ($LASTEXITCODE -ne 0)",
        "Build-OneDir",
        "Build-OneFile",
        "Build-Installer",
    ):
        if expected not in build_script:
            problems.append(f"packaging/build_windows.ps1: missing {expected}")
    if "py -3.12 -m venv" in build_script:
        problems.append("packaging/build_windows.ps1: hard-coded Python 3.12 launcher")
    if build_script.count("{") != build_script.count("}"):
        problems.append("packaging/build_windows.ps1: unbalanced braces")
    if build_script.count("(") != build_script.count(")"):
        problems.append("packaging/build_windows.ps1: unbalanced parentheses")

    batch_script = (ROOT / "packaging" / "build_windows.bat").read_text(encoding="utf-8")
    if "exit /b %EXIT_CODE%" not in batch_script:
        problems.append("packaging/build_windows.bat: exit code is not propagated")

    installer_script = (ROOT / "packaging" / "installer" / "Pyping.iss").read_text(encoding="utf-8")
    for expected in (f'#define MyAppVersion "{project_version}"', "SourceDir={#ProjectRoot}", r'Source: "dist\Pyping\*"'):
        if expected not in installer_script:
            problems.append(f"packaging/installer/Pyping.iss: missing {expected}")

    gui_source = (ROOT / "pyping_app" / "gui.py").read_text(encoding="utf-8")
    for expected in ("Section.TFrame", "compact_height", "MIN_LOG_HEIGHT", "OUTPUT_ROW_MIN_HEIGHT", "get_work_area"):
        if expected not in gui_source:
            problems.append(f"pyping_app/gui.py: missing responsive GUI marker {expected}")
    if "DEFAULT_WINDOW_HEIGHT * scale" in gui_source:
        problems.append("pyping_app/gui.py: fixed DPI-multiplied startup height remains")

    workflow = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")
    for expected in ("actions/checkout@v7", "actions/setup-python@v7", "actions/upload-artifact@v4"):
        if expected not in workflow:
            problems.append(f".github/workflows/build-windows.yml: missing {expected}")

    for spec_name in ("packaging/Pyping.spec", "packaging/Pyping-onefile.spec"):
        spec = (ROOT / spec_name).read_text(encoding="utf-8")
        for expected in ('"ping3"', 'console=False', 'uac_admin=False'):
            if expected not in spec:
                problems.append(f"{spec_name}: missing {expected}")

    if problems:
        print("static policy: FAILED")
        print("\n".join(problems))
        return 1
    print("static policy: OK")
    print(f"metadata: OK (v{project_version}, {len(first_keys)} translation keys)")
    print("release files: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
