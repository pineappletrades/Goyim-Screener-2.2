"""Intraday alerts (EMA/SMA touch, near, price) and the live bottom watchlist bar.

Alerts are checked every minute during US market hours while the app is open.
Each check: one batch quote request for every alert ticker (Public), plus daily bars
(cached for the day) to know where each moving average is.
"""
import threading
import time
import uuid
from datetime import date, datetime, timedelta

from . import notify, store


def ny_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:          # no time-zone data: approximate with US Eastern daylight time
        from datetime import timezone
        return datetime.now(timezone(timedelta(hours=-4)))


def market_open(now=None):
    now = now or ny_now()
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


def completed_closes(df):
    """Daily closes excluding today's still-forming bar."""
    closes = df["close"]
    if len(closes) and df.index[-1].date() >= ny_now().date() and market_open():
        closes = closes.iloc[:-1]
    return closes


def live_ma(closes, price, n, kind="EMA"):
    """Where the daily moving average sits today if the day closed at `price`."""
    if len(closes) < n:
        return None
    if kind == "SMA":
        return (float(closes.iloc[-(n - 1):].sum()) + price) / n if n > 1 else price
    prev = float(closes.ewm(span=n, adjust=False).mean().iloc[-1])
    k = 2 / (n + 1)
    return price * k + prev * (1 - k)


def describe(a):
    t = a["ticker"]
    ma = f"{a.get('ma_type', 'EMA')} {a.get('length')}"
    kind = a["kind"]
    if kind == "ma_touch":
        d = {"above": "from above", "below": "from below"}.get(a.get("direction"), "either way")
        s = f"Touches {ma} {d}"
    elif kind == "ma_near":
        s = f"Within {a.get('near_pct', 1):g}% of {ma}"
    elif kind == "price_above":
        s = f"Price above ${a.get('price'):,.2f}"
    elif kind == "price_below":
        s = f"Price below ${a.get('price'):,.2f}"
    else:
        s = f"Price crosses ${a.get('price'):,.2f}"
    if a.get("and_price") and a.get("and_cond"):
        s += f" and {a['and_cond']} ${a['and_price']:,.2f}"
    return s


class AlertChecker:
    TOUCH_TOL = 0.25            # % from the line that counts as touching

    def __init__(self, engine, log=print):
        self.engine = engine
        self.log = log
        self.last_check = None
        self.last_error = None
        self.extra_note = None          # optional callable(alert, info) -> str, added to EMA alert messages
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    # ---------- storage ----------
    @staticmethod
    def alerts():
        return store.load("alerts.json", [])

    def save_alert(self, a):
        with self._lock:
            lst = self.alerts()
            a = dict(a)
            a["ticker"] = str(a.get("ticker", "")).strip().upper()
            if not a["ticker"]:
                raise ValueError("Enter a ticker")
            if a.get("kind") in ("ma_touch", "ma_near") and not (2 <= int(a.get("length") or 0) <= 400):
                raise ValueError("Moving average length must be 2 to 400")
            if a.get("kind", "").startswith("price") and not a.get("price"):
                raise ValueError("Enter a price")
            if not a.get("id"):
                a.update(id=uuid.uuid4().hex[:10], created=datetime.now().isoformat(timespec="seconds"),
                         enabled=True, state={})
                lst.append(a)
            else:
                lst = [dict(x, **a) if x["id"] == a["id"] else x for x in lst]
            store.save("alerts.json", lst)
            return a

    def delete_alert(self, alert_id):
        with self._lock:
            store.save("alerts.json", [x for x in self.alerts() if x["id"] != alert_id])

    @staticmethod
    def history():
        return store.load("alert_history.json", [])

    # ---------- checking ----------
    def _bars(self, ticker):
        from .scanner import Context
        return Context(self.engine, ticker, {"id": "alerts", "fast": 150, "slow": 200},
                       self.engine.shared_cache(ticker)).get("bars")

    def evaluate(self, a, price, closes):
        """Return (fired: bool, message_bits: dict, new_side)."""
        kind = a["kind"]
        state = a.get("state") or {}
        info = {"price": price}
        fired, side = False, state.get("side")
        if kind in ("ma_touch", "ma_near"):
            ma = live_ma(closes, price, int(a["length"]), a.get("ma_type", "EMA"))
            if ma is None:
                return False, info, side
            info["ma"] = ma
            dist = (price - ma) / ma * 100
            info["dist"] = dist
            new_side = "above" if dist > 0 else "below"
            if kind == "ma_near":
                fired = abs(dist) <= float(a.get("near_pct", 1))
            else:
                d = a.get("direction", "either")
                at_line = abs(dist) <= self.TOUCH_TOL
                crossed = side is not None and side != new_side
                if d == "above":
                    fired = (side in (None, "above")) and (at_line or (crossed and new_side == "below"))
                elif d == "below":
                    fired = (side in (None, "below")) and (at_line or (crossed and new_side == "above"))
                else:
                    fired = at_line or crossed
            side = new_side
        else:
            target = float(a["price"])
            new_side = "above" if price >= target else "below"
            if kind == "price_above":
                fired = price >= target
            elif kind == "price_below":
                fired = price <= target
            else:
                fired = side is not None and side != new_side
            side = new_side
        if fired and a.get("and_price") and a.get("and_cond"):
            p2 = float(a["and_price"])
            fired = price <= p2 if a["and_cond"] == "below" else price >= p2
        return fired, info, side

    def check_now(self, force=False):
        lst = [a for a in self.alerts() if a.get("enabled", True)]
        today = date.today().isoformat()
        active = []
        for a in lst:
            if a.get("expires") and a["expires"] < today:
                continue
            if a.get("repeat") == "daily" and a.get("last_fired", "")[:10] == today:
                continue
            active.append(a)
        if not active:
            return []
        quotes, _src = self.engine.call_first("quotes", sorted({a["ticker"] for a in active}))
        fired_msgs = []
        updates = {}
        for a in active:
            q = quotes.get(a["ticker"])
            if not q:
                continue
            closes = None
            if a["kind"] in ("ma_touch", "ma_near"):
                bars = self._bars(a["ticker"])
                if bars is None:
                    continue
                closes = completed_closes(bars)
            fired, info, side = self.evaluate(a, q["last"], closes)
            upd = {"state": dict(a.get("state") or {}, side=side, last_price=q["last"])}
            if fired:
                now = datetime.now()
                upd["last_fired"] = now.isoformat(timespec="seconds")
                if a.get("repeat", "once") == "once":
                    upd["enabled"] = False
                msg = self.message(a, info, now)
                if self.extra_note and a["kind"] in ("ma_touch", "ma_near"):
                    try:
                        note = self.extra_note(a, info)     # e.g. gamma confirmation from the option chain
                        if note:
                            msg += "\n" + note
                    except Exception as e:
                        self.log(f"Alert extra note failed: {e}")
                fired_msgs.append(msg)
                hist = self.history()
                hist.insert(0, {"id": a["id"], "ticker": a["ticker"], "rule": describe(a), "price": q["last"],
                                "at": now.isoformat(timespec="seconds"), "note": a.get("note", "")})
                store.save("alert_history.json", hist[:300])
            updates[a["id"]] = upd
        with self._lock:
            store.save("alerts.json", [dict(x, **updates[x["id"]]) if x["id"] in updates else x
                                       for x in self.alerts()])
        if fired_msgs:
            self._send("\n\n".join(fired_msgs))
        self.last_check = datetime.now().isoformat(timespec="seconds")
        return fired_msgs

    @staticmethod
    def message(a, info, now):
        ma = f"{a.get('ma_type', 'EMA')} {a.get('length')}"
        if a["kind"] == "ma_touch":
            head = f"📍 <b>{a['ticker']} touched its {ma}</b>"
        elif a["kind"] == "ma_near":
            head = f"👀 <b>{a['ticker']} is within {a.get('near_pct', 1):g}% of its {ma}</b>"
        else:
            head = f"🔔 <b>{a['ticker']}: {describe(a).lower()}</b>"
        lines = [head, f"Price ${info['price']:,.2f}" + (f" · {ma} ${info['ma']:,.2f}" if "ma" in info else "")]
        if a.get("note"):
            lines.append("Note: " + a["note"])
        lines.append(now.strftime("%I:%M %p").lstrip("0"))
        return "\n".join(lines)

    def _send(self, text):
        s = store.settings()
        if not s.get("telegram_enabled", True):
            return
        tg = store.secrets().get("telegram", {})
        try:
            notify.send(tg.get("bot_token"), tg.get("chat_id"), text)
        except Exception as e:
            self.log(f"Alert Telegram not sent: {e}")

    def earnings_reminder(self):
        s = store.settings()
        if not s.get("earnings_reminders", True):
            return
        st = store.load("state.json", {})
        today = date.today().isoformat()
        if st.get("earnings_reminder_sent") == today or datetime.now().hour < 17:
            return
        st["earnings_reminder_sent"] = today
        store.save("state.json", st)
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        try:
            events, _ = self.engine.call_first("earnings_calendar", tomorrow, tomorrow)
        except Exception:
            return
        mine = set(store.watchlist()) | set(store.load("bar_watchlist.json", []))
        hits = [e for e in events if e["symbol"] in mine]
        if hits:
            self._send("📅 <b>Earnings tomorrow</b>\n" + "\n".join(
                f"{e['symbol']} · {e.get('time') or 'time not set'}"
                + (f" · EPS est ${e['eps_est']:.2f}" if e.get("eps_est") is not None else "") for e in hits))

    def _loop(self):
        while True:
            try:
                if market_open():
                    self.check_now()
                self.earnings_reminder()
                self.last_error = None
            except Exception as e:
                self.last_error = str(e)
                self.log(f"Alert check failed: {e}")
            time.sleep(60)


def bar_rows(engine, tickers):
    """Bottom watchlist bar: live price and % change for day, week, month, 2 and 3 months."""
    from .scanner import Context

    if not tickers:
        return []
    try:
        quotes, _ = engine.call_first("quotes", tickers)
    except Exception:
        quotes = {}
    rows = []
    for t in tickers:
        row = {"ticker": t, "price": None, "day": None, "week": None, "month": None, "m2": None, "m3": None}
        q = quotes.get(t)
        try:
            bars = Context(engine, t, {"id": "bar", "fast": 150, "slow": 200}, engine.shared_cache(t)).get("bars")
        except Exception:
            bars = None
        if bars is not None and len(bars):
            cc = completed_closes(bars)
            price = q["last"] if q else float(bars["close"].iloc[-1])
            row["price"] = price
            prev = q.get("prev_close") if q else None
            prev = prev or (float(cc.iloc[-1]) if len(cc) else None)
            row["day"] = (price / prev - 1) * 100 if prev else None
            for key, n in (("week", 5), ("month", 21), ("m2", 42), ("m3", 63)):
                row[key] = (price / float(cc.iloc[-n]) - 1) * 100 if len(cc) >= n else None
        elif q:
            row["price"] = q["last"]
            row["day"] = (q["last"] / q["prev_close"] - 1) * 100 if q.get("prev_close") else None
        rows.append(row)
    return rows
