# -*- mode: python ; coding: utf-8 -*-
import os

project_root = os.path.abspath(os.path.join(SPECPATH, ".."))
icon_path = os.path.join(project_root, "pyping_app", "assets", "pyping.ico")
version_path = os.path.join(project_root, "packaging", "windows_version_info.txt")

a = Analysis(
    [os.path.join(project_root, "PingTool.py")],
    pathex=[project_root],
    binaries=[],
    datas=[
        (os.path.join(project_root, "README.md"), "."),
        (os.path.join(project_root, "LICENSE"), "."),
        (os.path.join(project_root, "pyping_app", "assets"), "pyping_app/assets"),
    ],
    hiddenimports=[
        "ping3",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFont",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Pyping",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon_path,
    version=version_path,
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Pyping",
)
