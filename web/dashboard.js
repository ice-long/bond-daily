"use strict";

const categories = ["国债", "国开债", "农发债", "进出口行债"];
const columns = [
  ["code", "债券代码"], ["name", "债券简称"], ["category", "券种"],
  ["term_years", "待偿期（年）"], ["net_price", "成交净价（元）"],
  ["latest_yield", "最新收益率（%）"], ["weighted_yield", "加权收益率（%）"],
  ["change_bp", "涨跌（BP）"], ["volume", "交易量（亿）"],
];
const metricUnits = {latest_yield: "%", net_price: "元", volume: "亿"};
const state = {manifest: null, snapshot: null, rows: [], series: [], sort: "volume", descending: true};
const get = (id) => document.getElementById(id);
const option = (label, value = label) => new Option(label, value);

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

async function readJson(path) {
  const response = await fetch(path, {cache: "no-cache"});
  if (!response.ok) throw new Error(`无法读取数据（HTTP ${response.status}）`);
  return response.json();
}

function notice(message, kind) {
  get("notice").textContent = message;
  get("notice").dataset.kind = kind;
}

function formatNumber(key, value) {
  if (value === null || value === undefined) return "—";
  const prefix = key === "change_bp" && value > 0 ? "+" : "";
  const digits = ["latest_yield", "weighted_yield", "volume"].includes(key) ? 4 : 2;
  return prefix + value.toFixed(digits);
}

function showRows() {
  if (!state.snapshot) return;
  const category = get("category").value;
  const term = get("tenor").value;
  const [lower, upper] = term.split(",").map(Number);
  const query = get("search").value.trim().toLowerCase();
  state.rows = state.snapshot.records.filter((row) => {
    const matchesTerm = !term || (row.term_years !== null && row.term_years >= lower && row.term_years < upper);
    return (!category || row.category === category) && matchesTerm &&
      `${row.code} ${row.name}`.toLowerCase().includes(query);
  }).sort((a, b) => {
    const x = a[state.sort];
    const y = b[state.sort];
    if (x === y) return a.code.localeCompare(b.code);
    if (x === null) return 1;
    if (y === null) return -1;
    return state.descending ? y - x : x - y;
  });

  const body = document.createDocumentFragment();
  for (const row of state.rows) {
    const tr = element("tr");
    const codeButton = element("button", row.code, "bond-code");
    codeButton.type = "button";
    codeButton.dataset.code = row.code;
    codeButton.setAttribute("aria-label", `查看 ${row.name} ${row.code} 的走势`);
    const codeCell = element("td");
    codeCell.append(codeButton);
    tr.append(codeCell, element("td", row.name), element("td", row.category));
    for (const [key] of columns.slice(3)) {
      const cell = element("td", formatNumber(key, row[key]));
      if (key === "change_bp" && row[key]) cell.className = row[key] > 0 ? "positive" : "negative";
      tr.append(cell);
    }
    body.append(tr);
  }
  if (!state.rows.length) {
    const row = element("tr");
    const message = element("td", "当前筛选没有匹配的债券。", "empty");
    message.colSpan = columns.length;
    row.append(message);
    body.append(row);
  }
  get("rows").replaceChildren(body);
  get("row-count").textContent = `显示 ${state.rows.length} / ${state.snapshot.records.length} 只`;
  get("export").disabled = state.rows.length === 0;
  document.querySelectorAll("button[data-sort]").forEach((button) => {
    const header = button.closest("th");
    if (button.dataset.sort === state.sort) header.setAttribute("aria-sort", state.descending ? "descending" : "ascending");
    else header.removeAttribute("aria-sort");
  });
}

function showSnapshot() {
  const snapshot = state.snapshot;
  const demo = snapshot.mode === "demo";
  get("mode").textContent = demo ? "模拟行情" : "行情快照";
  get("mode").dataset.mode = snapshot.mode;
  get("hero-date").textContent = snapshot.date;
  get("source-time").textContent = `来源时间 ${snapshot.source_time.slice(11, 16)} · 北京时间`;
  get("source").textContent = `来源 ${snapshot.source} · 北京时间`;
  notice(demo ? "示例网页使用虚构的债券和行情数值。" : `数据来源 ${snapshot.source}`, demo ? "demo" : "info");
  get("summary").replaceChildren(...categories.map((category) => {
    const card = element("div", undefined, "tile");
    const count = element("strong", snapshot.records.filter((row) => row.category === category).length);
    count.append(element("small", "只"));
    card.append(element("span", category), count);
    return card;
  }));
  showRows();
  const first = state.rows[0] ?? snapshot.records[0];
  if (first) void selectBond(first);
}

async function loadSnapshot(refresh = false) {
  get("reload").disabled = true;
  get("date").disabled = true;
  try {
    if (refresh || !state.manifest) {
      const manifest = await readJson("./data/index.json");
      get("date").replaceChildren(...manifest.dates.map((day) => option(day)));
      state.manifest = manifest;
      if (manifest.repository) {
        get("manage").href = `https://github.com/${manifest.repository}/actions/workflows/publish-pages.yml`;
        get("manage").hidden = false;
      }
    }
    const day = get("date").value;
    state.snapshot = await readJson(`./data/${day}.json`);
    showSnapshot();
  } catch (error) {
    notice(`行情读取失败。${error.message}。请点击“刷新行情”重试。`, "error");
  } finally {
    get("reload").disabled = false;
    get("date").disabled = !state.manifest;
  }
}

async function selectBond(row, scroll = false) {
  const request = Symbol();
  state.seriesRequest = request;
  state.series = [];
  get("series-name").textContent = `${row.name} · ${row.code}`;
  drawChart();
  get("chart-summary").textContent = "正在读取走势…";
  if (scroll) get("trend").scrollIntoView({block: "nearest"});
  try {
    const values = await readJson(`./data/series/${encodeURIComponent(row.code)}.json`);
    if (state.seriesRequest !== request) return;
    state.series = values;
    drawChart();
  } catch (error) {
    if (state.seriesRequest === request) get("chart-summary").textContent = error.message;
  }
}

function drawChart() {
  const canvas = get("chart");
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  const metric = get("metric").value;
  const points = new Map(state.series.map((point) => [point.date, point[metric]]));
  const days = (state.manifest?.dates ?? []).slice().reverse();
  const values = [...points.values()].filter((value) => value !== null);
  get("series-details").hidden = !values.length;
  get("series-values").replaceChildren(...state.series.map((point) =>
    element("li", `${point.date}　${formatNumber(metric, point[metric])} ${metricUnits[metric]}`)));
  if (!values.length || width < 120) {
    get("chart-summary").textContent = "当前指标暂无走势数据。";
    return;
  }

  const left = 62;
  const right = 16;
  const top = 14;
  const bottom = height - 30;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const extent = max - min || Math.max(Math.abs(max) * .015, .001);
  const low = min - extent * .12;
  const high = max + extent * .12;
  const x = (index) => left + (width - left - right) * (days.length > 1 ? index / (days.length - 1) : .5);
  const y = (value) => bottom - (bottom - top) * (value - low) / (high - low);
  ctx.font = "11px system-ui, sans-serif";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let tick = 0; tick <= 4; tick++) {
    const value = low + (high - low) * tick / 4;
    ctx.strokeStyle = "#dce5ed";
    ctx.beginPath();
    ctx.moveTo(left, y(value));
    ctx.lineTo(width - right, y(value));
    ctx.stroke();
    ctx.fillStyle = "#506279";
    ctx.fillText(formatNumber(metric, value), left - 7, y(value));
  }
  const markerDates = new Set([0, Math.floor((days.length - 1) / 2), days.length - 1]);
  ctx.strokeStyle = "#086b76";
  ctx.lineWidth = 2.4;
  ctx.beginPath();
  let connected = false;
  days.forEach((day, index) => {
    const value = points.get(day);
    if (typeof value !== "number") { connected = false; return; }
    if (connected) ctx.lineTo(x(index), y(value));
    else ctx.moveTo(x(index), y(value));
    connected = true;
  });
  ctx.stroke();
  days.forEach((day, index) => {
    const value = points.get(day);
    if (typeof value === "number") {
      ctx.beginPath();
      ctx.arc(x(index), y(value), 3.2, 0, 2 * Math.PI);
      ctx.fillStyle = "#086b76";
      ctx.fill();
    }
    if (markerDates.has(index)) {
      ctx.textAlign = index === days.length - 1 && days.length > 1 ? "right" : "center";
      ctx.fillStyle = "#506279";
      ctx.fillText(day, x(index), bottom + 15);
    }
  });
  get("chart-summary").textContent = `${values.length} 个有数据的日期 · 单位 ${metricUnits[metric]}`;
}

function csvCell(value) {
  const raw = String(value ?? "");
  const safe = typeof value === "string" && /^[\s\uFEFF]*[=+\-@]/.test(raw) ? `'${raw}` : raw;
  return `"${safe.replaceAll('"', '""')}"`;
}

function downloadCsv() {
  const snapshot = state.snapshot;
  const csv = [
    ["行情日期", ...columns.map(([, name]) => name), "来源时间", "来源", "数据性质"],
    ...state.rows.map((row) => [snapshot.date, ...columns.map(([key]) => row[key]),
      snapshot.source_time, snapshot.source, snapshot.mode === "demo" ? "模拟行情" : "成交行情快照"]),
  ].map((row) => row.map(csvCell).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob(["\uFEFF", csv], {type: "text/csv;charset=utf-8"}));
  const link = element("a");
  link.download = `利率债_${snapshot.date}${snapshot.mode === "demo" ? "_示例" : ""}.csv`;
  link.href = url;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

get("reload").addEventListener("click", () => loadSnapshot(true));
get("date").addEventListener("change", () => loadSnapshot());
get("export").addEventListener("click", downloadCsv);
get("search").addEventListener("input", showRows);
for (const id of ["category", "tenor"]) get(id).addEventListener("change", showRows);
get("metric").addEventListener("change", drawChart);
get("rows").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-code]");
  if (button) void selectBond(state.rows.find((row) => row.code === button.dataset.code), true);
});
document.querySelectorAll("button[data-sort]").forEach((button) => button.addEventListener("click", () => {
  state.descending = state.sort !== button.dataset.sort || !state.descending;
  state.sort = button.dataset.sort;
  showRows();
}));
new ResizeObserver(() => { if (state.series.length) drawChart(); }).observe(get("chart"));
void loadSnapshot(true);
