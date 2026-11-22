import { birthReturnChart, lineChart, token, type ChartHandle } from "./charts";
import { loadJson } from "./load";

type Index = {
  as_of_et: string;
  books: { id: string; mode: string; href: string }[];
  runs: { id: string; role: string; n: number; avg_return_pct: number | null; href: string }[];
};

type BookNow = {
  id: string;
  mode: string;
  strategy_id: string;
  config_path: string;
  dna_path?: string;
  as_of_et: string;
  equity: number;
  equity_source: string;
  cash: number;
  contributed: number;
  pnl: number;
  pnl_pct: number;
  positions: { symbol: string; qty?: number; entry_price?: number }[];
  n_positions?: number;
  halted: boolean;
  halt_files: string[];
  last_bar_close_et: string;
  knobs: Record<string, unknown>;
  knob_diff?: Record<string, { sleeve: unknown; dna: unknown }>;
  max_concurrent?: number;
};

type Fills = { fills: Record<string, string | number>[] };
type Equity = { points: { t: string; t_et?: string; equity: number }[] };
type Identity = { config_path: string; strategy_id: string; knobs: Record<string, unknown> };
type Run = {
  id: string;
  role: string;
  strategy_id: string;
  config_path: string;
  knobs: Record<string, unknown>;
  n: number;
  avg_return_pct: number | null;
  med_return_pct: number | null;
  min_return_pct: number | null;
  max_return_pct: number | null;
  beats_btc: number;
  open_max_obs: number;
  points: { start: string; return_pct: number; btc_return_pct?: number; beats_btc: boolean }[];
};

const FEATURED_KNOBS = [
  "max_alloc_pct",
  "max_concurrent",
  "rotate_when_full",
  "rotate_min_hold_bars",
  "atr_stop_mult",
  "mode",
  "exit_mode",
] as const;

const ROLE_ORDER = ["PRODUCTION", "RESEARCH", "REJECTED", "INVALID"] as const;
const COLLAPSED_ROLES = new Set(["REJECTED", "INVALID"]);
const SLEEVE_KEY = "viz.sleeve";
const THEME_KEY = "viz.theme";

const app = document.getElementById("app")!;
const asof = document.getElementById("asof")!;
const themeBtn = document.getElementById("theme") as HTMLButtonElement | null;

let chartHandle: ChartHandle | null = null;

function dropChart() {
  chartHandle?.remove();
  chartHandle = null;
}

function esc(s: unknown): string {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function money(n: number): string {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function signedMoney(n: number): string {
  const core = money(Math.abs(n));
  if (n > 0) return `+${core}`;
  if (n < 0) return `−${core}`;
  return core;
}

function pct(n: number): string {
  const body = `${n.toFixed(2)}%`;
  if (n > 0) return `+${body}`;
  return body;
}

function compactNum(n: unknown): string {
  const v = typeof n === "number" ? n : Number(n);
  if (!Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  const opts: Intl.NumberFormatOptions =
    a >= 1000
      ? { maximumFractionDigits: 2 }
      : a >= 1
        ? { maximumFractionDigits: 4 }
        : a >= 0.01
          ? { maximumFractionDigits: 6 }
          : { maximumFractionDigits: 8 };
  return v.toLocaleString("en-US", opts);
}

function route(): { name: string; rest: string[] } {
  const raw = (location.hash.replace(/^#/, "") || "/book").split("?")[0];
  const parts = raw.split("/").filter(Boolean);
  return { name: parts[0] || "book", rest: parts.slice(1) };
}

function setNav(name: string) {
  document.querySelectorAll("nav a").forEach((a) => {
    const on = a.getAttribute("data-nav") === name;
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}

function setTitle(parts: string[]) {
  document.title = [...parts, "pro-trader viz"].filter(Boolean).join(" · ");
}

function defaultBookId(index: Index): string | undefined {
  try {
    const stored = localStorage.getItem(SLEEVE_KEY);
    if (stored && index.books.some((b) => b.id === stored)) return stored;
  } catch {
    /* private mode */
  }
  const live = index.books.find((b) => b.id === "live" || b.mode === "LIVE");
  return live?.id || index.books[0]?.id;
}

function rememberSleeve(id: string) {
  try {
    localStorage.setItem(SLEEVE_KEY, id);
  } catch {
    /* private mode */
  }
}

function knobRow(k: string, v: unknown, featured = false): string {
  return `<dt class="${featured ? "mono" : ""}">${esc(k)}</dt><dd><code>${esc(String(v))}</code></dd>`;
}

function knobTable(knobs: Record<string, unknown>, featuredFirst = false): string {
  const keys = Object.keys(knobs);
  const ordered = featuredFirst
    ? [...FEATURED_KNOBS.filter((k) => k in knobs), ...keys.filter((k) => !FEATURED_KNOBS.includes(k as (typeof FEATURED_KNOBS)[number]))]
    : keys;
  return `<dl>${ordered.map((k) => knobRow(k, knobs[k], FEATURED_KNOBS.includes(k as (typeof FEATURED_KNOBS)[number]))).join("")}</dl>`;
}

function overlayPeer(index: Index, a: string): string | undefined {
  const prefer = index.runs.filter((r) => r.id !== a && (r.role === "PRODUCTION" || r.role === "RESEARCH"));
  if (prefer[0]) return prefer[0].id;
  return index.runs.find((r) => r.id !== a && r.role !== "REJECTED" && r.role !== "INVALID")?.id;
}

function runTable(rows: Index["runs"]): string {
  if (!rows.length) return "";
  return `<div class="table-wrap"><table class="data"><thead><tr><th>Run</th><th>Role</th><th class="num">n</th><th class="num">Avg %</th></tr></thead>
    <tbody>${rows
      .map(
        (r) =>
          `<tr><td><a href="#/run/${esc(r.id)}">${esc(r.id)}</a></td><td class="mode ${esc(r.role)}">${esc(r.role)}</td><td class="num">${r.n}</td><td class="num">${r.avg_return_pct ?? "—"}</td></tr>`,
      )
      .join("")}</tbody></table></div>`;
}

function sleeveSwitch(index: Index, id: string): string {
  return `<div class="sleeves" role="tablist" aria-label="Sleeve">${index.books
    .map((b) => {
      const on = b.id === id;
      return `<a role="tab" aria-selected="${on}" class="${esc(b.mode)}${on ? " on" : ""}" href="#/book/${esc(b.id)}">${esc(b.mode)}</a>`;
    })
    .join("")}</div>`;
}

async function renderBook(index: Index, bookId?: string) {
  const id = bookId || defaultBookId(index);
  if (!id) {
    app.innerHTML = `<p class="err">No books in snapshots. Run the projector.</p>`;
    return;
  }
  rememberSleeve(id);
  const now = await loadJson<BookNow>(`/books/${id}/now.json`);
  const equity = await loadJson<Equity>(`/books/${id}/equity.json`);
  const fills = await loadJson<Fills>(`/books/${id}/fills.json`);
  const halt = now.halted
    ? `<div class="halt" role="alert">HALTED ${esc(now.halt_files.join(", "))}</div>`
    : "";
  const nPos = now.n_positions ?? now.positions.length;
  const slotCap = now.max_concurrent ?? now.knobs.max_concurrent;
  const maxC = slotCap == null || slotCap === "" ? "—" : slotCap;
  const pnlClass = now.pnl > 0 ? "up" : now.pnl < 0 ? "down" : "";
  const bar = now.last_bar_close_et
    ? `<p class="k">Last bar close ${esc(now.last_bar_close_et)}</p>`
    : "";
  const alloc = Number(now.knobs.max_alloc_pct);
  const lots = now.positions
    .map((p) => {
      const qty = Number(p.qty);
      const entry = Number(p.entry_price);
      const cost = Number.isFinite(qty) && Number.isFinite(entry) ? qty * entry : null;
      const weight = cost != null && now.equity ? (cost / now.equity) * 100 : null;
      return `<tr>
        <td>${esc(p.symbol)}</td>
        <td class="num">${p.qty == null ? "—" : compactNum(p.qty)}</td>
        <td class="num">${p.entry_price == null ? "—" : compactNum(p.entry_price)}</td>
        <td class="num">${cost == null ? "—" : money(cost)}</td>
        <td class="num">${weight == null ? "—" : `${weight.toFixed(1)}%`}</td>
      </tr>`;
    })
    .join("") || `<tr><td colspan="5">none</td></tr>`;
  const fillRows =
    fills.fills
      .slice()
      .reverse()
      .map((f) => {
        const when = f.ts_et || f.ts || "—";
        return `<tr>
        <td>${esc(when)}</td>
        <td class="mono">${esc(f.action)}</td>
        <td>${esc(f.symbol ?? "")}</td>
        <td class="num">${f.notional == null || f.notional === "" ? "—" : money(Number(f.notional))}</td>
        <td>${esc(f.reason ?? "")}</td>
        <td class="num">${f.qty == null || f.qty === "" ? "—" : compactNum(f.qty)}</td>
        <td class="num">${f.price == null || f.price === "" ? "—" : compactNum(f.price)}</td>
        <td class="num">${f.fee == null || f.fee === "" ? "—" : money(Number(f.fee))}</td>
      </tr>`;
      })
      .join("") || `<tr><td colspan="8">none</td></tr>`;
  const diffKeys = Object.keys(now.knob_diff || {});
  const diffHtml = diffKeys.length
    ? `<p class="diff">Diff vs DNA (${esc(now.dna_path || "config/trend_rotation_25_max8_rotate.json")}): ${diffKeys
        .map((k) => {
          const d = now.knob_diff![k];
          return `<code>${esc(k)}</code> sleeve=${esc(d.sleeve)} dna=${esc(d.dna)}`;
        })
        .join(" · ")}</p>`
    : `<p class="note">Sleeve knobs match DNA.</p>`;
  const featured = FEATURED_KNOBS.filter((k) => k in now.knobs);
  app.innerHTML = `
    ${halt}
    ${sleeveSwitch(index, id)}
    <p><span class="mode badge ${esc(now.mode)}">${esc(now.mode)}</span> · <span class="mono">${esc(now.strategy_id)}</span> · <code>${esc(now.config_path)}</code></p>
    <p class="k">Book ${esc(now.as_of_et || "—")}</p>
    <div class="row">
      <div class="card"><div class="k">Equity (${esc(now.equity_source)})</div><div class="v">${esc(money(now.equity))}</div></div>
      <div class="card"><div class="k">P&amp;L ${esc(pct(now.pnl_pct))}</div><div class="v ${pnlClass}">${esc(signedMoney(now.pnl))}</div></div>
      <div class="card"><div class="k">Cash</div><div class="v">${esc(money(now.cash))}</div></div>
      <div class="card"><div class="k">Slots</div><div class="v">${esc(String(nPos))}/${esc(String(maxC))}</div></div>
    </div>
    ${bar}
    <div id="eq" class="chart"></div>
    <p class="caption">Fills, then live mark · ${esc(now.as_of_et || "—")}</p>
    <h3>Lots</h3>
    <div class="table-wrap"><table class="data"><thead><tr><th>Symbol</th><th class="num">Qty</th><th class="num">Entry</th><th class="num">Cost</th><th class="num">Weight</th></tr></thead>
    <tbody>${lots}</tbody></table></div>
    <p class="note">Entry cost, not mark.${Number.isFinite(alloc) ? ` Clip ${esc((alloc * 100).toFixed(0))}% of equity.` : ""}</p>
    <h3>Fills</h3>
    <div class="table-wrap"><table class="data"><thead><tr><th>When</th><th>Action</th><th>Sym</th><th class="num">Notional</th><th>Reason</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Fee</th></tr></thead>
    <tbody>${fillRows}</tbody></table></div>
    <details class="fold">
      <summary>This sleeve · <code>${esc(now.config_path)}</code></summary>
      <dl>${featured.map((k) => knobRow(k, now.knobs[k], true)).join("")}</dl>
      ${diffHtml}
    </details>
  `;
  const haltedPrefix = now.halted ? "HALTED" : "";
  setTitle([haltedPrefix, now.mode, money(now.equity)].filter(Boolean));
  const el = document.getElementById("eq");
  if (el) {
    chartHandle = lineChart(
      el,
      equity.points.map((p) => ({ t: p.t, v: p.equity })),
      now.mode === "LIVE" ? token("--live", "#fb7185") : token("--paper", "#22d3ee"),
    );
  }
}

async function renderIdentity() {
  const ident = await loadJson<Identity>("/identity.json");
  app.innerHTML = `
    <p><span class="mode badge PRODUCTION">${esc(ident.strategy_id)}</span></p>
    <p class="k">DNA file: <code>${esc(ident.config_path)}</code></p>
    ${knobTable(ident.knobs, true)}
  `;
  setTitle(["Identity", ident.strategy_id]);
}

async function renderRuns(index: Index) {
  const groups = new Map<string, Index["runs"]>();
  for (const role of ROLE_ORDER) groups.set(role, []);
  for (const r of index.runs) {
    const role = ROLE_ORDER.includes(r.role as (typeof ROLE_ORDER)[number]) ? r.role : "RESEARCH";
    groups.get(role)!.push(r);
  }
  const chunks: string[] = [];
  for (const role of ROLE_ORDER) {
    const rows = groups.get(role)!;
    if (!rows.length) continue;
    const table = runTable(rows);
    if (COLLAPSED_ROLES.has(role)) {
      chunks.push(`<details class="fold"><summary>${esc(role)} (${rows.length})</summary>${table}</details>`);
    } else {
      chunks.push(`<h3>${esc(role)}</h3>${table}`);
    }
  }
  app.innerHTML = chunks.join("") || `<p class="k">No committed rollups.</p>`;
  setTitle(["Runs"]);
}

async function renderRun(id: string) {
  const run = await loadJson<Run>(`/runs/${id}.json`);
  const btc = run.points
    .filter((p) => p.btc_return_pct != null)
    .map((p) => ({ start: p.start, return_pct: p.btc_return_pct as number }));
  app.innerHTML = `
    <p><a href="#/runs">← runs</a></p>
    <p><span class="mode badge ${esc(run.role)}">${esc(run.role)}</span> · <span class="mono">${esc(run.strategy_id)}</span> · <code>${esc(run.config_path)}</code></p>
    <div class="row">
      <div class="card"><div class="k">n</div><div class="v">${run.n}</div></div>
      <div class="card"><div class="k">Avg %</div><div class="v">${run.avg_return_pct}</div></div>
      <div class="card"><div class="k">Med %</div><div class="v">${run.med_return_pct}</div></div>
      <div class="card"><div class="k">Min / max</div><div class="v">${run.min_return_pct} / ${run.max_return_pct}</div></div>
      <div class="card"><div class="k">Beats BTC</div><div class="v">${run.beats_btc}/${run.n}</div></div>
    </div>
    ${knobTable(run.knobs, true)}
    <div id="eq" class="chart tall"></div>
    <p class="legend">
      <span><span class="swatch" style="background:${esc(token("--amber", "#fbbf24"))}"></span>${esc(run.id)}</span>
      ${btc.length ? `<span><span class="swatch" style="background:${esc(token("--muted", "#94a3b8"))}"></span>BTC buy-hold</span>` : ""}
    </p>
    <p class="k">Birth-date return % — research, not a live book.</p>
  `;
  setTitle(["Run", run.id]);
  const el = document.getElementById("eq");
  if (el) {
    chartHandle = birthReturnChart(el, run.points, btc, token("--amber", "#fbbf24"), token("--muted", "#94a3b8"));
  }
}

function statCards(run: Run, color: string): string {
  return `
    <div class="card">
      <div class="k"><span class="swatch" style="background:${esc(color)}"></span>${esc(run.role)} · ${esc(run.id)}</div>
      <div class="v">${run.avg_return_pct ?? "—"}%</div>
      <p class="k">med ${run.med_return_pct ?? "—"} · min ${run.min_return_pct ?? "—"} · max ${run.max_return_pct ?? "—"} · beats BTC ${run.beats_btc}/${run.n}</p>
    </div>`;
}

async function renderOverlay(index: Index) {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const a = params.get("a") || index.runs.find((r) => r.role === "PRODUCTION")?.id || index.runs[0]?.id;
  const b = params.get("b") || (a ? overlayPeer(index, a) : undefined);
  const opts = (sel: string | undefined) =>
    index.runs
      .map((r) => `<option value="${esc(r.id)}" ${r.id === sel ? "selected" : ""}>${esc(r.role)} · ${esc(r.id)}</option>`)
      .join("");
  app.innerHTML = `
    <p class="k">Research overlay — birth-date return %, not a live book.</p>
    <p>
      <select id="a" aria-label="Series A">${opts(a)}</select>
      vs
      <select id="b" aria-label="Series B">${opts(b)}</select>
    </p>
    <div id="eq" class="chart tall"></div>
    <p class="legend" id="legend"></p>
    <div class="row" id="stats"></div>
  `;
  setTitle(["Overlay"]);
  const go = () => {
    const aa = (document.getElementById("a") as HTMLSelectElement).value;
    const bb = (document.getElementById("b") as HTMLSelectElement).value;
    location.hash = `#/overlay?a=${encodeURIComponent(aa)}&b=${encodeURIComponent(bb)}`;
  };
  document.getElementById("a")!.addEventListener("change", go);
  document.getElementById("b")!.addEventListener("change", go);
  if (!a || !b) return;
  const ra = await loadJson<Run>(`/runs/${a}.json`);
  const rb = await loadJson<Run>(`/runs/${b}.json`);
  const legend = document.getElementById("legend");
  if (legend) {
    legend.innerHTML = `<span><span class="swatch" style="background:${esc(token("--amber", "#fbbf24"))}"></span>${esc(ra.role)} · ${esc(ra.id)}</span>
      <span><span class="swatch" style="background:${esc(token("--cyan", "#22d3ee"))}"></span>${esc(rb.role)} · ${esc(rb.id)}</span>`;
  }
  const stats = document.getElementById("stats");
  if (stats) stats.innerHTML = statCards(ra, token("--amber", "#fbbf24")) + statCards(rb, token("--cyan", "#22d3ee"));
  const el = document.getElementById("eq");
  if (el) {
    chartHandle = birthReturnChart(el, ra.points, rb.points, token("--amber", "#fbbf24"), token("--cyan", "#22d3ee"));
  }
}

async function boot() {
  dropChart();
  let index: Index;
  try {
    index = await loadJson<Index>("/index.json");
  } catch {
    app.innerHTML = `<p class="err">No snapshots. From repo root: <code>.venv/bin/python viz/projector.py</code></p>`;
    setTitle(["No snapshots"]);
    return;
  }
  asof.textContent = index.as_of_et ? `snapshots ${index.as_of_et}` : "";
  const { name, rest } = route();
  setNav(name === "run" ? "runs" : name);
  try {
    if (name === "identity") await renderIdentity();
    else if (name === "runs") await renderRuns(index);
    else if (name === "run" && rest[0]) await renderRun(rest[0]);
    else if (name === "overlay") await renderOverlay(index);
    else await renderBook(index, rest[0]);
  } catch (e) {
    app.innerHTML = `<p class="err">${esc(e instanceof Error ? e.message : String(e))}</p>`;
    setTitle(["Error"]);
  }
}

window.addEventListener("hashchange", () => void boot());

type Theme = "light" | "dark";

function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function paintThemeBtn() {
  if (!themeBtn) return;
  const next = currentTheme() === "dark" ? "Light" : "Dark";
  themeBtn.textContent = next;
  themeBtn.setAttribute("aria-label", `Switch to ${next.toLowerCase()} theme`);
}

function initTheme() {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(THEME_KEY);
  } catch {
    stored = null;
  }
  const theme: Theme =
    stored === "light" || stored === "dark"
      ? stored
      : matchMedia("(prefers-color-scheme: light)").matches
        ? "light"
        : "dark";
  document.documentElement.dataset.theme = theme;
  paintThemeBtn();
  themeBtn?.addEventListener("click", () => {
    const next: Theme = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* private mode */
    }
    paintThemeBtn();
    void boot();
  });
  matchMedia("(prefers-color-scheme: light)").addEventListener("change", (e) => {
    let s: string | null = null;
    try {
      s = localStorage.getItem(THEME_KEY);
    } catch {
      s = null;
    }
    if (s === "light" || s === "dark") return;
    document.documentElement.dataset.theme = e.matches ? "light" : "dark";
    paintThemeBtn();
    void boot();
  });
}

initTheme();
void boot();
