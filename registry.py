"""Load providers and rules from their folders."""
import importlib.util
import inspect
import sys
import traceback
from pathlib import Path

from . import paths
from .plugin_api import Grader, Planner, Provider, Rule


class Registry:
    def __init__(self):
        self.provider_classes = []   # classes, sorted by priority
        self.rules = []              # Rule instances, in load order
        self.graders = []
        self.planners = []
        self.errors = []             # (file, message)

    def load(self):
        """Load into a fresh registry, then swap everything in at once, so another thread
        (e.g. the auto-scan) never sees a half-loaded, empty rule list."""
        new = Registry()
        for folder in (paths.PROVIDERS_DIR, paths.RULES_DIR):
            if not folder.exists():
                continue
            for f in sorted(folder.glob("*.py")):
                if f.name.startswith("_"):
                    continue
                new._load_file(f)
        new.provider_classes.sort(key=lambda c: (c.priority, c.name))
        seen = set()
        for r in new.rules:
            if r.id in seen:
                new.errors.append((r.id, f"Duplicate rule id '{r.id}'; the later one is ignored"))
            seen.add(r.id)
        new.rules = _dedupe(new.rules)
        self.provider_classes, self.rules, self.graders, self.planners, self.errors = (
            new.provider_classes, new.rules, new.graders, new.planners, new.errors)
        return self

    def _load_file(self, f: Path):
        mod_name = f"ema_plugin_{f.parent.name}_{f.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, f)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        except Exception:
            self.errors.append((f.name, traceback.format_exc(limit=3)))
            return
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if obj.__module__ != mod_name:
                continue
            try:
                if issubclass(obj, Provider) and obj is not Provider and obj.name:
                    self.provider_classes.append(obj)
                elif issubclass(obj, Rule) and obj is not Rule and obj.id:
                    self.rules.append(obj())
                elif issubclass(obj, Grader) and obj is not Grader:
                    self.graders.append(obj())
                elif issubclass(obj, Planner) and obj is not Planner:
                    self.planners.append(obj())
            except Exception:
                self.errors.append((f.name, traceback.format_exc(limit=3)))

    def rule(self, rule_id):
        return next((r for r in self.rules if r.id == rule_id), None)


def _dedupe(rules):
    out, seen = [], set()
    for r in rules:
        if r.id not in seen:
            out.append(r)
            seen.add(r.id)
    return out
