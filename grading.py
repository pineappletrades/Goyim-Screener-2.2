"""How passing stocks are graded, and the swing trade plan."""
from engine.plugin_api import Grader, Param, Planner


class GrowthGrader(Grader):
    name = "Growth grade"
    needs = ["revenue_yoy", "eps_yoy_pct"]
    params = [
        Param("a_revenue", "Grade A: revenue growth above", 20.0, unit="% YoY", min=0, step=1),
        Param("a_needs_accel", "Grade A: EPS growth accelerating", True, kind="bool"),
    ]

    def grade(self, ctx, p):
        rev, eps = ctx.get("revenue_yoy") or [], ctx.get("eps_yoy_pct") or []
        strong = bool(rev) and rev[0] is not None and rev[0] > p["a_revenue"]
        accel = len(eps) > 1 and eps[0] is not None and eps[1] is not None and eps[0] > eps[1]
        if strong and (accel or not p["a_needs_accel"]):
            return "A"
        return "B"


class SwingPlan(Planner):
    name = "Swing plan"
    needs = ["high", "low", "ma_slow", "atr"]
    params = [
        Param("risk_pct", "Risk per trade", 1.0, unit="% of account", min=0.1, max=10, step=0.1),
        Param("atr_buffer", "Stop buffer", 0.5, unit="× ATR below low / slow MA", min=0, max=3, step=0.1),
    ]

    def plan(self, ctx, p, account_size):
        high, low, slow, atr = ctx.get("high"), ctx.get("low"), ctx.get("ma_slow"), ctx.get("atr")
        entry = round(high + 0.01, 2)
        stop = round(min(low, slow) - p["atr_buffer"] * atr, 2)
        risk = entry - stop
        if risk <= 0:
            return None
        dollars = account_size * p["risk_pct"] / 100
        return {"entry": entry, "stop": stop, "risk_per_share": round(risk, 2),
                "shares": int(dollars // risk), "risk_dollars": round(dollars, 2),
                "target_2r": round(entry + 2 * risk, 2), "target_3r": round(entry + 3 * risk, 2)}
