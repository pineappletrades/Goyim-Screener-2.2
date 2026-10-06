"""Quarterly revenue, EPS and free cash flow from SEC EDGAR (free, no API key).

The SEC only asks for a User-Agent with your name and email on every request.
- Income figures: Q4 isn't filed as its own quarter, so it's full year minus Q1–Q3.
- Cash-flow figures are filed year-to-date (3, 6, 9, 12 months), so single quarters
  are the differences between those totals.
"""
import time
from datetime import date

from engine.plugin_api import KeyField, Provider

REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "RevenuesNetOfInterestExpense",
]
EPS_TAGS = ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"]
OCF_TAGS = ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
CAPEX_TAGS = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsForCapitalImprovements"]
GROSS_TAGS = ["GrossProfit"]
COST_TAGS = ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]
OPINC_TAGS = ["OperatingIncomeLoss"]
NETINC_TAGS = ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]
DA_TAGS = ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
           "DepreciationAmortizationAndAccretionNet", "Depreciation"]
CASH_TAGS = ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
DEBT_TOTAL_TAGS = ["LongTermDebt", "DebtInstrumentCarryingAmount"]
DEBT_NONCUR_TAGS = ["LongTermDebtNoncurrent"]
DEBT_CUR_TAGS = ["LongTermDebtCurrent", "DebtCurrent"]
EQUITY_TAGS = ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
DPS_TAGS = ["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"]
PRETAX_TAGS = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]
TAX_TAGS = ["IncomeTaxExpenseBenefit"]

# SEC industry code (SIC) -> broad sector
SIC_SECTORS = [
    ((100, 999), "Agriculture"), ((1000, 1499), "Energy and mining"), ((1500, 1799), "Industrials"),
    ((2000, 2099), "Consumer staples"), ((2100, 2199), "Consumer staples"), ((2200, 2799), "Consumer discretionary"),
    ((2800, 2829), "Materials"), ((2830, 2836), "Health care"), ((2837, 2899), "Materials"),
    ((2900, 2999), "Energy and mining"), ((3000, 3569), "Industrials"), ((3570, 3579), "Technology"),
    ((3580, 3599), "Industrials"), ((3600, 3659), "Technology"), ((3660, 3699), "Technology"),
    ((3700, 3799), "Industrials"), ((3800, 3829), "Technology"), ((3830, 3851), "Health care"),
    ((3852, 3999), "Industrials"), ((4000, 4799), "Industrials"), ((4800, 4899), "Communication"),
    ((4900, 4999), "Utilities"), ((5000, 5199), "Industrials"), ((5200, 5999), "Consumer discretionary"),
    ((6000, 6499), "Financials"), ((6500, 6799), "Real estate"), ((7000, 7369), "Consumer discretionary"),
    ((7370, 7379), "Technology"), ((7380, 7999), "Consumer discretionary"), ((8000, 8099), "Health care"),
    ((8100, 8999), "Industrials"), ((9000, 9999), "Other"),
]


def sector_for_sic(sic):
    try:
        n = int(sic)
    except (TypeError, ValueError):
        return None
    if 6798 <= n <= 6798:
        return "Real estate"
    for (a, b), name in SIC_SECTORS:
        if a <= n <= b:
            return name
    return None


def instant_series(facts):
    """Balance-sheet style facts (a value on a date): {end: val}, newest filing wins."""
    by_end = {}
    for f in facts:
        if "end" not in f or "start" in f:
            continue
        e = f["end"]
        if e not in by_end or f.get("filed", "") > by_end[e].get("filed", ""):
            by_end[e] = f
    return {e: float(f["val"]) for e, f in by_end.items()}


def _instant(us_gaap, tags, unit="USD"):
    merged = {}
    for tag in tags:
        facts = us_gaap.get(tag, {}).get("units", {}).get(unit)
        if facts:
            for e, v in instant_series(facts).items():
                merged.setdefault(e, v)
    return merged


def _near(series_by_end, end, before=20, after=20):
    """Value whose date is closest to `end` within a window."""
    target = date.fromisoformat(end)
    best, gap = None, None
    for e, v in series_by_end.items():
        d = (date.fromisoformat(e) - target).days
        if -before <= d <= after and (gap is None or abs(d) < gap):
            best, gap = v, abs(d)
    return best


def _days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _latest_per_period(facts):
    by_period = {}
    for f in facts:
        if "start" not in f or "end" not in f:
            continue
        k = (f["start"], f["end"])
        if k not in by_period or f.get("filed", "") > by_period[k].get("filed", ""):
            by_period[k] = f
    return by_period


def quarterly_series(facts):
    """Income-statement style facts (quarters filed directly, Q4 only inside the annual)."""
    by_period = _latest_per_period(facts)
    quarters, annuals = {}, []
    for (s, e), f in by_period.items():
        span = _days(s, e)
        if 80 <= span <= 100:
            quarters[e] = {"start": s, "end": e, "val": float(f["val"]), "derived": False}
        elif 350 <= span <= 380:
            annuals.append((s, e, float(f["val"])))
    for s, e, total in annuals:
        if e in quarters:
            continue
        inside = [q for q in quarters.values() if q["start"] >= s and q["end"] < e]
        if len(inside) == 3:
            quarters[e] = {"start": max(q["end"] for q in inside), "end": e,
                           "val": total - sum(q["val"] for q in inside), "derived": True}
    return sorted(quarters.values(), key=lambda q: q["end"], reverse=True)


def quarters_from_cumulative(facts):
    """Cash-flow style facts (year-to-date totals) -> single quarters, newest first."""
    by_period = _latest_per_period(facts)
    quarters = {}
    groups = {}
    for (s, e), f in by_period.items():
        span = _days(s, e)
        if 80 <= span <= 380:
            groups.setdefault(s, []).append((e, float(f["val"])))
    for s, items in groups.items():
        items.sort()
        prev_end, prev_val = s, 0.0
        for e, v in items:
            gap = _days(prev_end, e)
            if 80 <= gap <= 100:
                derived = _days(s, e) > 100
                if e not in quarters or (quarters[e]["derived"] and not derived):
                    quarters[e] = {"start": prev_end, "end": e, "val": v - prev_val, "derived": derived}
            prev_end, prev_val = e, v
    return sorted(quarters.values(), key=lambda q: q["end"], reverse=True)


def ttm(series):
    """Sum of the 4 most recent consecutive quarters, or None if there's a gap."""
    if len(series) < 4:
        return None
    last4 = series[:4]
    for a, b in zip(last4, last4[1:]):
        if not 80 <= _days(b["end"], a["end"]) <= 100:
            return None
    return sum(q["val"] for q in last4)


def _best(us_gaap, tags, unit, builder=quarterly_series, prefer_first=False):
    found = []
    for tag in tags:
        facts = us_gaap.get(tag, {}).get("units", {}).get(unit)
        if facts:
            s = builder(facts)
            if s:
                found.append(s)
    if not found:
        return []
    newest = max(s[0]["end"] for s in found)
    if prefer_first:   # e.g. diluted EPS unless it's stale
        for s in found:
            if _days(s[0]["end"], newest) <= 120:
                return s
    return max(found, key=lambda s: s[0]["end"])


def _year_ago(series, i):
    if i >= len(series):
        return None
    target = date.fromisoformat(series[i]["end"])
    for q in series[i + 1:]:
        if abs((target - date.fromisoformat(q["end"])).days - 365) <= 20:
            return q
    return None


def _yoy_pct(series, i):
    prior = _year_ago(series, i)
    if prior is None or prior["val"] == 0:
        return None
    return (series[i]["val"] - prior["val"]) / abs(prior["val"]) * 100


class SecEdgar(Provider):
    name = "SEC EDGAR"
    description = ("Quarterly revenue, profit, EPS, cash flow, cash and debt from company filings (10-Q / 10-K), "
                   "10 years back. Also builds the all-stocks list, the screener's fundamentals and sectors.")
    key_fields = [KeyField("user_agent", "Your name and email", secret=False,
                           help='Required by the SEC, e.g. "Jane Doe jane@example.com"')]
    groups = {
        "core": ["revenue_q", "eps_q", "revenue_yoy", "eps_yoy_up", "eps_yoy_pct", "eps_last",
                 "shares_outstanding", "latest_quarter_end", "ocf_ttm", "capex_ttm", "fcf_ttm", "fcf_q"],
        "history": ["history"],
        "company": ["sic_sector", "last_earnings_date"],
    }
    supplies = [f for fs in groups.values() for f in fs]
    expensive = True
    priority = 30
    signup_url = "https://www.sec.gov/edgar/sec-api-documentation"

    _tickers = None      # [(ticker, cik)] in SEC's order
    _facts_cache = {}    # cik -> (day, facts)

    def __init__(self, keys, log=print):
        super().__init__(keys, log)
        import requests

        self.session = requests.Session()

    def _get(self, url):
        ok, msg = self.ready()
        if not ok:
            raise RuntimeError(f"SEC EDGAR: {msg}")
        self.session.headers["User-Agent"] = self.keys["user_agent"]
        for attempt in range(3):
            r = self.session.get(url, timeout=60)
            if r.status_code == 200:
                time.sleep(0.12)        # SEC limit is ~10 requests/second
                return r.json()
            if r.status_code == 404:
                return {}
            time.sleep(2 * (attempt + 1))
        r.raise_for_status()
        return {}

    def ticker_list(self):
        if SecEdgar._tickers is None:
            data = self._get("https://www.sec.gov/files/company_tickers.json")
            SecEdgar._tickers = [(v["ticker"].upper(), int(v["cik_str"])) for v in data.values()]
        return SecEdgar._tickers

    def cik(self, ticker):
        t = ticker.upper().replace(".", "-")
        return next((c for tk, c in self.ticker_list() if tk == t), None)

    def _facts(self, cik):
        today = date.today().isoformat()
        hit = SecEdgar._facts_cache.get(cik)
        if hit and hit[0] == today:
            return hit[1]
        facts = self._get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json").get("facts", {})
        if len(SecEdgar._facts_cache) > 40:
            SecEdgar._facts_cache.pop(next(iter(SecEdgar._facts_cache)))
        SecEdgar._facts_cache[cik] = (today, facts)
        return facts

    # ---------- market-wide ----------
    def _frame(self, taxonomy, tag, unit, period):
        data = self._get(f"https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json")
        return {int(r["cik"]): (float(r["val"]), r.get("end", "")) for r in data.get("data", [])}

    @staticmethod
    def _recent_quarters(n, instant=False):
        today = date.today()
        y, q = today.year, (today.month - 1) // 3 + 1
        out = []
        for _ in range(n + (0 if instant else 1)):
            out.append((y, q))
            q -= 1
            if q == 0:
                y, q = y - 1, 4
        return out if instant else out[1:]      # duration frames: skip the quarter still in progress

    def shares_outstanding_all(self):
        """{ticker: shares} for every US filer, from SEC's market-wide 'frames' data (3 requests)."""
        latest = {}
        for y, q in self._recent_quarters(3, instant=True):
            for c, (v, e) in self._frame("dei", "EntityCommonStockSharesOutstanding", "shares", f"CY{y}Q{q}I").items():
                if c not in latest or e > latest[c][1]:
                    latest[c] = (v, e)
        return self._by_ticker({c: v for c, (v, e) in latest.items()})

    def _by_ticker(self, by_cik):
        out, seen = {}, set()
        for tk, c in self.ticker_list():
            if c in by_cik and c not in seen:      # first listed ticker = the company's main share class
                seen.add(c)
                out[tk.replace("-", ".")] = by_cik[c]
        return out

    def fundamentals_all(self, progress=None):
        """Screener fundamentals for every US filer from ~80 market-wide requests.
        Returns {ticker: {...}}. Quarterly values are matched by calendar quarter."""
        qs = self._recent_quarters(8)                     # newest first
        say = progress or (lambda m: None)

        def quarterly(tags, unit="USD"):
            per = {}                                     # cik -> {(y,q): val}
            for tag in tags:
                for (y, q) in qs:
                    say(f"SEC {tag} {y} Q{q}")
                    for c, (v, e) in self._frame("us-gaap", tag, unit, f"CY{y}Q{q}").items():
                        per.setdefault(c, {}).setdefault((y, q), v)
            return per

        def instant(tags, unit="USD"):
            per = {}
            for tag in tags:
                for (y, q) in self._recent_quarters(2, instant=True):
                    for c, (v, e) in self._frame("us-gaap", tag, unit, f"CY{y}Q{q}I").items():
                        if c not in per or e > per[c][1]:
                            per[c] = (v, e)
            return {c: v for c, (v, e) in per.items()}

        def annual(tags):
            per = {}
            y0 = date.today().year
            for tag in tags:
                for y in (y0 - 1, y0 - 2):
                    for c, (v, e) in self._frame("us-gaap", tag, "USD", f"CY{y}").items():
                        per.setdefault(c, {}).setdefault(y, v)
            return {c: d.get(y0 - 1, d.get(y0 - 2)) for c, d in per.items()}

        rev, eps = quarterly(REVENUE_TAGS[:3]), quarterly(EPS_TAGS[:2], "USD-per-shares")
        ni, gp = quarterly(NETINC_TAGS[:1]), quarterly(GROSS_TAGS)
        dps = quarterly(DPS_TAGS[:1], "USD-per-shares")
        ocf, capex = annual(OCF_TAGS[:1]), annual(CAPEX_TAGS[:1])
        cash, equity = instant(CASH_TAGS[:1]), instant(EQUITY_TAGS[:1])
        debt_t, debt_nc, debt_c = instant(DEBT_TOTAL_TAGS[:1]), instant(DEBT_NONCUR_TAGS), instant(DEBT_CUR_TAGS[:1])

        order = qs  # newest first

        def ttm(d, offset=0):
            if not d:
                return None
            for start in range(0, len(order) - 3 - offset):
                window = order[start + offset:start + offset + 4]
                if all(w in d for w in window):
                    return sum(d[w] for w in window), start
            return None

        out = {}
        for c in set(rev) | set(eps) | set(ni):
            r = {}
            rv = rev.get(c, {})
            t = ttm(rv)
            if t:
                r["rev_ttm"] = t[0]
                latest = order[t[1]]
                prior = (latest[0] - 1, latest[1])
                if prior in rv and rv[prior]:
                    r["rev_yoy"] = (rv[latest] / rv[prior] - 1) * 100
            e = eps.get(c, {})
            t = ttm(e)
            if t:
                r["eps_ttm"] = t[0]
                t2 = ttm(e, offset=4 + t[1]) if len(order) >= t[1] + 8 else None
                if t2 and t2[0]:
                    r["eps_ttm_yoy"] = (t[0] - t2[0]) / abs(t2[0]) * 100
            t = ttm(ni.get(c, {}))
            if t:
                r["ni_ttm"] = t[0]
            t = ttm(gp.get(c, {}))
            if t and r.get("rev_ttm"):
                r["gross_margin"] = t[0] / r["rev_ttm"] * 100
            if r.get("ni_ttm") is not None and r.get("rev_ttm"):
                r["net_margin"] = r["ni_ttm"] / r["rev_ttm"] * 100
            t = ttm(dps.get(c, {}))
            if t:
                r["dps_ttm"] = t[0]
            if c in ocf:
                r["ocf_fy"] = ocf[c]
                r["fcf_fy"] = ocf[c] - (capex.get(c) or 0.0)
            if c in cash:
                r["cash"] = cash[c]
            if c in equity:
                r["equity"] = equity[c]
            debt = debt_t.get(c)
            if debt is None and (c in debt_nc or c in debt_c):
                debt = (debt_nc.get(c) or 0.0) + (debt_c.get(c) or 0.0)
            if debt is not None:
                r["debt"] = debt
            out[c] = r
        return self._by_ticker(out)

    def company_info(self, ticker):
        """Sector (from SEC industry code) and the date of the latest earnings release (8-K item 2.02)."""
        cik = self.cik(ticker)
        if cik is None:
            return {}
        sub = self._get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
        recent = (sub.get("filings") or {}).get("recent") or {}
        last = None
        for form, items, d in zip(recent.get("form", []), recent.get("items", []), recent.get("filingDate", [])):
            if form in ("8-K", "8-K/A") and "2.02" in (items or ""):
                last = d if last is None or d > last else last
        return {"sic": sub.get("sic"), "sic_description": sub.get("sicDescription"),
                "sic_sector": sector_for_sic(sub.get("sic")), "last_earnings_date": last,
                "name": sub.get("name"), "exchange": (sub.get("exchanges") or [None])[0],
                "website": (sub.get("website") or "").strip() or None,
                "fiscal_year_end": sub.get("fiscalYearEnd")}

    # ---------- per ticker ----------
    def _history(self, facts):
        g = facts.get("us-gaap", {})
        rev = _best(g, REVENUE_TAGS, "USD")
        if not rev:
            return None
        quarters = sorted(q["end"] for q in rev[:44])   # 44 = 10 years of TTM growth
        derived_q4 = {q["end"] for q in rev if q.get("derived")}

        def by_end(series):
            return {q["end"]: q["val"] for q in series}

        gp = by_end(_best(g, GROSS_TAGS, "USD"))
        cost = by_end(_best(g, COST_TAGS, "USD"))
        revm = by_end(rev)
        series = {
            "revenue": revm,
            "gross_profit": gp or {e: revm[e] - cost[e] for e in revm if e in cost},
            "operating_income": by_end(_best(g, OPINC_TAGS, "USD")),
            "net_income": by_end(_best(g, NETINC_TAGS, "USD")),
            "eps": by_end(_best(g, EPS_TAGS, "USD/shares", prefer_first=True)),
            "ocf": by_end(_best(g, OCF_TAGS, "USD", builder=quarters_from_cumulative)),
            "capex": by_end(_best(g, CAPEX_TAGS, "USD", builder=quarters_from_cumulative)),
            "da": by_end(_best(g, DA_TAGS, "USD", builder=quarters_from_cumulative)),
            "dps": by_end(_best(g, DPS_TAGS, "USD/shares")),
            "pretax": by_end(_best(g, PRETAX_TAGS, "USD")),
            "tax": by_end(_best(g, TAX_TAGS, "USD")),
        }
        equity = _instant(g, EQUITY_TAGS)
        cash = _instant(g, CASH_TAGS)
        debt_total = _instant(g, DEBT_TOTAL_TAGS)
        debt_nc, debt_c = _instant(g, DEBT_NONCUR_TAGS), _instant(g, DEBT_CUR_TAGS)
        shares = instant_series(facts.get("dei", {}).get("EntityCommonStockSharesOutstanding", {})
                                .get("units", {}).get("shares", []))
        wavg = by_end(_best(g, ["WeightedAverageNumberOfDilutedSharesOutstanding"], "shares"))

        m = {k: [] for k in ("revenue", "gross_profit", "gross_margin", "operating_income", "ebitda", "net_income",
                             "eps", "ocf", "capex", "fcf", "cash", "debt", "shares", "equity", "dps",
                             "pretax", "tax")}
        for e in quarters:
            def g_(k):
                return series[k].get(e)
            r, gpv = g_("revenue"), g_("gross_profit")
            m["revenue"].append(r)
            m["gross_profit"].append(gpv)
            m["gross_margin"].append(gpv / r * 100 if gpv is not None and r else None)
            oi, da = g_("operating_income"), g_("da")
            m["operating_income"].append(oi)
            m["ebitda"].append(oi + da if oi is not None and da is not None else None)
            m["net_income"].append(g_("net_income"))
            m["eps"].append(g_("eps"))
            ocf, cx = g_("ocf"), g_("capex")
            m["ocf"].append(ocf)
            m["capex"].append(cx)
            m["fcf"].append(ocf - (cx or 0.0) if ocf is not None else None)
            m["cash"].append(_near(cash, e))
            d = _near(debt_total, e)
            if d is None:
                nc, cu = _near(debt_nc, e), _near(debt_c, e)
                d = (nc or 0.0) + (cu or 0.0) if (nc is not None or cu is not None) else None
            m["debt"].append(d)
            m["shares"].append(_near(shares, e, before=10, after=75) or wavg.get(e))
            m["equity"].append(_near(equity, e))
            for k in ("dps", "pretax", "tax"):
                m[k].append(g_(k))
        return {"quarters": quarters, "derived_q4": sorted(derived_q4 & set(quarters)), "metrics": m}

    def fetch(self, ctx):
        group = getattr(ctx, "requested_group", "core")
        if group == "company":
            info = self.company_info(ctx.ticker)
            return {"sic_sector": info.get("sic_sector"), "last_earnings_date": info.get("last_earnings_date")}
        cik = self.cik(ctx.ticker)
        if cik is None:
            return {}
        facts = self._facts(cik)
        if group == "history":
            return {"history": self._history(facts)}
        g = facts.get("us-gaap", {})
        rev = _best(g, REVENUE_TAGS, "USD")
        eps = _best(g, EPS_TAGS, "USD/shares", prefer_first=True)
        out = {}
        if rev:
            out["revenue_q"] = rev[:8]
            out["revenue_yoy"] = [_yoy_pct(rev, i) for i in range(4)]
            out["latest_quarter_end"] = rev[0]["end"]
        if eps:
            out["eps_q"] = eps[:8]
            out["eps_last"] = eps[0]["val"]
            out["eps_yoy_pct"] = [_yoy_pct(eps, i) for i in range(4)]
            ups = []
            for i in range(4):
                prior = _year_ago(eps, i)
                ups.append(None if prior is None else eps[i]["val"] > prior["val"])
            out["eps_yoy_up"] = ups

        ocf = _best(g, OCF_TAGS, "USD", builder=quarters_from_cumulative)
        capex = _best(g, CAPEX_TAGS, "USD", builder=quarters_from_cumulative)
        if ocf:
            capex_by_end = {q["end"]: q["val"] for q in capex}
            fcf_q = [{"end": q["end"], "val": q["val"] - capex_by_end.get(q["end"], 0.0), "derived": False}
                     for q in ocf[:8]]
            out["ocf_ttm"] = ttm(ocf)
            out["capex_ttm"] = ttm(capex) if capex else 0.0
            out["fcf_q"] = fcf_q
            if out["ocf_ttm"] is not None and out["capex_ttm"] is not None:
                out["fcf_ttm"] = out["ocf_ttm"] - out["capex_ttm"]

        so = facts.get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
        if so:
            out["shares_outstanding"] = float(max(so, key=lambda f: f.get("end", ""))["val"])
        return out


class MarketCap(Provider):
    name = "Market cap (computed)"
    description = "Shares outstanding (SEC) × latest close."
    needs = ["shares_outstanding", "close"]
    supplies = ["market_cap"]
    priority = 40

    def fetch(self, ctx):
        s, c = ctx.get("shares_outstanding"), ctx.get("close")
        return {"market_cap": s * c if s and c else None}


class ValuationHistory(Provider):
    name = "Valuation history (computed)"
    description = "P/E and P/S at each quarter-end: price (Public) against trailing 4 quarters (SEC)."
    needs = ["history", "bars_10y"]
    supplies = ["valuation_history"]
    priority = 45

    def fetch(self, ctx):
        h, bars = ctx.get("history"), ctx.get("bars_10y")
        if not h or bars is None or not len(bars):
            return {"valuation_history": None}
        import pandas as pd

        closes = bars["close"]
        idx = closes.index.tz_convert(None).normalize() if closes.index.tz is not None else closes.index
        closes = pd.Series(closes.values, index=idx)
        m, qs = h["metrics"], h["quarters"]
        pe, ps, price = [], [], []
        for i, e in enumerate(qs):
            p = closes[:pd.Timestamp(e)]
            px = float(p.iloc[-1]) if len(p) else None
            price.append(px)
            eps4 = m["eps"][max(0, i - 3):i + 1]
            rev4 = m["revenue"][max(0, i - 3):i + 1]
            sh = m["shares"][i]
            ok_e = len(eps4) == 4 and all(v is not None for v in eps4)
            ok_r = len(rev4) == 4 and all(v is not None for v in rev4)
            ttm_eps = sum(eps4) if ok_e else None
            pe.append(px / ttm_eps if px and ttm_eps and ttm_eps > 0 else None)
            ps.append(px * sh / sum(rev4) if px and sh and ok_r and sum(rev4) > 0 else None)
        return {"valuation_history": {"pe": pe, "ps": ps, "price": price}}
