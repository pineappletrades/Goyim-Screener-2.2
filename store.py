"""Read/write the JSON files in the data folder."""
import json
import os
import threading

from . import paths

_lock = threading.Lock()

DEFAULT_SETTINGS = {
    "account_size": 28291,
    "theme": "dark",              # dark | light | system
    "telegram_enabled": True,
    "telegram_on_manual": False,
    "telegram_commands": True,
    "gamma_in_alerts": True,      # add a gamma read (walls, flip) to EMA alert messages
    "auto_scan": {"enabled": True, "time": "13:20", "weekdays_only": True},
    "providers_enabled": {},      # provider name -> bool (missing = on)
    "scan_delay_seconds": 0.2,    # pause between tickers to respect API limits
}


def _path(name):
    return paths.DATA_DIR / name


# Your own data (backed up, and kept with a .bak copy on every save). Everything else in data/ is a cache.
USER_FILES = ["settings.json", "secrets.json", "watchlist.txt", "profiles.json", "alerts.json",
              "alert_history.json", "screens.json", "bar_watchlist.json", "last_scan.json", "state.json"]


def load(name, default):
    p = _path(name)
    if not p.exists():
        return json.loads(json.dumps(default))
    for candidate in (p, _path(name + ".bak")):          # if the file got damaged, use the previous copy
        try:
            with open(candidate, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            continue
    return json.loads(json.dumps(default))


def save(name, obj):
    with _lock:
        paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _path(name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())                         # on disk before we swap it in
        if name in USER_FILES and _path(name).exists():
            try:
                import shutil
                shutil.copy2(_path(name), _path(name + ".bak"))
            except Exception:
                pass
        os.replace(tmp, _path(name))


def settings():
    s = load("settings.json", DEFAULT_SETTINGS)
    for k, v in DEFAULT_SETTINGS.items():
        s.setdefault(k, v)
    if os.getenv("ACCOUNT_SIZE"):
        s["account_size"] = float(os.environ["ACCOUNT_SIZE"])
    return s


def secrets():
    """API keys, saved only on this computer. Environment variables override
    (that's how the GitHub backup scan gets its keys)."""
    s = load("secrets.json", {})
    for env, (group, key) in ENV_KEYS.items():
        if os.getenv(env):
            s.setdefault(group, {})[key] = os.environ[env]
    return s


# Environment variable -> (secrets group, key id)
ENV_KEYS = {
    "PUBLIC_API_SECRET_KEY": ("Public.com", "secret_key"),
    "PUBLIC_ACCOUNT_NUMBER": ("Public.com", "account_number"),
    "SEC_USER_AGENT": ("SEC EDGAR", "user_agent"),
    "TELEGRAM_BOT_TOKEN": ("telegram", "bot_token"),
    "TELEGRAM_CHAT_ID": ("telegram", "chat_id"),
}


def profiles():
    return load("profiles.json", [])


def sync_default_profiles():
    """Add strategies that ship with a newer version of the app, once.
    (A strategy you deleted is never re-added.)"""
    try:
        with open(paths.DEFAULTS_DIR / "data" / "profiles.json", encoding="utf-8") as fh:
            defaults = json.load(fh)
    except Exception:
        return []
    user = profiles()
    state = load("state.json", {})
    seen = set(state.get("seen_default_profiles", [p["id"] for p in user]))
    added = []
    for p in defaults:
        if p["id"] not in seen and all(u["id"] != p["id"] for u in user):
            user.insert(0, p)
            added.append(p["name"])
        seen.add(p["id"])
    if added:
        save("profiles.json", user)
    state["seen_default_profiles"] = sorted(seen)
    save("state.json", state)
    return added


def watchlist():
    p = _path("watchlist.txt")
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        t = line.split("#")[0].strip().upper()
        if t and t not in out:
            out.append(t)
    return out


def save_watchlist(tickers):
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    clean = []
    for t in tickers:
        t = str(t).strip().upper()
        if t and t not in clean:
            clean.append(t)
    _path("watchlist.txt").write_text("\n".join(clean) + "\n", encoding="utf-8")
    return clean
