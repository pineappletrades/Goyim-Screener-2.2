"""Price, volume and size filters."""
from engine.plugin_api import Param, Rule


class MinPrice(Rule):
    id = "min_price"
    name = "Minimum price"
    group = "Liquidity"
    needs = ["close"]
    params = [Param("min", "Price at least", 10.0, unit="$", min=0, step=1)]

    def check(self, ctx, p):
        c = ctx.get("close")
        return c >= p["min"], f"${c:.2f}"


class MinVolume(Rule):
    id = "min_volume"
    name = "Minimum average volume"
    group = "Liquidity"
    needs = ["avg_volume_50d"]
    params = [Param("min", "50-day avg volume at least", 500000, unit="shares", min=0, step=50000)]

    def check(self, ctx, p):
        v = ctx.get("avg_volume_50d")
        return v >= p["min"], f"{v / 1e6:.2f}M shares/day"


class MinMarketCap(Rule):
    id = "min_market_cap"
    name = "Minimum market cap"
    group = "Liquidity"
    needs = ["market_cap"]
    params = [Param("min_b", "Market cap at least", 0.5, unit="$ billion", min=0, step=0.5)]

    def check(self, ctx, p):
        m = ctx.get("market_cap")
        if m is None:
            return False, "no market cap data"
        return m >= p["min_b"] * 1e9, f"${m / 1e9:.1f}B"
