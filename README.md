# Goyim Screener

A Windows desktop app that finds stocks pulling back to their moving averages in an uptrend, checks that revenue and EPS are growing, grades them, and sends you a Telegram alert.

- **Today**: stocks in the buy zone, with a meter showing where price sits between the slow and fast average, plus names approaching the zone.
- **Stock detail**: click any row for a daily chart with both averages and the zone shaded, every rule's pass/fail, a swing plan, and quarterly revenue/EPS.
- **Charts**: daily candles up to 10 years with 5 EMA/SMA lines (each 2–400 with a slider), zoom, pan and range buttons. Needs Public.com.
- **Backtest**: search a ticker to see every time it pulled back to its 150/200 EMA (same rules as the Investing strategy, all adjustable) over the last 5 years of daily prices, and what happened 1 week to 1 year later, compared with buying on any day. Also in Telegram: /backtest NVDA.
- **EMA Finder**: search a ticker and it tests every EMA (or SMA) from 5 to 250 on the last 5 years to find the line price has bounced off most: tests, bounces, respect %, typical distance from the line, average rally and dip, and where price is now. One click puts it on the chart or sets an alert. Telegram: /bestema NVDA.
- **Gamma**: dealer gamma by strike from Public's option chains (read-only): call wall, put wall, gamma flip, pin vs fast-move levels and air pockets, expected move, expirations with max pain, estimated call/put premium flow, and whether your EMAs are backed by gamma. EMA alerts include a one-line gamma read. Telegram: /gamma SPY, /gamma SPY 580.
- **Research**: company header (sector, industry, market cap, website, price, after-hours, 52-week range, next earnings) and Statistics (TTM) plus a Growth (CAGR) table (1/3/5/10 years for share price, revenue, gross profit, EBITDA, operating income, net income, EPS, cash flow, FCF, dividends, shares, equity, cash): P/E, forward P/E, PEG, earnings yield, P/S, P/CF, P/FCF, FCF yield, P/B, EV/EBITDA, EV/sales, margins, ROE, ROIC, revenue CAGR 1/3/5/10y, net debt, debt/equity, dividend yield, payout and frequency. Then 14 data points per quarter for up to 10 years (price, revenue, revenue by segment, EBITDA, gross profit and margin, net income, cash from operations, free cash flow, EPS, capex, cash and debt, P/E, P/S). SEC EDGAR + Public; segments need Financial Datasets.
- **Compare**: up to 4 tickers side by side, with a TTM table and overlaid quarterly charts.
- **Rotation**: heatmap of sectors, size & style, industries, macro (or your own list) — % change 1D to 1Y, absolute or vs SPY — with a relative-rotation chart (Leading / Weakening / Lagging / Improving), rank changes, money moving in/out, and names turning up or rolling over. Telegram: /rotation.
- **Screener**: TradingView-style filters on a local table of every US stock over your size floor (rebuilt after each close). Forward P/E, analyst rating and upcoming earnings need Finnhub or Alpha Vantage; Index membership isn't available from any connected source.
- **Calendar**: earnings dates for your watchlist and companies over $10B (needs Finnhub or Alpha Vantage).
- **Alerts**: Telegram message when a ticker touches or nears any EMA/SMA, or crosses a price, with an optional extra price condition. Checked every minute during market hours while the app is open.
- **Telegram commands** (while the app is open): /check NVDA, /price, /setups, /scan, /alert NVDA ema 200, /alert TSLA below 300, /alerts, /delete 2, /watch add AMD, /earnings, /status, /help. Only your chat ID gets answers.
- **Bottom bar**: your quick watchlist with day, week, month, 2- and 3-month % change. Click a ticker to type a new one.
- **Your data stays put**: alerts, watchlists, strategies, settings and keys live in %APPDATA%\Goyim Screener, shared by every version, with a daily automatic backup and Back up / Restore / Import buttons in Settings.
- **Watchlist**: the tickers that get scanned.
- **Strategies**: any number of strategies, each with its own averages (EMA or SMA, any lengths) and rules you switch on/off and tune.
- **Data sources**: your API keys, and any new APIs you add.
- **Settings**: Telegram, automatic daily scan, account size.

## The Investing 150/200 strategy (alerts)

Scans **every US stock with a market cap over $5 billion** (about a thousand names, list rebuilt weekly) after each close and sends a Telegram alert for any stock where:

| Rule | Setting |
| --- | --- |
| Uptrend | Closed above **both** the 150 and 200 EMA (daily chart) on at least 15 of the prior 20 days |
| Touch | Today's low came within 0.5% of the 150 EMA **or** the 200 EMA, and the close held at or above that line. Each line alerts separately, and only on the first day of a touch |
| Free cash flow | Positive over the last 4 quarters (operating cash flow minus capital spending) |
| Revenue growth | Latest quarter up versus the same quarter last year |
| Diluted EPS growth | Latest quarter up versus the same quarter last year |
| Market cap | Above $5 billion |

Every number above is adjustable on the Strategies page. The alert shows the price, both EMA levels, revenue, EPS and FCF growth.

**Why alerts go to Telegram, not Public:** Public's API has no way to create price alerts; it can only place orders. The stock detail view shows each EMA's current price under **Key levels** if you want to set alerts in the Public app yourself.

---

## 1. Put the project on GitHub

1. Create a **private** repository on github.com (e.g. `goyim-screener`).
2. Upload everything in this folder (drag-and-drop on the repo page works, or `git push`).
   Make sure the hidden `.github` folder is included.

## 2. Get the .exe

GitHub builds it automatically after every upload.

1. Repo → **Actions** tab → if asked, click **"I understand my workflows, go ahead and enable them"**.
2. Click **Build Windows app** → **Run workflow** (first time only; after that it runs on every push).
3. Wait ~5 minutes for the green check, open the run, and download **Windows-app** under *Artifacts*.
4. Unzip it anywhere you like (e.g. `Documents\Goyim Screener`) and double-click **Goyim Screener.exe**.

Windows may show "Windows protected your PC" because the app isn't code-signed. Click **More info → Run anyway**. That's normal for personal apps.

Want a permanent download link? Push a tag like `v1.0.0` and the build is also published under **Releases**.

## 3. First launch

1. **Data sources** page:
   - **Public.com**: paste your API secret key and account number (public.com → Settings → API). Click **Test with AAPL**.
   - **SEC EDGAR**: type your name and email (the SEC requires it, no signup). Click **Test with AAPL**.
2. **Settings** page → **Telegram**: paste your bot token and chat ID, then **Send test message**.
3. **Today** → **Scan now**.

The app creates three folders next to `Goyim Screener.exe`: `providers`, `rules` and `data`. Your keys, strategies and watchlist live in `data`. Keys stay on your computer.

## 4. Updating the app

Download the new zip and extract it **over** your existing folder. It only replaces `Goyim Screener.exe` and `_internal`, so your `providers`, `rules` and `data` folders are kept.

On the next launch the app updates any provider or rule file you haven't edited, and adds strategies that are new in that version. If you've edited a file, yours is kept and the new version is saved beside it as `<name>.py.new`.

---

## Changing things later

| You want to… | Do this | Rebuild? |
| --- | --- | --- |
| Change EMA lengths, zone width, growth thresholds, risk % | Strategies page | No |
| Switch a rule on/off, or make a second strategy | Strategies page | No |
| Change account size, scan time, Telegram | Settings page | No |
| Add a new rule (RSI, sector filter, …) | Add a class to a file in `rules\`, then **Reload files** | No |
| Add a new API | Drop a provider file into `providers\`, then **Reload files** and add its key | No |
| Change screens or app behavior | Edit `app/`, push to GitHub, download the new build | Yes (automatic) |

### Optional data sources

Each one unlocks more features. Anything a source doesn't cover stays greyed out with a "Needs …" note, and nothing else is affected.

| Source | Cost | Unlocks |
| --- | --- | --- |
| Finnhub | Free key (some parts paid) | Upcoming earnings dates and estimates, analyst ratings, P/E, sector |
| Alpha Vantage | Free key, 25 requests/day | Forward P/E, PEG, analyst ratings and target price, sector and industry |
| Financial Datasets | Paid per request | Revenue by segment, sector and industry, ratios (PEG, ROE, EV/EBITDA) |

Paste a key on **Data sources** and click **Test with AAPL**. The message lists which parts your plan includes. When two sources supply the same data, the app uses the first that works, so if Finnhub's free plan lacks something it falls back to Alpha Vantage, then Financial Datasets.

### Adding a new API

Copy `providers\finnhub.py` as a starting point: list the fields it `supplies`, fetch them in `fetch()`, then click **Reload files** on Data sources.

Every provider says which data fields it `supplies`. Every rule says which fields it `needs`. A rule whose fields nobody supplies is shown as unavailable and skipped, so nothing breaks.

### Adding a rule

Add a class to any file in `rules\` (or a new `.py` file there):

```python
from engine.plugin_api import Param, Rule

class MaxDistanceFromSlow(Rule):
    id = "max_dist_slow"                 # unique
    name = "Not too far above slow MA"
    group = "Trend"
    needs = ["dist_slow_pct"]            # fields from any provider
    params = [Param("max", "At most", 6.0, unit="% above slow MA")]

    def check(self, ctx, p):
        d = ctx.get("dist_slow_pct")
        return d <= p["max"], f"{d:+.1f}% above slow MA"
```

Click **Reload files** and it appears in Strategies with its own switch and setting. If a file has a mistake, the Data sources page shows the error and the rest of the app keeps working. To reset a file you've edited, copy it back from `_internal\defaults`.

---

## The backup alert (works when your PC is off)

`.github/workflows/daily-scan.yml` runs the same engine on GitHub every weekday at 1:20 PM PST / 2:20 PM PDT and sends the Telegram alert.

Repo → **Settings → Secrets and variables → Actions** → add these secrets:
`PUBLIC_API_SECRET_KEY`, `PUBLIC_ACCOUNT_NUMBER`, `SEC_USER_AGENT`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
Optionally add a variable `ACCOUNT_SIZE`.

The backup uses the strategy and watchlist in `app/defaults/data/` in the repo. Edit those files on GitHub to change what it scans. If you'd rather not get two alerts on days your PC is on, turn off **Send alerts** in the app's Settings, or disable the workflow.

---

## Project layout

```
app/
  desktop.py        the window and everything the screens can call
  headless.py       the backup scan for GitHub Actions
  engine/           scanning engine, plugin loader, Telegram, scheduler
  defaults/         starting copies of providers/, rules/ and data/ (copied next to the .exe on first run)
  ui/               the screens (HTML/CSS/JS)
build/              PyInstaller recipe, icon, build.bat (manual build on your own PC)
.github/workflows/  build-windows.yml (makes the .exe), daily-scan.yml (backup alert)
```

## Known limits

- SEC data is GAAP EPS (not "adjusted"), arrives when the 10-Q is filed, and Q4 is derived from the annual report.
- Without an earnings-date source, check the next report date yourself before a swing entry.
- Public's API rate limits aren't published. If big watchlists show errors, raise **Pause between tickers** in Settings.
- The auto-scan only runs while the app is open. The GitHub backup covers the rest.
- Free cash flow is from SEC filings; banks and insurers report cash flow differently, so their FCF is less meaningful.
- A scan of every stock over $5B makes about a thousand price requests and takes several minutes.

*A shortlist tool for your own research. Not financial advice.*
