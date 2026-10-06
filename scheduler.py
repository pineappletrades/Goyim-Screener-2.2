"""Runs the scan automatically after the close while the app is open."""
import threading
import time
from datetime import datetime

from . import store


class AutoScanner:
    def __init__(self, run_scan, log=print):
        self.run_scan = run_scan        # callable(send_telegram: bool)
        self.log = log
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()

    def next_run_text(self):
        cfg = store.settings().get("auto_scan", {})
        if not cfg.get("enabled"):
            return "Auto-scan off"
        state = store.load("state.json", {})
        today = datetime.now().strftime("%Y-%m-%d")
        when = cfg.get("time", "13:20")
        if state.get("last_auto_scan") == today:
            return f"Auto-scan done today · next at {when}"
        return f"Auto-scan today at {when}"

    def _due(self):
        cfg = store.settings().get("auto_scan", {})
        if not cfg.get("enabled"):
            return False
        now = datetime.now()
        if cfg.get("weekdays_only", True) and now.weekday() >= 5:
            return False
        try:
            hh, mm = [int(x) for x in cfg.get("time", "13:20").split(":")]
        except ValueError:
            return False
        if (now.hour, now.minute) < (hh, mm):
            return False
        return store.load("state.json", {}).get("last_auto_scan") != now.strftime("%Y-%m-%d")

    def _loop(self):
        while True:
            try:
                if self._due():
                    state = store.load("state.json", {})
                    state["last_auto_scan"] = datetime.now().strftime("%Y-%m-%d")
                    store.save("state.json", state)
                    self.log("Auto-scan starting")
                    self.run_scan(True)
            except Exception as e:
                self.log(f"Auto-scan error: {e}")
            time.sleep(30)
