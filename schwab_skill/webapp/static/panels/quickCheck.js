/**
 * Quick-check panel — fast `/api/check/<ticker>` lookup that renders a
 * summary card and (when LightweightCharts is loaded) a small candle
 * chart underneath. The chart lifecycle is tracked locally in
 * `_activeChart` so re-runs replace the existing instance cleanly.
 */

import { api } from "../modules/api.js";
import { safeText, prettyJson } from "../modules/format.js";
import { logEvent } from "../modules/logger.js";

export function renderQuickCheckCard(data, error) {
  const ph = document.getElementById("checkPlaceholder");
  const sum = document.getElementById("checkSummary");
  const det = document.getElementById("checkJsonDetails");
  const pre = document.getElementById("checkOutput");
  if (!sum) return;
  if (error) {
    if (ph) { ph.textContent = error; ph.classList.remove("hidden"); }
    sum.classList.add("hidden"); sum.innerHTML = "";
    if (det) det.classList.add("hidden");
    if (pre) pre.textContent = "";
    return;
  }
  if (ph) ph.classList.add("hidden");
  const d = data || {};

  const title = d.title || d.ticker || "Quick Check";
  const desc = (d.description || "").replace(/\*\*/g, "");
  const fields = d.fields || [];

  let fieldsHtml = "";
  if (fields.length) {
    fieldsHtml = '<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; margin-top: 10px;">';
    for (const f of fields) {
      const val = (f.value || "").replace(/\*\*/g, "").replace(/\n/g, "<br>");
      fieldsHtml += `<div class="preset-subsection" style="padding: 10px;">
        <h3 style="margin: 0 0 6px; font-size: 0.82rem;">${safeText(f.name)}</h3>
        <div style="font-size: 0.84rem; color: #dbe6ff; line-height: 1.5;">${val}</div>
      </div>`;
    }
    fieldsHtml += "</div>";
  } else {
    const items = [];
    const price = d.price ?? d.current_price ?? d.last_price;
    const stage2 = d.stage_2 ?? d.is_stage_2;
    const vcp = d.vcp ?? d.vcp_detected;
    const score = d.signal_score ?? d.score;
    const sector = d.sector ?? d.sector_etf;
    if (price != null) items.push(`<li><strong>Price:</strong> $${Number(price).toFixed(2)}</li>`);
    if (stage2 != null) items.push(`<li><strong>Stage 2:</strong> <span class="pill ${stage2 ? 'good' : 'bad'} small">${stage2 ? 'Yes' : 'No'}</span></li>`);
    if (vcp != null) items.push(`<li><strong>VCP:</strong> <span class="pill ${vcp ? 'good' : 'bad'} small">${vcp ? 'Detected' : 'None'}</span></li>`);
    if (score != null) items.push(`<li><strong>Signal Score:</strong> ${Number(score).toFixed(1)}/100</li>`);
    if (sector) items.push(`<li><strong>Sector:</strong> ${safeText(sector)}</li>`);
    Object.entries(d).forEach(([k, v]) => {
      if (v != null && typeof v !== "object" && !["title", "description", "color", "timestamp", "ticker"].includes(k)) {
        items.push(`<li><strong>${safeText(k)}:</strong> ${safeText(String(v))}</li>`);
      }
    });
    if (items.length) fieldsHtml = `<ul class="tool-summary-list">${items.join("")}</ul>`;
  }

  sum.classList.remove("hidden");
  sum.innerHTML = `
    <h4 class="tool-summary-title">${safeText(title)}</h4>
    ${desc ? `<p class="tool-summary-p" style="margin-bottom: 4px;">${safeText(desc)}</p>` : ""}
    ${fieldsHtml}
  `;
  if (det) det.classList.remove("hidden");
  if (pre) pre.textContent = prettyJson(data);
}

let _activeChart = null;

export async function renderTickerChart(ticker) {
  const container = document.getElementById("tickerChartContainer");
  if (!container || typeof LightweightCharts === "undefined") return;
  container.classList.remove("hidden");
  container.innerHTML = "";

  const out = await api.get(`/api/chart/${encodeURIComponent(ticker)}`);
  if (!out.ok || !out.data?.candles?.length) {
    container.innerHTML = `<div class="muted" style="padding:12px">No chart data available for ${safeText(ticker)}.</div>`;
    return;
  }

  const chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: 280,
    layout: { background: { type: "solid", color: "transparent" }, textColor: "#9ca3b8" },
    grid: { vertLines: { color: "rgba(99,120,200,0.06)" }, horzLines: { color: "rgba(99,120,200,0.06)" } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: "rgba(99,120,200,0.15)" },
    timeScale: { borderColor: "rgba(99,120,200,0.15)", timeVisible: false },
  });
  const candleSeries = chart.addCandlestickSeries({
    upColor: "#34d399", downColor: "#fb7185",
    borderUpColor: "#34d399", borderDownColor: "#fb7185",
    wickUpColor: "#34d399", wickDownColor: "#fb7185",
  });
  candleSeries.setData(out.data.candles);

  const volSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "",
    scaleMargins: { top: 0.85, bottom: 0 },
  });
  volSeries.setData(out.data.candles.map((c) => ({
    time: c.time,
    value: c.volume,
    color: c.close >= c.open ? "rgba(52,211,153,0.25)" : "rgba(251,113,133,0.25)",
  })));

  chart.timeScale().fitContent();
  _activeChart = chart;

  const ro = new ResizeObserver(() => {
    if (_activeChart) _activeChart.applyOptions({ width: container.clientWidth });
  });
  ro.observe(container);
}

export async function quickCheck() {
  const ticker = document.getElementById("tickerInput").value.trim().toUpperCase();
  if (!ticker) return;
  renderQuickCheckCard(null, "Loading...");
  const out = await api.get(`/api/check/${ticker}`);
  if (!out.ok) {
    renderQuickCheckCard(null, `Check failed: ${out.error}`);
    logEvent({ kind: "system", severity: "error", message: `Check ${ticker} failed: ${out.error}` });
    return;
  }
  renderQuickCheckCard(out.data, null);
  renderTickerChart(ticker);
  logEvent({ kind: "system", severity: "info", message: `Check complete for ${ticker}.` });
}
