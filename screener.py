"""The screener's local database: one row per US stock above a market-cap floor.

  Fundamentals  SEC market-wide 'frames' (about 80 requests for every company), refreshed weekly
  Prices        Public daily bars per stock, refreshed on each build (daily)
  Sector, last earnings date   SEC company filings, cached and refreshed gradually
Filters then run instantly on this table inside the app.
"""
import time
from datetime import datetime, timedelta

from . import store, universe

EMA_LENGTHS = [9, 10, 20, 21, 50, 100, 150, 200]
SMA_LENGTHS = [20, 50, 100, 150, 200]


def technicals(df, spy_returns=None):
    import numpy as np

    c = df["close"]
    if len(c) < 30:
        return {}
    last, prev = float(c.iloc[-1]), float(c.iloc[-2])
    out = {"price": round(last, 4), "chg_pct": (last / prev - 1) * 100 if prev else None,
           "avg_volume": float(df["volume"].iloc[-50:].mean()), "bar_date": df.index[-1].date().isoformat()}
    for name, n in (("perf_1w", 5), ("perf_1m", 21), ("perf_3m", 63), ("perf_6m", 126), ("perf_1y", 252)):
        out[name] = (last / float(c.iloc[-1 - n]) - 1) * 100 if len(c) > n else None
    year = df.index[-1].year
    prior = c[c.index.year < year]
    out["perf_ytd"] = (last / float(prior.iloc[-1]) - 1) * 100 if len(prior) else None
    for n in EMA_LENGTHS:
        out[f"ema_{n}"] = float(c.ewm(span=n, adjust=False).mean().iloc[-1])
    for n in SMA_LENGTHS:
        out[f"sma_{n}"] = float(c.rolling(n).mean().iloc[-1]) if len(c) >= n else None
    hi, lo = float(df["high"].iloc[-252:].max()), float(df["low"].iloc[-252:].min())
    out.update(high_52w=hi, low_52w=lo, from_high_pct=(last / hi - 1) * 100 if hi else None)
    if spy_returns is not None:
        r = c.pct_change().iloc[-252:]
        joined = r.to_frame("s").join(spy_returns.rename("m"), how="inner").dropna()
        if len(joined) > 100 and joined["m"].var() > 0:
            out["beta"] = float(np.cov(joined["s"], joined["m"])[0, 1] / joined["m"].var())
    return out


def derived(row):
    p, sh = row.get("price"), row.get("shares")
    cap = p * sh if p and sh else row.get("market_cap")
    row["market_cap"] = cap
    eps, rev = row.get("eps_ttm"), row.get("rev_ttm")
    row["pe"] = p / eps if p and eps and eps > 0 else None
    row["ps"] = cap / rev if cap and rev and rev > 0 else None
    if cap is not None:
        row["ev"] = cap + (row.get("debt") or 0) - (row.get("cash") or 0)
    fcf = row.get("fcf_fy")
    row["ev_fcf"] = row["ev"] / fcf if row.get("ev") and fcf and fcf > 0 else None
    row["fcf_ps"] = fcf / sh if fcf is not None and sh else None
    eq, ni = row.get("equity"), row.get("ni_ttm")
    row["roe"] = ni / eq * 100 if ni is not None and eq and eq > 0 else None
    dps = row.get("dps_ttm")
    row["div_yield"] = dps / p * 100 if dps and p else 0.0 if p else None
    g = row.get("eps_ttm_yoy")
    row["peg"] = row["pe"] / g if row.get("pe") and g and g > 0 else None
    return row


class ScreenerBuilder:
    def __init__(self, engine, log=print):
        self.engine = engine
        self.log = log
        self.status = {"running": False, "done": 0, "total": 0, "stage": "", "error": None}
        self._stop = False

    def stop(self):
        self._stop = True

    def build(self, min_cap_b=2.0, info_budget=400):
        st = self.status
        st.update(running=True, done=0, total=0, stage="Building the stock list", error=None)
        self._stop = False
        try:
            eng = self.engine
            if eng.provider_for("bars") is None:
                raise RuntimeError("Add your Public.com key on Data sources to build the screener")
            tickers, _ = universe.build(eng, float(min_cap_b), log=self.log)
            uni = store.load(f"universe_{float(min_cap_b):g}B.json", {})
            if not uni.get("shares"):          # list saved by an older version: rebuild once
                tickers, _ = universe.build(eng, float(min_cap_b), log=self.log, force=True)
                uni = store.load(f"universe_{float(min_cap_b):g}B.json", {})
            shares = uni.get("shares", {})

            # fundamentals: weekly
            fund = store.load("fundamentals_all.json", {})
            fresh = fund.get("built") and datetime.now() - datetime.fromisoformat(fund["built"]) < timedelta(days=7)
            if not fresh:
                st["stage"] = "Downloading fundamentals from SEC (weekly, about 80 requests)"
                rows, _src = eng.call_first("fundamentals_all", progress=lambda m: st.update(stage="SEC: " + m))
                fund = {"built": datetime.now().isoformat(timespec="seconds"), "rows": rows}
                store.save("fundamentals_all.json", fund)
            frows = fund.get("rows", {})

            # sector and last earnings date: cached, refreshed gradually
            info = store.load("company_info.json", {})
            now = datetime.now()
            stale = [t for t in tickers if t not in info
                     or now - datetime.fromisoformat(info[t].get("fetched", "2000-01-01")) > timedelta(days=7)]
            secs = eng.providers_with("company_info")
            if secs:
                for i, t in enumerate(stale[:info_budget]):
                    if self._stop:
                        break
                    st["stage"] = f"Company info {i + 1} of {min(len(stale), info_budget)}"
                    try:
                        info[t] = dict(secs[0].company_info(t), fetched=now.isoformat(timespec="seconds"))
                    except Exception as e:
                        self.log(f"company info {t}: {e}")
                store.save("company_info.json", info)

            # prices and technicals: every build
            from .scanner import Context
            prof = {"id": "screener", "fast": 150, "slow": 200}
            spy = Context(eng, "SPY", prof, {"values": {}, "fetched": set()}).get("bars")
            spy_ret = spy["close"].pct_change() if spy is not None else None
            out, delay = [], float(eng.settings.get("scan_delay_seconds", 0.2) or 0)
            st.update(total=len(tickers), stage="Prices and technicals")
            fails = []
            for i, t in enumerate(tickers):
                if self._stop:
                    break
                st.update(done=i, current=t)
                try:
                    df = Context(eng, t, prof, {"values": {}, "fetched": set()}).get("bars")
                    if df is None or len(df) < 30:
                        continue
                    row = {"ticker": t, "shares": shares.get(t)}
                    row.update(technicals(df, spy_ret))
                    row.update(frows.get(t, {}))
                    ci = info.get(t, {})
                    row.update(sector=ci.get("sic_sector"), industry=ci.get("sic_description"),
                               last_earnings_date=ci.get("last_earnings_date"), name=ci.get("name"))
                    out.append(derived(row))
                except Exception as e:
                    fails.append(f"{t}: {e}")
                    if len(fails) >= 5 and not out:
                        raise RuntimeError(fails[0])
                if delay:
                    time.sleep(delay)
            db = {"built": datetime.now().isoformat(timespec="seconds"), "min_cap_b": float(min_cap_b),
                  "count": len(out), "rows": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}
                                              for r in out],
                  "skipped": fails[:20], "complete": not self._stop}
            store.save("screener_db.json", db)
            st.update(done=len(tickers), stage=f"Done: {len(out)} stocks")
            return db
        except Exception as e:
            st.update(error=str(e), stage="Stopped")
            self.log(f"Screener build failed: {e}")
            raise
        finally:
            st["running"] = False
