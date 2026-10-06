"""Financial Datasets (financialdatasets.ai) — paid API, billed per request.

Supplies what SEC + Public can't: revenue by segment, sector/industry/exchange, and ready-made
ratios (P/E, PEG, ROE, EV/EBITDA, debt/equity...). To save credits, each group of fields is
its own request and is only made when a rule or screen actually needs one of its fields.

Docs: https://docs.financialdatasets.ai  (auth header: X-API-KEY)
Not available here: forward EPS estimates, analyst ratings, upcoming earnings dates.
"""
import time

from engine.plugin_api import KeyField, Provider

BASE = "https://api.financialdatasets.ai"

METRIC_FIELDS = {
    # our field name         : Financial Datasets snapshot field
    "pe_ratio": "price_to_earnings_ratio",
    "ps_ratio": "price_to_sales_ratio",
    "pb_ratio": "price_to_book_ratio",
    "peg_ratio": "peg_ratio",
    "ev_to_ebitda": "enterprise_value_to_ebitda_ratio",
    "fcf_yield": "free_cash_flow_yield",
    "roe": "return_on_equity",
    "roic": "return_on_invested_capital",
    "gross_margin": "gross_margin",
    "operating_margin": "operating_margin",
    "net_margin": "net_margin",
    "debt_to_equity": "debt_to_equity",
    "current_ratio": "current_ratio",
    "payout_ratio": "payout_ratio",
    "enterprise_value": "enterprise_value",
}


PERCENT_FIELDS = ["fcf_yield", "roe", "roic", "gross_margin", "operating_margin", "net_margin", "payout_ratio"]


class FinancialDatasets(Provider):
    name = "Financial Datasets"
    description = ("Revenue by segment, sector and industry, and valuation and quality ratios "
                   "(P/E, PEG, ROE, EV/EBITDA, margins). Paid per request; only used when needed.")
    key_fields = [KeyField("api_key", "API key", help="financialdatasets.ai → Dashboard → API keys")]
    supplies = list(METRIC_FIELDS) + ["sector", "industry", "exchange", "revenue_segments"]
    groups = {
        "metrics": list(METRIC_FIELDS),
        "company": ["sector", "industry", "exchange"],
        "segments": ["revenue_segments"],
    }
    expensive = True
    optional = True
    priority = 60          # SEC (free) wins wherever both supply the same field
    signup_url = "https://www.financialdatasets.ai"

    def _get(self, path, params):
        import requests

        ok, msg = self.ready()
        if not ok:
            raise RuntimeError(f"Financial Datasets: {msg}")
        for attempt in range(3):
            r = requests.get(BASE + path, params=params, timeout=30,
                             headers={"X-API-KEY": self.keys["api_key"]})
            if r.status_code == 200:
                return r.json()
            if r.status_code in (401, 403):
                raise RuntimeError("Financial Datasets rejected the API key")
            if r.status_code == 402:
                raise RuntimeError("Financial Datasets: out of credits")
            if r.status_code == 404:
                return {}
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Financial Datasets error {r.status_code}")

    def fetch(self, ctx):
        group = getattr(ctx, "requested_group", "metrics")
        t = ctx.ticker.replace(".", "-")
        if group == "company":
            f = self._get("/company/facts", {"ticker": t}).get("company_facts", {}) or {}
            return {"sector": f.get("sector") or f.get("sic_sector"),
                    "industry": f.get("industry") or f.get("sic_industry"),
                    "exchange": f.get("exchange")}
        if group == "segments":
            rows = self._get("/financials/segments",
                             {"ticker": t, "period": "quarterly", "limit": 40}).get("segmented_financials", [])
            out = []
            for row in rows:
                inc = row.get("income_statement") or {}
                rev = inc.get("revenue") or {}
                # revenue is grouped by breakdown type, e.g. {"segment": [...], "product": [...]}
                if isinstance(rev, dict):
                    kind = next((k for k in ("segment", "product") if rev.get(k)), None) \
                        or next((k for k, v in rev.items() if isinstance(v, list) and v), None)
                    segs = rev.get(kind, []) if kind else []
                else:
                    kind, segs = "segment", rev
                if segs:
                    out.append({"end": row.get("report_period"), "breakdown": kind,
                                "segments": [{"label": s.get("label"), "value": s.get("value")} for s in segs
                                             if isinstance(s, dict) and s.get("value") is not None]})
            out.sort(key=lambda r: r["end"] or "", reverse=True)
            return {"revenue_segments": out}
        snap = self._get("/financial-metrics/snapshot", {"ticker": t}).get("snapshot", {}) or {}
        out = {ours: snap.get(theirs) for ours, theirs in METRIC_FIELDS.items()}
        for f in PERCENT_FIELDS:                      # app convention: percentages, e.g. 31.0 = 31%
            if out.get(f) is not None:
                out[f] = out[f] * 100
        return out
