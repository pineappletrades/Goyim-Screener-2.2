"""Where everything lives.

Installed .exe:  %APPDATA%\\Goyim Screener\\providers, \\rules, \\data  — the same folder for every version, so
                 your alerts, watchlist, keys and settings stay when you download a new build.
                 (Put an empty file named portable.txt next to the .exe to keep data next to the .exe instead.)
Source run:      <repo>/app/user/...   (created on first run from app/defaults)
Override:        set EMA_ZONE_HOME to use any folder (the GitHub backup scan uses app/defaults)
"""
import os
import shutil
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

# Read-only files shipped with the app (UI, default plugins)
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
DEFAULTS_DIR = BUNDLE_DIR / "defaults"
UI_DIR = BUNDLE_DIR / "ui"

EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else None


def _app_data_home():
    base = os.getenv("APPDATA") or os.getenv("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Goyim Screener"


if os.getenv("EMA_ZONE_HOME"):
    HOME = Path(os.environ["EMA_ZONE_HOME"]).resolve()
elif FROZEN:
    HOME = EXE_DIR if (EXE_DIR / "portable.txt").exists() else _app_data_home()
else:
    HOME = Path(__file__).resolve().parent.parent / "user"

PROVIDERS_DIR = HOME / "providers"
RULES_DIR = HOME / "rules"
DATA_DIR = HOME / "data"


def _hash(p: Path) -> str:
    import hashlib

    return hashlib.sha256(p.read_bytes()).hexdigest()


# Fingerprints of plugin files shipped in earlier versions (so unedited copies update automatically)
SHIPPED_HASHES = {
    "02350738d06bdabd6ce8b739429707f4e1b8882681a37ae10f9bd1fb8d6b5740",
    "1be413ad6b72c579ccc5246493c654a0e62305c6609ff084b5b725ddd2ca36ff",
    "2ed1ab9455ae21ce8677cffe0d15735277cd0599d26ef49a92cfbe1612feb6a7",
    "51b6e7ad3a11adb71866d60efcf5803ea7a5c08e22ca7c9857902946913a2e5c",
    "5b96072655854e3e544f5c7346b4407b46e11defc9698384a4fc242c6ab7f550",
    "6216d5601d2a7485dbbb183b6a94a8fde1074070bf658f5babc1f0e3fd4c3c7c",
    "735d29a603c97c190aed8f9dcfe7c31f31ca0c3fca694e65b238111b61cac951",
    "8824e3a3d48cfc473659d1669bfd172911969b382c005ada92dbfd1996a0dbe9",
}


def ensure_user_files() -> list:
    """Copy default files that don't exist yet, and update default plugin files you haven't edited.

    Files you've changed are never overwritten; the newer default is saved next to yours as
    <name>.py.new so you can compare.
    """
    import json

    changed = []
    if HOME.resolve() == DEFAULTS_DIR.resolve():
        return changed
    record_path = HOME / "data" / "plugin_versions.json"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except Exception:
        record = {}
    for sub in ("providers", "rules", "data"):
        src_root = DEFAULTS_DIR / sub
        if not src_root.exists():
            continue
        for src in src_root.rglob("*"):
            if src.is_dir() or "__pycache__" in src.parts:
                continue
            rel = str(Path(sub) / src.relative_to(src_root))
            dst = HOME / rel
            new_hash = _hash(src)
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                record[rel] = new_hash
                changed.append(rel)
            elif sub != "data" and record.get(rel) != new_hash:
                cur = _hash(dst)
                if cur == new_hash:
                    pass                                              # already the newest version
                elif cur in (record.get(rel), *SHIPPED_HASHES):       # still an old default: safe to update
                    shutil.copy2(src, dst)
                    changed.append(rel + " (updated)")
                else:                                                 # you edited it: keep yours
                    shutil.copy2(src, dst.with_suffix(dst.suffix + ".new"))
                    changed.append(rel + " (kept your edits; new version saved as .new)")
                record[rel] = new_hash
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return changed


def restore_default(rel_path: str) -> None:
    """Overwrite one plugin file with the shipped default (keeps a .bak of yours)."""
    src = DEFAULTS_DIR / rel_path
    dst = HOME / rel_path
    if dst.exists():
        shutil.copy2(dst, dst.with_suffix(dst.suffix + ".bak"))
    shutil.copy2(src, dst)


def migrate_from_exe_folder():
    """Versions before 2.5 kept data next to the .exe. If this copy of the app still has that folder and the
    shared data folder is empty, move the data over once so nothing is lost."""
    if not FROZEN or EXE_DIR is None or HOME == EXE_DIR:
        return []
    if (HOME / "data" / "settings.json").exists() or not (EXE_DIR / "data").is_dir():
        return []
    copied = []
    for sub in ("data", "providers", "rules"):
        src = EXE_DIR / sub
        if src.is_dir():
            shutil.copytree(src, HOME / sub, dirs_exist_ok=True)
            copied.append(sub)
    return copied
