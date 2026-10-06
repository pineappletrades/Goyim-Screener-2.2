"""Finnhub (finnhub.io) — free key available; some endpoints are paid-only.

Supplies upcoming earnings (date, time, estimates), analyst ratings, P/E and other ratios,
sector and exchange. Each group below is its own request, made only when needed.
If your plan doesn't include a group, the app falls back to the next source
(Alpha Vantage, then Financial Datasets) or shows that feature as unavailable.

API: https://finnhub.io/api/v1  (key sent as ?token=)
"""
import time
from datetime import date, timedelta

from engine.plugin_api import KeyField, Provider

BASE = "https://finnhub.io/api/v1"


def _num(v):
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def rating_from_counts(sb, b, h, s, ss):
    total = (sb or 0) + (b or 0) + (h or 0) + (s or 0) + (ss or 0)
    if not total:
        return None, None
    score = (5 * (sb or 0) + 4 * (b or 0) + 3 * (h or 0) + 2 * (s or 0) + (ss or 0)) / total
    label = ("Strong buy" if score >= 4.5 else "Buy" if score >= 3.5 else "Hold" if score >= 2.5
             else "Sell" if score >= 1.5 else "Strong sell")
    return round(score, 2), label


class Finnhub(Provider):
    name = "Finnhub"
    description = "Upcoming earnings dates and estimates, analyst ratings, P/E and other ratios, sector."
    key_fields = [KeyField("api_key", "API key", help="finnhub.io → Dashboard → API key")]
    groups = {
        "calendar": ["next_earnings_date", "next_earnings_time", "next_eps_estimate", "next_revenue_estimate",
                     "last_eps_surprise_pct"],
        "recommendation": ["analyst_rating", "analyst_score", "analyst_counts"],
        "metrics": ["pe_ratio", "ps_ratio", "pb_ratio", "beta", "dividend_yield", "roe", "gross_margin",
                    "net_margin", "high_52w", "low_52w"],
        "profile": ["sector", "exchange", "ipo_date"],
    }
    supplies = [f for fs in groups.values() for f in fs]
    expensive = True
    optional = True
    priority = 30
    signup_url = "https://finnhub.io/register"

    def _get(self, path, params):
        import requests

        ok, msg = self.ready()
        if not ok:
            raise RuntimeError(f"Finnhub: {msg}")
        params = dict(params, token=self.keys["api_key"])
        for attempt in range(3):
            r = requests.get(BASE + path, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401:
                raise RuntimeError("Finnhub rejected the API key")
            if r.status_code == 403:
                raise RuntimeError(f"your Finnhub plan doesn't include {path}")
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))      # free plan: about 60 requests a minute
                continue
            time.sleep(1 + attempt)
        raise RuntimeError(f"Finnhub error {r.status_code} on {path}")

    def fetch(self, ctx):
        group = getattr(ctx, "requested_group", "calendar")
        sym = ctx.ticker

        if group == "calendar":
            today = date.today()
            data = self._get("/calendar/earnings", {"symbol": sym, "from": (today - timedelta(days=120)).isoformat(),
                                                    "to": (today + timedelta(days=180)).isoformat()})
            rows = [r for r in (data or {}).get("earningsCalendar", []) if r.get("date")]
            future = sorted((r for r in rows if r["date"] >= today.isoformat()), key=lambda r: r["date"])
            past = sorted((r for r in rows if r["date"] < today.isoformat() and r.get("epsActual") is not None),
                          key=lambda r: r["date"], reverse=True)
            out = {"next_earnings_date": None, "next_earnings_time": None, "next_eps_estimate": None,
                   "next_revenue_estimate": None, "last_eps_surprise_pct": None}
            if future:
                n = future[0]
                out.update(next_earnings_date=n["date"],
                           next_earnings_time={"bmo": "Before open", "amc": "After close",
                                               "dmh": "During market"}.get(n.get("hour"), None),
                           next_eps_estimate=_num(n.get("epsEstimate")),
                           next_revenue_estimate=_num(n.get("revenueEstimate")))
            if past:
                a, e = _num(past[0].get("epsActual")), _num(past[0].get("epsEstimate"))
                if a is not None and e:
                    out["last_eps_surprise_pct"] = (a - e) / abs(e) * 100
            return out

        if group == "recommendation":
            rows = self._get("/stock/recommendation", {"symbol": sym}) or []
            if not rows:
                return {"analyst_rating": None, "analyst_score": None, "analyst_counts": None}
            r = max(rows, key=lambda x: x.get("period", ""))
            counts = {k: r.get(k, 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}
            score, label = rating_from_counts(*counts.values())
            return {"analyst_rating": label, "analyst_score": score, "analyst_counts": counts}

        if group == "metrics":
            m = (self._get("/stock/metric", {"symbol": sym, "metric": "all"}) or {}).get("metric", {}) or {}
            return {"pe_ratio": _num(m.get("peTTM")), "ps_ratio": _num(m.get("psTTM")),
                    "pb_ratio": _num(m.get("pbQuarterly") or m.get("pbAnnual")), "beta": _num(m.get("beta")),
                    "dividend_yield": _num(m.get("currentDividendYieldTTM")),       # already in %
                    "roe": _num(m.get("roeTTM")), "gross_margin": _num(m.get("grossMarginTTM")),
                    "net_margin": _num(m.get("netProfitMarginTTM")),
                    "high_52w": _num(m.get("52WeekHigh")), "low_52w": _num(m.get("52WeekLow"))}

        p = self._get("/stock/profile2", {"symbol": sym}) or {}
        return {"sector": p.get("finnhubIndustry") or None, "exchange": p.get("exchange") or None,
                "ipo_date": p.get("ipo") or None}

    def earnings_calendar(self, start, end):
        """Every company reporting between two dates (one request). [{symbol, date, time, eps_est, ...}]"""
        data = self._get("/calendar/earnings", {"from": start, "to": end})
        out = []
        for r in (data or {}).get("earningsCalendar", []):
            if not r.get("symbol") or not r.get("date"):
                continue
            out.append({"symbol": r["symbol"].upper(), "date": r["date"],
                        "time": {"bmo": "Before open", "amc": "After close", "dmh": "During market"}.get(r.get("hour")),
                        "eps_est": _num(r.get("epsEstimate")), "eps_act": _num(r.get("epsActual")),
                        "rev_est": _num(r.get("revenueEstimate")), "rev_act": _num(r.get("revenueActual"))})
        return out
