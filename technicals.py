"""Indicators computed from price bars, using each strategy's moving-average settings.

Edit this file to add new computed fields (e.g. RSI). Add the field name to `supplies`,
compute it in fetch(), and any rule can then use it.
"""
from engine.plugin_api import Provider


def moving_average(series, n, kind):
    if kind == "SMA":
        return series.rolling(n).mean()
    return series.ewm(span=n, adjust=False).mean()   # matches ThinkorSwim ExpAverage


class Technicals(Provider):
    name = "Technicals (computed)"
    description = "Moving averages, ATR, volume average and candle shape, computed from price bars."
    needs = ["bars"]
    per_profile = True
    priority = 20
    supplies = [
        "close", "open", "high", "low", "bar_date",
        "ma_fast", "ma_slow", "ma_fast_series", "ma_slow_series",
        "dist_fast_pct", "dist_slow_pct", "dist_fast_series",
        "atr", "avg_volume_50d", "reversal_candle", "reversal_series",
    ]

    def fetch(self, ctx):
        import pandas as pd

        df = ctx.get("bars")
        if df is None or len(df) < 60:
            return {}
        prof = ctx.profile
        kind = prof.get("ma_type", "EMA")
        fast = moving_average(df["close"], int(prof.get("fast", 150)), kind)
        slow = moving_average(df["close"], int(prof.get("slow", 200)), kind)

        prev = df["close"].shift(1)
        tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / 14, adjust=False).mean()

        rng = (df["high"] - df["low"]).where(lambda s: s > 0)
        reversal = (df["close"] > df["open"]) & ((df["close"] - df["low"]) >= 0.66 * rng)
        last = df.iloc[-1]
        dist_fast = (df["close"] / fast - 1) * 100
        return {
            "close": float(last["close"]), "open": float(last["open"]),
            "high": float(last["high"]), "low": float(last["low"]),
            "bar_date": df.index[-1].date().isoformat(),
            "ma_fast": float(fast.iloc[-1]), "ma_slow": float(slow.iloc[-1]),
            "ma_fast_series": fast, "ma_slow_series": slow,
            "dist_fast_pct": float(dist_fast.iloc[-1]),
            "dist_slow_pct": float((last["close"] / slow.iloc[-1] - 1) * 100),
            "dist_fast_series": dist_fast,
            "atr": float(atr.iloc[-1]),
            "avg_volume_50d": float(df["volume"].iloc[-50:].mean()),
            "reversal_candle": bool(reversal.iloc[-1]),
            "reversal_series": reversal.fillna(False),
        }
