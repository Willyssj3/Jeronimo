# -*- mode: python ; coding: utf-8 -*-
# Construcción con PyInstaller: pyinstaller --noconfirm packaging/TraductorDeLibros.spec
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

datas = (
    collect_data_files("traductor", includes=["app/ui/*"])
    + collect_data_files("pdfminer")
    + collect_data_files("pypdfium2_raw")
    + collect_data_files("pypdfium2")
)
binaries = collect_dynamic_libs("pypdfium2_raw")
hiddenimports = (
    collect_submodules("traductor")
    + collect_submodules("keyring.backends")
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
    name="TraductorDeLibros",
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="TraductorDeLibros")
