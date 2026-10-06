"""Telegram commands: the app reads messages you send your bot and answers them.

Runs while the desktop app is open. Only your chat (the chat ID in Settings) gets answers;
anyone else who finds the bot is ignored. Read-only on your brokerage: no trading commands.
"""
import html
import threading
import time
from datetime import date, timedelta

import requests

from . import alerts as alerts_mod
from . import notify, store

E = html.escape
API = "https://api.telegram.org/bot{token}/{method}"

COMMANDS = [
    ("help", "Show the command list"),
    ("check", "Check a stock: /check NVDA"),
    ("price", "Price and % change: /price NVDA AAPL"),
    ("setups", "Latest scan results"),
    ("scan", "Run the scan now"),
    ("alert", "New alert: /alert NVDA ema 200 · /alert TSLA below 300"),
    ("alerts", "List your alerts"),
    ("delete", "Delete an alert: /delete 2"),
    ("watch", "Watchlist: /watch · /watch add AMD · /watch remove AMD"),
    ("earnings", "Upcoming earnings for your watchlist"),
    ("backtest", "Past 150/200 EMA pullbacks: /backtest NVDA"),
    ("bestema", "Which EMA the stock respects most: /bestema NVDA"),
    ("gamma", "Gamma walls, flip, flow: /gamma SPY · /gamma SPY 580"),
    ("rotation", "Sector rotation vs SPY: /rotation · /rotation industries"),
    ("status", "Is the app running, market open, data sources"),
]

HELP = """<b>Goyim Screener commands</b>
/check NVDA — price, 150/200 EMA distance, and whether it passes each strategy
/price NVDA AAPL — price and % change (day, week, month)
/setups — the latest scan's setups
/scan — run the scan now and send the results
/alert NVDA ema 200 — when NVDA touches its 200 EMA
/alert NVDA sma 50 — same with an SMA
/alert NVDA near ema 150 2 — within 2% of the 150 EMA
/alert TSLA below 300 · /alert TSLA above 400 · /alert TSLA cross 350
  add <code>daily</code> to repeat once a day, or <code>and below 400</code> for an extra price check
/alerts — your alerts, numbered
/delete 2 — delete alert #2 (/off 2 and /on 2 pause and resume)
/watch — show the watchlist · /watch add AMD CRWD · /watch remove AMD
/backtest NVDA — every past 150/200 EMA pullback and how it did after
/bestema NVDA — which EMA (5–250) it has bounced off most in 5 years
/gamma SPY — call/put walls, gamma flip, expected move, flow and whether the 150/200 EMA is backed
/gamma SPY 580 — will price reject at 580 or move through it fast?
/rotation — sectors leading/lagging SPY · also: /rotation style, industries, macro
/earnings — upcoming earnings for your watchlist (next 14 days)
/status — app, market and data source status
Tip: send just a ticker in capitals (NVDA) or with $ ($nvda) to check it.

Commands work while the app is open on your PC."""


class TelegramBot:
    POLL_TIMEOUT = 25

    def __init__(self, api, log=print):
        self.api = api                  # desktop.Api: reuses its scan, alerts and engine
        self.log = log
        self.offset = None
        self.token = None
        self.last_error = None
        self._stop = False
        self._commands_set_for = None

    # ---------- plumbing ----------
    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="telegram-bot").start()

    def stop(self):
        self._stop = True

    def _creds(self):
        tg = store.secrets().get("telegram", {})
        return tg.get("bot_token"), str(tg.get("chat_id") or "").strip()

    def _call(self, token, method, http_timeout=30, **params):
        r = requests.post(API.format(token=token, method=method), json=params, timeout=http_timeout)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data.get('description') or r.status_code}")
        return data.get("result")

    def reply(self, text):
        token, chat = self._creds()
        notify.send(token, chat, text)

    def _loop(self):
        while not self._stop:
            enabled = store.settings().get("telegram_commands", True)
            token, chat = self._creds()
            if not (enabled and token and chat):
                time.sleep(10)
                continue
            if token != self.token:                 # new or changed bot: start fresh
                self.token, self.offset = token, None
            try:
                if self._commands_set_for != token:
                    self._call(token, "setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])
                    self._commands_set_for = token
                params = {"timeout": self.POLL_TIMEOUT, "allowed_updates": ["message"]}
                if self.offset is not None:
                    params["offset"] = self.offset
                updates = self._call(token, "getUpdates", http_timeout=self.POLL_TIMEOUT + 10, **params) or []
                self.last_error = None
                for u in updates:
                    self.offset = u["update_id"] + 1
                    msg = u.get("message") or {}
                    if str((msg.get("chat") or {}).get("id")) != chat:
                        continue                    # not you: ignore
                    if time.time() - msg.get("date", 0) > 600:
                        continue                    # sent while the app was closed long ago
                    text = (msg.get("text") or "").strip()
                    if text:
                        threading.Thread(target=self._handle, args=(text,), daemon=True).start()
            except Exception as e:
                err = str(e)
                if err != self.last_error:
                    self.log(f"Telegram commands: {err}")
                self.last_error = err
                # 409 = a webhook is set or another copy of the app is reading this bot
                time.sleep(30 if "409" in err or "Conflict" in err or "webhook" in err.lower() else 8)

    def _handle(self, text):
        try:
            out = self.handle(text)
        except Exception as e:
            out = f"⚠️ {E(str(e))}"
        if out:
            try:
                self.reply(out)
            except Exception as e:
                self.log(f"Telegram reply failed: {e}")

    # ---------- commands ----------
    def handle(self, text):
        parts = text.split()
        cmd = parts[0].lower().lstrip("/").split("@")[0]
        args = parts[1:]
        fn = {
            "start": self.c_help, "help": self.c_help, "check": self.c_check, "price": self.c_price,
            "p": self.c_price, "setups": self.c_setups, "scan": self.c_scan, "alert": self.c_alert,
            "alerts": self.c_alerts, "delete": self.c_delete, "del": self.c_delete, "off": self.c_off,
            "on": self.c_on, "watch": self.c_watch, "watchlist": self.c_watch, "earnings": self.c_earnings,
            "status": self.c_status, "backtest": self.c_backtest, "bt": self.c_backtest,
            "bestema": self.c_bestema, "ema": self.c_bestema, "gamma": self.c_gamma, "gex": self.c_gamma,
            "rotation": self.c_rotation, "sectors": self.c_rotation,
        }.get(cmd)
        if fn is None:
            word = parts[0].lstrip("$")
            if (not text.startswith("/") and len(parts) == 1 and word.isalpha() and len(word) <= 5
                    and (parts[0].startswith("$") or word.isupper())):
                return self.c_check([word])         # "NVDA" or "$nvda" on its own = /check
            return "I don't know that one. Send /help for the list."
        return fn(args)

    def c_help(self, args):
        return HELP

    def _tickers(self, args):
        return [a.strip(",").upper() for a in args if a.strip(",")]

    def c_price(self, args):
        tickers = self._tickers(args)[:10]
        if not tickers:
            return "Which ticker? e.g. /price NVDA"
        rows = alerts_mod.bar_rows(self.api._engine, tickers)
        lines = []
        for r in rows:
            if r.get("price") is None:
                lines.append(f"<b>{E(r['ticker'])}</b> no price (check the ticker or Public.com key)")
                continue
            pc = lambda v: "–" if v is None else f"{v:+.1f}%"
            lines.append(f"<b>{E(r['ticker'])}</b> ${r['price']:,.2f}  day {pc(r['day'])} · wk {pc(r['week'])} · mo {pc(r['month'])} · 3mo {pc(r['m3'])}")
        return "\n".join(lines)

    def _ma_lines(self, t):
        from .scanner import Context
        e = self.api._engine
        try:
            bars = Context(e, t, {"id": "bot", "fast": 150, "slow": 200}, e.shared_cache(t)).get("bars")
        except Exception as ex:
            return None, f"No price data for {E(t)}: {E(str(ex))}"
        if bars is None or not len(bars):
            return None, f"No price data for {E(t)}"
        try:
            q, _ = e.call_first("quotes", [t])
            price = (q.get(t) or {}).get("last") or float(bars["close"].iloc[-1])
        except Exception:
            price = float(bars["close"].iloc[-1])
        closes = alerts_mod.completed_closes(bars)
        out = []
        for n in (150, 200):
            ma = alerts_mod.live_ma(closes, price, n, "EMA")
            if ma:
                d = (price / ma - 1) * 100
                where = "above" if d >= 0 else "below"
                out.append(f"EMA {n}: ${ma:,.2f} ({abs(d):.1f}% {where})")
        return price, "\n".join(out)

    def c_check(self, args):
        tickers = self._tickers(args)
        if not tickers:
            return "Which ticker? e.g. /check NVDA"
        t = tickers[0]
        price, ma_text = self._ma_lines(t)
        if price is None:
            return ma_text
        lines = [f"<b>{E(t)}</b> ${price:,.2f}", ma_text]
        res = self.api.check_ticker(t)
        if not res.get("ok"):
            lines.append(f"Strategies: {E(res.get('error', 'not checked'))}")
            return "\n".join(lines)
        icon = {"setup": "✅", "approaching": "🟡", "fail": "❌", "unavailable": "⚪"}
        for r in res["scan"].get("results", []):
            if r["ticker"] != t:
                continue
            failed = [x["name"] for x in r.get("rules", []) if x.get("result") == "fail"]
            near = [x["name"] for x in r.get("rules", []) if x.get("result") == "near"]
            s = f"{icon.get(r['status'], '•')} <b>{E(r.get('profile_name', r['profile']))}</b>: {r['status']}"
            if r.get("grade"):
                s += f" (grade {r['grade']})"
            if failed:
                s += "\n   missing: " + E(", ".join(failed[:4]))
            elif near:
                s += "\n   close on: " + E(", ".join(near[:4]))
            lines.append(s)
        return "\n".join(lines)

    def c_setups(self, args):
        last = store.load("last_scan.json", None)
        if not last:
            return "No scan yet. Send /scan to run one."
        return notify.build_message(last)

    def c_scan(self, args):
        if self.api._engine.status.get("running"):
            return "A scan is already running. I'll send the results when it's done."
        self.reply("🔎 Scanning… this can take a while for the all-stocks strategies.")
        res = self.api._run_scan(False)
        if not res.get("ok"):
            return f"⚠️ Scan failed: {E(res.get('error', ''))}"
        return notify.build_message(res["scan"])

    # ----- alerts -----
    def _alert_list(self):
        return self.api._alerts.alerts()

    def c_alerts(self, args):
        lst = self._alert_list()
        if not lst:
            return "No alerts. e.g. /alert NVDA ema 200"
        lines = ["<b>Your alerts</b>"]
        for i, a in enumerate(lst, 1):
            state = "" if a.get("enabled", True) else " (off)"
            lines.append(f"{i}. <b>{E(a['ticker'])}</b> {E(alerts_mod.describe(a))}{state}")
        lines.append("\n/delete N to remove · /off N to pause · /on N to resume")
        return "\n".join(lines)

    def _pick(self, args):
        lst = self._alert_list()
        if not args or not args[0].isdigit() or not (1 <= int(args[0]) <= len(lst)):
            raise ValueError(f"Send the alert number from /alerts (1 to {len(lst)})" if lst else "You have no alerts")
        return lst[int(args[0]) - 1]

    def c_delete(self, args):
        a = self._pick(args)
        self.api._alerts.delete_alert(a["id"])
        return f"🗑 Deleted: {E(a['ticker'])} {E(alerts_mod.describe(a))}"

    def c_off(self, args):
        a = self._pick(args)
        self.api._alerts.save_alert({**a, "enabled": False})
        return f"⏸ Paused: {E(a['ticker'])} {E(alerts_mod.describe(a))}"

    def c_on(self, args):
        a = self._pick(args)
        self.api._alerts.save_alert({**a, "enabled": True, "state": {}})
        return f"▶️ On: {E(a['ticker'])} {E(alerts_mod.describe(a))}"

    @staticmethod
    def parse_alert(args):
        """/alert NVDA ema 200 [above|below|either] [daily] [and below 400]
           /alert NVDA near ema 150 2
           /alert TSLA below 300 | above 400 | cross 350"""
        if len(args) < 3:
            raise ValueError("e.g. /alert NVDA ema 200 · /alert NVDA near ema 150 2 · /alert TSLA below 300")
        words = [w.lower().strip(",$") for w in args[1:]]
        a = {"ticker": args[0].upper().strip(","), "repeat": "once", "note": "Set from Telegram"}
        if "daily" in words:
            a["repeat"] = "daily"
            words.remove("daily")
        if "and" in words:                      # extra price condition
            i = words.index("and")
            tail = words[i + 1:]
            words = words[:i]
            if len(tail) >= 2 and tail[0] in ("above", "below"):
                a["and_cond"], a["and_price"] = tail[0], float(tail[1])
        if words[0] == "near":
            words = words[1:]
            a["kind"] = "ma_near"
            a["near_pct"] = float(words[2]) if len(words) > 2 else 1.0
        if words[0] in ("ema", "sma"):
            a.setdefault("kind", "ma_touch")
            a["ma_type"] = words[0].upper()
            a["length"] = int(words[1])
            if a["kind"] == "ma_touch":
                a["direction"] = next((w for w in words[2:] if w in ("above", "below", "either")), "either")
        elif words[0] in ("above", "below", "cross", "crosses"):
            a["kind"] = {"above": "price_above", "below": "price_below"}.get(words[0], "price_cross")
            a["price"] = float(words[1])
        else:
            raise ValueError("After the ticker use ema / sma / near / above / below / cross")
        return a

    def c_alert(self, args):
        a = self.parse_alert(args)
        saved = self.api._alerts.save_alert(a)
        return (f"🔔 Alert set: <b>{E(saved['ticker'])}</b> {E(alerts_mod.describe(saved))}"
                f" ({'once a day' if saved.get('repeat') == 'daily' else 'once'})\n"
                "Checked every minute during market hours while the app is open.")

    # ----- watchlist / earnings / status -----
    def c_watch(self, args):
        wl = store.watchlist()
        if not args:
            return f"<b>Watchlist ({len(wl)})</b>\n" + (E(", ".join(wl)) or "empty") + \
                "\n/watch add AMD · /watch remove AMD"
        action, tickers = args[0].lower(), self._tickers(args[1:])
        if action in ("add", "+") and tickers:
            new = wl + [t for t in tickers if t not in wl]
        elif action in ("remove", "rm", "del", "-") and tickers:
            new = [t for t in wl if t not in tickers]
        else:
            return "e.g. /watch add AMD CRWD or /watch remove AMD"
        saved = store.save_watchlist(new)
        return f"Watchlist ({len(saved)}): {E(', '.join(saved))}"

    def c_earnings(self, args):
        e = self.api._engine
        if not e.providers_with("earnings_calendar"):
            return "Earnings dates need Finnhub or Alpha Vantage (add a key on the Data sources page)."
        watch = set(store.watchlist()) | set(store.load("bar_watchlist.json", []))
        today = date.today()
        events, src = e.call_first("earnings_calendar", today.isoformat(), (today + timedelta(days=14)).isoformat())
        mine = sorted((ev for ev in events if ev["symbol"] in watch), key=lambda ev: ev["date"])
        if not mine:
            return "None of your watchlist reports in the next 14 days."
        lines = ["<b>Upcoming earnings (next 14 days)</b>"]
        for ev in mine:
            d = date.fromisoformat(ev["date"]).strftime("%a %b %d")
            extra = f" · {ev['time']}" if ev.get("time") else ""
            est = f" · EPS est ${ev['eps_est']:.2f}" if ev.get("eps_est") is not None else ""
            lines.append(f"{d}: <b>{E(ev['symbol'])}</b>{E(extra)}{est}")
        return "\n".join(lines)

    def c_backtest(self, args):
        tickers = self._tickers(args)
        if not tickers:
            return "Which ticker? e.g. /backtest NVDA"
        r = self.api.run_backtest(tickers[0])
        if not r.get("ok"):
            return f"⚠️ {E(r.get('error', 'Backtest failed'))}"
        p, evs = r["params"], r["events"]
        if not evs:
            return f"<b>{E(r['ticker'])}</b>: no pullbacks to the {p['kind']} {p['fast']}/{p['slow']} since {r['period']['start']}."
        sm = r["summary"]["all"]
        pc = lambda v: "–" if v is None else f"{v:+.1f}%"
        lines = [f"<b>{E(r['ticker'])} backtest</b> · {p['kind']} {p['fast']}/{p['slow']} pullbacks since {r['period']['start']}",
                 f"{len(evs)} pullbacks ({(r['summary'].get('fast') or {}).get('count', 0)} to the {p['fast']}, "
                 f"{(r['summary'].get('slow') or {}).get('count', 0)} to the {p['slow']})"]
        for h, label in (("20", "1 month"), ("60", "3 months"), ("252", "1 year")):
            x, b = sm["horizons"][h], r["baseline"][h]
            if x["n"]:
                lines.append(f"After {label}: {x['win']:.0f}% up, avg {pc(x['avg'])} (any day: avg {pc(b['avg'])})")
        lines.append(f"Avg deepest drop in the next 60 days: {pc(sm['avg_max_drop_60'])}")
        lines.append("\n<b>Last 5</b>")
        for e in evs[-5:][::-1]:
            lines.append(f"{e['date']} · {p['fast'] if e['line'] == 'fast' else p['slow']} · ${e['price']:,.2f} → "
                         f"1mo {pc(e['returns']['20'])}, 3mo {pc(e['returns']['60'])}")
        return "\n".join(lines)

    def c_bestema(self, args):
        tickers = self._tickers(args)
        if not tickers:
            return "Which ticker? e.g. /bestema NVDA"
        r = self.api.find_emas(tickers[0])
        if not r.get("ok"):
            return f"⚠️ {E(r.get('error', 'Failed'))}"
        if not r.get("best"):
            return f"<b>{E(r['ticker'])}</b>: no line was tested enough times since {r['period']['start']}."
        k = r["params"]["kind"]
        pc = lambda v: "–" if v is None else f"{v:+.1f}%"
        lines = [f"<b>{E(r['ticker'])}: most respected {k}</b> (since {r['period']['start']})"]
        for i, x in enumerate(r["top"][:5], 1):
            lines.append(f"{i}. {k} {x['length']}: bounced {x['bounces']}/{x['tests']} ({x['respect']:.0f}%), "
                         f"typical distance {x['avg_abs_dist']:.1f}%, now {pc(x['now_dist'])}")
        b = r["top"][0]
        lines.append(f"\nPrice is {pc(b['now_dist'])} from the {k} {b['length']} (${b['now_ma']:,.2f}). "
                     f"/alert {E(r['ticker'])} {k.lower()} {b['length']} above")
        return "\n".join(lines)

    def c_gamma(self, args):
        tickers = self._tickers(args[:1])
        if not tickers:
            return "Which ticker? e.g. /gamma SPY or /gamma SPY 580"
        t = tickers[0]
        g = self.api.get_gamma(t, "near")
        if not g.get("ok"):
            return f"⚠️ {E(g.get('error', 'Gamma failed'))}"
        money = lambda v: "–" if v is None else (("-" if v < 0 else "+") + (f"${abs(v)/1e9:.2f}B" if abs(v) >= 1e9 else f"${abs(v)/1e6:.0f}M"))
        p = lambda v: "–" if v is None else f"${v:,.2f}"
        lines = [f"<b>{E(t)} gamma</b> · price {p(g['spot'])} · next {len(g['used'])} expirations",
                 f"Dealer gamma: <b>{g['regime']}</b> ({money(g['total_gex'])} per 1%) — "
                 + ("moves damped, levels hold better" if g["regime"] == "positive" else "moves can speed up"),
                 f"Gamma flip: {p(g['flip'])}" + (f" (price {'above' if g['spot'] >= g['flip'] else 'below'})" if g.get("flip") else ""),
                 f"Call wall (ceiling/target): {p(g['call_wall_above'])} · Put wall (support): {p(g['put_wall_below'])}"]
        em = g.get("expected_move")
        if em:
            lines.append(f"Expected move to {em['date']}: ±{em['pct']:.1f}% ({p(em['low'])} – {p(em['high'])})")
        if g.get("pins"):
            lines.append("Pin/reject: " + ", ".join(f"${x:,.0f}" for x in g["pins"][:4]))
        if g.get("pockets"):
            lines.append("Air pockets (fast): " + ", ".join(f"${a:,.0f}–${b:,.0f}" for a, b in g["pockets"][:2]))
        for c in g["confirmations"]:
            if c["name"] in ("EMA 150", "EMA 200", "EMA 50"):
                lines.append(f"{c['name']} {p(c['level'])}: {c['label']}")
        f = g["flow"]
        lines.append(f"Flow today (est.): net premium {money(f['net_premium'])} · {f['tilt']}")
        if len(args) > 1:
            try:
                v = self.api.gamma_check(t, float(args[1].strip("$,")))
                if v.get("ok"):
                    word = {"reject": "likely to stall/reject", "slow": "likely to slow down",
                            "fast": "could move through fast", "open": "little in the way"}[v["verdict"]]
                    lines.append(f"\n<b>At {p(v['price'])}: {word}</b>\n{E(v['note'])}")
            except ValueError:
                pass
        return "\n".join(lines)

    def c_rotation(self, args):
        word = (args[0].lower() if args else "sectors")
        group = {"sectors": "Sectors", "sector": "Sectors", "style": "Size & style", "size": "Size & style",
                 "industries": "Industries", "industry": "Industries", "macro": "Macro", "mine": "My list",
                 "my": "My list"}.get(word, "Sectors")
        r = self.api.get_rotation(group)
        if not r.get("ok"):
            return f"⚠️ {E(r.get('error', 'Rotation failed'))}"
        sm, rows = r["summary"], {x["ticker"]: x for x in r["rows"]}
        pc = lambda v: "–" if v is None else f"{v:+.1f}%"
        lines = [f"<b>Rotation · {E(group)}</b> (vs SPY)"]
        for q, icon in (("Leading", "🟢"), ("Improving", "🔵"), ("Weakening", "🟡"), ("Lagging", "🔴")):
            if sm.get(q):
                lines.append(f"{icon} {q}: " + ", ".join(f"{t} {pc(rows[t]['rel']['1M'])}" for t in sm[q]))
        lines.append("In (1W): " + ", ".join(sm["inflow"]) + " · Out: " + ", ".join(sm["outflow"]))
        if sm.get("turning_up"):
            lines.append("Turning up: " + ", ".join(sm["turning_up"]))
        if sm.get("rolling_over"):
            lines.append("Rolling over: " + ", ".join(sm["rolling_over"]))
        lines.append("<i>% = 1-month performance vs SPY</i>")
        return "\n".join(lines)

    def c_status(self, args):
        e = self.api._engine
        ok = [p.name for p in e.providers if p.ready()[0] and getattr(p, "key_fields", None)]
        last = store.load("last_scan.json", None)
        lines = ["<b>Goyim Screener is running</b>",
                 f"Market: {'open' if alerts_mod.market_open() else 'closed'}",
                 f"Data sources connected: {E(', '.join(ok)) or 'none'}",
                 f"Active alerts: {sum(1 for a in self._alert_list() if a.get('enabled', True))}"]
        if last:
            lines.append(f"Last scan: {E(str(last.get('run_at', ''))[:16].replace('T', ' '))}")
        if e.status.get("running"):
            lines.append(f"Scanning now: {e.status.get('done', 0)}/{e.status.get('total', 0)}")
        return "\n".join(lines)
