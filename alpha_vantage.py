"""Alpha Vantage (alphavantage.co) — free key available (25 requests a day on the free plan).

Supplies forward P/E, PEG, analyst ratings and target price, ratios, sector and industry
(one 'overview' request per stock), plus the next earnings date and EPS estimate.
Because the free plan allows only 25 requests a day, it's best for Research and Compare,
not for screening hundreds of stocks. Other sources are tried first where they overlap.

API: https://www.alphavantage.co/query?function=...&apikey=...
"""
import csv
import io
import time
from datetime import date

from engine.plugin_api import KeyField, Provider

BASE = "https://www.alphavantage.co/query"


def _num(v, pct=False):
    try:
        if v in (None, "", "None", "-"):
            return None
        f = float(v)
        return f * 100 if pct else f
    except (TypeError, ValueError):
        return None


class AlphaVantage(Provider):
    name = "Alpha Vantage"
    description = ("Forward P/E, PEG, analyst ratings and target price, ratios, sector and industry, "
                   "and next earnings date. Free plan: 25 requests a day.")
    key_fields = [KeyField("api_key", "API key", help="alphavantage.co → Get free API key")]
    groups = {
        "overview": ["forward_pe", "pe_ratio", "peg_ratio", "ps_ratio", "pb_ratio", "ev_to_ebitda", "beta",
                     "dividend_yield", "roe", "net_margin", "sector", "industry", "exchange",
                     "analyst_rating", "analyst_score", "analyst_counts", "analyst_target_price",
                     "high_52w", "low_52w"],
        "calendar": ["next_earnings_date", "next_eps_estimate", "next_earnings_time"],
    }
    supplies = [f for fs in groups.values() for f in fs]
    expensive = True
    optional = True
    priority = 35
    signup_url = "https://www.alphavantage.co/support/#api-key"

    def _get(self, params, as_text=False):
        import requests

        ok, msg = self.ready()
        if not ok:
            raise RuntimeError(f"Alpha Vantage: {msg}")
        r = requests.get(BASE, params=dict(params, apikey=self.keys["api_key"]), timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Alpha Vantage error {r.status_code}")
        text = r.text
        if text.lstrip().startswith("{"):
            data = r.json()
            for k in ("Note", "Information", "Error Message"):
                if k in data:
                    msg = data[k]
                    if "rate limit" in msg.lower() or "requests per day" in msg.lower() or "premium" in msg.lower():
                        raise RuntimeError("Alpha Vantage limit reached or premium-only: " + msg[:120])
                    raise RuntimeError("Alpha Vantage: " + msg[:160])
            return data
        return text if as_text else {}

    def fetch(self, ctx):
        group = getattr(ctx, "requested_group", "overview")
        sym = ctx.ticker.replace(".", "-")

        if group == "calendar":
            text = self._get({"function": "EARNINGS_CALENDAR", "symbol": sym, "horizon": "6month"}, as_text=True)
            rows = list(csv.DictReader(io.StringIO(text))) if isinstance(text, str) else []
            today = date.today().isoformat()
            future = sorted((r for r in rows if (r.get("reportDate") or "") >= today), key=lambda r: r["reportDate"])
            if not future:
                return {"next_earnings_date": None, "next_eps_estimate": None, "next_earnings_time": None}
            n = future[0]
            tod = (n.get("timeOfTheDay") or "").lower()
            return {"next_earnings_date": n["reportDate"], "next_eps_estimate": _num(n.get("estimate")),
                    "next_earnings_time": {"pre-market": "Before open", "post-market": "After close"}.get(tod)}

        o = self._get({"function": "OVERVIEW", "symbol": sym})
        if not o or not o.get("Symbol"):
            return {}
        counts = {"strongBuy": int(_num(o.get("AnalystRatingStrongBuy")) or 0),
                  "buy": int(_num(o.get("AnalystRatingBuy")) or 0),
                  "hold": int(_num(o.get("AnalystRatingHold")) or 0),
                  "sell": int(_num(o.get("AnalystRatingSell")) or 0),
                  "strongSell": int(_num(o.get("AnalystRatingStrongSell")) or 0)}
        total = sum(counts.values())
        score = label = None
        if total:
            score = round((5 * counts["strongBuy"] + 4 * counts["buy"] + 3 * counts["hold"] + 2 * counts["sell"]
                           + counts["strongSell"]) / total, 2)
            label = ("Strong buy" if score >= 4.5 else "Buy" if score >= 3.5 else "Hold" if score >= 2.5
                     else "Sell" if score >= 1.5 else "Strong sell")
        return {
            "forward_pe": _num(o.get("ForwardPE")), "pe_ratio": _num(o.get("TrailingPE") or o.get("PERatio")),
            "peg_ratio": _num(o.get("PEGRatio")), "ps_ratio": _num(o.get("PriceToSalesRatioTTM")),
            "pb_ratio": _num(o.get("PriceToBookRatio")), "ev_to_ebitda": _num(o.get("EVToEBITDA")),
            "beta": _num(o.get("Beta")), "dividend_yield": _num(o.get("DividendYield"), pct=True),
            "roe": _num(o.get("ReturnOnEquityTTM"), pct=True), "net_margin": _num(o.get("ProfitMargin"), pct=True),
            "sector": (o.get("Sector") or "").title() or None, "industry": (o.get("Industry") or "").title() or None,
            "exchange": o.get("Exchange") or None,
            "analyst_rating": label, "analyst_score": score, "analyst_counts": counts if total else None,
            "analyst_target_price": _num(o.get("AnalystTargetPrice")),
            "high_52w": _num(o.get("52WeekHigh")), "low_52w": _num(o.get("52WeekLow")),
        }

    def earnings_calendar(self, start, end):
        """Every company reporting in the next 3 months (one request), filtered to the date range."""
        text = self._get({"function": "EARNINGS_CALENDAR", "horizon": "3month"}, as_text=True)
        rows = list(csv.DictReader(io.StringIO(text))) if isinstance(text, str) else []
        out = []
        for r in rows:
            d = r.get("reportDate") or ""
            if start <= d <= end and r.get("symbol"):
                tod = (r.get("timeOfTheDay") or "").lower()
                out.append({"symbol": r["symbol"].upper(), "date": d,
                            "time": {"pre-market": "Before open", "post-market": "After close"}.get(tod),
                            "eps_est": _num(r.get("estimate")), "eps_act": None, "rev_est": None, "rev_act": None})
        return out
