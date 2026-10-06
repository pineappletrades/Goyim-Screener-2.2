"""Fundamental constraints. Rules whose data isn't supplied by any connected provider
show as 'needs …' in the app and are skipped in scans."""
from datetime import date

from engine.plugin_api import Param, Rule


def _fmt(xs):
    return ", ".join("n/a" if x is None else f"{x:+.0f}%" for x in xs)


class RevenueGrowth(Rule):
    id = "revenue_growth"
    name = "Revenue growing"
    group = "Fundamentals"
    description = "Revenue up more than X% versus the same quarter last year, for the last N quarters."
    needs = ["revenue_yoy"]
    params = [
        Param("min_pct", "Growth above", 10.0, unit="% YoY", min=-100, max=500, step=1),
        Param("quarters", "For the last", 1, unit="quarters", min=1, max=4, step=1),
    ]

    def check(self, ctx, p):
        yoy = ctx.get("revenue_yoy")
        if not yoy:
            return False, "no revenue data"
        last = yoy[: int(p["quarters"])]
        ok = all(x is not None and x > p["min_pct"] for x in last)
        return ok, f"YoY {_fmt(yoy)}"


class EpsPositive(Rule):
    id = "eps_positive"
    name = "EPS positive"
    group = "Fundamentals"
    description = "The latest quarter was profitable."
    needs = ["eps_last"]

    def check(self, ctx, p):
        e = ctx.get("eps_last")
        if e is None:
            return False, "no EPS data"
        return e > 0, f"latest EPS {e:.2f}"


class EpsUpYoY(Rule):
    id = "eps_up_yoy"
    name = "EPS up year over year"
    group = "Fundamentals"
    description = "EPS beat the same quarter last year in at least N of the last 4 quarters (latest must be up)."
    needs = ["eps_yoy_up"]
    params = [Param("min_quarters", "Up in at least", 1, unit="of last 4 quarters", min=1, max=4, step=1)]

    def check(self, ctx, p):
        ups = ctx.get("eps_yoy_up")
        if not ups or ups[0] is None:
            return False, "no EPS history"
        count = sum(1 for u in ups[:4] if u)
        return bool(ups[0]) and count >= p["min_quarters"], f"up YoY in {count} of last 4"


class EpsUpSequential(Rule):
    id = "eps_up_seq"
    name = "EPS rising quarter over quarter"
    group = "Fundamentals"
    description = "Each of the last N quarters' EPS beat the quarter before it."
    needs = ["eps_q"]
    params = [Param("quarters", "Rising for", 2, unit="quarters", min=1, max=6, step=1)]
    default_enabled = False

    def check(self, ctx, p):
        q = [x["val"] for x in (ctx.get("eps_q") or [])]
        n = int(p["quarters"])
        if len(q) < n + 1:
            return False, "not enough EPS history"
        ok = all(q[i] > q[i + 1] for i in range(n))
        return ok, "EPS " + " ← ".join(f"{x:.2f}" for x in q[: n + 1])


class NoEarningsSoon(Rule):
    id = "no_earnings_soon"
    name = "No earnings soon"
    group = "Fundamentals"
    description = "Skip stocks reporting earnings within N days (avoids holding a swing through the report)."
    needs = ["next_earnings_date"]
    params = [Param("min_days", "Next earnings at least", 14, unit="days away", min=0, max=90, step=1)]

    def check(self, ctx, p):
        d = ctx.get("next_earnings_date")
        if not d:
            return True, "no upcoming date found"
        days = (date.fromisoformat(str(d)[:10]) - date.today()).days
        return days >= p["min_days"], f"earnings in {days} days ({d})"


class MaxPE(Rule):
    id = "max_pe"
    name = "P/E not too high"
    group = "Fundamentals"
    description = "Trailing P/E at or below a limit."
    needs = ["pe_ratio"]
    params = [Param("max", "P/E at most", 40.0, min=0, step=1)]
    default_enabled = False

    def check(self, ctx, p):
        pe = ctx.get("pe_ratio")
        if pe is None:
            return False, "no P/E data"
        return 0 < pe <= p["max"], f"P/E {pe:.1f}"


class PositiveFCF(Rule):
    id = "fcf_positive"
    name = "Free cash flow positive"
    group = "Fundamentals"
    description = "Operating cash flow minus capital spending over the last 4 quarters is above zero."
    needs = ["fcf_ttm"]
    default_enabled = False

    def check(self, ctx, p):
        f = ctx.get("fcf_ttm")
        if f is None:
            return False, "no cash-flow data"
        return f > 0, f"FCF last 4 quarters ${f / 1e9:,.2f}B"


class MaxForwardPE(Rule):
    id = "max_forward_pe"
    name = "Forward P/E not too high"
    group = "Fundamentals"
    description = "Price divided by analysts' expected EPS for the next 12 months, at or below a limit."
    needs = ["forward_pe"]
    params = [Param("max", "Forward P/E at most", 30.0, min=0, step=1)]
    default_enabled = False

    def check(self, ctx, p):
        pe = ctx.get("forward_pe")
        if pe is None:
            return False, "no forward P/E data"
        return 0 < pe <= p["max"], f"forward P/E {pe:.1f}"


class MinAnalystRating(Rule):
    id = "analyst_rating"
    name = "Analysts rate it a buy"
    group = "Fundamentals"
    description = "Average analyst rating at or above a level (5 = strong buy, 3 = hold, 1 = strong sell)."
    needs = ["analyst_score"]
    params = [Param("min", "Rating at least", "Buy", kind="choice", choices=["Strong buy", "Buy", "Hold"])]
    default_enabled = False

    def check(self, ctx, p):
        s = ctx.get("analyst_score")
        if s is None:
            return False, "no analyst ratings"
        need = {"Strong buy": 4.5, "Buy": 3.5, "Hold": 2.5}[p["min"]]
        return s >= need, f"average rating {s:.1f} of 5"
