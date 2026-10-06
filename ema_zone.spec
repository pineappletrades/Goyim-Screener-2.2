# PyInstaller recipe for "Goyim Screener.exe" (one-folder build).
# Built automatically by .github/workflows/build-windows.yml, or locally with build\build.bat
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
APP = os.path.join(ROOT, "app")

hidden = (
    collect_submodules("public_api_sdk")
    + collect_submodules("engine")
    + collect_submodules("webview")
    + ["pandas", "numpy", "requests", "pydantic", "httpx", "dotenv", "zoneinfo", "tzdata"]
    # Standard-library modules your own plugin files might import later.
    # (PyInstaller only packs what the app itself imports, so these are listed explicitly.)
    + ["json", "csv", "math", "statistics", "decimal", "fractions", "re", "datetime", "zoneinfo",
       "collections", "itertools", "functools", "typing", "dataclasses", "urllib.parse",
       "urllib.request", "http.client", "concurrent.futures", "sqlite3", "xml.etree.ElementTree"]
)

ui_files = [(os.path.join(APP, "ui", f), "ui") for f in os.listdir(os.path.join(APP, "ui"))
            if f != "mock.js"]

a = Analysis(
    [os.path.join(APP, "desktop.py")],
    pathex=[APP],
    datas=ui_files + [(os.path.join(APP, "defaults"), "defaults")] + collect_data_files("tzdata"),
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Goyim Screener",
    console=False,
    icon=os.path.join(SPECPATH, "icon.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Goyim Screener")
