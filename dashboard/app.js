/* Crypto Top 50 Quant Tracker -- dashboard rendering.
   Reads window.ANALYSIS_DATA (injected by data.js, see src/export.py for why
   this isn't a fetch() of analysis.json: file:// + CORS blocks that in Chrome). */

(() => {
  "use strict";

  const DATA = window.ANALYSIS_DATA || null;
  const MILESTONES = ["20", "50", "100", "200"];

  // Dates as ordinal day numbers (not a "category" x-axis) -- chartjs-plugin-zoom's
  // zoom/pan is unreliable on category scales with thousands of categories (verified:
  // zooming collapsed the whole axis to a single visible tick). A numeric axis with a
  // tick formatter gives correct, smooth zoom/pan instead.
  const DAY_MS = 86400000;
  const dateToOrdinal = (dateStr) => Math.floor(Date.parse(dateStr) / DAY_MS);
  const ordinalToDateLabel = (n) => new Date(n * DAY_MS).toISOString().slice(0, 10);

  // Fixed category -> hue order. Matches config/categories.yaml's definition order.
  // Categories beyond the 8th fold into a shared neutral gray rather than inventing
  // new hues (color-cycling breaks CVD-safety guarantees).
  const CATEGORY_ORDER = [
    "Layer 1", "DeFi", "Meme", "Stablecoin",
    "AI / DePIN", "Layer 2", "Exchange Token", "Oracle / Infra",
  ];
  const CAT_VARS = ["--cat-1", "--cat-2", "--cat-3", "--cat-4", "--cat-5", "--cat-6", "--cat-7", "--cat-8"];

  const root = getComputedStyle(document.documentElement);
  const cssVar = (name) => root.getPropertyValue(name).trim();

  function categoryColor(category) {
    const idx = CATEGORY_ORDER.indexOf(category);
    if (idx >= 0 && idx < CAT_VARS.length) return cssVar(CAT_VARS[idx]);
    return cssVar("--cat-other");
  }

  function fmtPct(v, digits = 1) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    const s = v.toFixed(digits);
    return (v > 0 ? "+" : "") + s + "%";
  }

  function pctClass(v) {
    if (v === null || v === undefined) return "muted-text";
    return v >= 0 ? "pos-text" : "neg-text";
  }

  function fmtNum(v) {
    if (v === null || v === undefined) return "—";
    return String(v);
  }

  function fmtUsd(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    return "$" + Math.round(v).toLocaleString("en-US");
  }

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else node.setAttribute(k, v);
    }
    for (const c of [].concat(children)) {
      if (c === null || c === undefined) continue;
      const isNode = c instanceof Node;
      node.appendChild(isNode ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  // ---- diverging color scale for the returns heatmap ----

  function hexToRgb(hex) {
    const h = hex.replace("#", "");
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function rgbLerp(c1, c2, t) {
    return `rgb(${Math.round(lerp(c1[0], c2[0], t))}, ${Math.round(lerp(c1[1], c2[1], t))}, ${Math.round(lerp(c1[2], c2[2], t))})`;
  }

  function divergingColor(value, maxAbs) {
    const neutral = hexToRgb(cssVar("--neutral") || "#383835");
    const pos = hexToRgb(cssVar("--pos") || "#3987e5");
    const neg = hexToRgb(cssVar("--neg") || "#e66767");
    if (value === null || value === undefined || maxAbs === 0) return `rgb(${neutral.join(",")})`;
    const t = Math.min(1, Math.abs(value) / maxAbs);
    return value >= 0 ? rgbLerp(neutral, pos, t) : rgbLerp(neutral, neg, t);
  }

  // ---- tabs ----

  function setupTabs() {
    const buttons = document.querySelectorAll("#tabs button");
    buttons.forEach((btn) => {
      btn.addEventListener("click", () => {
        buttons.forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById("view-" + btn.dataset.view).classList.add("active");
      });
    });
  }

  function setupZoomResetButtons() {
    document.querySelectorAll("[data-reset-zoom]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const canvas = document.getElementById(btn.dataset.resetZoom);
        const chart = canvas && Chart.getChart(canvas);
        if (chart && chart.resetZoom) chart.resetZoom();
      });
    });
  }

  // ---- overview ----

  function renderKpis() {
    const m = DATA.meta;
    const tiles = [
      { label: "Snapshots", value: m.snapshot_days + " days", sub: `${m.first_snapshot_date || "?"} -> ${m.last_snapshot_date || "?"}` },
      { label: "Coins tracked", value: m.coins_tracked, sub: "" },
      { label: "Tenures", value: m.tenures_total, sub: `${m.tenures_active} active, ${m.tenures_closed} closed` },
      { label: "Returns computed", value: `${m.returns_computed} / ${m.returns_expected}`, sub: "" },
    ];
    const grid = document.getElementById("kpi-grid");
    grid.innerHTML = "";
    for (const t of tiles) {
      grid.appendChild(el("div", { class: "kpi-tile" }, [
        el("div", { class: "label" }, t.label),
        el("div", { class: "value" }, String(t.value)),
        t.sub ? el("div", { class: "sub" }, t.sub) : null,
      ]));
    }
  }

  function compositionRow(c, showN) {
    const color = categoryColor(c.category);
    return el("div", { class: "comp-row" }, [
      el("div", { class: "name" }, [
        el("span", { class: "swatch", style: `background:${color}` }),
        c.category,
      ]),
      el("div", { class: "pct" }, c.pct.toFixed(1) + "%"),
      el("div", { class: "bar-track" }, el("div", { class: "bar-fill", style: `width:${c.pct}%;background:${color}` })),
      el("div", { class: "n" }, showN ? `n=${c.count}` : ""),
    ]);
  }

  function renderComposition() {
    const comp = DATA.composition_current || [];
    const target1 = document.getElementById("overview-composition");
    const target2 = document.getElementById("comp-full");
    [target1, target2].forEach((t) => (t.innerHTML = ""));
    if (comp.length === 0) {
      target1.appendChild(el("div", { class: "empty-state" }, "no data"));
      target2.appendChild(el("div", { class: "empty-state" }, "no data"));
      return;
    }
    for (const c of comp) target1.appendChild(compositionRow(c, false));
    for (const c of comp) target2.appendChild(compositionRow(c, true));
  }

  function renderOverviewReturns() {
    const target = document.getElementById("overview-returns");
    target.innerHTML = "";
    const stats = (DATA.category_stats || [])
      .filter((c) => c.returns && c.returns["100"] && c.returns["100"].mean !== null)
      .sort((a, b) => b.returns["100"].mean - a.returns["100"].mean);

    if (stats.length === 0) {
      target.appendChild(el("div", { class: "empty-state" }, "no d100 returns computed yet"));
      return;
    }
    const maxAbs = Math.max(...stats.map((s) => Math.abs(s.returns["100"].mean)), 1);
    for (const c of stats) {
      const r = c.returns["100"];
      const color = categoryColor(c.category);
      const barPct = (Math.abs(r.mean) / maxAbs) * 100;
      target.appendChild(el("div", { class: "comp-row" }, [
        el("div", { class: "name" }, [el("span", { class: "swatch", style: `background:${color}` }), c.category]),
        el("div", { class: "pct " + pctClass(r.mean) }, fmtPct(r.mean)),
        el("div", { class: "bar-track" }, el("div", { class: "bar-fill", style: `width:${barPct}%;background:${r.mean >= 0 ? "var(--pos)" : "var(--neg)"}` })),
        el("div", { class: "n" }, `n=${r.n}`),
      ]));
    }
  }

  // ---- benchmark vs BTC ----

  function renderBenchmark() {
    const target = document.getElementById("benchmark-panel");
    target.innerHTML = "";
    const bench = (DATA.global && DATA.global.benchmark_vs_btc) || {};
    const d100 = bench["100"];

    if (!d100 || d100.alpha === null) {
      target.appendChild(el("div", { class: "empty-state" }, "not enough overlapping price data yet"));
      return;
    }

    target.appendChild(el("div", { class: "benchmark-hero" }, [
      el("span", { class: "alpha " + pctClass(d100.alpha) }, fmtPct(d100.alpha)),
      el("span", { class: "alpha-label" }, `alpha at d100 vs. holding BTC (n=${d100.n})`),
    ]));

    target.appendChild(el("div", { class: "benchmark-row head" }, [
      el("div", {}, "Day"), el("div", { class: "num" }, "Entrants"), el("div", { class: "num" }, "BTC"),
      el("div", { class: "num" }, "Alpha"), el("div", { class: "num" }, "n"),
    ]));
    for (const m of MILESTONES) {
      const b = bench[m];
      if (!b || b.alpha === null) continue;
      target.appendChild(el("div", { class: "benchmark-row" }, [
        el("div", {}, `d${m}`),
        el("div", { class: "num " + pctClass(b.avg_strategy_return) }, fmtPct(b.avg_strategy_return)),
        el("div", { class: "num " + pctClass(b.avg_btc_return) }, fmtPct(b.avg_btc_return)),
        el("div", { class: "num " + pctClass(b.alpha) }, fmtPct(b.alpha)),
        el("div", { class: "num muted-text" }, String(b.n)),
      ]));
    }
  }

  // ---- hall of fame / hall of shame ----

  function hofRow(entry) {
    const color = categoryColor(entry.category);
    return el("div", { class: "hof-row" }, [
      el("div", {}, [el("span", { class: "cat-dot", style: `background:${color}` }), entry.symbol]),
      el("div", { class: "muted-text" }, entry.category),
      el("div", { class: "muted-text" }, `d${entry.milestone_day}`),
      el("div", { class: "num " + pctClass(entry.return_pct) }, fmtPct(entry.return_pct)),
    ]);
  }

  function renderHallOfFame() {
    const target = document.getElementById("hall-of-fame");
    target.innerHTML = "";
    const hof = DATA.hall_of_fame || { winners: [], losers: [] };
    if (hof.winners.length === 0) {
      target.appendChild(el("div", { class: "empty-state" }, "no data"));
      return;
    }
    target.appendChild(el("div", { class: "hof-list" }, [
      el("h4", {}, "Winners"),
      ...hof.winners.map(hofRow),
    ]));
    target.appendChild(el("div", { class: "hof-list" }, [
      el("h4", {}, "Losers"),
      ...hof.losers.map(hofRow),
    ]));
  }

  // ---- composition view: coins grouped by category ----

  function renderCompByCategory() {
    const target = document.getElementById("comp-by-category");
    target.innerHTML = "";
    const byCat = {};
    for (const c of DATA.coins || []) {
      (byCat[c.category] = byCat[c.category] || []).push(c);
    }
    const cats = Object.keys(byCat).sort((a, b) => (byCat[b].length - byCat[a].length));
    if (cats.length === 0) {
      target.appendChild(el("div", { class: "empty-state" }, "no data"));
      return;
    }
    for (const cat of cats) {
      const coins = byCat[cat].sort((a, b) => (a.current_rank ?? 999) - (b.current_rank ?? 999));
      const color = categoryColor(cat);
      const badges = coins.map((c) =>
        el("span", { class: "badge", style: `border-color:${color};color:${color}` },
          c.symbol + (c.currently_in_top50 ? "" : " (exited)"))
      );
      target.appendChild(el("div", { style: "margin-bottom:14px" }, [
        el("div", { style: `color:${color};font-weight:700;margin-bottom:6px` }, `${cat} (${coins.length})`),
        el("div", {}, badges),
      ]));
    }
  }

  // ---- returns heatmap ----

  function renderHeatmap(tableId, statKey) {
    const table = document.getElementById(tableId);
    table.innerHTML = "";
    const stats = DATA.category_stats || [];
    if (stats.length === 0) {
      table.appendChild(el("tr", {}, el("td", {}, "no data")));
      return;
    }
    let maxAbs = 1;
    for (const c of stats) for (const m of MILESTONES) {
      const v = c.returns[m] && c.returns[m][statKey];
      if (v !== null && v !== undefined) maxAbs = Math.max(maxAbs, Math.abs(v));
    }

    const thead = el("tr", {}, [el("th", {}, "Category"), ...MILESTONES.map((m) => el("th", {}, `d${m}`))]);
    table.appendChild(thead);

    for (const c of stats) {
      const tr = el("tr", {});
      tr.appendChild(el("td", { class: "rowlabel" }, c.category));
      for (const m of MILESTONES) {
        const cell = c.returns[m];
        const v = cell ? cell[statKey] : null;
        const n = cell ? cell.n : 0;
        const td = el("td", { class: "cell", style: `background:${divergingColor(v, maxAbs)}` }, [
          document.createTextNode(v === null ? "—" : fmtPct(v)),
          el("span", { class: "n" }, `n=${n}`),
        ]);
        tr.appendChild(td);
      }
      table.appendChild(tr);
    }
  }

  // ---- survival ----

  let survivalChart = null;

  function renderSurvival() {
    const curves = DATA.survival_curves || {};
    const stats = DATA.category_stats || [];
    // Cap to categories with enough tenures to be readable & meaningful.
    const eligible = stats
      .filter((s) => curves[s.category] && s.n_tenures >= 2)
      .sort((a, b) => b.n_tenures - a.n_tenures)
      .slice(0, 8)
      .map((s) => s.category);

    const legend = document.getElementById("survival-legend");
    legend.innerHTML = "";

    if (eligible.length === 0) {
      document.getElementById("survival-chart").parentElement.innerHTML =
        '<div class="empty-state">not enough data for survival curves</div>';
    } else {
      const datasets = eligible.map((cat) => {
        const color = categoryColor(cat);
        legend.appendChild(el("div", { class: "item" }, [el("span", { class: "swatch", style: `background:${color}` }), cat]));
        return {
          label: cat,
          data: curves[cat].map((p) => ({ x: p.day, y: p.survival_pct })),
          borderColor: color,
          backgroundColor: color,
          stepped: true,
          borderWidth: 2,
          pointRadius: 0,
          tension: 0,
        };
      });

      const ctx = document.getElementById("survival-chart").getContext("2d");
      if (survivalChart) survivalChart.destroy();
      survivalChart = new Chart(ctx, {
        type: "line",
        data: { datasets },
        options: chartBaseOptions({
          xTitle: "days since top 50 entry",
          yTitle: "% still in",
          yMin: 0, yMax: 100,
          showLegend: false,
        }),
      });
    }

    renderSurvivalTable();
  }

  function renderSurvivalTable() {
    const table = document.getElementById("survival-table");
    table.innerHTML = "";
    const stats = DATA.category_stats || [];
    if (stats.length === 0) {
      table.appendChild(el("tr", {}, el("td", {}, "no data")));
      return;
    }
    table.appendChild(el("tr", {}, [
      el("th", {}, "Category"), el("th", {}, "n"), el("th", {}, "Avg duration"),
      el("th", {}, ">90d"), el("th", {}, ">180d"), el("th", {}, ">365d"),
    ]));
    for (const c of stats) {
      const s = c.survival;
      table.appendChild(el("tr", {}, [
        el("td", {}, [el("span", { class: "cat-dot", style: `background:${categoryColor(c.category)}` }), c.category]),
        el("td", { class: "num" }, String(c.n_tenures)),
        el("td", { class: "num" }, c.avg_duration_days.toFixed(0) + "d"),
        el("td", { class: "num" }, s.gt_90d.pct !== null ? `${s.gt_90d.pct.toFixed(0)}%` : "—"),
        el("td", { class: "num" }, s.gt_180d.pct !== null ? `${s.gt_180d.pct.toFixed(0)}%` : "—"),
        el("td", { class: "num" }, s.gt_365d.pct !== null ? `${s.gt_365d.pct.toFixed(0)}%` : "—"),
      ]));
    }
  }

  function chartBaseOptions({ xTitle, yTitle, yMin, yMax, showLegend, yType, xType, xTicksLimit, zoomPan, xDateFormat }) {
    const muted = cssVar("--text-muted");
    const grid = cssVar("--gridline");
    const font = { family: cssVar("--mono") || "monospace", size: 11 };
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "nearest", intersect: false },
      plugins: {
        legend: { display: !!showLegend, labels: { color: muted, font } },
        tooltip: {
          backgroundColor: cssVar("--surface-2"),
          titleColor: cssVar("--text-primary"),
          bodyColor: cssVar("--text-secondary"),
          borderColor: cssVar("--border"),
          borderWidth: 1,
          bodyFont: font,
          titleFont: font,
          callbacks: xDateFormat ? { title: (items) => ordinalToDateLabel(items[0].parsed.x) } : {},
        },
        // Horizontal-only: these are all time series where "explore a date range"
        // is the useful gesture; y stays auto-scaled/fixed rather than zoomable.
        zoom: zoomPan ? {
          pan: { enabled: true, mode: "x" },
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: "x" },
          limits: { x: { minRange: 5 } },
        } : undefined,
      },
      scales: {
        x: {
          type: xType || "linear",
          title: { display: !!xTitle, text: xTitle, color: muted, font },
          ticks: {
            color: muted,
            font,
            ...(xTicksLimit ? { maxTicksLimit: xTicksLimit, autoSkip: true } : {}),
            ...(xDateFormat ? { callback: (value) => ordinalToDateLabel(value) } : {}),
          },
          grid: { color: grid },
        },
        y: {
          type: yType || "linear",
          min: yMin, max: yMax,
          title: { display: !!yTitle, text: yTitle, color: muted, font },
          ticks: { color: muted, font },
          grid: { color: grid },
        },
      },
    };
  }

  // ---- coins table ----

  const COIN_COLUMNS = [
    { key: "symbol", label: "Symbol" },
    { key: "category", label: "Category" },
    { key: "status", label: "Status" },
    { key: "current_rank", label: "Rank", num: true },
    { key: "best_rank", label: "Best rank", num: true },
    { key: "entries_count", label: "Entries", num: true },
    { key: "days_total_in_top50", label: "Total days", num: true },
    { key: "avg_tenure_days", label: "Avg tenure", num: true },
    { key: "r20", label: "d20", num: true },
    { key: "r50", label: "d50", num: true },
    { key: "r100", label: "d100", num: true },
    { key: "r200", label: "d200", num: true },
  ];

  let coinsSortState = { key: "current_rank", dir: 1 };

  function coinsRows() {
    return (DATA.coins || []).map((c) => ({
      ...c,
      status: c.currently_in_top50 ? "active" : "exited",
      r20: c.returns["20"], r50: c.returns["50"], r100: c.returns["100"], r200: c.returns["200"],
    }));
  }

  function renderCoinsTable() {
    const rows = coinsRows();

    const catSelect = document.getElementById("coin-category-filter");
    if (catSelect.options.length <= 1) {
      const cats = [...new Set(rows.map((r) => r.category))].sort();
      for (const cat of cats) catSelect.appendChild(el("option", { value: cat }, cat));
    }

    const search = document.getElementById("coin-search").value.trim().toLowerCase();
    const catFilter = catSelect.value;
    const statusFilter = document.getElementById("coin-status-filter").value;

    let filtered = rows.filter((r) => {
      if (search && !(r.symbol.toLowerCase().includes(search) || r.name.toLowerCase().includes(search))) return false;
      if (catFilter && r.category !== catFilter) return false;
      if (statusFilter === "active" && !r.currently_in_top50) return false;
      if (statusFilter === "exited" && r.currently_in_top50) return false;
      return true;
    });

    filtered.sort((a, b) => {
      const { key, dir } = coinsSortState;
      let av = a[key], bv = b[key];
      if (av === null || av === undefined) av = key.startsWith("r") || av === null ? -Infinity : "";
      if (bv === null || bv === undefined) bv = key.startsWith("r") || bv === null ? -Infinity : "";
      if (typeof av === "string") return dir * av.localeCompare(bv);
      return dir * ((av ?? -Infinity) - (bv ?? -Infinity));
    });

    const thead = document.querySelector("#coins-table thead");
    thead.innerHTML = "";
    const headRow = el("tr", {});
    for (const col of COIN_COLUMNS) {
      const arrow = coinsSortState.key === col.key ? (coinsSortState.dir === 1 ? " ↑" : " ↓") : "";
      const th = el("th", {}, col.label + arrow);
      th.addEventListener("click", () => {
        if (coinsSortState.key === col.key) coinsSortState.dir *= -1;
        else coinsSortState = { key: col.key, dir: col.num ? -1 : 1 };
        renderCoinsTable();
      });
      headRow.appendChild(th);
    }
    thead.appendChild(headRow);

    const tbody = document.querySelector("#coins-table tbody");
    tbody.innerHTML = "";
    if (filtered.length === 0) {
      tbody.appendChild(el("tr", {}, el("td", { colspan: String(COIN_COLUMNS.length) }, "no results")));
      return;
    }
    for (const r of filtered) {
      const tr = el("tr", {});
      tr.appendChild(el("td", {}, r.symbol));
      tr.appendChild(el("td", {}, [el("span", { class: "cat-dot", style: `background:${categoryColor(r.category)}` }), r.category]));
      tr.appendChild(el("td", { class: r.currently_in_top50 ? "pos-text" : "muted-text" }, r.status));
      tr.appendChild(el("td", { class: "num" }, r.current_rank ?? "—"));
      tr.appendChild(el("td", { class: "num" }, r.best_rank ?? "—"));
      tr.appendChild(el("td", { class: "num" }, fmtNum(r.entries_count)));
      tr.appendChild(el("td", { class: "num" }, fmtNum(r.days_total_in_top50)));
      tr.appendChild(el("td", { class: "num" }, r.avg_tenure_days.toFixed(0)));
      for (const key of ["r20", "r50", "r100", "r200"]) {
        tr.appendChild(el("td", { class: "num " + pctClass(r[key]) }, fmtPct(r[key])));
      }
      tr.addEventListener("click", () => openCoinModal(r.coin_id));
      tbody.appendChild(tr);
    }
  }

  function setupCoinsFilters() {
    document.getElementById("coin-search").addEventListener("input", renderCoinsTable);
    document.getElementById("coin-category-filter").addEventListener("change", renderCoinsTable);
    document.getElementById("coin-status-filter").addEventListener("change", renderCoinsTable);
  }

  // ---- coin detail modal ----

  let coinModalChart = null;

  function openCoinModal(coinId) {
    const coin = (DATA.coins || []).find((c) => c.coin_id === coinId);
    const series = (DATA.coin_series && DATA.coin_series[coinId]) || { price_series: [], tenures: [] };
    if (!coin) return;

    document.getElementById("coin-modal-title").textContent = `${coin.symbol} -- ${coin.name}`;
    const catBadge = document.getElementById("coin-modal-category");
    catBadge.textContent = coin.category;
    catBadge.style.borderColor = categoryColor(coin.category);
    catBadge.style.color = categoryColor(coin.category);

    const ctx = document.getElementById("coin-modal-chart").getContext("2d");
    if (coinModalChart) coinModalChart.destroy();
    const labels = series.price_series.map((p) => p.date);
    // Category x-axis (labels array) instead of a "time" scale -- Chart.js's time
    // scale needs an external date-adapter library we don't vendor; plain date-string
    // labels render fine and we don't need axis-level date math for a sparkline.
    coinModalChart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "price (USD)",
          data: series.price_series.map((p) => p.price),
          borderColor: categoryColor(coin.category),
          backgroundColor: categoryColor(coin.category),
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.15,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            ticks: {
              color: cssVar("--text-muted"),
              font: { family: cssVar("--mono"), size: 10 },
              maxTicksLimit: 8,
              autoSkip: true,
            },
            grid: { color: cssVar("--gridline") },
          },
          y: {
            ticks: { color: cssVar("--text-muted"), font: { family: cssVar("--mono"), size: 10 } },
            grid: { color: cssVar("--gridline") },
          },
        },
      },
    });

    const table = document.getElementById("coin-modal-tenures");
    table.innerHTML = "";
    table.appendChild(el("tr", {}, [
      el("th", {}, "Entry"), el("th", {}, "Entry $"), el("th", {}, "Exit"),
      el("th", {}, "Exit $"), el("th", {}, "Days"), el("th", {}, "Status"),
    ]));
    for (const t of series.tenures) {
      table.appendChild(el("tr", {}, [
        el("td", {}, t.entry_date),
        el("td", { class: "num" }, t.entry_price !== null ? t.entry_price.toFixed(4) : "—"),
        el("td", {}, t.exit_date || "—"),
        el("td", { class: "num" }, t.exit_price !== null ? t.exit_price.toFixed(4) : "—"),
        el("td", { class: "num" }, String(t.duration_days)),
        el("td", { class: t.is_active ? "pos-text" : "muted-text" }, t.is_active ? "active" : "exited"),
      ]));
    }

    document.getElementById("coin-modal-backdrop").style.display = "flex";
  }

  function closeCoinModal() {
    document.getElementById("coin-modal-backdrop").style.display = "none";
  }

  function setupCoinModal() {
    document.getElementById("coin-modal-close").addEventListener("click", closeCoinModal);
    document.getElementById("coin-modal-backdrop").addEventListener("click", (e) => {
      if (e.target.id === "coin-modal-backdrop") closeCoinModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeCoinModal();
    });
  }

  // ---- timeline ----

  let churnChart = null;

  function renderTimeline() {
    const list = document.getElementById("timeline-list");
    list.innerHTML = "";
    const months = DATA.timeline || [];
    if (months.length === 0) {
      list.appendChild(el("div", { class: "empty-state" }, "no data"));
    } else {
      for (const m of months) {
        const entries = m.entries.map((c) => el("span", { class: "badge entry" }, c.symbol));
        const exits = m.exits.map((c) => el("span", { class: "badge exit" }, c.symbol));
        list.appendChild(el("div", { style: "margin-bottom:10px" }, [
          el("div", { class: "month", style: "margin-bottom:4px;font-weight:700" }, m.month),
          el("div", {}, [...entries, ...exits]),
        ]));
      }
    }

    const churn = DATA.global.monthly_churn || [];
    const ctx = document.getElementById("churn-chart").getContext("2d");
    if (churnChart) churnChart.destroy();
    if (churn.length === 0) return;
    churnChart = new Chart(ctx, {
      type: "bar",
      data: {
        labels: churn.map((c) => c.month),
        datasets: [
          { label: "entries", data: churn.map((c) => c.entries), backgroundColor: cssVar("--pos") },
          { label: "exits", data: churn.map((c) => -c.exits), backgroundColor: cssVar("--neg") },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { labels: { color: cssVar("--text-muted"), font: { family: cssVar("--mono") } } },
        },
        scales: {
          x: { stacked: true, ticks: { color: cssVar("--text-muted"), font: { family: cssVar("--mono"), size: 10 } }, grid: { color: cssVar("--gridline") } },
          y: { stacked: true, ticks: { color: cssVar("--text-muted") }, grid: { color: cssVar("--gridline") } },
        },
      },
    });
  }

  // ---- bitcoin position/strength ----

  let rainbowChart = null;
  let feargreedChart = null;
  let dominanceChart = null;
  let stablecoinChart = null;

  // Fixed rainbow hues, cheap (blue) to bubble (red) -- a deliberate palette for
  // this one chart's meaning, separate from the categorical --cat-* variables
  // used for coin categories elsewhere.
  const RAINBOW_COLORS = [
    "#1e3a8a", "#2563eb", "#0891b2", "#059669", "#65a30d",
    "#ca8a04", "#ea580c", "#dc2626", "#991b1b",
  ];

  function renderBtcRegime() {
    const grid = document.getElementById("btc-regime-kpis");
    grid.innerHTML = "";
    const regime = (DATA.btc_indicators || {}).regime;
    if (!regime) {
      grid.appendChild(el("div", { class: "empty-state" }, "no data"));
      return;
    }
    const rainbow = (DATA.btc_indicators || {}).rainbow;
    const tiles = [
      { label: "BTC price", value: fmtUsd(regime.current_price), sub: "as of " + regime.as_of },
      { label: "Drawdown from ATH", value: fmtPct(regime.drawdown_from_ath_pct), sub: "ATH " + fmtUsd(regime.all_time_high) },
      { label: "SMA 200", value: regime.sma["200"] !== null ? fmtUsd(regime.sma["200"]) : "—", sub: "" },
      { label: "SMA 350", value: regime.sma["350"] !== null ? fmtUsd(regime.sma["350"]) : "—", sub: "" },
      { label: "Regime", value: regime.label, sub: "current position, not a prediction" },
      { label: "Rainbow band", value: rainbow ? rainbow.current_band_label : "—", sub: "where price sits vs. the fitted trend" },
    ];
    for (const t of tiles) {
      grid.appendChild(el("div", { class: "kpi-tile" }, [
        el("div", { class: "label" }, t.label),
        el("div", { class: "value" }, String(t.value)),
        t.sub ? el("div", { class: "sub" }, t.sub) : null,
      ]));
    }
  }

  function renderBtcRainbow() {
    const rainbow = (DATA.btc_indicators || {}).rainbow;
    const box = document.getElementById("rainbow-chart").parentElement;
    const legend = document.getElementById("rainbow-legend");
    legend.innerHTML = "";
    if (!rainbow) {
      box.innerHTML = '<div class="empty-state">not enough BTC price history cached yet</div>';
      return;
    }

    // fit_line/band_edges are downsampled (weekly) server-side while price_series
    // stays daily, so datasets have different lengths -- explicit {x, y} points
    // (x = ordinal day number, not a date string) let each dataset position
    // itself correctly regardless of length, and give Chart.js a real numeric
    // axis so zoom/pan behaves properly (a "category" axis with thousands of
    // categories made zoom collapse to a single visible tick).
    const asPoints = (series) => series.map((p) => ({ x: dateToOrdinal(p.date), y: p.value }));
    const datasets = [];
    const lastEdge = rainbow.band_edges.length - 1;
    for (let i = 0; i < lastEdge; i++) {
      datasets.push({
        label: rainbow.band_labels[i],
        data: asPoints(rainbow.band_edges[i]),
        borderWidth: 0,
        pointRadius: 0,
        backgroundColor: RAINBOW_COLORS[i] + "55",
        fill: i + 1,
        tension: 0.1,
      });
    }
    datasets.push({
      label: "upper band edge",
      data: asPoints(rainbow.band_edges[lastEdge]),
      borderWidth: 0,
      pointRadius: 0,
      fill: false,
      tension: 0.1,
    });
    datasets.push({
      label: "fit trend",
      data: asPoints(rainbow.fit_line),
      borderColor: cssVar("--text-muted"),
      borderDash: [4, 4],
      borderWidth: 1,
      pointRadius: 0,
      fill: false,
      tension: 0.1,
    });
    datasets.push({
      label: "BTC price",
      data: asPoints(rainbow.price_series),
      borderColor: cssVar("--text-primary"),
      borderWidth: 2,
      pointRadius: 0,
      fill: false,
      tension: 0.1,
    });

    for (let i = 0; i < rainbow.band_labels.length; i++) {
      legend.appendChild(el("div", { class: "item" }, [
        el("span", { class: "swatch", style: `background:${RAINBOW_COLORS[i]}` }),
        rainbow.band_labels[i],
      ]));
    }

    const ctx = document.getElementById("rainbow-chart").getContext("2d");
    if (rainbowChart) rainbowChart.destroy();
    rainbowChart = new Chart(ctx, {
      type: "line",
      data: { datasets },
      options: chartBaseOptions({
        yTitle: "price (USD, log scale)",
        showLegend: false,
        yType: "logarithmic",
        xTicksLimit: 10,
        xDateFormat: true,
        zoomPan: true,
      }),
    });
  }

  function renderBtcFearGreed() {
    const fg = (DATA.btc_indicators || {}).fear_greed;
    const currentBox = document.getElementById("feargreed-current");
    currentBox.innerHTML = "";
    if (!fg) {
      currentBox.appendChild(el("div", { class: "empty-state" }, "no data"));
      return;
    }
    currentBox.appendChild(el("div", { class: "benchmark-hero" }, [
      el("span", { class: "alpha" }, String(fg.current.value)),
      el("span", { class: "alpha-label" }, `${fg.current.classification} -- as of ${fg.current.date}`),
    ]));

    const recent = fg.history.slice(-180);
    const ctx = document.getElementById("feargreed-chart").getContext("2d");
    if (feargreedChart) feargreedChart.destroy();
    feargreedChart = new Chart(ctx, {
      type: "line",
      data: {
        datasets: [{
          label: "Fear & Greed",
          data: recent.map((p) => ({ x: dateToOrdinal(p.date), y: p.value })),
          borderColor: cssVar("--cat-4"),
          backgroundColor: cssVar("--cat-4"),
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.2,
        }],
      },
      options: chartBaseOptions({ yMin: 0, yMax: 100, showLegend: false, xTicksLimit: 10, xDateFormat: true, zoomPan: true }),
    });
  }

  function renderBtcDominance() {
    const dom = (DATA.btc_indicators || {}).dominance || { history: [] };
    const currentBox = document.getElementById("dominance-current");
    currentBox.innerHTML = "";
    const box = document.getElementById("dominance-chart").parentElement;
    if (!dom.history || dom.history.length === 0) {
      currentBox.appendChild(el("div", { class: "empty-state" }, "no data yet -- this builds up one point per day"));
      box.innerHTML = '<div class="empty-state">no data yet -- this builds up one point per day</div>';
      return;
    }
    const latest = dom.history[dom.history.length - 1];
    currentBox.appendChild(el("div", { class: "benchmark-hero" }, [
      el("span", { class: "alpha" }, latest.btc_dominance_pct !== null ? latest.btc_dominance_pct.toFixed(1) + "%" : "—"),
      el("span", { class: "alpha-label" }, `BTC dominance -- as of ${latest.date} (${dom.history.length} day${dom.history.length === 1 ? "" : "s"} of history so far)`),
    ]));
    if (dom.history.length === 1) {
      box.innerHTML = '<div class="empty-state">only 1 day recorded so far -- a line needs at least 2 points; check back tomorrow</div>';
      return;
    }
    const ctx = document.getElementById("dominance-chart").getContext("2d");
    if (dominanceChart) dominanceChart.destroy();
    dominanceChart = new Chart(ctx, {
      type: "line",
      data: {
        datasets: [{
          label: "BTC dominance %",
          data: dom.history.map((p) => ({ x: dateToOrdinal(p.date), y: p.btc_dominance_pct })),
          borderColor: cssVar("--cat-1"),
          backgroundColor: cssVar("--cat-1"),
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.2,
        }],
      },
      options: chartBaseOptions({ yTitle: "% dominance", showLegend: false, xTicksLimit: 10, xDateFormat: true, zoomPan: true }),
    });
  }

  function renderBtcStablecoin() {
    const trend = (DATA.btc_indicators || {}).stablecoin_supply || [];
    const box = document.getElementById("stablecoin-chart").parentElement;
    if (trend.length === 0) {
      box.innerHTML = '<div class="empty-state">no data</div>';
      return;
    }
    const ctx = document.getElementById("stablecoin-chart").getContext("2d");
    if (stablecoinChart) stablecoinChart.destroy();
    stablecoinChart = new Chart(ctx, {
      type: "line",
      data: {
        datasets: [{
          label: "Stablecoin market cap",
          data: trend.map((p) => ({ x: dateToOrdinal(p.date), y: p.total_market_cap_usd })),
          borderColor: cssVar("--cat-4"),
          backgroundColor: cssVar("--cat-4"),
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.15,
        }],
      },
      options: chartBaseOptions({ yTitle: "USD", showLegend: false, xTicksLimit: 10, xDateFormat: true, zoomPan: true }),
    });
  }

  // ---- altcoin season index ----

  let altcoinSeasonChart = null;

  function renderAltcoinSeason() {
    const asi = DATA.altcoin_season_index || { history: [], current: null };
    const currentBox = document.getElementById("altcoin-season-current");
    currentBox.innerHTML = "";
    if (!asi.current || asi.current.pct_beating_btc === null) {
      currentBox.appendChild(el("div", { class: "empty-state" }, "not enough history yet (needs 90+ days)"));
    } else {
      const pct = asi.current.pct_beating_btc;
      const phase = pct >= 75 ? "Altcoin Season" : pct <= 25 ? "Bitcoin Season" : "Neutral";
      currentBox.appendChild(el("div", { class: "benchmark-hero" }, [
        el("span", { class: "alpha" }, pct.toFixed(0) + "%"),
        el("span", { class: "alpha-label" }, `${phase} -- as of ${asi.current.date} (n=${asi.current.n})`),
      ]));
    }

    const points = (asi.history || []).filter((h) => h.pct_beating_btc !== null);
    const box = document.getElementById("altcoin-season-chart").parentElement;
    if (points.length === 0) {
      box.innerHTML = '<div class="empty-state">not enough history yet</div>';
      return;
    }
    const ctx = document.getElementById("altcoin-season-chart").getContext("2d");
    if (altcoinSeasonChart) altcoinSeasonChart.destroy();
    altcoinSeasonChart = new Chart(ctx, {
      type: "line",
      data: {
        datasets: [{
          label: "% beating BTC (90d)",
          data: points.map((p) => ({ x: dateToOrdinal(p.date), y: p.pct_beating_btc })),
          borderColor: cssVar("--cat-3"),
          backgroundColor: cssVar("--cat-3"),
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.15,
        }],
      },
      options: chartBaseOptions({ yMin: 0, yMax: 100, showLegend: false, xTicksLimit: 10, xDateFormat: true, zoomPan: true }),
    });
  }

  // ---- compare two coins ----

  let compareChart = null;

  function mostRecentTenure(coinId) {
    const series = (DATA.coin_series && DATA.coin_series[coinId]) || null;
    if (!series || !series.tenures || series.tenures.length === 0) return null;
    return series.tenures[series.tenures.length - 1];
  }

  function compareSeriesFor(coinId) {
    const tenure = mostRecentTenure(coinId);
    const series = (DATA.coin_series && DATA.coin_series[coinId]) || null;
    if (!tenure || !series) return [];
    const entryTime = new Date(tenure.entry_date).getTime();
    return series.price_series
      .filter((p) => p.date >= tenure.entry_date)
      .map((p) => ({
        x: Math.round((new Date(p.date).getTime() - entryTime) / 86400000),
        y: ((p.price - tenure.entry_price) / tenure.entry_price) * 100,
      }));
  }

  function renderCompareChart() {
    const idA = document.getElementById("compare-coin-a").value;
    const idB = document.getElementById("compare-coin-b").value;
    const coinA = (DATA.coins || []).find((c) => c.coin_id === idA);
    const coinB = (DATA.coins || []).find((c) => c.coin_id === idB);
    const box = document.getElementById("compare-chart").parentElement;
    if (!idA || !idB) {
      box.innerHTML = '<div class="empty-state">pick two coins</div>';
      return;
    }
    const ctx = document.getElementById("compare-chart").getContext("2d");
    if (compareChart) compareChart.destroy();
    compareChart = new Chart(ctx, {
      type: "line",
      data: {
        datasets: [
          {
            label: coinA ? coinA.symbol : idA,
            data: compareSeriesFor(idA),
            borderColor: cssVar("--cat-1"),
            backgroundColor: cssVar("--cat-1"),
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.1,
          },
          {
            label: coinB ? coinB.symbol : idB,
            data: compareSeriesFor(idB),
            borderColor: cssVar("--cat-2"),
            backgroundColor: cssVar("--cat-2"),
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.1,
          },
        ],
      },
      options: chartBaseOptions({
        xTitle: "days since entry",
        yTitle: "% return since entry",
        showLegend: true,
      }),
    });
  }

  function setupCompareView() {
    const coins = (DATA.coins || []).slice().sort((a, b) => a.symbol.localeCompare(b.symbol));
    const selA = document.getElementById("compare-coin-a");
    const selB = document.getElementById("compare-coin-b");
    for (const sel of [selA, selB]) {
      sel.innerHTML = "";
      for (const c of coins) {
        sel.appendChild(el("option", { value: c.coin_id }, `${c.symbol} -- ${c.name}`));
      }
    }
    if (coins.length > 1) {
      selA.value = coins[0].coin_id;
      selB.value = coins[1].coin_id;
    }
    selA.addEventListener("change", renderCompareChart);
    selB.addEventListener("change", renderCompareChart);
    renderCompareChart();
  }

  // ---- boot ----

  function main() {
    if (!DATA || !DATA.meta || DATA.meta.snapshot_days === 0) {
      document.getElementById("empty-banner").style.display = "block";
    }
    setupTabs();
    setupCoinsFilters();
    setupCoinModal();
    setupZoomResetButtons();
    if (!DATA) return;

    document.getElementById("generated-at").textContent = "generated: " + (DATA.meta.generated_at || "?");
    renderKpis();
    renderComposition();
    renderOverviewReturns();
    renderBenchmark();
    renderHallOfFame();
    renderCompByCategory();
    renderHeatmap("heatmap-mean", "mean");
    renderHeatmap("heatmap-median", "median");
    renderSurvival();
    renderCoinsTable();
    renderTimeline();
    renderBtcRegime();
    renderBtcRainbow();
    renderBtcFearGreed();
    renderBtcDominance();
    renderBtcStablecoin();
    renderAltcoinSeason();
    setupCompareView();
  }

  document.addEventListener("DOMContentLoaded", main);
})();
