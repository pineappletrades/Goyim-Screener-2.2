"""Statistics (TTM) for the Research page, worked out from quarterly filings and the price.

Everything here is computed from SEC EDGAR (quarterly history) + Public.com (price), so it works with
just those two keys. Forward P/E and the next earnings date come from an optional source if connected.
"""
from datetime import datetime, time as dtime

TAX_FALLBACK = 0.21     # US federal rate, used for ROIC when the filings don't give a usable tax rate


def _ttm(vals, end=None):
    """Sum of the 4 quarters ending at index `end` (default: the latest), or None if any is missing."""
    end = len(vals) if end is None else end
    xs = vals[max(0, end - 4):end]
    return sum(xs) if len(xs) == 4 and all(v is not None for v in xs) else None


def _last(vals):
    for v in reversed(vals or []):
        if v is not None:
            return v
    return None


def _div(a, b, pos_only=True):
    if a is None or b is None or b == 0 or (pos_only and b < 0):
        return None
    return a / b


def _cagr(vals, years):
    now, then = _ttm(vals), _ttm(vals, len(vals) - 4 * years)
    if now is None or then is None or then <= 0 or now <= 0 or len(vals) < 4 * years + 4:
        return None
    return ((now / then) ** (1 / years) - 1) * 100


def _cagr_point(vals, years):
    """CAGR for a balance or count (shares, cash): latest value vs the value `years` back."""
    vals = list(vals or [])
    if len(vals) < 4 * years + 1:
        return None
    now, then = vals[-1], vals[-1 - 4 * years]
    if now is None or then is None or now <= 0 or then <= 0:
        return None
    return ((now / then) ** (1 / years) - 1) * 100


def _cagr_price(bars, years):
    """Share price CAGR from the price history (no dividends)."""
    if bars is None or not len(bars):
        return None
    import pandas as pd

    c = bars["close"]
    target = c.index[-1] - pd.DateOffset(years=years)
    before = c[c.index <= target]
    if len(before) and (target - before.index[-1]).days <= 40:
        start = before.index[-1]
    elif (c.index[0] - target).days <= 40:       # history starts just after the date (e.g. exactly 10 years)
        start = c.index[0]
    else:
        return None
    then, now = float(c[start]), float(c.iloc[-1])
    yrs = (c.index[-1] - start).days / 365.25      # the real span, so a few days short doesn't skew it
    return ((now / then) ** (1 / yrs) - 1) * 100 if then > 0 and now > 0 and yrs > 0.5 else None


CAGR_YEARS = (1, 3, 5, 10)


def cagr_table(m, bars=None, price=None):
    """Rows of {label, values: {1: %, 3: %, 5: %, 10: %}}. None = not enough history or a loss at either end."""
    flows = [("Revenue", "revenue"), ("Gross profit", "gross_profit"), ("EBITDA", "ebitda"),
             ("Operating income", "operating_income"), ("Net income", "net_income"), ("EPS (diluted)", "eps"),
             ("Cash from operations", "ocf"), ("Free cash flow", "fcf"), ("Dividend per share", "dps")]
    rows = []
    if bars is not None and len(bars):
        rows.append({"label": "Share price", "key": "price", "values": {y: _cagr_price(bars, y) for y in CAGR_YEARS}})
    for label, k in flows:
        vals = m.get(k) or []
        if k == "dps":
            if not any(v for v in vals[-4:]):
                continue
            vals = [v or 0.0 for v in vals]
        rows.append({"label": label, "key": k, "values": {y: _cagr(vals, y) for y in CAGR_YEARS}})
    for label, k in (("Shares outstanding", "shares"), ("Book value (equity)", "equity"), ("Cash", "cash")):
        rows.append({"label": label, "key": k, "values": {y: _cagr_point(m.get(k), y) for y in CAGR_YEARS}})
    for r in rows:
        r["values"] = {str(y): (round(v, 2) if v is not None else None) for y, v in r["values"].items()}
    return rows


def after_hours(quote, bars):
    """Extended-hours price when the latest trade is outside 9:30-16:00 New York time."""
    if not quote or not quote.get("ts") or bars is None or not len(bars):
        return None
    try:
        from zoneinfo import ZoneInfo
        ts = datetime.fromisoformat(quote["ts"].replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return None
    if dtime(9, 30) <= ts.time() <= dtime(16, 0, 1):
        return None
    return quote["last"]


def build(r, bars=None, extra=None):
    """r: research dict (history, valuation, quote). Returns {"profile": {...}, "groups": [...]}."""
    extra = extra or {}
    h = r.get("history") or {}
    m = h.get("metrics") or {}
    q = r.get("quote") or {}
    close = float(bars["close"].iloc[-1]) if bars is not None and len(bars) else None
    ah = after_hours(q, bars)
    price = (close if ah is not None and close else q.get("last")) or close or _last((r.get("valuation") or {}).get("price"))
    shares = _last(m.get("shares"))
    mcap = price * shares if price and shares else None

    rev, ni, oi = _ttm(m.get("revenue", [])), _ttm(m.get("net_income", [])), _ttm(m.get("operating_income", []))
    ebitda, ocf, fcf = _ttm(m.get("ebitda", [])), _ttm(m.get("ocf", [])), _ttm(m.get("fcf", []))
    eps, eps_prev = _ttm(m.get("eps", [])), _ttm(m.get("eps", []), len(m.get("eps", [])) - 4)
    cash, debt, equity = _last(m.get("cash")), _last(m.get("debt")), _last(m.get("equity"))
    dps = _ttm([v or 0.0 for v in m.get("dps", [])]) if any(v for v in m.get("dps", [])[-4:]) else 0.0
    ev = mcap + (debt or 0) - (cash or 0) if mcap else None

    pe = _div(price, eps)
    eps_growth = (eps - eps_prev) / abs(eps_prev) * 100 if eps is not None and eps_prev else None
    peg = pe / eps_growth if pe and eps_growth and eps_growth > 0 else extra.get("peg_ratio")
    pretax, tax = _ttm(m.get("pretax", [])), _ttm(m.get("tax", []))
    rate = tax / pretax if pretax and tax is not None and pretax > 0 and 0 <= tax / pretax < 0.5 else TAX_FALLBACK
    invested = (debt or 0) + equity - (cash or 0) if equity is not None else None
    roic = oi * (1 - rate) / invested * 100 if oi is not None and invested and invested > 0 else None
    paid_qs = sum(1 for v in m.get("dps", [])[-4:] if v)
    freq = {0: "No dividend", 1: "Annual", 2: "Semiannual", 3: "Quarterly", 4: "Quarterly"}.get(paid_qs, "Monthly")

    hi = lo = None
    if bars is not None and len(bars) > 20:
        last_year = bars.iloc[-252:]
        hi, lo = float(last_year["high"].max()), float(last_year["low"].min())
    prev = q.get("prev_close")
    profile = {
        "name": extra.get("name"), "website": extra.get("website"), "exchange": extra.get("exchange"),
        "sector": extra.get("sector") or r.get("sector"), "industry": extra.get("industry"),
        "market_cap": mcap, "revenue_ttm": rev, "shares": shares, "price": price,
        "change": price - prev if price and prev else None,
        "change_pct": (price / prev - 1) * 100 if price and prev else None,
        "after_hours": ah,
        "high_52w": hi or extra.get("high_52w"), "low_52w": lo or extra.get("low_52w"),
        "next_earnings_date": extra.get("next_earnings_date"), "last_earnings_date": extra.get("last_earnings_date"),
    }
    X, P, M = "x", "%", "$"
    groups = [
        ("Earnings-based valuation", [
            ("Price to earnings (P/E)", pe, X),
            ("Forward P/E", extra.get("forward_pe"), X, "forward_pe"),
            ("Price/earnings to growth (PEG)", peg, X),
            ("Earnings yield", _div(eps, price) and _div(eps, price) * 100, P)]),
        ("Revenue & cash flow", [
            ("Price to sales (P/S)", _div(mcap, rev), X),
            ("Price to cash flow (P/CF)", _div(mcap, ocf), X),
            ("Price to free cash flow (P/FCF)", _div(mcap, fcf), X),
            ("Free cash flow yield", _div(fcf, mcap) and fcf / mcap * 100, P)]),
        ("Asset-based", [
            ("Price to book (P/B)", _div(mcap, equity), X),
            ("Book value per share", _div(equity, shares), M)]),
        ("Enterprise value", [
            ("Enterprise value", ev, "big"),
            ("EV to EBITDA", _div(ev, ebitda), X),
            ("EV to sales", _div(ev, rev), X)]),
        ("Profitability", [
            ("Profit margin", _div(ni, rev) and ni / rev * 100, P),
            ("Operating margin", _div(oi, rev) and oi / rev * 100, P),
            ("Return on equity (ROE)", _div(ni, equity) and ni / equity * 100, P),
            ("Return on invested capital (ROIC)", roic, P)]),
        ("Financial health", [
            ("Free cash flow", fcf, "big"), ("Net income", ni, "big"),
            ("Net debt", (debt or 0) - cash if cash is not None else None, "big"),
            ("Debt to equity", _div(debt, equity), X)]),
        ("Dividend", [
            ("Dividend yield", _div(dps, price) * 100 if dps and price else 0.0 if price else None, P),
            ("Dividend per share (TTM)", dps, M),
            ("Payout ratio", _div(dps, eps) * 100 if dps and eps and eps > 0 else (0.0 if dps == 0 else None), P),
            ("Payout frequency", freq if m.get("dps") is not None else None, "text")]),
    ]
    out = []
    for title, rows in groups:
        out.append({"title": title, "rows": [
            {"label": row[0], "value": (round(row[1], 4) if isinstance(row[1], float) else row[1]),
             "fmt": row[2], "needs": row[3] if len(row) > 3 else None} for row in rows]})
    return {"profile": profile, "groups": out, "cagr": cagr_table(m, bars),
            "eps_growth_1y": eps_growth}
