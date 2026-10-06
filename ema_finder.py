"""EMA finder: which moving-average length has this stock respected the most?

For every length in a range (default 5–250) over the last ~5 years of daily prices:
  test    = price comes down to the line from above: the day's low gets within `tolerance`% of it
            (or dips under it) after closing above it on most of the prior 10 days
  bounce  = after the test, price rallies `bounce_pct`% off the line within `lookahead` days
            before any close more than `break_pct`% below the line
  break   = a close more than `break_pct`% below the line comes first
Respect = bounces / tests. Lengths are ranked by a confidence-adjusted respect rate (Wilson lower bound),
so a line with 9 bounces out of 10 beats one with 2 out of 2.
Distance stats show how far price usually sits from each line, in %.
"""
import math

import numpy as np


def _ma(close, n, kind):
    return close.rolling(n).mean() if kind == "SMA" else close.ewm(span=n, adjust=False).mean()


def _wilson(k, n, z=1.28):        # ~80% one-sided lower bound
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    adj = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - adj) / d


def _r(x, d=2):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else round(x, d)


def analyse_length(c, lo, hi, ma, start, tolerance, break_pct, bounce_pct, lookahead, cooldown, keep_events=False):
    n = len(c)
    tol, brk, up = 1 + tolerance / 100, 1 - break_pct / 100, 1 + bounce_pct / 100
    above = c > ma
    # closed above the line on at least 7 of the prior 10 days -> coming down to it from above
    prior = np.convolve(above.astype(float), np.ones(10), "full")[:n]
    prior = np.concatenate([[0], prior[:-1]])
    tests = bounces = breaks = 0
    gains, undercuts, days_to_bounce, events = [], [], [], []
    last = -10 ** 9
    i = start
    while i < n - 1:
        m = ma[i]
        if not (prior[i] >= 7 and lo[i] <= m * tol and c[i - 1] > ma[i - 1]):
            i += 1
            continue
        if i - last <= cooldown:
            last = i
            i += 1
            continue
        last = i
        tests += 1
        outcome, j_end = "open", min(n - 1, i + lookahead)
        low_seen = lo[i]
        for j in range(i, j_end + 1):
            low_seen = min(low_seen, lo[j])
            if c[j] < ma[j] * brk:
                outcome = "break"
                break
            if hi[j] >= ma[i] * up and j > i:
                outcome = "bounce"
                days_to_bounce.append(j - i)
                break
        if outcome == "open" and j_end - i < lookahead:
            tests -= 1                           # too recent to judge yet
            outcome = "pending"
        elif outcome == "open":
            outcome = "fade"                     # neither bounced nor broke: counts as not respected
        if outcome == "bounce":
            bounces += 1
        elif outcome == "break":
            breaks += 1
        k = min(n, i + 21)
        if outcome != "pending":
            gains.append((hi[i + 1:k].max() / m - 1) * 100 if k > i + 1 else 0.0)
            undercuts.append((low_seen / m - 1) * 100)
        if keep_events:
            events.append({"i": int(i), "outcome": outcome, "ma": _r(m), "low": _r(lo[i]), "close": _r(c[i])})
        i += 1
    dist = (c[start:] / ma[start:] - 1) * 100
    above_d = dist[dist > 0]
    out = {
        "tests": tests, "bounces": bounces, "breaks": breaks,
        "respect": _r(100 * bounces / tests, 1) if tests else None,
        "score": _r(100 * _wilson(bounces, tests), 1),
        "avg_bounce": _r(np.mean(gains)) if gains else None,
        "avg_undercut": _r(np.mean(undercuts)) if undercuts else None,
        "days_to_bounce": _r(np.median(days_to_bounce), 1) if days_to_bounce else None,
        "avg_dist": _r(np.mean(dist)), "avg_abs_dist": _r(np.mean(np.abs(dist))),
        "median_dist_above": _r(np.median(above_d)) if len(above_d) else None,
        "pct_days_above": _r(100 * (dist > 0).mean(), 1),
        "now_dist": _r(dist[-1]), "now_ma": _r(ma[-1]),
    }
    if keep_events:
        out["events"] = events
    return out


def run(bars, min_len=5, max_len=250, step=1, kind="EMA", tolerance=1.0, break_pct=2.0, bounce_pct=3.0,
        lookahead=15, cooldown=5, min_tests=3):
    if bars is None or len(bars) < 300:
        raise ValueError("Need at least 300 days of daily prices")
    min_len, max_len, step = max(2, int(min_len)), min(400, int(max_len)), max(1, int(step))
    lookahead, cooldown, min_tests = int(lookahead), int(cooldown), int(min_tests)
    if max_len < min_len:
        min_len, max_len = max_len, min_len
    close = bars["close"]
    c, lo, hi = close.values.astype(float), bars["low"].values.astype(float), bars["high"].values.astype(float)
    n = len(c)
    # every length is judged over the same days; the first `max_len` days settle the averages
    start = min(max(max_len, 50), n - 120)
    rows = []
    for L in range(min_len, max_len + 1, step):
        ma = _ma(close, L, kind).values
        r = analyse_length(c, lo, hi, ma, start, tolerance, break_pct, bounce_pct, lookahead, cooldown)
        r["length"] = L
        rows.append(r)
    ranked = sorted((r for r in rows if r["tests"] >= min_tests),
                    key=lambda r: (r["score"], r["bounces"], -r["avg_abs_dist"]), reverse=True)
    # neighbouring lengths (179, 180, 181...) behave almost the same, so the top list keeps one per area
    top = []
    for r in ranked:
        if all(abs(r["length"] - t["length"]) > max(3, 0.08 * t["length"]) for t in top):
            top.append(r)
        if len(top) == 10:
            break
    best = ranked[0]["length"] if ranked else None
    detail = None
    if best:
        ma = _ma(close, best, kind)
        detail = analyse_length(c, lo, hi, ma.values, start, tolerance, break_pct, bounce_pct, lookahead, cooldown,
                                keep_events=True)
        for e in detail["events"]:
            e["date"] = close.index[e.pop("i")].strftime("%Y-%m-%d")
        step_c = max(1, (n - start) // 900)
        idx = list(range(start, n, step_c)) + ([n - 1] if (n - 1 - start) % step_c else [])
        detail["chart"] = {"t": [close.index[x].strftime("%Y-%m-%d") for x in idx],
                           "c": [_r(c[x]) for x in idx], "ma": [_r(ma.values[x]) for x in idx]}
    classics = {L: next((r for r in rows if r["length"] == L), None) for L in (9, 20, 21, 50, 100, 150, 200)}
    return {"rows": rows, "top": top, "best": best, "best_detail": detail,
            "classics": {str(k): v for k, v in classics.items() if v},
            "params": {"min_len": min_len, "max_len": max_len, "step": step, "kind": kind, "tolerance": tolerance,
                       "break_pct": break_pct, "bounce_pct": bounce_pct, "lookahead": lookahead,
                       "cooldown": cooldown, "min_tests": min_tests},
            "period": {"start": close.index[start].strftime("%Y-%m-%d"), "end": close.index[-1].strftime("%Y-%m-%d"),
                       "days": n - start}}
