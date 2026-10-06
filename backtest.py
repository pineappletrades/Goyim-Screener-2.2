"""Backtest: every time a stock pulled back to its 150/200 EMA, and what happened next.

Uses the same touch logic as the Investing strategy's "Pullback touches the fast or slow MA" rule:
  - uptrend: closed above BOTH lines on at least `above_days` of the prior 20 days
  - touch:   the day's low came within `tolerance`% above the line (and, if `hold`, closed at or above it)
  - each line is its own event; a new event needs `cooldown` days without a touch of that line
Also finds "zone" visits: the close sat between the two lines.

Daily bars only (Public gives 5 years of daily data). The first `slow` days are used to warm up the averages.
"""
import math

HORIZONS = [5, 20, 60, 120, 252]          # trading days: 1 week, 1 month, 3 months, 6 months, 1 year


def _ma(s, n, kind):
    return s.rolling(n).mean() if kind == "SMA" else s.ewm(span=n, adjust=False).mean()


def _r(x, d=2):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(float(x), d)


def run(bars, fast=150, slow=200, kind="EMA", tolerance=0.5, above_days=15, hold=True, cooldown=10,
        mode="touch", stop_pct=None):
    """mode: "touch" (low touched a line in an uptrend) | "zone" (close between the lines, any trend)."""
    import pandas as pd

    if bars is None or len(bars) < slow + 30:
        raise ValueError(f"Need at least {slow + 30} days of daily prices (have {0 if bars is None else len(bars)})")
    c, lo, hi = bars["close"], bars["low"], bars["high"]
    f, s = _ma(c, fast, kind), _ma(c, slow, kind)
    warm = slow                                  # ignore the period where the averages are still settling
    tol = 1 + tolerance / 100
    above_both = (c > f) & (c > s)
    uptrend = above_both.shift(1).rolling(20).sum() >= above_days

    raw = {}
    if mode == "zone":
        top, bot = pd.concat([f, s], axis=1).max(axis=1), pd.concat([f, s], axis=1).min(axis=1)
        raw["zone"] = (c <= top) & (c >= bot)
    else:
        for key, line in (("fast", f), ("slow", s)):
            t = lo <= line * tol
            if hold:
                t &= c >= line
            raw[key] = t & uptrend

    n = len(c)
    events = []
    for key, mask in raw.items():
        last = -10 ** 9
        vals = mask.values
        for i in range(warm, n):
            if not vals[i]:
                continue
            if i - last > cooldown:
                events.append((i, key))
            last = i                              # a run of touch days counts once
    events.sort()

    out = []
    cv, lv, hv = c.values, lo.values, hi.values
    fv, sv = f.values, s.values
    for i, key in events:
        entry = float(cv[i])
        line = fv[i] if key == "fast" else sv[i] if key == "slow" else None
        ev = {"date": c.index[i].strftime("%Y-%m-%d"), "i": int(i), "line": key,
              "price": _r(entry), "ma_fast": _r(fv[i]), "ma_slow": _r(sv[i]),
              "low": _r(lv[i]), "dist_line_pct": _r((entry / line - 1) * 100) if line else None,
              "returns": {}, "complete": {}}
        for h in HORIZONS:
            j = i + h
            if j < n:
                ev["returns"][str(h)] = _r((cv[j] / entry - 1) * 100)
                ev["complete"][str(h)] = True
            else:
                ev["returns"][str(h)] = None
        # worst drop and best gain over the next 60 trading days (intraday lows/highs)
        j = min(n, i + 61)
        if j > i + 1:
            ev["max_drop_60"] = _r((min(lv[i + 1:j]) / entry - 1) * 100)
            ev["max_gain_60"] = _r((max(hv[i + 1:j]) / entry - 1) * 100)
            # did it close more than 3% below the slow line within 20 days (the support "broke")?
            k = min(n, i + 21)
            ev["broke_slow_20"] = bool(any(cv[x] < sv[x] * 0.97 for x in range(i + 1, k)))
        else:
            ev["max_drop_60"] = ev["max_gain_60"] = None
            ev["broke_slow_20"] = None
        if stop_pct:
            stop = (line or entry) * (1 - stop_pct / 100)
            hit = next((x for x in range(i + 1, min(n, i + 253)) if lv[x] <= stop), None)
            ev["stopped_days"] = (hit - i) if hit else None
        out.append(ev)

    # baseline: buying on ANY day after warm-up, same horizons (what the touches are compared against)
    base = {}
    for h in HORIZONS:
        rets = [(cv[x + h] / cv[x] - 1) * 100 for x in range(warm, n - h)]
        base[str(h)] = _summ(rets)

    summary = {}
    for group in ("all", "fast", "slow", "zone"):
        evs = out if group == "all" else [e for e in out if e["line"] == group]
        if not evs:
            continue
        summary[group] = {
            "count": len(evs),
            "horizons": {str(h): _summ([e["returns"][str(h)] for e in evs if e["returns"][str(h)] is not None])
                         for h in HORIZONS},
            "avg_max_drop_60": _r(_mean([e["max_drop_60"] for e in evs if e["max_drop_60"] is not None])),
            "avg_max_gain_60": _r(_mean([e["max_gain_60"] for e in evs if e["max_gain_60"] is not None])),
            "broke_rate": _r(_pct([e["broke_slow_20"] for e in evs if e["broke_slow_20"] is not None])),
        }

    step = max(1, (n - warm) // 900)            # thin the chart series to keep the page light
    idx = list(range(max(0, warm - 60), n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    chart = {"t": [c.index[x].strftime("%Y-%m-%d") for x in idx], "c": [_r(cv[x]) for x in idx],
             "f": [_r(fv[x]) for x in idx], "s": [_r(sv[x]) for x in idx]}
    return {"events": out, "summary": summary, "baseline": base, "chart": chart,
            "params": {"fast": fast, "slow": slow, "kind": kind, "tolerance": tolerance, "above_days": above_days,
                       "hold": hold, "cooldown": cooldown, "mode": mode},
            "period": {"start": c.index[warm].strftime("%Y-%m-%d"), "end": c.index[-1].strftime("%Y-%m-%d"),
                       "days": n - warm},
            "horizons": HORIZONS}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _pct(flags):
    return 100 * sum(1 for x in flags if x) / len(flags) if flags else None


def _summ(rets):
    rets = [r for r in rets if r is not None]
    if not rets:
        return {"n": 0, "win": None, "avg": None, "median": None, "best": None, "worst": None}
    srt = sorted(rets)
    m = len(srt)
    med = srt[m // 2] if m % 2 else (srt[m // 2 - 1] + srt[m // 2]) / 2
    return {"n": m, "win": _r(100 * sum(1 for r in rets if r > 0) / m, 1), "avg": _r(sum(rets) / m),
            "median": _r(med), "best": _r(srt[-1]), "worst": _r(srt[0])}
