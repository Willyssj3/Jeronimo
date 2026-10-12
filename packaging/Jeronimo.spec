# -*- mode: python ; coding: utf-8 -*-
# Construcción con PyInstaller (desde packaging/): pyinstaller --noconfirm Jeronimo.spec
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

datas = (
    collect_data_files("traductor", includes=["app/ui/*", "app/ui/fonts/*", "assets/fonts/*", "assets/*.png",
                                              "locales/*.json"])
    + collect_data_files("pdfminer")
    + collect_data_files("pypdfium2_raw")
    + collect_data_files("pypdfium2")
    + collect_data_files("docx")
    + collect_data_files("reportlab")
)
binaries = collect_dynamic_libs("pypdfium2_raw")
hiddenimports = (
    collect_submodules("traductor")
    + collect_submodules("keyring.backends")
    + collect_submodules("winrt")
    + ["win32ctypes.core"]
)

a = Analysis(
    ["lanzador.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Jeronimo",
    console=False,
    icon=os.path.join(SPECPATH, "jeronimo.ico"),
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Jeronimo")
