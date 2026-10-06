"""'All stocks' lists: every US-listed company above a market-cap floor.

Shares outstanding come from SEC (one market-wide request per quarter), prices from any
provider with a `last_prices()` method (Public.com). Cached for 7 days in data/universe_<N>B.json.
"""
import time
from datetime import datetime, timedelta

from . import store


def _providers(engine, method):
    return engine.providers_with(method)


def build(engine, min_cap_b: float, log=print, max_age_days: int = 7, force=False):
    """Return (tickers sorted by market cap, info string)."""
    name = f"universe_{min_cap_b:g}B.json"
    cached = store.load(name, None)
    if cached and not force:
        age = datetime.now() - datetime.fromisoformat(cached["built"])
        if age < timedelta(days=max_age_days) and cached.get("tickers"):
            return cached["tickers"], f"{len(cached['tickers'])} stocks over ${min_cap_b:g}B (list from {cached['built'][:10]})"

    share_src = _providers(engine, "shares_outstanding_all")
    price_src = _providers(engine, "last_prices")
    if not share_src or not price_src:
        raise RuntimeError("Building the all-stocks list needs SEC EDGAR (shares) and Public.com (prices) connected")

    t0 = time.time()
    shares = share_src[0].shares_outstanding_all()
    log(f"Universe: {len(shares)} companies with share counts from SEC")
    # Skip obvious small caps before asking for prices: even at $5,000/share they can't reach the floor.
    floor = min_cap_b * 1e9
    candidates = [t for t, s in shares.items() if s * 5000 >= floor]
    prices = price_src[0].last_prices(candidates)
    caps = {t: shares[t] * prices[t] for t in candidates if t in prices}
    tickers = sorted((t for t, c in caps.items() if c >= floor), key=lambda t: -caps[t])
    if not tickers:
        raise RuntimeError("All-stocks list came back empty (check SEC EDGAR and Public.com on Data sources)")
    store.save(name, {"built": datetime.now().isoformat(timespec="seconds"), "min_cap_b": min_cap_b,
                      "tickers": tickers, "market_caps": {t: round(caps[t]) for t in tickers},
                      "shares": {t: shares[t] for t in tickers}})
    log(f"Universe: {len(tickers)} stocks over ${min_cap_b:g}B ({time.time() - t0:.0f}s)")
    return tickers, f"{len(tickers)} stocks over ${min_cap_b:g}B (list rebuilt today)"
