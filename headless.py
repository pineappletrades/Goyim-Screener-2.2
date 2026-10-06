"""Backup scan for GitHub Actions: same engine and plugins, keys from environment variables.

Uses the strategy files in app/defaults (edit those in the repo to change the backup's rules).
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("EMA_ZONE_HOME", str(HERE / "defaults"))
sys.path.insert(0, str(HERE))

from engine import notify, store  # noqa: E402
from engine.scanner import Engine  # noqa: E402


def main():
    dry = "--dry-run" in sys.argv
    eng = Engine()
    if eng.registry.errors:
        for f, err in eng.registry.errors:
            print(f"Plugin error in {f}: {err}")
    out = eng.scan(record=False)
    msg = notify.build_message(out)
    if dry:
        print(msg)
        return 0
    tg = store.secrets().get("telegram", {})
    notify.send(tg.get("bot_token"), tg.get("chat_id"), msg)
    print("Sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
