"""Telegram alerts."""
import html

import requests

MAX_LEN = 4000


def build_message(scan: dict) -> str:
    e = html.escape
    res = scan.get("results", [])
    setups = [r for r in res if r["status"] == "setup" and r.get("grade") in ("A", "B", None)]
    setups.sort(key=lambda r: (r.get("grade") or "Z", r["ticker"]))
    appr = [r for r in res if r["status"] == "approaching"]

    m = scan.get("market")
    head = [f"<b>Goyim Screener Scan — {e(str(scan.get('data_date') or scan.get('run_at', '')[:10]))}</b>"]
    if m:
        head.append(f"Market: SPY {'above' if m['above'] else 'BELOW'} its {e(m['label'])}"
                    + ("" if m["above"] else " → half size / long-term adds only"))
    head.append(f"{len(setups)} setups · {len(appr)} approaching · {scan.get('scanned', 0)} scanned")
    parts = ["\n".join(head)]
    for prof, names in (scan.get("not_checked") or {}).items():
        parts.append(f"⚠️ <b>{e(prof)}</b> didn't check: " + e("; ".join(names)) + ". Add a data source to include these.")
    if scan.get("fatal"):
        parts.append(f"⚠️ <b>Scan stopped:</b> {e(scan['fatal'])}\nFix it in the app's Data sources page.")

    for r in setups:
        tags = []
        if r.get("entered_zone_today"):
            tags.append("🆕 ENTERED ZONE")
        if r.get("reversal_candle") and not (r.get("touched_fast") or r.get("touched_slow")):
            tags.append("🟢 REVERSAL")
        if r.get("touched_fast"):
            tags.append(f"📍 TOUCHED {r.get('fast_label', 'fast MA')}")
        if r.get("touched_slow"):
            tags.append(f"📍 TOUCHED {r.get('slow_label', 'slow MA')}")
        lines = [f"<b>{e(r['ticker'])}</b>  Grade {r.get('grade') or '-'}  ·  {e(r['profile_name'])}"
                 + (f"  |  {' · '.join(tags)}" if tags else "")]
        if r.get("close") is not None:
            lines.append(f"${r['close']:.2f}  |  {r.get('fast_label', 'fast')} ${r.get('ma_fast', 0):.2f} "
                         f"({r.get('dist_fast_pct', 0):+.1f}%)  |  {r.get('slow_label', 'slow')} ${r.get('ma_slow', 0):.2f} "
                         f"({r.get('dist_slow_pct', 0):+.1f}%)")
        fund = []
        if r.get("revenue_yoy"):
            fund.append("Rev YoY " + ", ".join("n/a" if x is None else f"{x:+.0f}%" for x in r["revenue_yoy"][:4]))
        if r.get("eps_yoy_pct") and r["eps_yoy_pct"][0] is not None:
            fund.append(f"EPS YoY {r['eps_yoy_pct'][0]:+.0f}%")
        if r.get("fcf_ttm") is not None:
            fund.append(f"FCF ${r['fcf_ttm'] / 1e9:,.2f}B")
        if r.get("market_cap"):
            fund.append(f"Mkt cap ${r['market_cap'] / 1e9:,.0f}B")
        if fund:
            lines.append("  |  ".join(fund))
        p = r.get("plan")
        if p:
            lines.append(f"Plan: buy-stop ${p['entry']} · stop ${p['stop']} · {p['shares']} sh · "
                         f"2R ${p['target_2r']} · 3R ${p['target_3r']}")
        parts.append("\n".join(lines))

    if appr:
        seen = {}
        for r in appr:
            seen.setdefault(r["ticker"], r.get("dist_fast_pct"))
        near = sorted(seen.items(), key=lambda x: x[1] or 0)
        parts.append("<b>Approaching:</b> " + ", ".join(
            f"{e(t)} {d:+.1f}%" if d is not None else e(t) for t, d in near[:25])
            + (f" and {len(near) - 25} more" if len(near) > 25 else ""))
    if scan.get("errors"):
        parts.append("<i>Skipped: " + e("; ".join(scan["errors"][:8])) + "</i>")
    parts.append("<i>Shortlist only, not a buy signal. Not financial advice.</i>")
    return "\n\n".join(parts)


def send(token: str, chat_id: str, text: str) -> None:
    if not (token and chat_id):
        raise RuntimeError("Telegram bot token / chat ID not set (Settings → Telegram)")
    chunks, cur = [], ""
    for block in text.split("\n\n"):
        if cur and len(cur) + len(block) + 2 > MAX_LEN:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        chunks.append(cur)
    for c in chunks:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": c, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"Telegram error {r.status_code}: {r.text[:200]}")
