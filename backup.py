"""Backups of your own data (alerts, watchlists, strategies, settings, optionally API keys).

- Automatic: once a day into <data folder>/backups, keeping the last 30.
- Manual: "Back up now" writes a file to Documents\\Goyim Screener Backups (easy to find and copy).
- Restore: from any backup file; the current data is backed up first, so a restore can be undone.
- Import from an old app folder (versions before 2.5 kept data next to the .exe).
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

from . import paths, store

FORMAT = "goyim-screener-backup"
KEEP_AUTO = 30


def _documents_dir():
    home = Path.home()
    for d in (home / "Documents", home / "OneDrive" / "Documents", home):
        if d.is_dir():
            return d / "Goyim Screener Backups"
    return home / "Goyim Screener Backups"


def snapshot(include_keys=True):
    files = {}
    for name in store.USER_FILES:
        if name == "secrets.json" and not include_keys:
            continue
        p = paths.DATA_DIR / name
        if not p.exists():
            continue
        if name.endswith(".txt"):
            files[name] = p.read_text(encoding="utf-8")
        else:
            files[name] = store.load(name, None)
    alerts = files.get("alerts.json") or []
    return {"format": FORMAT, "version": 1, "created": datetime.now().isoformat(timespec="seconds"),
            "includes_keys": include_keys and "secrets.json" in files,
            "summary": {"alerts": len(alerts), "watchlist": len((files.get("watchlist.txt") or "").split()),
                        "strategies": len(files.get("profiles.json") or [])},
            "files": files}


def write(folder: Path, include_keys=True, prefix="backup"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{prefix}-{datetime.now():%Y-%m-%d-%H%M%S}.json"
    path.write_text(json.dumps(snapshot(include_keys), indent=1, default=str), encoding="utf-8")
    return path


def backup_now(include_keys=True):
    return write(_documents_dir(), include_keys)


def auto_backup():
    """At most once a day; keeps the newest KEEP_AUTO files."""
    folder = paths.DATA_DIR / "backups"
    today = datetime.now().strftime("%Y-%m-%d")
    if folder.is_dir() and any(p.name.startswith(f"auto-{today}") for p in folder.glob("auto-*.json")):
        return None
    if not any((paths.DATA_DIR / n).exists() for n in ("alerts.json", "watchlist.txt", "settings.json")):
        return None
    path = write(folder, include_keys=True, prefix="auto")
    for old in sorted(folder.glob("auto-*.json"))[:-KEEP_AUTO]:
        old.unlink(missing_ok=True)
    return path


def list_backups():
    out = []
    for folder, kind in ((_documents_dir(), "manual"), (paths.DATA_DIR / "backups", "auto")):
        if folder.is_dir():
            for p in folder.glob("*.json"):
                out.append({"path": str(p), "name": p.name, "kind": kind,
                            "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")})
    return sorted(out, key=lambda x: x["modified"], reverse=True)


def read(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != FORMAT or not isinstance(data.get("files"), dict):
        raise ValueError("That file isn't a Goyim Screener backup")
    return data


def restore(path, keep_current_keys=True):
    data = read(path)
    write(paths.DATA_DIR / "backups", include_keys=True, prefix="before-restore")   # undo point
    restored = []
    for name, content in data["files"].items():
        if name not in store.USER_FILES:
            continue                                    # never write anything else from a file
        if name == "secrets.json" and keep_current_keys and (paths.DATA_DIR / "secrets.json").exists():
            current = store.load("secrets.json", {})
            merged = {**(content or {}), **current}     # keep keys you already have, add missing ones
            store.save(name, merged)
        elif name.endswith(".txt"):
            paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
            (paths.DATA_DIR / name).write_text(str(content), encoding="utf-8")
        else:
            store.save(name, content)
        restored.append(name)
    return {"restored": restored, "summary": data.get("summary", {}), "created": data.get("created")}


def import_folder(folder):
    """Copy data from an old app folder (the one with Goyim Screener.exe, or its data folder)."""
    folder = Path(folder)
    src = folder / "data" if (folder / "data").is_dir() else folder
    found = [n for n in store.USER_FILES if (src / n).exists()]
    if not found:
        raise ValueError("No Goyim Screener data found there. Pick the folder that has Goyim Screener.exe in it.")
    write(paths.DATA_DIR / "backups", include_keys=True, prefix="before-import")
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    for n in found:
        shutil.copy2(src / n, paths.DATA_DIR / n)
    for sub in ("providers", "rules"):                  # your edited plugin files, if any
        if (folder / sub).is_dir() and folder != paths.HOME:
            shutil.copytree(folder / sub, paths.HOME / sub, dirs_exist_ok=True)
    return found
