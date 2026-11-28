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
    excludes=[
        "pytest",
        "unittest",
        # Pillow components Pyping never uses.  Only PNG output is required
        # (Image/ImageDraw/ImageFont + PngImagePlugin); excluding the other
        # format plugins and auxiliary backends keeps their pure-Python
        # modules and native codec libraries (webp/avif/lcms/raqm/morph/tk)
        # out of the bundle.
        "PIL.AvifImagePlugin",
        "PIL.BlpImagePlugin",
        "PIL.BmpImagePlugin",
        "PIL.BufrStubImagePlugin",
        "PIL.CurImagePlugin",
        "PIL.DcxImagePlugin",
        "PIL.DdsImagePlugin",
        "PIL.EpsImagePlugin",
        "PIL.FitsImagePlugin",
        "PIL.FliImagePlugin",
        "PIL.FpxImagePlugin",
        "PIL.FtexImagePlugin",
        "PIL.GbrImagePlugin",
        "PIL.GifImagePlugin",
        "PIL.GribStubImagePlugin",
        "PIL.Hdf5StubImagePlugin",
        "PIL.IcnsImagePlugin",
        "PIL.IcoImagePlugin",
        "PIL.ImImagePlugin",
        "PIL.ImageCms",
        "PIL.ImageMath",
        "PIL.ImageMorph",
        "PIL.ImageQt",
        "PIL.ImageShow",
        "PIL.ImageTk",
        "PIL.ImtImagePlugin",
        "PIL.IptcImagePlugin",
        "PIL.Jpeg2KImagePlugin",
        "PIL.JpegImagePlugin",
        "PIL.McIdasImagePlugin",
        "PIL.MicImagePlugin",
        "PIL.MpegImagePlugin",
        "PIL.MpoImagePlugin",
        "PIL.MspImagePlugin",
        "PIL.PalmImagePlugin",
        "PIL.PcdImagePlugin",
        "PIL.PcxImagePlugin",
        "PIL.PdfImagePlugin",
        "PIL.PixarImagePlugin",
        "PIL.PsdImagePlugin",
        "PIL.QoiImagePlugin",
        "PIL.SgiImagePlugin",
        "PIL.SpiderImagePlugin",
        "PIL.SunImagePlugin",
        "PIL.TgaImagePlugin",
        "PIL.TiffImagePlugin",
        "PIL.WebPImagePlugin",
        "PIL.WmfImagePlugin",
        "PIL.XVThumbImagePlugin",
        "PIL.XbmImagePlugin",
        "PIL.XpmImagePlugin",
    ],
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
