/* Crypto Top 50 Quant Tracker -- dashboard rendering.
   Reads window.ANALYSIS_DATA (injected by data.js, see src/export.py for why
   this isn't a fetch() of analysis.json: file:// + CORS blocks that in Chrome). */

(() => {
  "use strict";

  const DATA = window.ANALYSIS_DATA || null;
  const MILESTONES = ["20", "50", "100", "200"];

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

  // ---- overview ----

  function renderKpis() {
    const m = DATA.meta;
    const tiles = [
      { label: "Snapshots", value: m.snapshot_days + " dias", sub: `${m.first_snapshot_date || "?"} -> ${m.last_snapshot_date || "?"}` },
      { label: "Monedas trackeadas", value: m.coins_tracked, sub: "" },
      { label: "Tenures", value: m.tenures_total, sub: `${m.tenures_active} activos, ${m.tenures_closed} cerrados` },
      { label: "Retornos calculados", value: `${m.returns_computed} / ${m.returns_expected}`, sub: "" },
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
      target1.appendChild(el("div", { class: "empty-state" }, "sin datos"));
      target2.appendChild(el("div", { class: "empty-state" }, "sin datos"));
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
      target.appendChild(el("div", { class: "empty-state" }, "todavia no hay retornos d100 calculados"));
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

  // ---- composicion view: coins grouped by category ----

  function renderCompByCategory() {
    const target = document.getElementById("comp-by-category");
    target.innerHTML = "";
    const byCat = {};
    for (const c of DATA.coins || []) {
      (byCat[c.category] = byCat[c.category] || []).push(c);
    }
    const cats = Object.keys(byCat).sort((a, b) => (byCat[b].length - byCat[a].length));
    if (cats.length === 0) {
      target.appendChild(el("div", { class: "empty-state" }, "sin datos"));
      return;
    }
    for (const cat of cats) {
      const coins = byCat[cat].sort((a, b) => (a.current_rank ?? 999) - (b.current_rank ?? 999));
      const color = categoryColor(cat);
      const badges = coins.map((c) =>
        el("span", { class: "badge", style: `border-color:${color};color:${color}` },
          c.symbol + (c.currently_in_top50 ? "" : " (fuera)"))
      );
      target.appendChild(el("div", { style: "margin-bottom:14px" }, [
        el("div", { style: `color:${color};font-weight:700;margin-bottom:6px` }, `${cat} (${coins.length})`),
        el("div", {}, badges),
      ]));
    }
  }

  // ---- retornos heatmap ----

  function renderHeatmap(tableId, statKey) {
    const table = document.getElementById(tableId);
    table.innerHTML = "";
    const stats = DATA.category_stats || [];
    if (stats.length === 0) {
      table.appendChild(el("tr", {}, el("td", {}, "sin datos")));
      return;
    }
    let maxAbs = 1;
    for (const c of stats) for (const m of MILESTONES) {
      const v = c.returns[m] && c.returns[m][statKey];
      if (v !== null && v !== undefined) maxAbs = Math.max(maxAbs, Math.abs(v));
    }

    const thead = el("tr", {}, [el("th", {}, "Categoria"), ...MILESTONES.map((m) => el("th", {}, `d${m}`))]);
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
        '<div class="empty-state">sin datos suficientes para curvas de supervivencia</div>';
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
          xTitle: "dias desde entrada al top 50",
          yTitle: "% que sigue dentro",
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
      table.appendChild(el("tr", {}, el("td", {}, "sin datos")));
      return;
    }
    table.appendChild(el("tr", {}, [
      el("th", {}, "Categoria"), el("th", {}, "n"), el("th", {}, "Duracion prom."),
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

  function chartBaseOptions({ xTitle, yTitle, yMin, yMax, showLegend }) {
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
        },
      },
      scales: {
        x: {
          type: "linear",
          title: { display: !!xTitle, text: xTitle, color: muted, font },
          ticks: { color: muted, font },
          grid: { color: grid },
        },
        y: {
          min: yMin, max: yMax,
          title: { display: !!yTitle, text: yTitle, color: muted, font },
          ticks: { color: muted, font },
          grid: { color: grid },
        },
      },
    };
  }

  // ---- monedas table ----

  const COIN_COLUMNS = [
    { key: "symbol", label: "Symbol" },
    { key: "category", label: "Categoria" },
    { key: "status", label: "Estado" },
    { key: "current_rank", label: "Rank", num: true },
    { key: "best_rank", label: "Mejor rank", num: true },
    { key: "entries_count", label: "Entradas", num: true },
    { key: "days_total_in_top50", label: "Dias total", num: true },
    { key: "avg_tenure_days", label: "Estadia prom.", num: true },
    { key: "r20", label: "d20", num: true },
    { key: "r50", label: "d50", num: true },
    { key: "r100", label: "d100", num: true },
    { key: "r200", label: "d200", num: true },
  ];

  let coinsSortState = { key: "current_rank", dir: 1 };

  function coinsRows() {
    return (DATA.coins || []).map((c) => ({
      ...c,
      status: c.currently_in_top50 ? "activa" : "salio",
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
      tbody.appendChild(el("tr", {}, el("td", { colspan: String(COIN_COLUMNS.length) }, "sin resultados")));
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
      tbody.appendChild(tr);
    }
  }

  function setupCoinsFilters() {
    document.getElementById("coin-search").addEventListener("input", renderCoinsTable);
    document.getElementById("coin-category-filter").addEventListener("change", renderCoinsTable);
    document.getElementById("coin-status-filter").addEventListener("change", renderCoinsTable);
  }

  // ---- timeline ----

  let churnChart = null;

  function renderTimeline() {
    const list = document.getElementById("timeline-list");
    list.innerHTML = "";
    const months = DATA.timeline || [];
    if (months.length === 0) {
      list.appendChild(el("div", { class: "empty-state" }, "sin datos"));
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
          { label: "entradas", data: churn.map((c) => c.entries), backgroundColor: cssVar("--pos") },
          { label: "salidas", data: churn.map((c) => -c.exits), backgroundColor: cssVar("--neg") },
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

  // ---- boot ----

  function main() {
    if (!DATA || !DATA.meta || DATA.meta.snapshot_days === 0) {
      document.getElementById("empty-banner").style.display = "block";
    }
    setupTabs();
    setupCoinsFilters();
    if (!DATA) return;

    document.getElementById("generated-at").textContent = "generado: " + (DATA.meta.generated_at || "?");
    renderKpis();
    renderComposition();
    renderOverviewReturns();
    renderCompByCategory();
    renderHeatmap("heatmap-mean", "mean");
    renderHeatmap("heatmap-median", "median");
    renderSurvival();
    renderCoinsTable();
    renderTimeline();
  }

  document.addEventListener("DOMContentLoaded", main);
})();
