"""The building blocks that files in the `providers` and `rules` folders use.

  Provider  — supplies data fields for a ticker (from an API, or computed from other fields)
  Rule      — a pass/fail constraint on those fields, with adjustable parameters
  Grader    — turns a passing stock into a grade (A/B/C)
  Planner   — builds a trade plan (entry, stop, size, targets)

Any .py file in those folders is loaded at startup. Files starting with "_" are ignored
(use that for templates or to switch a file off).
"""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Param:
    """An adjustable setting shown in the app."""
    id: str
    label: str
    default: Any
    kind: str = "number"          # number | bool | choice | text
    unit: str = ""
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    choices: list = field(default_factory=list)
    help: str = ""

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class KeyField:
    """A credential a provider needs (shown on the Data sources screen)."""
    id: str
    label: str
    secret: bool = True
    help: str = ""

    def to_dict(self):
        return dict(self.__dict__)


class Provider:
    name: str = ""                  # unique, shown in the app
    description: str = ""
    supplies: list = []             # field names this provider returns
    needs: list = []                # fields it needs from other providers (for computed data)
    key_fields: list = []           # list[KeyField]
    expensive: bool = False         # True = only fetched after the cheap (price) rules pass
    per_profile: bool = False       # True = output depends on strategy settings (e.g. EMA lengths)
    priority: int = 50              # when two providers supply a field, lower number wins
    signup_url: str = ""
    groups: dict = {}               # optional {group: [fields]} so one API call per group, only when needed

    def group_for(self, field):
        for g, fields in self.groups.items():
            if field in fields:
                return g
        return "all"

    def __init__(self, keys: dict, log=print):
        self.keys = keys or {}
        self.log = log

    def ready(self):
        """Return (ok, message). Default: all key fields filled in."""
        missing = [k.label for k in self.key_fields if not self.keys.get(k.id)]
        return (not missing, "Missing: " + ", ".join(missing) if missing else "Ready")

    def fetch(self, ctx) -> dict:
        """Return {field: value} for ctx.ticker. Use ctx.get('field') for needed fields
        and ctx.profile for strategy settings."""
        raise NotImplementedError


class Rule:
    id: str = ""
    name: str = ""
    group: str = "Other"            # Trend | Liquidity | Fundamentals | Market | Other
    description: str = ""
    needs: list = []
    params: list = []               # list[Param]
    default_enabled: bool = True

    def check(self, ctx, p: dict):
        """Return (passed: bool, detail: str). p = this rule's parameter values."""
        raise NotImplementedError

    def near(self, ctx, p: dict) -> bool:
        """Optional: True when the stock is close to passing (used for 'Approaching')."""
        return False


class Grader:
    name: str = ""
    needs: list = []
    params: list = []

    def grade(self, ctx, p: dict) -> str:
        return "B"


class Planner:
    name: str = ""
    needs: list = []
    params: list = []

    def plan(self, ctx, p: dict, account_size: float) -> Optional[dict]:
        return None


class FieldUnavailable(Exception):
    """Raised when no enabled provider can supply a field."""
