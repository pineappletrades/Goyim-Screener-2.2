"""The scan engine: ties providers, rules, graders and planners together."""
import threading
import time
import traceback
from datetime import datetime

from . import paths, store, universe
from .plugin_api import FieldUnavailable
from .registry import Registry


class ProviderError(Exception):
    """A data source failed (bad key, network, rate limit). Reported, never treated as 'didn't pass'."""


class Context:
    """Data for one ticker under one strategy profile. Fields are fetched lazily."""

    def __init__(self, engine, ticker, profile, shared):
        self.engine = engine
        self.ticker = ticker
        self.profile = profile
        self.shared = shared                      # {"values": {}, "fetched": set()} shared across profiles
        self.local = {"values": {}, "fetched": set()}

    def get(self, field):
        for bucket in (self.local, self.shared):
            if field in bucket["values"] and bucket["values"][field] is not None:
                return bucket["values"][field]
        provs = self.engine.providers_for(field)
        if not provs:
            raise FieldUnavailable(field)
        # Try each source that supplies this field, best first, until one has a value.
        # (e.g. Finnhub's free plan lacks something -> fall back to Alpha Vantage.)
        for prov in provs:
            bucket = self.local if prov.per_profile else self.shared
            group = prov.group_for(field)
            key = f"{prov.name}:{group}"
            if key not in bucket["fetched"]:
                bucket["fetched"].add(key)
                self.requested_group = group      # lets a provider call only the endpoint it needs
                try:
                    data = prov.fetch(self) or {}
                except ProviderError:
                    raise
                except Exception as e:
                    if len(provs) > 1 and prov is not provs[-1]:
                        self.engine.log(f"{prov.name} ({group}) failed for {self.ticker}, trying next source: {e}")
                        continue
                    raise ProviderError(f"{prov.name}: {e}") from e
                for k, v in data.items():
                    if v is not None or k not in bucket["values"]:
                        bucket["values"][k] = v
            val = bucket["values"].get(field)
            if val is not None:
                return val
        return None

    def peek(self, field, default=None):
        """Value only if already fetched (never triggers an API call)."""
        for bucket in (self.local, self.shared):
            if field in bucket["values"]:
                return bucket["values"][field]
        return default

    def set(self, field, value):
        """Let a rule publish a computed value (e.g. 'entered_zone_today') for display."""
        self.local["values"][field] = value

    def other(self, ticker):
        return Context(self.engine, ticker, self.profile, self.engine.shared_cache(ticker))


class Engine:
    def __init__(self, log=print):
        self.log = log
        self.registry = Registry()
        self.providers = []
        self._cache = {}          # ticker -> shared bucket, reset daily
        self._cache_day = None
        self.status = {"running": False, "done": 0, "total": 0, "current": "", "log": [], "started": None}
        self._stop = threading.Event()
        self._reload_lock = threading.Lock()
        self._field_map = {}
        self.providers = []
        self.reload()

    # ---------- setup ----------
    def reload(self):
        with self._reload_lock:
            settings = store.settings()
            secrets = store.secrets()
            self.registry.load()
            enabled = settings.get("providers_enabled", {})
            providers = []
            for cls in self.registry.provider_classes:
                if enabled.get(cls.name, True):
                    try:
                        providers.append(cls(secrets.get(cls.name, {}), log=self.log))
                    except Exception:
                        self.registry.errors.append((cls.name, traceback.format_exc(limit=2)))
            field_map = {}
            for p in providers:  # already sorted by priority
                try:
                    usable = p.ready()[0]
                except Exception:
                    usable = False
                if not usable:          # no key yet: its features stay greyed out until you add one
                    continue
                for f in p.supplies:
                    field_map.setdefault(f, []).append(p)
            # swap in all at once
            self.settings, self.secrets, self.providers, self._field_map = settings, secrets, providers, field_map
            self._cache = {}

    def providers_with(self, method):
        """Connected (keyed) sources that offer a market-wide method, best first."""
        out = []
        for p in self.providers:
            try:
                if callable(getattr(p, method, None)) and p.ready()[0]:
                    out.append(p)
            except Exception:
                pass
        return out

    def call_first(self, method, *args, **kwargs):
        """Call `method` on the first connected source that has it; fall back to the next on error."""
        errors = []
        for p in self.providers_with(method):
            try:
                return getattr(p, method)(*args, **kwargs), p.name
            except Exception as e:
                errors.append(f"{p.name}: {e}")
        raise RuntimeError("; ".join(errors) if errors else f"no connected data source offers {method}")

    def provider_for(self, field):
        lst = self._field_map.get(field)
        return lst[0] if lst else None

    def providers_for(self, field):
        return self._field_map.get(field, [])

    def field_available(self, field):
        return field in self._field_map

    def is_expensive(self, field, _seen=None):
        _seen = _seen or set()
        p = self.provider_for(field)
        if p is None or field in _seen:
            return False
        _seen.add(field)
        return p.expensive or any(self.is_expensive(n, _seen) for n in p.needs)

    def missing_fields(self, needs):
        missing = []
        for f in needs:
            p = self.provider_for(f)
            if p is None:
                missing.append(f)
            else:
                missing += [m for m in self.missing_fields(p.needs) if m not in missing]
        return missing

    def sources_label(self, fields):
        """'SEC EDGAR' / 'Finnhub or Alpha Vantage' — the sources that could supply these fields."""
        names = []
        for f in fields:
            for cls in self.registry.provider_classes:
                if f in cls.supplies and cls.name not in names and cls.key_fields:
                    names.append(cls.name)
        if not names:
            return "a data source for " + ", ".join(fields)
        return " or ".join(names) if len(names) <= 3 else ", ".join(names[:-1]) + " or " + names[-1]

    def shared_cache(self, ticker):
        today = datetime.now().date()
        if self._cache_day != today:
            self._cache, self._cache_day = {}, today
        return self._cache.setdefault(ticker, {"values": {}, "fetched": set()})

    # ---------- profile helpers ----------
    @staticmethod
    def rule_cfg(profile, rule):
        cfg = profile.get("rules", {}).get(rule.id, {})
        params = {p.id: p.default for p in rule.params}
        params.update(cfg.get("params", {}))
        enabled = cfg.get("enabled", rule.default_enabled)
        return enabled, params

    @staticmethod
    def params_for(obj, saved):
        p = {x.id: x.default for x in obj.params}
        p.update(saved or {})
        return p

    def active_rules(self, profile):
        out = []
        for r in self.registry.rules:
            enabled, params = self.rule_cfg(profile, r)
            if enabled:
                out.append((r, params))
        return out

    # ---------- evaluation ----------
    def evaluate(self, ticker, profile, full=False):
        """Run one profile's rules on one ticker.
        full=True evaluates every rule (used by the detail screen)."""
        ctx = Context(self, ticker, profile, self.shared_cache(ticker))
        res = {"ticker": ticker, "profile": profile["id"], "profile_name": profile.get("name", profile["id"]),
               "status": "fail", "rules": [], "grade": None, "plan": None,
               "fast_label": f"{profile.get('ma_type', 'EMA')} {profile.get('fast', '')}",
               "slow_label": f"{profile.get('ma_type', 'EMA')} {profile.get('slow', '')}"}
        cheap, costly, skipped = [], [], []
        for r, p in self.active_rules(profile):
            missing = self.missing_fields(r.needs)
            if missing:
                skipped.append({"id": r.id, "name": r.name, "group": r.group, "result": "skipped",
                                "detail": "needs " + self.sources_label(missing)})
                continue
            (costly if any(self.is_expensive(f) for f in r.needs) else cheap).append((r, p))

        def run(rule, p):
            try:
                ok, detail = rule.check(ctx, p)
            except ProviderError:
                raise
            except Exception as e:
                ok, detail = False, f"error: {e}"
            near = False
            if not ok:
                try:
                    near = bool(rule.near(ctx, p))
                except ProviderError:
                    raise
                except Exception:
                    near = False
            res["rules"].append({"id": rule.id, "name": rule.name, "group": rule.group,
                                 "result": "pass" if ok else ("near" if near else "fail"), "detail": detail})
            return ok, near

        hard_fail, near_miss = False, False
        for r, p in cheap:
            ok, near = run(r, p)
            if not ok:
                near_miss |= near
                hard_fail |= not near
        stop_early = (hard_fail or near_miss) and not full
        if not stop_early:
            for r, p in costly:
                ok, _ = run(r, p)
                hard_fail |= not ok
        res["rules"] += skipped

        if hard_fail:
            res["status"] = "fail"
        elif near_miss:
            res["status"] = "approaching"
        elif not (cheap or costly):
            res["status"] = "unavailable"      # nothing could be checked: never counts as a pass
        else:
            res["status"] = "setup"
        res["skipped"] = [f"{x['name']} ({x['detail']})" for x in skipped]

        if res["status"] == "setup" or full:
            if self.registry.graders:
                g = self.registry.graders[0]
                try:
                    if not self.missing_fields(g.needs):
                        res["grade"] = g.grade(ctx, self.params_for(g, profile.get("grader")))
                except Exception as e:
                    res["grade_error"] = str(e)
            if self.registry.planners and profile.get("planner", {}).get("enabled", True):
                pl = self.registry.planners[0]
                try:
                    if not self.missing_fields(pl.needs):
                        res["plan"] = pl.plan(ctx, self.params_for(pl, profile.get("planner", {}).get("params")),
                                              float(self.settings.get("account_size", 0)))
                except Exception as e:
                    res["plan_error"] = str(e)

        # Summary fields for display (only what's already been fetched)
        for f in ("close", "dist_fast_pct", "dist_slow_pct", "ma_fast", "ma_slow", "entered_zone_today",
                  "reversal_candle", "revenue_yoy", "eps_last", "market_cap", "latest_quarter_end", "bar_date",
                  "touched_fast", "touched_slow", "fcf_ttm", "eps_yoy_pct"):
            v = ctx.peek(f)
            if v is not None:
                res[f] = _jsonable(v)
        return res, ctx

    # ---------- scanning ----------
    def stop(self):
        self._stop.set()

    def scan(self, tickers=None, profile_ids=None, progress=None, record=True):
        profiles = [p for p in store.profiles() if p.get("enabled", True)]
        if profile_ids:
            profiles = [p for p in profiles if p["id"] in profile_ids]
        self._stop.clear()
        st = self.status
        st.update(running=True, done=0, total=0, current="Building stock lists", log=[],
                  started=datetime.now().isoformat())

        def say(msg):
            st["log"] = (st["log"] + [msg])[-200:]
            self.log(msg)

        out = {"run_at": datetime.now().isoformat(timespec="seconds"), "results": [], "errors": [],
               "profiles": [{"id": p["id"], "name": p.get("name", p["id"])} for p in profiles],
               "market": None, "universes": {}}

        try:
            # Which tickers each strategy scans
            per_profile = {}
            watch = store.watchlist()
            for p in profiles:
                if tickers:
                    per_profile[p["id"]] = list(tickers)
                    continue
                u = p.get("universe") or {}
                if u.get("source") == "all":
                    try:
                        lst, info = universe.build(self, float(u.get("min_market_cap_b", 5)), log=say)
                        per_profile[p["id"]] = lst
                        out["universes"][p["id"]] = info
                    except Exception as e:
                        per_profile[p["id"]] = watch
                        out["universes"][p["id"]] = f"All-stocks list unavailable, used your watchlist instead: {e}"
                        out["errors"].append(f"{p.get('name')}: {e}")
                        say(f"All-stocks list failed: {e}")
                else:
                    per_profile[p["id"]] = watch
                    out["universes"][p["id"]] = f"Watchlist ({len(watch)} tickers)"
            all_tickers, seen = [], set()
            for p in profiles:
                for t in per_profile[p["id"]]:
                    if t not in seen:
                        seen.add(t)
                        all_tickers.append(t)
            members = {pid: set(lst) for pid, lst in per_profile.items()}
            out["scanned"] = len(all_tickers)
            st.update(total=len(all_tickers), current="")
            if self.provider_for("bars") is None:
                out["fatal"] = "No price data source. Add your Public.com key on Data sources."
                all_tickers = []
            out["not_checked"] = {}
            for p in profiles:
                names = []
                for r, _ in self.active_rules(p):
                    miss = self.missing_fields(r.needs)
                    if miss:
                        names.append(f"{r.name} (needs {self.sources_label(miss)})")
                if names:
                    out["not_checked"][p.get("name", p["id"])] = names

            if profiles:
                out["market"] = self.market_regime(profiles[0])
            delay = float(self.settings.get("scan_delay_seconds", 0) or 0)
            provider_fails = []
            for i, t in enumerate(all_tickers):
                if self._stop.is_set():
                    say("Scan stopped.")
                    break
                st["current"] = t
                for prof in profiles:
                    if t not in members[prof["id"]]:
                        continue
                    try:
                        res, _ = self.evaluate(t, prof)
                        if res["status"] == "fail" and len(members[prof["id"]]) > 200:
                            res = {k: res[k] for k in ("ticker", "profile", "profile_name", "status")}
                        out["results"].append(res)
                        if res["status"] != "fail":
                            say(f"{t} [{res['profile_name']}]: {res['status']}"
                                + (f", grade {res['grade']}" if res.get("grade") else ""))
                    except FieldUnavailable as e:
                        out["errors"].append(f"{t}: no provider for '{e}'")
                    except ProviderError as e:
                        out["errors"].append(f"{t}: {e}")
                        say(f"{t}: {e}")
                        provider_fails.append(str(e))
                        break   # same data source would fail for the other strategies too
                    except Exception as e:
                        out["errors"].append(f"{t}: {e}")
                        say(f"{t}: error — {e}")
                st["done"] = i + 1
                if progress:
                    progress(st)
                # Stop early when a data source is clearly broken (e.g. a wrong API key)
                if len(provider_fails) >= 3 and not out["results"] and len(set(provider_fails)) == 1:
                    out["fatal"] = provider_fails[0]
                    say(f"Scan stopped: {provider_fails[0]}")
                    break
                if delay:
                    time.sleep(delay)
            if out["results"]:
                out["data_date"] = next((r.get("bar_date") for r in out["results"] if r.get("bar_date")), None)
            if record:
                store.save("last_scan.json", out)
            say(f"Done: {sum(r['status'] == 'setup' for r in out['results'])} setups, "
                f"{sum(r['status'] == 'approaching' for r in out['results'])} approaching.")
            return out
        finally:
            st["running"] = False
            st["current"] = ""

    def market_regime(self, profile):
        try:
            ctx = Context(self, "SPY", profile, self.shared_cache("SPY"))
            close, slow = ctx.get("close"), ctx.get("ma_slow")
            if close is None or slow is None:
                return None
            return {"above": bool(close > slow), "close": round(float(close), 2), "ma_slow": round(float(slow), 2),
                    "label": f"{profile.get('ma_type', 'EMA')} {profile.get('slow', '')}"}
        except Exception as e:
            self.log(f"Market regime unavailable: {e}")
            return None

    # ---------- detail screen ----------
    def detail(self, ticker, profile_id):
        prof = next((p for p in store.profiles() if p["id"] == profile_id), None) or store.profiles()[0]
        res, ctx = self.evaluate(ticker, prof, full=True)
        out = {"result": res, "chart": None, "fundamentals": None, "overlays": []}
        try:
            bars = ctx.get("bars")
            if bars is not None and len(bars):
                n = 520
                b = bars.iloc[-n:]
                out["chart"] = {
                    "t": [d.strftime("%Y-%m-%d") for d in b.index],
                    "o": _r(b["open"]), "h": _r(b["high"]), "l": _r(b["low"]), "c": _r(b["close"]),
                    "v": [int(x) for x in b["volume"]],
                    "fast": _r(ctx.get("ma_fast_series").iloc[-n:]) if self.field_available("ma_fast_series") else None,
                    "slow": _r(ctx.get("ma_slow_series").iloc[-n:]) if self.field_available("ma_slow_series") else None,
                    "fast_label": f"{prof.get('ma_type', 'EMA')} {prof.get('fast')}",
                    "slow_label": f"{prof.get('ma_type', 'EMA')} {prof.get('slow')}",
                }
                for r, p in self.active_rules(prof):
                    chart_fn = getattr(r, "chart", None)
                    if chart_fn and not self.missing_fields(r.needs):
                        try:
                            ov = chart_fn(ctx, p, n)
                            if ov:
                                out["overlays"].append(_jsonable(ov))
                        except Exception as e:
                            self.log(f"{r.id} chart: {e}")
        except Exception as e:
            out["chart_error"] = str(e)
        fund = {}
        for f in ("revenue_q", "eps_q", "revenue_yoy", "eps_yoy_pct", "market_cap", "shares_outstanding",
                  "next_earnings_date", "fcf_q", "fcf_ttm"):
            if self.field_available(f):
                try:
                    fund[f] = _jsonable(ctx.get(f))
                except Exception as e:
                    fund[f + "_error"] = str(e)
        out["fundamentals"] = fund
        return out


def _r(series):
    return [None if x != x else round(float(x), 4) for x in series]


def _jsonable(v):
    try:
        import numpy as np
        import pandas as pd
        if isinstance(v, (pd.Series, pd.DataFrame)):
            return None
        if isinstance(v, np.generic):
            return v.item()
    except ImportError:
        pass
    if isinstance(v, float) and v != v:
        return None
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v
