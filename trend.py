"""Trend and EMA-zone rules. Each class is one switchable constraint in the app."""
from engine.plugin_api import Param, Rule


class MaStacked(Rule):
    id = "ma_stacked"
    name = "Fast MA above slow MA"
    group = "Trend"
    description = "The uptrend is intact: the fast moving average sits above the slow one."
    needs = ["ma_fast", "ma_slow"]

    def check(self, ctx, p):
        f, s = ctx.get("ma_fast"), ctx.get("ma_slow")
        return f > s, f"fast {f:.2f} vs slow {s:.2f}"


class SlowRising(Rule):
    id = "slow_rising"
    name = "Slow MA rising"
    group = "Trend"
    description = "The slow moving average is higher than it was N days ago (a rising floor)."
    needs = ["ma_slow_series"]
    params = [Param("lookback", "Compare with N days ago", 20, unit="days", min=1, max=250, step=1)]

    def check(self, ctx, p):
        s = ctx.get("ma_slow_series")
        n = int(p["lookback"])
        now, then = s.iloc[-1], s.iloc[-1 - n]
        chg = (now / then - 1) * 100
        return now > then, f"{chg:+.2f}% over {n} days"


class PulledBack(Rule):
    id = "pulled_back"
    name = "Real pullback (was extended)"
    group = "Trend"
    description = "Price was at least X% above the fast MA recently, so this is a pullback, not a sideways chop."
    needs = ["dist_fast_series"]
    params = [
        Param("min_extension", "Was at least", 8.0, unit="% above fast MA", min=0, max=100, step=0.5),
        Param("lookback", "Within the last", 60, unit="days", min=5, max=250, step=1),
    ]

    def check(self, ctx, p):
        d = ctx.get("dist_fast_series").iloc[-int(p["lookback"]):]
        mx = d.max()
        return mx >= p["min_extension"], f"max {mx:+.1f}% above fast MA in {int(p['lookback'])}d"


class InZone(Rule):
    id = "in_zone"
    name = "Price in the buy zone"
    group = "Trend"
    description = "Today's low touched the fast MA (within X%) and the close held above the slow MA (minus Y%)."
    needs = ["low", "close", "ma_fast", "ma_slow", "ma_fast_series", "ma_slow_series", "bars"]
    params = [
        Param("above_fast", "Zone top: above fast MA", 2.0, unit="%", min=0, max=20, step=0.5),
        Param("below_slow", "Zone bottom: below slow MA", 2.0, unit="%", min=0, max=20, step=0.5),
        Param("approach", "'Approaching' if within", 6.0, unit="% above fast MA", min=0, max=30, step=0.5),
    ]

    def _flags(self, ctx, p):
        bars = ctx.get("bars")
        fast, slow = ctx.get("ma_fast_series"), ctx.get("ma_slow_series")
        return (bars["low"] <= fast * (1 + p["above_fast"] / 100)) & (bars["close"] >= slow * (1 - p["below_slow"] / 100))

    def check(self, ctx, p):
        flags = self._flags(ctx, p)
        ok = bool(flags.iloc[-1])
        ctx.set("entered_zone_today", bool(ok and not flags.iloc[-2]))
        low, fast, slow = ctx.get("low"), ctx.get("ma_fast"), ctx.get("ma_slow")
        level = "slow MA" if low < slow * (1 + p["below_slow"] / 100) else "fast MA"
        if ok:
            return True, f"touching the {level}"
        return False, f"low {(low / fast - 1) * 100:+.1f}% vs fast MA"

    def near(self, ctx, p):
        d = ctx.get("dist_fast_pct")
        return p["above_fast"] < d <= p["approach"]

    def chart(self, ctx, p, n):
        """Shade the zone and mark zone entries / reversal candles on the detail chart."""
        fast, slow = ctx.get("ma_fast_series").iloc[-n:], ctx.get("ma_slow_series").iloc[-n:]
        flags = self._flags(ctx, p).iloc[-n:]
        rev = ctx.get("reversal_series").iloc[-n:] if ctx.engine.field_available("reversal_series") else None
        entries = [i for i in range(1, len(flags)) if flags.iloc[i] and not flags.iloc[i - 1]]
        revs = [i for i in range(len(flags)) if flags.iloc[i] and rev is not None and rev.iloc[i]]
        return {
            "band": {"upper": [round(float(x), 4) for x in fast * (1 + p["above_fast"] / 100)],
                     "lower": [round(float(x), 4) for x in slow * (1 - p["below_slow"] / 100)],
                     "label": "Buy zone"},
            "markers": [{"i": i, "kind": "entry", "label": "Entered zone"} for i in entries]
                       + [{"i": i, "kind": "reversal", "label": "Reversal candle in zone"} for i in revs],
        }


class ReversalToday(Rule):
    id = "reversal_today"
    name = "Reversal candle today"
    group = "Trend"
    description = "Only alert when today's candle is green and closed in the top third of its range."
    needs = ["reversal_candle"]
    default_enabled = False

    def check(self, ctx, p):
        r = ctx.get("reversal_candle")
        return r, "reversal candle" if r else "no reversal candle"


class MarketRegime(Rule):
    id = "market_regime"
    name = "SPY above its slow MA"
    group = "Market"
    description = "Only take setups when the overall market (SPY) is above its slow moving average."
    needs = ["close", "ma_slow"]
    default_enabled = False

    def check(self, ctx, p):
        spy = ctx.other("SPY")
        c, s = spy.get("close"), spy.get("ma_slow")
        return c > s, f"SPY {c:.2f} vs {s:.2f}"


class MaTouch(Rule):
    """Investing-style alert: an uptrending stock pulls back and touches the fast or slow MA.

    Each line alerts separately: one alert the day price first touches the fast MA, another
    the day it first touches the slow MA.
    """
    id = "ma_touch"
    name = "Pullback touches the fast or slow MA"
    group = "Trend"
    description = ("Price has been trading above both averages, then today's low touched one of them "
                   "(each line alerts on its own).")
    needs = ["bars", "ma_fast_series", "ma_slow_series", "dist_fast_pct"]
    default_enabled = False
    params = [
        Param("lines", "Alert on", "Both (separate alerts)", kind="choice",
              choices=["Both (separate alerts)", "Fast MA only", "Slow MA only"]),
        Param("tolerance", "Touch counts within", 0.5, unit="% above the line", min=0, max=5, step=0.1),
        Param("above_days", "Closed above both lines on", 15, unit="of the prior 20 days", min=0, max=20, step=1),
        Param("hold", "Close must hold at or above the touched line", True, kind="bool"),
        Param("new_only", "Only the first day of a touch", True, kind="bool"),
        Param("approach", "'Approaching' if within", 3.0, unit="% above fast MA", min=0, max=20, step=0.5),
    ]

    def _series(self, ctx, p):
        bars = ctx.get("bars")
        fast, slow = ctx.get("ma_fast_series"), ctx.get("ma_slow_series")
        tol = 1 + p["tolerance"] / 100
        lines = {}
        if p["lines"] != "Slow MA only":
            lines["fast"] = fast
        if p["lines"] != "Fast MA only":
            lines["slow"] = slow
        touched = {}
        for k, line in lines.items():
            t = bars["low"] <= line * tol
            if p["hold"]:
                t &= bars["close"] >= line
            touched[k] = t
        above_both = (bars["close"] > fast) & (bars["close"] > slow)
        uptrend = above_both.shift(1).rolling(20).sum() >= p["above_days"]
        return bars, fast, slow, touched, uptrend

    def check(self, ctx, p):
        bars, fast, slow, touched, uptrend = self._series(ctx, p)
        hits = []
        for k, t in touched.items():
            today = bool(t.iloc[-1])
            if p["new_only"]:
                today = today and not bool(t.iloc[-2])
            ctx.set(f"touched_{k}", today and bool(uptrend.iloc[-1]))
            if today:
                hits.append(k)
        ok = bool(uptrend.iloc[-1]) and bool(hits)
        if not uptrend.iloc[-1]:
            days = int(((bars["close"] > fast) & (bars["close"] > slow)).iloc[-21:-1].sum())
            return False, f"above both lines on only {days} of the prior 20 days"
        if not hits:
            low = bars["low"].iloc[-1]
            return False, f"low {(low / fast.iloc[-1] - 1) * 100:+.1f}% vs fast, {(low / slow.iloc[-1] - 1) * 100:+.1f}% vs slow"
        names = {"fast": "fast MA", "slow": "slow MA"}
        return True, "touched the " + " and ".join(names[h] for h in hits)

    def near(self, ctx, p):
        bars, fast, slow, touched, uptrend = self._series(ctx, p)
        return bool(uptrend.iloc[-1]) and 0 < ctx.get("dist_fast_pct") <= p["approach"]

    def chart(self, ctx, p, n):
        bars, fast, slow, touched, uptrend = self._series(ctx, p)
        marks = []
        for k, t in touched.items():
            t = (t & uptrend).iloc[-n:]
            prev = t.shift(1, fill_value=False)
            for i in range(len(t)):
                if t.iloc[i] and (not p["new_only"] or not prev.iloc[i]):
                    marks.append({"i": i, "kind": f"touch_{k}",
                                  "label": f"Touched {'fast' if k == 'fast' else 'slow'} MA"})
        return {"markers": marks, "legend": {"touch_fast": "Touched fast MA", "touch_slow": "Touched slow MA"}}
