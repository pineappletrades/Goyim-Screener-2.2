/* Goyim Screener desktop UI. Talks to Python through window.pywebview.api (see desktop.py). */
"use strict";

const S = { state: null, page: "today", pIdx: 0, draft: null, dirty: false, range: 252, detail: null, polling: null };
let api = null;

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n, d = 2) => (n == null || isNaN(n) ? "–" : Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const pct = (n, d = 1) => (n == null || isNaN(n) ? "–" : (n > 0 ? "+" : "") + Number(n).toFixed(d) + "%");
const money = (n) => (n == null ? "–" : Math.abs(n) >= 1e9 ? "$" + (n / 1e9).toFixed(2) + "B" : "$" + (n / 1e6).toFixed(0) + "M");
const clone = (o) => JSON.parse(JSON.stringify(o));

function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 3800);
}

async function call(name, ...args) {
  try {
    return await api[name](...args);
  } catch (e) {
    toast(`${name} failed: ${e.message || e}`, true);
    return { ok: false, error: String(e) };
  }
}

/* ---------------- boot ---------------- */
async function boot() {
  api = window.pywebview ? window.pywebview.api : window.mockApi;
  await refresh();
  bindChrome();
  show("today");
  pollScan(true);
  if (window.PAGES && window.PAGES.boot) window.PAGES.boot();
}

const darkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
function applyTheme(choice) {
  const t = choice === "system" ? (darkQuery && !darkQuery.matches ? "light" : "dark") : (choice || "dark");
  document.documentElement.dataset.theme = t;
  const tb = document.getElementById("themeBtn");
  if (tb && typeof icon === "function") tb.innerHTML = icon(t === "light" ? "moon" : "sun", 19);
  if (S.detail && S.detail.data && S.detail.data.chart) drawChart();
}
if (darkQuery) darkQuery.addEventListener("change", () => S.state && S.state.settings.theme === "system" && applyTheme("system"));

async function refresh() {
  S.state = await call("get_state");
  applyTheme(S.state.settings.theme);
  if (!S.draft || !S.dirty) S.draft = clone(S.state.profiles || []);
  $("#version").textContent = "v" + S.state.version;
  $("#autoText").textContent = S.state.auto_text || "";
  renderMarket();
  renderScanMeta();
  renderPage();
}

function bindChrome() {
  $$(".nav").forEach((b) => {
    b.insertAdjacentHTML("afterbegin", icon(b.dataset.page));
    b.onclick = () => show(b.dataset.page);
  });
  $("#searchIc").innerHTML = icon("search", 18);
  const themeBtn = $("#themeBtn");
  const setThemeIcon = () => (themeBtn.innerHTML = icon(document.documentElement.dataset.theme === "light" ? "moon" : "sun", 19));
  setThemeIcon();
  themeBtn.onclick = async () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    applyTheme(next); setThemeIcon();
    const patch = clone(S.state.settings); patch.theme = next;
    const r = await call("save_settings", patch);
    if (r.ok) S.state.settings = patch;
  };
  $("#scanBtn").onclick = startScan;
  $("#checkForm").onsubmit = (e) => {
    e.preventDefault();
    const t = $("#checkInput").value.trim().toUpperCase();
    if (!t) return;
    if (window.PAGES && window.PAGES.search && window.PAGES.search(t)) return;
    openDetail(t, (S.state.profiles[0] || {}).id);
  };
  $("#dClose").onclick = closeDetail;
  $("#detail").onclick = (e) => { if (e.target.id === "detail") closeDetail(); };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#detail").hidden) closeDetail(); });
  $("#dProfile").onchange = () => openDetail(S.detail.ticker, $("#dProfile").value);
  $$("[data-open]").forEach((b) => (b.onclick = () => call("open_folder", b.dataset.open)));
  $("#reloadPlugins").onclick = async () => {
    S.state = await call("reload_plugins");
    S.dirty = false;
    S.draft = clone(S.state.profiles);
    toast("Reloaded providers and rules");
    renderPage();
  };
  $("#addTicker").onsubmit = async (e) => {
    e.preventDefault();
    const add = $("#addTickerInput").value.split(/[\s,]+/).map((x) => x.trim().toUpperCase()).filter(Boolean);
    if (!add.length) return;
    const r = await call("save_watchlist", [...S.state.watchlist, ...add]);
    if (r.ok) { S.state.watchlist = r.watchlist; $("#addTickerInput").value = ""; renderWatchlist(); toast(`Added ${add.join(", ")}`); }
  };
}

function show(page) {
  if (S.page === "strategies" && page !== "strategies" && S.dirty && !confirm("You have unsaved strategy changes. Leave without saving?")) return;
  if (page !== "strategies" && S.dirty) { S.dirty = false; S.draft = clone(S.state.profiles); }
  S.page = page;
  $$(".nav").forEach((b) => b.classList.toggle("on", b.dataset.page === page));
  $$(".page").forEach((p) => (p.hidden = p.id !== "page-" + page));
  renderPage();
}

function renderPage() {
  const core = { today: renderToday, watchlist: renderWatchlist, strategies: renderStrategies, sources: renderSources, settings: renderSettings };
  const fn = core[S.page] || (window.PAGES || {})[S.page];
  if (fn) fn();
}

/* ---------------- top bar ---------------- */
function renderMarket() {
  const m = S.state.last_scan && S.state.last_scan.market;
  $("#market").innerHTML = m
    ? `<span class="dot" style="background:var(${m.above ? "--pass" : "--fail"})"></span>
       <span>SPY ${m.above ? "above" : "below"} its ${esc(m.label)}</span>
       <span class="muted num">${fmt(m.close)} / ${fmt(m.ma_slow)}</span>`
    : `<span class="muted">Market regime shows after the first scan</span>`;
}

function renderScanMeta(status) {
  const btn = $("#scanBtn");
  const bar = $("#progress");
  if (status && status.running) {
    btn.innerHTML = icon("x", 17) + "Stop";
    btn.classList.remove("primary");
    $("#scanMeta").textContent = `Scanning ${status.current || ""} (${status.done}/${status.total})`;
    bar.hidden = false;
    bar.firstElementChild.style.width = (status.total ? (100 * status.done) / status.total : 0) + "%";
    return;
  }
  btn.innerHTML = icon("refresh", 17) + "Scan now";
  btn.classList.add("primary");
  bar.hidden = true;
  const ls = S.state.last_scan;
  $("#scanMeta").textContent = ls ? "Last scan " + timeAgo(ls.run_at) : "No scans yet";
}

function timeAgo(iso) {
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

async function startScan() {
  const st = await call("scan_status");
  if (st.running) { await call("stop_scan"); return; }
  const r = await call("start_scan", !!S.state.settings.telegram_on_manual);
  if (!r.ok) return toast(r.error, true);
  pollScan();
}

async function pollScan(once = false) {
  clearTimeout(S.polling);
  const st = await call("scan_status");
  renderScanMeta(st);
  if (S.page === "settings") renderLog(st.app_log);
  if (st.running) {
    S.wasRunning = true;
    S.polling = setTimeout(() => pollScan(), 700);
  } else if (S.wasRunning) {
    S.wasRunning = false;
    await refresh();
    const ls = S.state.last_scan;
    if (ls) toast(`Scan done: ${ls.results.filter((r) => r.status === "setup").length} in the zone`);
  } else if (once) {
    S.polling = setTimeout(() => pollScan(true), 30000); // catch auto-scans
  }
}

/* ---------------- today ---------------- */
function profileById(id) { return (S.state.profiles || []).find((p) => p.id === id) || {}; }

function zoneParams(prof) {
  const p = (((prof.rules || {}).in_zone || {}).params) || {};
  return { above: p.above_fast ?? 2, below: p.below_slow ?? 2 };
}

function zoneMeter(r) {
  if (r.ma_fast == null || r.ma_slow == null || r.close == null) return "";
  const z = zoneParams(profileById(r.profile));
  const lo = r.ma_slow * (1 - z.below / 100), hi = r.ma_fast * (1 + z.above / 100);
  const min = Math.min(lo, r.close, r.ma_slow) * 0.985, max = Math.max(hi, r.close, r.ma_fast) * 1.015;
  const W = 150, x = (v) => ((v - min) / (max - min)) * W;
  const inZone = r.close >= lo && r.close <= hi;
  return `<div class="meter" title="Price relative to the buy zone">
    <svg width="${W}" height="22" viewBox="0 0 ${W} 22" aria-hidden="true">
      <line x1="0" y1="11" x2="${W}" y2="11" stroke="var(--line)" stroke-width="2"/>
      <rect x="${x(lo)}" y="5" width="${Math.max(2, x(hi) - x(lo))}" height="12" rx="3" fill="var(--zone)" stroke="var(--zone-edge)"/>
      <line x1="${x(r.ma_slow)}" y1="3" x2="${x(r.ma_slow)}" y2="19" stroke="var(--slow)" stroke-width="2"/>
      <line x1="${x(r.ma_fast)}" y1="3" x2="${x(r.ma_fast)}" y2="19" stroke="var(--fast)" stroke-width="2"/>
      <circle cx="${x(r.close)}" cy="11" r="5" fill="${inZone ? "var(--text)" : "var(--near)"}" stroke="var(--bg)" stroke-width="2"/>
    </svg></div>`;
}

function resultRow(r, showRev = true) {
  const tags = [];
  if (r.entered_zone_today) tags.push(`<span class="tag entry">Entered zone today</span>`);
  if (r.touched_fast) tags.push(`<span class="tag entry">Touched ${esc(r.fast_label || "fast MA")}</span>`);
  if (r.touched_slow) tags.push(`<span class="tag slow">Touched ${esc(r.slow_label || "slow MA")}</span>`);
  if (r.reversal_candle && !r.touched_fast && !r.touched_slow) tags.push(`<span class="tag rev">Reversal candle</span>`);
  const rev = r.revenue_yoy && r.revenue_yoy[0];
  return `<tr data-t="${esc(r.ticker)}" data-p="${esc(r.profile)}" tabindex="0">
    <td><div class="tk">${esc(r.ticker)}</div><div class="prof">${esc(r.profile_name)}</div></td>
    <td>${r.grade ? `<span class="grade ${esc(r.grade)}">${esc(r.grade)}</span>` : ""}</td>
    <td class="r num">${fmt(r.close)}</td>
    <td>${zoneMeter(r)}</td>
    <td class="r num">${pct(r.dist_fast_pct)}</td>
    <td class="r num">${pct(r.dist_slow_pct)}</td>
    ${showRev ? `<td class="r num ${rev > 0 ? "pos" : rev < 0 ? "neg" : ""}">${rev == null ? "–" : pct(rev, 0)}</td>` : ""}
    <td>${tags.join("")}</td>
  </tr>`;
}

function resultTable(rows, showRev = true) {
  return `<table class="res"><thead><tr>
      <th>Ticker</th><th>${showRev ? "Grade" : ""}</th><th style="text-align:right">Price</th>
      <th>Zone <span class="key" style="margin-left:6px"><i style="background:var(--slow)"></i>slow <i style="background:var(--fast)"></i>fast</span></th>
      <th style="text-align:right">vs fast</th><th style="text-align:right">vs slow</th>
      ${showRev ? `<th style="text-align:right">Revenue YoY</th>` : ""}<th></th>
    </tr></thead><tbody>${rows.map((r) => resultRow(r, showRev)).join("")}</tbody></table>`;
}

function renderToday() {
  const el = $("#todayBody");
  const ls = S.state.last_scan;
  const keysMissing = (S.state.providers || []).filter((p) => p.enabled && !p.ready && p.keys.length);
  if (!ls) {
    el.innerHTML = `<div class="empty">
      <h2>No scan yet</h2>
      ${keysMissing.length
        ? `<p>First add your keys for ${keysMissing.map((p) => esc(p.name)).join(" and ")} on the Data sources page, then run a scan.</p>
           <button class="btn primary" id="goSources">Add keys</button>`
        : `<p>Scan your watchlist of ${S.state.watchlist.length} tickers to see which ones are in the zone.</p>
           <button class="btn primary" id="firstScan">Scan now</button>`}
    </div>`;
    const g = $("#goSources"); if (g) g.onclick = () => show("sources");
    const f = $("#firstScan"); if (f) f.onclick = startScan;
    return;
  }
  const setups = ls.results.filter((r) => r.status === "setup").sort((a, b) => (a.grade || "Z").localeCompare(b.grade || "Z") || a.ticker.localeCompare(b.ticker));
  const appr = ls.results.filter((r) => r.status === "approaching").sort((a, b) => a.dist_fast_pct - b.dist_fast_pct);
  const date = ls.data_date ? new Date(ls.data_date + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" }) : "";
  el.innerHTML = `
    ${Object.entries(ls.not_checked || {}).map(([prof, names]) => `<div class="err" style="margin-bottom:12px;border-color:var(--near);background:var(--near-bg)"><b>${esc(prof)}</b> couldn't check: ${esc(names.join("; "))}. Add a data source to include these.</div>`).join("")}
    ${ls.fatal ? `<div class="err" style="margin-bottom:18px"><b>The last scan stopped early.</b> ${esc(ls.fatal)}
      <div style="margin-top:8px"><button class="btn ghost" id="fixSources">Check data sources</button></div></div>` : ""}
    <div class="today-head"><span class="big">${setups.length}</span>
      <div><h2>${setups.length === 1 ? "alert" : "alerts"} today</h2>
      <div class="muted">${esc(date)} close, ${ls.scanned} stocks scanned with ${ls.profiles.map((p) => esc(p.name)).join(" and ")}</div>
      ${ls.universes ? Object.entries(ls.universes).map(([id, info]) => `<div class="muted small">${esc((ls.profiles.find((p) => p.id === id) || {}).name || id)}: ${esc(info)}</div>`).join("") : ""}</div></div>
    <div class="group">${setups.length ? resultTable(setups) : `<div class="empty"><p>Nothing passed every rule today. Names close to the zone are listed below.</p></div>`}</div>
    <div class="group"><h3>Approaching <span class="muted">Trend rules pass and price is just above the zone</span></h3>
      ${appr.length ? resultTable(appr, false) : `<p class="muted">None right now.</p>`}</div>
    ${ls.errors && ls.errors.length ? `<div class="group"><h3>Skipped <span class="muted">${ls.errors.length} tickers couldn't be checked</span></h3>
      <div class="err"><pre>${esc(ls.errors.slice(0, 30).join("\n"))}</pre></div></div>` : ""}`;
  const fx = $("#fixSources"); if (fx) fx.onclick = () => show("sources");
  $$("tr[data-t]", el).forEach((tr) => {
    tr.onclick = () => openDetail(tr.dataset.t, tr.dataset.p);
    tr.onkeydown = (e) => { if (e.key === "Enter") openDetail(tr.dataset.t, tr.dataset.p); };
  });
}

/* ---------------- watchlist ---------------- */
function renderWatchlist() {
  const ls = S.state.last_scan;
  const status = {};
  (ls ? ls.results : []).forEach((r) => {
    if (r.status === "setup" || (r.status === "approaching" && status[r.ticker] !== "setup")) status[r.ticker] = r.status;
  });
  const wl = S.state.watchlist;
  $("#wlList").innerHTML = wl.length
    ? wl.map((t) => `<span class="chip"><span class="st ${status[t] || ""}" title="${status[t] || "not in zone"}"></span>
        <span class="t" data-t="${esc(t)}">${esc(t)}</span><button data-rm="${esc(t)}" aria-label="Remove ${esc(t)}">×</button></span>`).join("")
    : `<div class="empty"><p>Your watchlist is empty. Add the tickers you want scanned.</p></div>`;
  $$("[data-rm]").forEach((b) => (b.onclick = async () => {
    const r = await call("save_watchlist", wl.filter((x) => x !== b.dataset.rm));
    if (r.ok) { S.state.watchlist = r.watchlist; renderWatchlist(); }
  }));
  $$(".chip .t").forEach((s) => (s.onclick = () => openDetail(s.dataset.t, (S.state.profiles[0] || {}).id)));
}

/* ---------------- strategies ---------------- */
function paramInput(p, value, path, disabled) {
  const v = value ?? p.default;
  const id = "f_" + path.replace(/\W/g, "_");
  let input;
  if (p.kind === "bool") {
    input = `<label class="switch"><input type="checkbox" id="${id}" data-path="${esc(path)}" data-kind="bool" ${v ? "checked" : ""} ${disabled ? "disabled" : ""}><span></span></label>`;
  } else if (p.kind === "choice") {
    input = `<select id="${id}" data-path="${esc(path)}" ${disabled ? "disabled" : ""}>${p.choices.map((c) => `<option ${c == v ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
  } else if (p.kind === "text") {
    input = `<input id="${id}" data-path="${esc(path)}" value="${esc(v)}" ${disabled ? "disabled" : ""}>`;
  } else {
    input = `<span><input type="number" id="${id}" data-path="${esc(path)}" data-kind="number" value="${esc(v)}"
      ${p.min != null ? `min="${p.min}"` : ""} ${p.max != null ? `max="${p.max}"` : ""} step="${p.step ?? "any"}" ${disabled ? "disabled" : ""}><span class="unit">${esc(p.unit)}</span></span>`;
  }
  return `<div class="field"><label for="${id}" title="${esc(p.help || "")}">${esc(p.label)}</label>${input}</div>`;
}

function setPath(obj, path, val) {
  const keys = path.split(".");
  let o = obj;
  keys.slice(0, -1).forEach((k) => { if (o[k] == null || typeof o[k] !== "object") o[k] = {}; o = o[k]; });
  o[keys.at(-1)] = val;
}

function renderStrategies() {
  const profs = S.draft;
  if (S.pIdx >= profs.length) S.pIdx = Math.max(0, profs.length - 1);
  $("#profileTabs").innerHTML = profs.map((p, i) =>
    `<button class="tab ${i === S.pIdx ? "on" : ""}" data-i="${i}">${esc(p.name)}${p.enabled === false ? " (off)" : ""}</button>`).join("")
    + `<button class="tab" id="newProfile">New strategy</button>`;
  $$(".tab[data-i]").forEach((b) => (b.onclick = () => { S.pIdx = +b.dataset.i; renderStrategies(); }));
  $("#newProfile").onclick = () => {
    const base = clone(profs[S.pIdx] || { rules: {}, ma_type: "EMA", fast: 150, slow: 200 });
    base.name = "New strategy"; base.id = "s" + Date.now().toString(36); base.enabled = true;
    profs.push(base); S.pIdx = profs.length - 1; S.dirty = true; renderStrategies();
  };
  const p = profs[S.pIdx];
  if (!p) { $("#profileBody").innerHTML = `<div class="empty"><p>No strategies yet. Create one to start scanning.</p></div>`; return; }

  const groups = {};
  S.state.rules.forEach((r) => (groups[r.group] = groups[r.group] || []).push(r));
  const order = ["Trend", "Market", "Liquidity", "Fundamentals"];
  const gnames = Object.keys(groups).sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99));

  const ruleHtml = (r) => {
    const cfg = (p.rules || {})[r.id] || {};
    const on = cfg.enabled ?? r.default_enabled;
    const missing = r.missing.length > 0;
    return `<div class="rule ${on ? "" : "off"} ${missing ? "unavailable" : ""}">
      <label class="switch" title="${missing ? "Needs a data source" : "Turn rule on/off"}"><input type="checkbox" data-path="rules.${esc(r.id)}.enabled" data-kind="bool" ${on ? "checked" : ""} aria-label="${esc(r.name)}"><span></span></label>
      <div><div class="rname">${esc(r.name)}</div>
        ${r.description ? `<div class="rdesc">${esc(r.description)}</div>` : ""}
        ${missing ? `<div class="need">Needs a key for ${esc(r.missing_sources || r.missing.join(", "))}. Add it on Data sources and this rule starts working.</div>` : ""}
      </div>
      ${r.params.length ? `<div class="fields">${r.params.map((pp) => paramInput(pp, ((cfg.params || {})[pp.id]), `rules.${r.id}.params.${pp.id}`, false)).join("")}</div>` : ""}
    </div>`;
  };

  const g = S.state.grader, pl = S.state.planner;
  $("#profileBody").innerHTML = `
    <div class="card"><h3>Basics</h3><div class="fields">
      <div class="field"><label for="pName">Name</label><input id="pName" data-path="name" value="${esc(p.name)}"></div>
      <div class="field"><label>Included in scans</label><label class="switch"><input type="checkbox" data-path="enabled" data-kind="bool" ${p.enabled !== false ? "checked" : ""} aria-label="Included in scans"><span></span></label></div>
      <div class="field"><label for="pType">Average type</label><select id="pType" data-path="ma_type"><option ${p.ma_type !== "SMA" ? "selected" : ""}>EMA</option><option ${p.ma_type === "SMA" ? "selected" : ""}>SMA</option></select></div>
      <div class="field ma-fast"><label for="pFast">Fast average</label><span><input id="pFast" type="number" data-kind="number" data-path="fast" min="2" max="400" step="1" value="${p.fast}"><span class="unit">days</span></span></div>
      <div class="field ma-slow"><label for="pSlow">Slow average</label><span><input id="pSlow" type="number" data-kind="number" data-path="slow" min="3" max="500" step="1" value="${p.slow}"><span class="unit">days</span></span></div>
    </div>
    <div class="fields" style="margin-top:16px">
      <div class="field"><label for="pUni">Stocks to scan</label><select id="pUni" data-path="universe.source">
        <option value="watchlist" ${(p.universe || {}).source !== "all" ? "selected" : ""}>My watchlist</option>
        <option value="all" ${(p.universe || {}).source === "all" ? "selected" : ""}>All US stocks above a market cap</option></select></div>
      ${(p.universe || {}).source === "all" ? `<div class="field"><label for="pCap">Market cap at least</label><span><input id="pCap" type="number" data-kind="number" data-path="universe.min_market_cap_b" min="0.1" step="0.5" value="${esc((p.universe || {}).min_market_cap_b ?? 5)}"><span class="unit">$ billion</span></span></div>
        <p class="muted small" style="margin:0;max-width:44ch;align-self:center">The list is built from SEC share counts and Public prices, and refreshed weekly. A full scan takes several minutes.</p>` : ""}
    </div></div>
    ${gnames.map((gn) => `<div class="card"><h3>${esc(gn)}</h3>${groups[gn].map(ruleHtml).join("")}</div>`).join("")}
    ${g ? `<div class="card"><h3>${esc(g.name)}</h3><p class="muted small" style="margin:-6px 0 12px">Stocks that pass every rule get an A or a B.</p>
      <div class="fields">${g.params.map((pp) => paramInput(pp, (p.grader || {})[pp.id], "grader." + pp.id)).join("")}</div></div>` : ""}
    ${pl ? `<div class="card"><h3>${esc(pl.name)}</h3><div class="fields">
      <div class="field"><label>Show a trade plan</label><label class="switch"><input type="checkbox" data-path="planner.enabled" data-kind="bool" ${(p.planner || {}).enabled !== false ? "checked" : ""} aria-label="Show a trade plan"><span></span></label></div>
      ${pl.params.map((pp) => paramInput(pp, ((p.planner || {}).params || {})[pp.id], "planner.params." + pp.id)).join("")}</div>
      <p class="muted small" style="margin:10px 0 0">Share counts use your account size from Settings.</p></div>` : ""}
    <div class="save-bar">
      <button class="btn primary" id="saveProfiles" ${S.dirty ? "" : "disabled"}>Save changes</button>
      <button class="btn ghost" id="discardProfiles" ${S.dirty ? "" : "disabled"}>Discard</button>
      <span class="muted small">${S.dirty ? "Unsaved changes" : "Saved"}</span>
      <button class="btn danger" id="deleteProfile" style="margin-left:auto">Delete strategy</button>
    </div>`;

  $$("#profileBody [data-path]").forEach((inp) => {
    inp.addEventListener(inp.type === "checkbox" || inp.tagName === "SELECT" ? "change" : "input", () => {
      let v = inp.dataset.kind === "bool" ? inp.checked : inp.value;
      if (inp.dataset.kind === "number") v = inp.value === "" ? null : Number(inp.value);
      setPath(p, inp.dataset.path, v);
      S.dirty = true;
      if (inp.type === "checkbox" || inp.tagName === "SELECT") renderStrategies();
      else { $("#saveProfiles").disabled = false; $("#discardProfiles").disabled = false; $(".save-bar .muted").textContent = "Unsaved changes"; }
      if (inp.dataset.path === "name") $(`.tab[data-i="${S.pIdx}"]`).textContent = v;
    });
  });
  $("#saveProfiles").onclick = async () => {
    const r = await call("save_profiles", S.draft);
    if (!r.ok) return toast(r.error, true);
    S.dirty = false; await refresh(); toast("Strategies saved. They apply from the next scan.");
  };
  $("#discardProfiles").onclick = () => { S.draft = clone(S.state.profiles); S.dirty = false; renderStrategies(); };
  $("#deleteProfile").onclick = () => {
    if (!confirm(`Delete "${p.name}"? This takes effect when you save.`)) return;
    profs.splice(S.pIdx, 1); S.pIdx = 0; S.dirty = true; renderStrategies();
  };
}

/* ---------------- data sources ---------------- */
function renderSources() {
  const errs = S.state.plugin_errors || [];
  $("#pluginErrors").innerHTML = errs.map((e) => `<div class="err"><b>${esc(e.file)}</b> didn't load<pre>${esc(e.error)}</pre></div>`).join("");
  $("#providerList").innerHTML = S.state.providers.map((p, i) => `
    <div class="card" data-prov="${i}">
      <div class="prov-head">
        <label class="switch"><input type="checkbox" class="pen" ${p.enabled ? "checked" : ""} aria-label="Use ${esc(p.name)}"><span></span></label>
        <h3>${esc(p.name)}</h3>${p.optional ? `<span class="tag">Optional</span>` : ""}
        <span class="status ${p.enabled ? (p.ready ? "ok" : "bad") : ""}">${p.enabled ? esc(p.computed ? "Computed" : p.message) : "Off"}</span>
        ${p.signup_url ? `<button class="btn ghost" data-url="${esc(p.signup_url)}" style="margin-left:auto">${p.keys.some((k) => k.secret) ? "Get a key" : "Learn more"}</button>` : ""}
      </div>
      <div class="muted" style="margin-top:6px">${esc(p.description)}</div>
      <div class="supplies">${p.supplies.map((f) => `<code>${esc(f)}</code>`).join("")}</div>
      ${p.needs.length ? `<div class="muted small">Built from: ${p.needs.map(esc).join(", ")}</div>` : ""}
      ${p.keys.length ? `<div class="keyrow">
        ${p.keys.map((k) => `<div class="field"><label for="k_${i}_${esc(k.id)}" title="${esc(k.help)}">${esc(k.label)}</label>
          <input id="k_${i}_${esc(k.id)}" data-key="${esc(k.id)}" type="${k.secret ? "password" : "text"}"
            placeholder="${k.is_set ? (k.secret ? "Saved, type to replace" : "") : esc(k.help || "Not set")}" value="${k.secret ? "" : esc(k.value)}"></div>`).join("")}
        <button class="btn primary savek">Save</button>
        <button class="btn ghost testk">Test with AAPL</button>
      </div>` : ""}
    </div>`).join("");

  $$("[data-prov]").forEach((card) => {
    const p = S.state.providers[+card.dataset.prov];
    $(".pen", card).onchange = async (e) => { await call("set_provider_enabled", p.name, e.target.checked); await refresh(); };
    const sk = $(".savek", card);
    if (sk) sk.onclick = async () => {
      const keys = {};
      $$("[data-key]", card).forEach((inp) => { if (inp.value !== "" || inp.type !== "password") keys[inp.dataset.key] = inp.value; });
      const r = await call("save_provider_keys", p.name, keys);
      if (r.ok) { toast(`Saved ${p.name} keys`); await refresh(); }
    };
    const tk = $(".testk", card);
    if (tk) tk.onclick = async () => {
      tk.disabled = true; tk.textContent = "Testing";
      const r = await call("test_provider", p.name, "AAPL");
      tk.disabled = false; tk.textContent = "Test with AAPL";
      toast(r.message || (r.ok ? `${p.name} works` : `${p.name}: ${r.error}`), !r.ok);
    };
    $$("[data-url]", card).forEach((b) => (b.onclick = () => call("open_url", b.dataset.url)));
  });
}

/* ---------------- settings ---------------- */
function renderSettings() {
  const s = S.state.settings, tg = S.state.telegram, a = s.auto_scan || {};
  const th = s.theme || "dark";
  $("#settingsBody").innerHTML = `
    <div class="card"><h3>Appearance</h3>
      <div class="fields">
        <div class="field"><label for="themeSel">Theme</label>
          <select id="themeSel">
            <option value="dark" ${th === "dark" ? "selected" : ""}>Dark</option>
            <option value="light" ${th === "light" ? "selected" : ""}>Light</option>
            <option value="system" ${th === "system" ? "selected" : ""}>Match Windows</option>
          </select></div>
      </div>
    </div>
    <div class="card"><h3>Telegram alerts</h3>
      <p class="muted small" style="margin:-6px 0 12px">Create a bot with @BotFather, message it once, then open
        api.telegram.org/bot&lt;token&gt;/getUpdates to find your chat ID.</p>
      <div class="keyrow">
        <div class="field"><label for="tgTok">Bot token</label><input id="tgTok" type="password" placeholder="${tg.is_set ? "Saved, type to replace" : "123456789:ABC..."}"></div>
        <div class="field"><label for="tgChat">Chat ID</label><input id="tgChat" value="${esc(tg.chat_id)}" style="width:160px"></div>
        <button class="btn primary" id="tgSave">Save</button>
        <button class="btn ghost" id="tgTest">Send test message</button>
      </div>
      <div class="fields" style="margin-top:16px">
        ${sw("telegram_enabled", "Send alerts", s.telegram_enabled)}
        ${sw("telegram_on_manual", "Also send when I press Scan now", s.telegram_on_manual)}
        ${sw("telegram_commands", "Answer commands I send the bot", s.telegram_commands !== false)}
        ${sw("gamma_in_alerts", "Add a gamma read to EMA alerts", s.gamma_in_alerts !== false)}
      </div>
      <p class="muted small" style="margin:12px 0 0">Commands (while this app is open): /check NVDA · /price NVDA · /setups · /scan ·
        /alert NVDA ema 200 · /alert TSLA below 300 · /alerts · /delete 2 · /watch add AMD · /earnings · /status · /help.
        Only your chat ID gets answers. Type "/" in the bot chat to see the menu.</p>
    </div>
    <div class="card"><h3>Automatic scan</h3>
      <p class="muted small" style="margin:-6px 0 12px">Runs once a day while this app is open. Times are your computer's local time; the market closes at 1:00 PM Pacific.</p>
      <div class="fields">
        ${sw("auto_scan.enabled", "Scan automatically", a.enabled)}
        <div class="field"><label for="aTime">Scan at</label><input id="aTime" type="time" data-set="auto_scan.time" value="${esc(a.time || "13:20")}"></div>
        ${sw("auto_scan.weekdays_only", "Weekdays only", a.weekdays_only !== false)}
      </div>
    </div>
    <div class="card"><h3>Account and scanning</h3><div class="fields">
      <div class="field"><label for="acct">Account size for swing sizing</label><span><input id="acct" type="number" data-set="account_size" data-kind="number" min="0" step="100" value="${esc(s.account_size)}"><span class="unit">$</span></span></div>
      <div class="field"><label for="delay">Pause between tickers</label><span><input id="delay" type="number" data-set="scan_delay_seconds" data-kind="number" min="0" max="5" step="0.1" value="${esc(s.scan_delay_seconds)}"><span class="unit">seconds</span></span></div>
    </div></div>
    <div class="card"><h3>Your data and backups</h3>
      <p class="muted" style="margin:0 0 6px">Alerts, watchlists, strategies, settings and keys are saved in one folder that every version of the app uses, so downloading a new build keeps everything:</p>
      <p class="num" style="margin:0 0 12px">${esc(S.state.home)}</p>
      <div class="toolbar" style="margin:0 0 10px">
        <button class="btn primary" id="bkNow">Back up now</button>
        <button class="btn ghost" id="bkRestore">Restore from a backup…</button>
        <button class="btn ghost" id="bkImport">Import from an old app folder…</button>
        <button class="btn ghost" data-open2="data">Open data folder</button>
      </div>
      <label class="muted small" style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="bkKeys" checked> Include API keys and Telegram token in backups (keep the file private)</label>
      <div id="bkInfo" class="muted small" style="margin-top:10px"></div>
    </div>
    <div class="card"><h3>Activity</h3><div id="logBox" class="logbox">Loading</div></div>`;

  $$("#settingsBody [data-set]").forEach((inp) => inp.addEventListener("change", async () => {
    const patch = clone(S.state.settings);
    let v = inp.type === "checkbox" ? inp.checked : inp.value;
    if (inp.dataset.kind === "number") v = Number(inp.value);
    setPath(patch, inp.dataset.set, v);
    const r = await call("save_settings", patch);
    if (r.ok) { S.state.settings = patch; toast("Saved"); const st = await call("get_state"); $("#autoText").textContent = st.auto_text; }
  }));
  $("#themeSel").onchange = async (e) => {
    const patch = clone(S.state.settings);
    patch.theme = e.target.value;
    applyTheme(patch.theme);
    const r = await call("save_settings", patch);
    if (r.ok) S.state.settings = patch;
  };
  $("#tgSave").onclick = async () => {
    const r = await call("save_telegram", $("#tgTok").value || null, $("#tgChat").value);
    if (r.ok) { toast("Telegram saved"); await refresh(); }
  };
  const bkInfo = async () => {
    const r = await call("data_info");
    if (!r || !r.ok) return;
    const last = r.backups[0];
    $("#bkInfo").innerHTML = `${r.alerts} alerts and ${r.watchlist} watchlist tickers saved. ` +
      (last ? `Last backup: ${esc(new Date(last.modified).toLocaleString())} (${last.kind === "auto" ? "automatic, daily" : "manual"}).` : "No backups yet.") +
      ` Manual backups go to ${esc(r.backups_folder)}.`;
  };
  bkInfo();
  $("#bkNow").onclick = async () => {
    const r = await call("backup_now", $("#bkKeys").checked);
    if (r.ok) { toast("Backed up"); bkInfo(); } else toast(r.error, true);
  };
  $("#bkRestore").onclick = async () => {
    if (!confirm("Restore alerts, watchlists, strategies and settings from a backup file? Your current data is backed up first, so you can undo this.")) return;
    const r = await call("restore_backup", null, true);
    if (r.cancelled) return;
    if (r.ok) { toast(`Restored ${r.summary.alerts ?? ""} alerts from ${String(r.created || "").slice(0, 10)}`); await refresh(); renderSettings(); }
    else toast(r.error, true);
  };
  $("#bkImport").onclick = async () => {
    const r = await call("import_old_folder");
    if (r.cancelled) return;
    if (r.ok) { toast(`Imported ${r.files.length} files from the old folder`); await refresh(); renderSettings(); }
    else toast(r.error, true);
  };
  $("#tgTest").onclick = async () => {
    const r = await call("test_telegram");
    toast(r.ok ? "Test message sent. Check Telegram." : r.error, !r.ok);
  };
  $$("[data-open2]").forEach((b) => (b.onclick = () => call("open_folder", b.dataset.open2)));
  call("scan_status").then((st) => renderLog(st.app_log));
}

function sw(path, label, on) {
  const id = "s_" + path.replace(/\W/g, "_");
  return `<div class="field"><label for="${id}">${esc(label)}</label><label class="switch"><input type="checkbox" id="${id}" data-set="${esc(path)}" ${on ? "checked" : ""}><span></span></label></div>`;
}

function renderLog(lines) {
  const b = $("#logBox");
  if (b) b.textContent = (lines && lines.length ? lines.slice().reverse().join("\n") : "Nothing yet.");
}

/* ---------------- detail ---------------- */
async function openDetail(ticker, profileId) {
  S.detail = { ticker, profileId };
  $("#detail").hidden = false;
  $("#dTitle").textContent = ticker;
  $("#dSub").textContent = "Loading price and fundamentals";
  $("#dBody").innerHTML = "";
  $("#dProfile").innerHTML = S.state.profiles.map((p) => `<option value="${esc(p.id)}" ${p.id === profileId ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  $("#dClose").focus();
  const d = await call("get_detail", ticker, profileId);
  if (S.detail.ticker !== ticker) return;
  if (!d.ok) { $("#dSub").textContent = d.error; return; }
  S.detail.data = d;
  renderDetail();
}

function closeDetail() { $("#detail").hidden = true; S.detail = null; }

function renderDetail() {
  const d = S.detail.data, r = d.result;
  const statusText = { setup: "Passes every rule today", approaching: "Approaching the zone", fail: "Doesn't pass right now" }[r.status];
  $("#dSub").innerHTML = `<span class="num">${fmt(r.close)}</span> &nbsp; ${esc(statusText)}${r.grade ? ` &nbsp; <span class="grade ${esc(r.grade)}" style="width:22px;height:22px;font-size:12px">${esc(r.grade)}</span>` : ""}
    ${r.bar_date ? `<span class="small"> &nbsp; data through ${esc(r.bar_date)}</span>` : ""}`;

  const order = ["Trend", "Market", "Liquidity", "Fundamentals"];
  const rules = r.rules.slice().sort((a, b) => (order.indexOf(a.group) + 1 || 99) - (order.indexOf(b.group) + 1 || 99));
  const mark = { pass: "✓", fail: "✕", near: "≈", skipped: "–" };
  const p = r.plan;
  const f = d.fundamentals || {};
  $("#dBody").innerHTML = `
    ${d.chart ? `<div class="chart-wrap" id="chartWrap">
      <div class="chart-tools">
        <span class="key"><i style="background:var(--fast)"></i>${esc(d.chart.fast_label)}</span>
        <span class="key"><i style="background:var(--slow)"></i>${esc(d.chart.slow_label)}</span>
        ${d.overlays.some((o) => o.band) ? `<span class="key"><i class="zone"></i>Buy zone</span>` : ""}
        ${legendKeys(d.overlays)}
        <div class="ranges">${[["3M", 63], ["6M", 126], ["1Y", 252], ["2Y", 520]].map(([l, n]) => `<button data-n="${n}" class="${S.range === n ? "on" : ""}">${l}</button>`).join("")}</div>
      </div>
      <div id="chart"></div><div class="tip" id="tip" hidden></div></div>`
      : `<div class="err">No price data${d.chart_error ? ": " + esc(d.chart_error) : ""}. Check the Public.com key on the Data sources page.</div>`}
    <div class="two">
      <div class="card"><h3>Rule check</h3><ul class="checks">
        ${rules.map((x) => `<li class="${x.result}"><span class="mk">${mark[x.result]}</span><span>${esc(x.name)}</span><span class="d">${esc(x.detail)}</span></li>`).join("")}
      </ul></div>
      <div class="card"><h3>${p ? "Swing plan" : "Key levels"}</h3>
        ${p ? `<div class="plan">
            <div><b>${fmt(p.entry)}</b><span>Buy-stop entry</span></div>
            <div><b>${fmt(p.stop)}</b><span>Stop</span></div>
            <div><b>${p.shares}</b><span>Shares ($${fmt(p.risk_dollars, 0)} risk)</span></div>
            <div><b>${fmt(p.risk_per_share)}</b><span>Risk per share</span></div>
            <div><b>${fmt(p.target_2r)}</b><span>Target 2R (sell half)</span></div>
            <div><b>${fmt(p.target_3r)}</b><span>Target 3R</span></div></div>
            <p class="muted small" style="margin:14px 0 0">Entry is just above today's high. Only valid if tomorrow trades through it. Check the next earnings date first.</p>`
          : `<div class="plan">
              <div><b style="color:var(--fast)">${fmt(r.ma_fast)}</b><span>${esc(r.fast_label || "Fast MA")} (${pct(r.dist_fast_pct)} away)</span></div>
              <div><b style="color:var(--slow)">${fmt(r.ma_slow)}</b><span>${esc(r.slow_label || "Slow MA")} (${pct(r.dist_slow_pct)} away)</span></div>
              <div><b>${fmt(r.close)}</b><span>Last close</span></div></div>
            <p class="muted small" style="margin:14px 0 0">These levels move a little each day. Use them to set price alerts in the Public app if you want a heads-up during the day.</p>`}
      </div>
    </div>
    <div class="two">
      <div class="card bars-chart"><h3>Quarterly revenue</h3>${qBars(f.revenue_q, (v) => money(v))}
        ${f.revenue_yoy ? `<p class="muted small" style="margin:8px 0 0">YoY, newest first: <span class="num">${f.revenue_yoy.map((x) => pct(x, 0)).join(", ")}</span></p>` : ""}</div>
      <div class="card bars-chart"><h3>Quarterly EPS (diluted)</h3>${qBars(f.eps_q, (v) => "$" + v.toFixed(2))}
        ${f.market_cap ? `<p class="muted small" style="margin:8px 0 0">Market cap about ${money(f.market_cap)}</p>` : ""}</div>
    </div>
    ${f.fcf_q ? `<div class="two"><div class="card bars-chart"><h3>Quarterly free cash flow</h3>${qBars(f.fcf_q, (v) => money(v))}
        <p class="muted small" style="margin:8px 0 0">Last 4 quarters: <span class="num">${f.fcf_ttm == null ? "not enough data" : money(f.fcf_ttm)}</span> (operating cash flow minus capital spending)</p></div><div></div></div>` : ""}
    <p class="muted small">Dimmer bars are Q4 values calculated as full year minus Q1–Q3. Source: SEC filings (GAAP).</p>`;

  if (d.chart) {
    $$(".ranges button").forEach((b) => (b.onclick = () => { S.range = +b.dataset.n; renderDetail(); }));
    drawChart();
  }
}

const MARK_COLORS = { entry: "var(--fast)", reversal: "var(--pass)", touch_fast: "var(--fast)", touch_slow: "var(--slow)" };

function legendKeys(overlays) {
  const names = {};
  overlays.forEach((o) => {
    const lg = o.legend || { entry: "Entered zone", reversal: "Reversal in zone" };
    (o.markers || []).forEach((m) => { if (lg[m.kind]) names[m.kind] = lg[m.kind]; });
  });
  return Object.entries(names).map(([k, label]) =>
    `<span class="key"><i style="background:${MARK_COLORS[k] || "var(--pass)"};width:8px;height:8px;border-radius:50%"></i>${esc(label)}</span>`).join("");
}

function qBars(q, label) {
  if (!q || !q.length) return `<p class="muted">No data from a connected source.</p>`;
  const items = q.slice(0, 8).reverse();
  const W = 460, H = 150, pad = 22, bw = (W - 10) / items.length;
  const vals = items.map((x) => x.val);
  const max = Math.max(0, ...vals), min = Math.min(0, ...vals);
  const y = (v) => pad + ((max - v) / (max - min || 1)) * (H - pad * 2);
  return `<svg viewBox="0 0 ${W} ${H + 18}" role="img" aria-label="Quarterly values">
    <line x1="0" x2="${W}" y1="${y(0)}" y2="${y(0)}" stroke="var(--line)"/>
    ${items.map((x, i) => {
      const top = Math.min(y(x.val), y(0)), h = Math.max(1, Math.abs(y(x.val) - y(0)));
      const cx = 5 + i * bw + bw / 2;
      const col = x.val < 0 ? "var(--fail)" : "var(--slow)";
      return `<rect x="${5 + i * bw + bw * 0.18}" y="${top}" width="${bw * 0.64}" height="${h}" rx="2" fill="${col}" opacity="${x.derived ? 0.5 : 0.9}"><title>${esc(x.end)}: ${esc(label(x.val))}</title></rect>
        <text x="${cx}" y="${x.val < 0 ? top + h + 12 : top - 5}" text-anchor="middle" font-size="10.5" fill="var(--text)" font-family="Bahnschrift, sans-serif">${esc(label(x.val))}</text>
        <text x="${cx}" y="${H + 14}" text-anchor="middle" font-size="10" fill="var(--muted)" font-family="Bahnschrift, sans-serif">${esc(qLabel(x.end))}</text>`;
    }).join("")}</svg>`;
}

function qLabel(end) {
  const d = new Date(end + "T12:00:00");
  if (isNaN(d)) return end;
  return d.toLocaleDateString(undefined, { month: "short" }) + " '" + String(d.getFullYear()).slice(2);
}

function drawChart() {
  const d = S.detail.data, c = d.chart;
  const total = c.t.length, n = Math.min(S.range, total), s = total - n;
  const sl = (arr) => (arr ? arr.slice(s) : null);
  const T = sl(c.t), O = sl(c.o), Hh = sl(c.h), L = sl(c.l), C = sl(c.c), V = sl(c.v), F = sl(c.fast), SL = sl(c.slow);
  const ov = d.overlays.find((o) => o.band);
  const bandU = ov ? sl(ov.band.upper) : null, bandL = ov ? sl(ov.band.lower) : null;

  const wrap = $("#chart");
  const W = Math.max(600, wrap.clientWidth || 900), H = 380, VH = 54, R = 58, top = 10;
  const PH = H - VH - top - 22;
  const vals = [...L, ...Hh, ...(F || []).filter((x) => x != null), ...(SL || []).filter((x) => x != null)];
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const padv = (hi - lo) * 0.06; lo -= padv; hi += padv;
  const cw = (W - R) / n;
  const x = (i) => i * cw + cw / 2;
  const y = (v) => top + ((hi - v) / (hi - lo)) * PH;
  const vmax = Math.max(...V) || 1;
  const vy = (v) => H - 20 - (v / vmax) * VH;

  const line = (arr) => {
    let dstr = "", pen = false;
    arr.forEach((v, i) => { if (v == null) { pen = false; return; } dstr += (pen ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1); pen = true; });
    return dstr;
  };
  let band = "";
  if (bandU && bandL) {
    const pts = [];
    bandU.forEach((v, i) => v != null && pts.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`));
    for (let i = bandL.length - 1; i >= 0; i--) if (bandL[i] != null) pts.push(`${x(i).toFixed(1)},${y(bandL[i]).toFixed(1)}`);
    band = `<polygon points="${pts.join(" ")}" fill="var(--zone)"/>`;
  }
  const ticks = [];
  const step = (hi - lo) / 5;
  for (let k = 0; k <= 5; k++) ticks.push(lo + step * k);
  const months = [];
  T.forEach((t, i) => { if (i > 0 && t.slice(5, 7) !== T[i - 1].slice(5, 7)) months.push(i); });
  const every = Math.ceil(months.length / 8) || 1;
  const bodyW = Math.max(1, cw * 0.62);

  const markers = d.overlays.flatMap((o) => (o.markers || []).map((m) => ({ ...m, i: m.i - s })))
    .filter((m) => m.i >= 0 && m.i < n);

  wrap.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" id="svgc" role="img" aria-label="${esc(S.detail.ticker)} daily price chart">
    ${ticks.map((v) => `<line x1="0" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)" stroke-opacity=".45"/>
      <text x="${W - R + 8}" y="${y(v) + 4}" font-size="11" fill="var(--muted)" font-family="Bahnschrift, sans-serif">${fmt(v)}</text>`).join("")}
    ${months.filter((_, k) => k % every === 0).map((i) => `<text x="${x(i)}" y="${H - 4}" font-size="11" fill="var(--muted)" text-anchor="middle" font-family="Bahnschrift, sans-serif">${new Date(T[i] + "T12:00:00").toLocaleDateString(undefined, { month: "short", year: T[i].slice(5, 7) === "01" ? "2-digit" : undefined })}</text>`).join("")}
    ${band}
    ${V.map((v, i) => `<rect x="${x(i) - bodyW / 2}" y="${vy(v)}" width="${bodyW}" height="${H - 20 - vy(v)}" fill="${C[i] >= O[i] ? "var(--pass)" : "var(--fail)"}" opacity=".28"/>`).join("")}
    ${C.map((cl, i) => {
      const up = cl >= O[i], col = up ? "var(--pass)" : "var(--fail)";
      const yb = y(Math.max(cl, O[i])), hb = Math.max(1, Math.abs(y(cl) - y(O[i])));
      return `<line x1="${x(i)}" x2="${x(i)}" y1="${y(Hh[i])}" y2="${y(L[i])}" stroke="${col}" stroke-width="1"/>
        <rect x="${x(i) - bodyW / 2}" y="${yb}" width="${bodyW}" height="${hb}" fill="${up ? "var(--bg)" : col}" stroke="${col}" stroke-width="1"/>`;
    }).join("")}
    ${SL ? `<path d="${line(SL)}" fill="none" stroke="var(--slow)" stroke-width="2"/>` : ""}
    ${F ? `<path d="${line(F)}" fill="none" stroke="var(--fast)" stroke-width="2"/>` : ""}
    ${markers.map((m) => `<circle cx="${x(m.i)}" cy="${y(L[m.i]) + 9}" r="3.5" fill="${MARK_COLORS[m.kind] || "var(--pass)"}"><title>${esc(T[m.i])}: ${esc(m.label)}</title></circle>`).join("")}
    <line id="xh" y1="${top}" y2="${H - 20}" stroke="var(--muted)" stroke-dasharray="3 3" visibility="hidden"/>
    <rect x="0" y="0" width="${W - R}" height="${H}" fill="transparent" id="hit"/>
  </svg>`;

  const tip = $("#tip"), xh = $("#xh");
  $("#hit").onmousemove = (e) => {
    const box = e.currentTarget.getBoundingClientRect();
    const i = Math.max(0, Math.min(n - 1, Math.floor((e.clientX - box.left) / cw)));
    xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xh.setAttribute("visibility", "visible");
    const chg = i > 0 ? (C[i] / C[i - 1] - 1) * 100 : 0;
    tip.innerHTML = `${esc(T[i])}<br>O ${fmt(O[i])} H ${fmt(Hh[i])} L ${fmt(L[i])} C ${fmt(C[i])} <span class="${chg >= 0 ? "pos" : "neg"}">${pct(chg, 2)}</span>
      ${F ? `<br><span style="color:var(--fast)">${esc(c.fast_label)} ${fmt(F[i])}</span>` : ""}${SL ? ` &nbsp;<span style="color:var(--slow)">${esc(c.slow_label)} ${fmt(SL[i])}</span>` : ""}
      <br><span class="muted">Vol ${(V[i] / 1e6).toFixed(2)}M</span>`;
    tip.hidden = false;
    const left = x(i) + 24 > W - 260 ? x(i) - 250 : x(i) + 24;
    tip.style.left = left + "px"; tip.style.top = "44px";
  };
  $("#hit").onmouseleave = () => { tip.hidden = true; xh.setAttribute("visibility", "hidden"); };
}

window.addEventListener("resize", () => { if (S.detail && S.detail.data && S.detail.data.chart) drawChart(); });

/* ---------------- start ---------------- */
if (location.search.includes("mock")) {
  const s = document.createElement("script");
  s.src = "mock.js";
  s.onload = boot;
  document.body.appendChild(s);
} else if (window.pywebview && window.pywebview.api) {
  boot();
} else {
  window.addEventListener("pywebviewready", boot, { once: true });
}
