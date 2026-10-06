"""Rotation summary: where money is moving across sectors, styles, industries and macro assets.

For every ETF: % change over 1D/1W/1M/3M/6M/YTD/1Y, the same vs SPY, trend (above the 50/200 EMA),
distance from the 52-week high, and a relative-rotation reading (like an RRG):
  RS        = ETF price / SPY price
  RS        is smoothed (5-day EMA) before the two readings below
  RS-Ratio  = 100 x RS / its 50-day average          (above 100 = outperforming SPY lately)
  RS-Mom    = 100 x RS-Ratio / RS-Ratio 10 days ago  (above 100 = that outperformance is improving)
  Leading = ratio>100 & mom>100 · Weakening = ratio>100 & mom<100 · Lagging = both <100 · Improving = ratio<100 & mom>100
Prices: daily bars from Public (cached for the day).
"""
import math

GROUPS = {
    "Sectors": [
        ("XLK", "Technology"), ("XLC", "Communication"), ("XLY", "Consumer discretionary"), ("XLF", "Financials"),
        ("XLI", "Industrials"), ("XLV", "Health care"), ("XLP", "Consumer staples"), ("XLE", "Energy"),
        ("XLB", "Materials"), ("XLU", "Utilities"), ("XLRE", "Real estate"),
    ],
    "Size & style": [
        ("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("DIA", "Dow 30"), ("IWM", "Russell 2000 (small caps)"),
        ("RSP", "S&P 500 equal weight"), ("IWF", "Large-cap growth"), ("IWD", "Large-cap value"),
        ("MTUM", "Momentum"), ("USMV", "Low volatility"), ("QUAL", "Quality"),
    ],
    "Industries": [
        ("SMH", "Semiconductors"), ("IGV", "Software"), ("XBI", "Biotech"), ("KRE", "Regional banks"),
        ("XHB", "Homebuilders"), ("ITA", "Aerospace & defense"), ("XOP", "Oil & gas producers"),
        ("GDX", "Gold miners"), ("XRT", "Retail"), ("IYT", "Transports"), ("TAN", "Solar"), ("CIBR", "Cybersecurity"),
    ],
    "Macro": [
        ("TLT", "20+ yr Treasuries"), ("IEF", "7-10 yr Treasuries"), ("HYG", "High-yield bonds"), ("GLD", "Gold"),
        ("SLV", "Silver"), ("USO", "Oil"), ("UUP", "US dollar"), ("EFA", "Developed markets"), ("EEM", "Emerging markets"),
        ("IBIT", "Bitcoin"),
    ],
}
PERIODS = [("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("YTD", None), ("1Y", 252)]
QUADRANTS = ("Leading", "Weakening", "Lagging", "Improving")


def _r(x, d=2):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else round(x, d)


def _ret(c, n):
    if n is None:                                   # year to date
        prior = c[c.index.year < c.index[-1].year]
        return (c.iloc[-1] / prior.iloc[-1] - 1) * 100 if len(prior) else None
    return (c.iloc[-1] / c.iloc[-1 - n] - 1) * 100 if len(c) > n else None


def quadrant(ratio, mom):
    if ratio is None or mom is None:
        return None
    if ratio >= 100:
        return "Leading" if mom >= 100 else "Weakening"
    return "Improving" if mom >= 100 else "Lagging"


def analyse(bars_by_ticker, names, bench="SPY", tail_weeks=8):
    """bars_by_ticker: {ticker: daily OHLC DataFrame}. Returns rows for the heatmap and the rotation chart."""
    import pandas as pd

    spy = bars_by_ticker.get(bench)
    spy_c = spy["close"] if spy is not None and len(spy) else None
    rows = []
    for t, df in bars_by_ticker.items():
        if df is None or len(df) < 30:
            continue
        c = df["close"]
        row = {"ticker": t, "name": names.get(t, t), "price": _r(c.iloc[-1]), "abs": {}, "rel": {}}
        for label, n in PERIODS:
            a = _ret(c, n)
            row["abs"][label] = _r(a)
            b = _ret(spy_c, n) if spy_c is not None else None
            row["rel"][label] = _r(a - b) if a is not None and b is not None else None
        ema50 = c.ewm(span=50, adjust=False).mean().iloc[-1]
        ema200 = c.ewm(span=200, adjust=False).mean().iloc[-1] if len(c) >= 200 else None
        row["vs_ema50"] = _r((c.iloc[-1] / ema50 - 1) * 100)
        row["vs_ema200"] = _r((c.iloc[-1] / ema200 - 1) * 100) if ema200 else None
        hi = df["high"].iloc[-252:].max()
        row["from_high"] = _r((c.iloc[-1] / hi - 1) * 100)
        # relative rotation vs SPY
        if spy_c is not None and t != bench:
            joined = pd.concat([c.rename("x"), spy_c.rename("s")], axis=1, join="inner").dropna()
            rs = (joined["x"] / joined["s"]).ewm(span=5, adjust=False).mean()      # smoothed so the tails aren't noise
            ratio = 100 * rs / rs.rolling(50).mean()
            mom = (100 * ratio / ratio.shift(10)).ewm(span=5, adjust=False).mean()
            ok = ratio.notna() & mom.notna()
            ratio, mom = ratio[ok], mom[ok]
            if len(ratio):
                row["rs_ratio"], row["rs_mom"] = _r(ratio.iloc[-1], 2), _r(mom.iloc[-1], 2)
                row["quadrant"] = quadrant(row["rs_ratio"], row["rs_mom"])
                weekly = list(range(len(ratio) - 1, -1, -5))[:tail_weeks][::-1]
                row["tail"] = [{"date": ratio.index[i].strftime("%Y-%m-%d"), "ratio": _r(ratio.iloc[i], 2),
                                "mom": _r(mom.iloc[i], 2)} for i in weekly]
                prev = row["tail"][-5] if len(row["tail"]) >= 5 else row["tail"][0]
                row["quadrant_4w_ago"] = quadrant(prev["ratio"], prev["mom"])
        rows.append(row)

    # rank changes: 1M relative performance now vs a month ago (who's climbing the table)
    def rel_1m(df, offset):
        c = df["close"]
        if spy_c is None or len(c) < 22 + offset:
            return None
        s = spy_c.reindex(c.index).ffill()
        a = c.iloc[-1 - offset] / c.iloc[-22 - offset] - 1
        b = s.iloc[-1 - offset] / s.iloc[-22 - offset] - 1
        return a - b
    now = {r["ticker"]: rel_1m(bars_by_ticker[r["ticker"]], 0) for r in rows}
    before = {r["ticker"]: rel_1m(bars_by_ticker[r["ticker"]], 21) for r in rows}
    rank_now = {t: i + 1 for i, t in enumerate(sorted((t for t in now if now[t] is not None), key=lambda t: -now[t]))}
    rank_before = {t: i + 1 for i, t in enumerate(sorted((t for t in before if before[t] is not None), key=lambda t: -before[t]))}
    for r in rows:
        r["rank"] = rank_now.get(r["ticker"])
        r["rank_change"] = (rank_before[r["ticker"]] - rank_now[r["ticker"]]) \
            if r["ticker"] in rank_now and r["ticker"] in rank_before else None
    return rows


def summary(rows):
    out = {q: [r["ticker"] for r in rows if r.get("quadrant") == q] for q in QUADRANTS}
    movers = [r for r in rows if r.get("rel", {}).get("1W") is not None and not r.get("benchmark")
              and r["ticker"] != "SPY"]
    out["inflow"] = [r["ticker"] for r in sorted(movers, key=lambda r: -r["rel"]["1W"])[:3]]
    out["outflow"] = [r["ticker"] for r in sorted(movers, key=lambda r: r["rel"]["1W"])[:3]]
    out["turning_up"] = [r["ticker"] for r in rows
                         if r.get("quadrant") in ("Improving", "Leading") and r.get("quadrant_4w_ago") in ("Lagging",)]
    out["rolling_over"] = [r["ticker"] for r in rows
                           if r.get("quadrant") in ("Weakening", "Lagging") and r.get("quadrant_4w_ago") == "Leading"]
    return out
