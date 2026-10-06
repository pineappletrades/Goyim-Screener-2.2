"""Gamma exposure (GEX) from an option chain: walls, gamma flip, pins vs fast zones, expirations, flow.

Convention (the common "dealer" model): customers are net long options, dealers are on the other side, so
  call GEX per strike = +gamma x open interest x 100 x spot^2 x 1%
  put  GEX per strike = -gamma x open interest x 100 x spot^2 x 1%
In $ of stock dealers buy/sell per 1% move. Positive total = dealers damp moves (buy dips, sell rips);
negative = dealers chase moves (moves can speed up). This is a model, not a fact: real positioning varies.
"""
import math
from datetime import date, datetime

R = 0.04            # risk-free rate for re-pricing gamma at other prices


def _phi(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_gamma(S, K, T, iv):
    if not (S and K and T and iv) or iv <= 0 or T <= 0:
        return 0.0
    st = iv * math.sqrt(T)
    d1 = (math.log(S / K) + (R + 0.5 * iv * iv) * T) / st
    return _phi(d1) / (S * st)


def years_to(exp, now=None):
    """Time to 4:00 PM New York on the expiration date, in years (at least one hour)."""
    try:
        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
        now = now or datetime.now(ny)
        end = datetime.fromisoformat(exp + "T16:00:00").replace(tzinfo=ny)
    except Exception:
        now = now or datetime.now()
        end = datetime.fromisoformat(exp + "T16:00:00")
    hours = max(1.0, (end - now).total_seconds() / 3600)
    return hours / (365 * 24)


def _ny_date(ts):
    if not ts:
        return None
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return ts[:10]


def _r(x, d=2):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(float(x), d)


def analyse(spot, chains, today=None, ema_levels=None, now=None):
    """spot: underlying price. chains: {expiration: [contract rows]} (rows from the Public provider).
    ema_levels: {"EMA 200": 398.5, ...} to confirm against. Returns everything the Gamma page shows."""
    today = today or date.today().isoformat()
    unit = 100 * spot * spot * 0.01
    strikes, exps = {}, []
    contracts = []                          # for re-pricing the profile
    flow = {"call_bought": 0.0, "call_sold": 0.0, "put_bought": 0.0, "put_sold": 0.0}
    hot = []
    for exp, rows in sorted(chains.items()):
        T = years_to(exp, now)
        e = {"date": exp, "days": (date.fromisoformat(exp) - date.fromisoformat(today)).days,
             "call_oi": 0, "put_oi": 0, "net_gex": 0.0, "abs_gex": 0.0, "call_vol": 0, "put_vol": 0}
        pain = {}
        for c in rows:
            K, oi, g = c["strike"], c.get("oi") or 0, c.get("gamma")
            if g is None and c.get("iv"):
                g = bs_gamma(spot, K, T, c["iv"])
            g = g or 0.0
            sign = 1 if c["type"] == "call" else -1
            gex = sign * g * oi * unit
            s = strikes.setdefault(K, {"strike": K, "call_gex": 0.0, "put_gex": 0.0, "call_oi": 0, "put_oi": 0,
                                       "call_vol": 0, "put_vol": 0, "call_prem": 0.0, "put_prem": 0.0})
            s[c["type"] + "_gex"] += gex
            s[c["type"] + "_oi"] += oi
            e[c["type"] + "_oi"] += oi
            e["net_gex"] += gex
            e["abs_gex"] += abs(gex)
            if c.get("iv") and oi:
                contracts.append((K, T, c["iv"], oi, sign))
            pain[K] = pain.get(K, 0)
            # today's flow: only contracts whose last trade was today
            vol = c.get("volume") or 0
            traded_today = _ny_date(c.get("last_ts")) == today
            if vol and traded_today:
                price = c.get("last") or c.get("mid") or 0
                prem = vol * price * 100
                s[c["type"] + "_vol"] += vol
                s[c["type"] + "_prem"] += prem
                e[c["type"] + "_vol"] += vol
                mid = c.get("mid")
                side = "bought" if (mid is None or (c.get("last") or 0) >= mid) else "sold"
                flow[f"{c['type']}_{side}"] += prem
                if prem >= 50_000:
                    hot.append({"exp": exp, "type": c["type"], "strike": K, "volume": vol, "oi": oi,
                                "premium": _r(prem, 0), "side": side, "vol_oi": _r(vol / oi, 2) if oi else None,
                                "last": c.get("last"), "bid": c.get("bid"), "ask": c.get("ask")})
        # max pain: the settlement price where option holders collectively get paid the least
        ks = sorted({c["strike"] for c in rows})
        if ks:
            def payout(P):
                tot = 0.0
                for c in rows:
                    oi = c.get("oi") or 0
                    if c["type"] == "call":
                        tot += max(0.0, P - c["strike"]) * oi
                    else:
                        tot += max(0.0, c["strike"] - P) * oi
                return tot
            e["max_pain"] = min(ks, key=payout)
        e["net_gex"], e["abs_gex"] = _r(e["net_gex"], 0), _r(e["abs_gex"], 0)
        exps.append(e)

    total_abs = sum(e["abs_gex"] or 0 for e in exps) or 1
    for e in exps:
        e["share"] = _r(100 * (e["abs_gex"] or 0) / total_abs, 1)
        e["pc_oi"] = _r(e["put_oi"] / e["call_oi"], 2) if e["call_oi"] else None

    rows = sorted(strikes.values(), key=lambda r: r["strike"])
    for r in rows:
        r["net_gex"] = r["call_gex"] + r["put_gex"]
    near = [r for r in rows if 0.75 * spot <= r["strike"] <= 1.25 * spot]
    total = sum(r["net_gex"] for r in rows)

    call_wall = max(near, key=lambda r: r["call_gex"], default=None)
    put_wall = min(near, key=lambda r: r["put_gex"], default=None)
    call_wall_above = max((r for r in near if r["strike"] >= spot), key=lambda r: r["call_gex"], default=None)
    put_wall_below = min((r for r in near if r["strike"] <= spot), key=lambda r: r["put_gex"], default=None)

    # gamma profile: total GEX if the stock were at each price (gamma re-priced with each contract's IV)
    profile = []
    lo, hi = spot * 0.85, spot * 1.15
    steps = 60
    for i in range(steps + 1):
        S = lo + (hi - lo) * i / steps
        u = 100 * S * S * 0.01
        tot = sum(sign * bs_gamma(S, K, T, iv) * oi * u for K, T, iv, oi, sign in contracts)
        profile.append({"price": _r(S), "gex": _r(tot, 0)})
    flip = None
    for a, b in zip(profile, profile[1:]):
        if a["gex"] is None or b["gex"] is None:
            continue
        if (a["gex"] <= 0 < b["gex"]) or (a["gex"] >= 0 > b["gex"]):
            x = a["price"] + (b["price"] - a["price"]) * (0 - a["gex"]) / ((b["gex"] - a["gex"]) or 1)
            if flip is None or abs(x - spot) < abs(flip - spot):
                flip = x
    regime = "positive" if total > 0 else "negative"

    # levels: big positive net GEX = pin / reject; big negative = acceleration; thin = air pocket
    biggest = max((abs(r["net_gex"]) for r in near), default=0) or 1
    pins = sorted((r for r in near if r["net_gex"] > 0.25 * biggest), key=lambda r: -r["net_gex"])[:6]
    accel = sorted((r for r in near if r["net_gex"] < -0.25 * biggest), key=lambda r: r["net_gex"])[:6]
    # air pockets: stretches near price with almost no gamma (grouped into 0.5% buckets so $1 strikes don't count)
    bucket = spot * 0.005
    buckets = {}
    for r in near:
        if 0.93 * spot <= r["strike"] <= 1.07 * spot:
            b = round((r["strike"] - spot) / bucket)
            buckets[b] = buckets.get(b, 0.0) + abs(r["net_gex"])
    bmax = max(buckets.values(), default=0) or 1
    pockets, run = [], []
    for b in range(-14, 15):
        if buckets.get(b, 0.0) < 0.06 * bmax:
            run.append(b)
        else:
            if len(run) >= 2:
                pockets.append([_r(spot + run[0] * bucket), _r(spot + run[-1] * bucket)])
            run = []
    if len(run) >= 2:
        pockets.append([_r(spot + run[0] * bucket), _r(spot + run[-1] * bucket)])
    pockets = sorted(pockets, key=lambda p: min(abs(p[0] - spot), abs(p[1] - spot)))[:4]

    # expected move into the nearest expiration from the at-the-money straddle
    exp_move = None
    if chains:
        first = sorted(chains)[0]
        rws = chains[first]
        if rws:
            atm = min({c["strike"] for c in rws}, key=lambda k: abs(k - spot))
            cm = next((c.get("mid") for c in rws if c["type"] == "call" and c["strike"] == atm), None)
            pm = next((c.get("mid") for c in rws if c["type"] == "put" and c["strike"] == atm), None)
            if cm and pm:
                exp_move = {"date": first, "dollars": _r(cm + pm), "pct": _r((cm + pm) / spot * 100),
                            "low": _r(spot - cm - pm), "high": _r(spot + cm + pm)}

    def verdict_at(price):
        """Would price likely pin/reject here or slice through? Based on GEX near the price and the regime there."""
        win = [r for r in rows if abs(r["strike"] / price - 1) <= 0.01]
        local = sum(r["net_gex"] for r in win)
        prof = min(profile, key=lambda p: abs(p["price"] - price)) if profile else None
        above_flip = flip is None or price >= flip
        strength = local / biggest if biggest else 0
        if strength > 0.4 and above_flip:
            v, note = "reject", "Heavy positive gamma here and above the flip: dealers lean against moves, price tends to stall or pin."
        elif strength > 0.15:
            v, note = "slow", "Some positive gamma here: expect hesitation, not a hard wall."
        elif strength < -0.25 or not above_flip:
            v, note = "fast", "Negative gamma here (below the flip): dealers hedge in the direction of the move, so price can move through quickly."
        else:
            v, note = "open", "Little gamma at this price: nothing much holding it, so it can pass through easily."
        return {"price": _r(price), "verdict": v, "note": note, "local_gex": _r(local, 0),
                "profile_gex": prof["gex"] if prof else None, "above_flip": above_flip}

    confirmations = []
    for name, level in (ema_levels or {}).items():
        if not level:
            continue
        side = "support" if level <= spot else "resistance"
        near_strikes = [r for r in rows if abs(r["strike"] / level - 1) <= 0.015]
        wall_close = (put_wall is not None and abs(put_wall["strike"] / level - 1) <= 0.015) if side == "support" \
            else (call_wall is not None and abs(call_wall["strike"] / level - 1) <= 0.015)
        local = sum(r["net_gex"] for r in near_strikes)
        pos_cluster = local > 0.2 * biggest
        above_flip = flip is None or level >= flip
        score = (2 if wall_close else 0) + (1 if pos_cluster else 0) + (1 if above_flip else -1)
        label = ("Strong " + side) if score >= 3 else (side.capitalize() + " backed by gamma") if score >= 1 \
            else "Weak: gamma won't help much here"
        why = []
        if wall_close:
            why.append(("put" if side == "support" else "call") + " wall within 1.5%")
        if pos_cluster:
            why.append("big positive gamma at the line")
        why.append("above the gamma flip" if above_flip else "below the gamma flip (moves can speed up)")
        confirmations.append({"name": name, "level": _r(level), "side": side, "score": score, "label": label,
                              "why": why, "dist_pct": _r((level / spot - 1) * 100)})

    out_flow = {k: _r(v, 0) for k, v in flow.items()}
    out_flow["net_premium"] = _r((flow["call_bought"] - flow["call_sold"]) - (flow["put_bought"] - flow["put_sold"]), 0)
    out_flow["call_total"] = _r(flow["call_bought"] + flow["call_sold"], 0)
    out_flow["put_total"] = _r(flow["put_bought"] + flow["put_sold"], 0)
    out_flow["tilt"] = "bullish" if out_flow["net_premium"] and out_flow["net_premium"] > 0 else "bearish"

    slim = [{k: (_r(v, 0) if k.endswith(("gex", "prem")) else v) for k, v in r.items()} for r in near]
    return {
        "spot": _r(spot), "total_gex": _r(total, 0), "regime": regime, "flip": _r(flip),
        "call_wall": call_wall and call_wall["strike"], "put_wall": put_wall and put_wall["strike"],
        "call_wall_above": call_wall_above and call_wall_above["strike"],
        "put_wall_below": put_wall_below and put_wall_below["strike"],
        "strikes": slim, "profile": profile, "expirations": exps,
        "pins": [r["strike"] for r in pins], "accel": [r["strike"] for r in accel], "pockets": pockets,
        "expected_move": exp_move, "flow": out_flow,
        "hot": sorted(hot, key=lambda h: -(h["premium"] or 0))[:12],
        "confirmations": confirmations, "verdict_at": verdict_at,
    }
