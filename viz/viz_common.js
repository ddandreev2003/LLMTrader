/**
 * Shared dashboard utilities: 5m candlesticks (Lightweight Charts) + event log.
 * Compatible with Lightweight Charts v4 (addCandlestickSeries) and v5 (addSeries).
 */
window.VizCommon = (function () {
  const EVENT_KINDS = ["all", "trade", "signal", "proposal", "shock", "reject", "halt", "system"];
  let candleChart = null;
  let candleSeries = null;
  let shockMarkersApi = null;
  let activeTicker = null;
  let eventFilter = "all";
  let lastEventCount = 0;
  let lastEvents = [];
  let eventLogId = "event-log";

  function formatTs(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function toUnixTime(isoStr) {
    if (!isoStr) return 0;
    const ms = Date.parse(isoStr);
    if (Number.isNaN(ms)) return 0;
    return Math.floor(ms / 1000);
  }

  function addCandleSeries(chart, options) {
    const LC = window.LightweightCharts;
    if (typeof chart.addCandlestickSeries === "function") {
      return chart.addCandlestickSeries(options);
    }
    if (LC && LC.CandlestickSeries && typeof chart.addSeries === "function") {
      return chart.addSeries(LC.CandlestickSeries, options);
    }
    console.error("[VizCommon] Lightweight Charts: unsupported API (need v4 or v5)");
    return null;
  }

  function initCandleTabs(tabContainerId, tickers, onSelect) {
    const el = document.getElementById(tabContainerId);
    if (!el) return;
    el.innerHTML = "";
    const list = tickers && tickers.length ? tickers : ["T", "SBER"];
    list.forEach((t, i) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ticker-tab" + (i === 0 ? " active" : "");
      btn.textContent = t;
      btn.dataset.ticker = t;
      btn.onclick = () => {
        el.querySelectorAll(".ticker-tab").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        activeTicker = t;
        if (onSelect) onSelect(t);
      };
      el.appendChild(btn);
    });
    activeTicker = list[0];
  }

  function initCandleChart(containerId) {
    const container = document.getElementById(containerId);
    if (!container || !window.LightweightCharts) {
      console.error("[VizCommon] Lightweight Charts not loaded");
      return;
    }
    container.innerHTML = "";
    shockMarkersApi = null;
    const h = container.clientHeight || container.parentElement?.clientHeight || 280;
    candleChart = LightweightCharts.createChart(container, {
      layout: { background: { color: "#121a26" }, textColor: "#8b9cb3" },
      grid: { vertLines: { color: "#2d3a4f" }, horzLines: { color: "#2d3a4f" } },
      width: container.clientWidth || 600,
      height: h,
      timeScale: { timeVisible: true, secondsVisible: false },
    });
    candleSeries = addCandleSeries(candleChart, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });
    window.addEventListener("resize", () => {
      if (candleChart && container) {
        candleChart.applyOptions({
          width: container.clientWidth,
          height: container.clientHeight || h,
        });
      }
    });
  }

  function buildShockMarkers(shocks, barHistory) {
    const timestamps = barHistory?.timestamps || [];
    return (shocks || [])
      .map((s) => {
        const tick = s.tick;
        if (tick == null || tick < 0 || tick >= timestamps.length) return null;
        const time = toUnixTime(timestamps[tick]);
        if (!time) return null;
        const impact = Number(s.price_impact_pct) || 0;
        return {
          time,
          position: impact >= 0 ? "belowBar" : "aboveBar",
          color: s.color || "#f59e0b",
          shape: "circle",
          text: `${s.type || "shock"} ${impact > 0 ? "+" : ""}${impact}%`,
        };
      })
      .filter(Boolean);
  }

  function updateShockMarkers(shocks, barHistory) {
    if (!candleSeries) return;
    const markers = buildShockMarkers(shocks, barHistory);
    const LC = window.LightweightCharts;
    if (LC.createSeriesMarkers) {
      if (!shockMarkersApi) {
        shockMarkersApi = LC.createSeriesMarkers(candleSeries, markers);
      } else {
        shockMarkersApi.setMarkers(markers);
      }
    } else if (typeof candleSeries.setMarkers === "function") {
      candleSeries.setMarkers(markers);
    }
  }

  function updateCandleChart(ticker, barHistory, shocks) {
    if (!candleSeries || !barHistory) return;
    activeTicker = ticker || activeTicker;
    const candles = (barHistory.candles && barHistory.candles[activeTicker]) || [];
    const timestamps = barHistory.timestamps || [];
    const data = candles
      .map((c, i) => ({
        time: toUnixTime(timestamps[i]),
        open: Number(c.o),
        high: Number(c.h),
        low: Number(c.l),
        close: Number(c.c),
      }))
      .filter((d) => d.time > 0 && d.open > 0);
    if (data.length === 0) return;
    candleSeries.setData(data);
    updateShockMarkers(shocks, barHistory);
    if (candleChart) candleChart.timeScale().fitContent();
  }

  function initEventFilters(containerId, logContainerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    if (logContainerId) eventLogId = logContainerId;
    el.innerHTML = "";
    EVENT_KINDS.forEach((kind) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "filter-chip" + (kind === "all" ? " active" : "");
      btn.textContent = kind === "all" ? "All" : kind;
      btn.dataset.filter = kind;
      btn.onclick = () => {
        eventFilter = kind;
        el.querySelectorAll(".filter-chip").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        renderEventLog(eventLogId, lastEvents, false);
      };
      el.appendChild(btn);
    });
  }

  function renderEventLog(logContainerId, events, preserveScroll) {
    const log = document.getElementById(logContainerId);
    if (!log) return;
    lastEvents = events || [];
    const prevScroll = log.scrollTop;
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    const filtered = lastEvents.filter((e) => {
      if (eventFilter === "all") return true;
      return e.kind === eventFilter;
    });
    log.innerHTML =
      filtered
        .slice()
        .reverse()
        .map((e) => {
          const tick = e.tick != null ? e.tick : "—";
          const agent = e.agent_id || "—";
          const ticker = e.ticker || "—";
          return `<div class="event-row event ${e.kind || ""}">
        <span class="ev-time">${formatTs(e.ts)}</span>
        <span class="ev-kind">${e.kind || ""}</span>
        <span class="ev-tick">${tick}</span>
        <span class="ev-agent">${agent}</span>
        <span class="ev-ticker">${ticker}</span>
        <span class="ev-msg">${e.message || ""}</span>
      </div>`;
        })
        .join("") || '<div class="event-row muted">Событий пока нет</div>';

    if (preserveScroll && filtered.length === lastEventCount) {
      log.scrollTop = prevScroll;
    } else if (atBottom || filtered.length > lastEventCount) {
      log.scrollTop = 0;
    }
    lastEventCount = filtered.length;
  }

  function renderShockList(containerId, shocks) {
    const el = document.getElementById(containerId);
    if (!el) return;
    const list = shocks || [];
    el.innerHTML =
      list
        .slice()
        .reverse()
        .map(
          (s) => `
        <div class="shock-item" style="border-left-color:${s.color || "#f59e0b"}">
          <strong>tick ${s.tick ?? "—"}</strong> · ${s.type || "shock"}
          <span style="color:${(s.price_impact_pct || 0) >= 0 ? "#22c55e" : "#ef4444"}">
            ${s.price_impact_pct > 0 ? "+" : ""}${s.price_impact_pct ?? 0}%
          </span>
          <div style="color:#8b9cb3;margin-top:4px">${s.description || ""}</div>
        </div>`
        )
        .join("") || '<div class="muted">Шоков пока нет</div>';
  }

  let lastShockFlashTick = -1;
  const agentSparkCharts = {};

  function agentLabel(state, agentId) {
    const snap = (state.agents || {})[agentId];
    const meta = (state.agents_meta || {})[agentId];
    return snap?.display_name || meta?.display_name || agentId;
  }

  function decimatePoints(points, maxPts) {
    if (!points || points.length <= maxPts) return points || [];
    const step = Math.ceil(points.length / maxPts);
    return points.filter((_, i) => i % step === 0);
  }

  function formatHoldings(snap) {
    const h = snap?.holdings || {};
    const parts = Object.entries(h).map(([t, q]) => `${t}: ${q}`);
    return parts.length ? parts.join(" · ") : "—";
  }

  function renderAgentPanels(containerId, state, ChartLib) {
    const el = document.getElementById(containerId);
    if (!el) return;
    const meta = state.agents_meta || {};
    const agents = state.agents || {};
    const trades = state.agent_trades || {};
    const equity = state.agent_equity || {};
    const ids = Object.keys(meta).length ? Object.keys(meta) : Object.keys(agents);
    if (!ids.length) {
      el.innerHTML = '<div class="muted">Агенты появятся после старта симуляции</div>';
      return;
    }

    ids.forEach((id) => {
      let panel = el.querySelector(`[data-agent-panel="${id}"]`);
      if (!panel) {
        panel = document.createElement("div");
        panel.className = "agent-panel";
        panel.dataset.agentPanel = id;
        panel.innerHTML = `
          <div class="agent-panel-accent"></div>
          <div class="agent-panel-head">
            <h3 class="agent-panel-title"></h3>
            <span class="agent-panel-strategy"></span>
          </div>
          <div class="agent-panel-stats"></div>
          <div class="agent-panel-holdings"></div>
          <div class="agent-panel-chart-wrap"><canvas class="agent-spark"></canvas></div>
          <div class="agent-panel-trades-title">Последние сделки</div>
          <ul class="agent-panel-trades"></ul>`;
        el.appendChild(panel);
      }

      const m = meta[id] || {};
      const snap = agents[id] || {};
      const color = snap.nft_color || m.nft_color || "#3b82f6";
      const title = agentLabel(state, id);
      panel.querySelector(".agent-panel-accent").style.background = color;
      panel.querySelector(".agent-panel-title").textContent = title;
      panel.querySelector(".agent-panel-strategy").textContent =
        snap.strategy || m.strategy || "";
      const pnl = snap.pnl || 0;
      const pnlCls = pnl >= 0 ? "pnl-pos" : "pnl-neg";
      panel.querySelector(".agent-panel-stats").innerHTML = `
        <span>${(snap.portfolio_value || 0).toLocaleString("ru")} ₽</span>
        <span class="${pnlCls}">${pnl >= 0 ? "+" : ""}${pnl.toLocaleString("ru")} ₽</span>
        <span class="muted">cash ${(snap.cash || 0).toLocaleString("ru")} ₽</span>`;
      panel.querySelector(".agent-panel-holdings").textContent =
        "Позиции: " + formatHoldings(snap);

      const canvas = panel.querySelector(".agent-spark");
      const curve = decimatePoints(
        (equity[id] || []).map((p) => p.value),
        500
      );
      if (ChartLib && canvas && curve.length > 1) {
        if (agentSparkCharts[id]) {
          agentSparkCharts[id].data.labels = curve.map((_, i) => i);
          agentSparkCharts[id].data.datasets[0].data = curve;
          agentSparkCharts[id].data.datasets[0].borderColor = color;
          agentSparkCharts[id].update("none");
        } else {
          agentSparkCharts[id] = new ChartLib(canvas, {
            type: "line",
            data: {
              labels: curve.map((_, i) => i),
              datasets: [
                {
                  data: curve,
                  borderColor: color,
                  borderWidth: 1.5,
                  pointRadius: 0,
                  tension: 0.2,
                },
              ],
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              plugins: { legend: { display: false } },
              scales: {
                x: { display: false },
                y: { display: false },
              },
            },
          });
        }
      }

      const tradeList = panel.querySelector(".agent-panel-trades");
      const recent = (trades[id] || []).slice().reverse().slice(0, 5);
      tradeList.innerHTML =
        recent
          .map(
            (t) =>
              `<li>${t.action} ${t.ticker} ×${t.quantity} @ ${Number(t.price).toFixed(2)}</li>`
          )
          .join("") || '<li class="muted">—</li>';
    });

    el.querySelectorAll("[data-agent-panel]").forEach((p) => {
      if (!ids.includes(p.dataset.agentPanel)) p.remove();
    });
  }

  function flashLatestShock(shocks) {
    const list = shocks || [];
    if (!list.length) return;
    const latest = list[list.length - 1];
    const tick = latest.tick ?? -1;
    if (tick <= lastShockFlashTick) return;
    lastShockFlashTick = tick;
    const flash = document.getElementById("shock-flash");
    if (!flash) return;
    flash.style.display = "block";
    flash.innerHTML = `<strong>⚡ ШОК: ${latest.type || "shock"}</strong><br>
      tick ${latest.tick ?? "—"} · ${latest.price_impact_pct > 0 ? "+" : ""}${latest.price_impact_pct ?? 0}%<br>
      <small>${latest.description || ""}</small>`;
    setTimeout(() => {
      flash.style.display = "none";
    }, 8000);
  }

  function renderLaunchPanel(containerId, show) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.style.display = show ? "block" : "none";
    if (show && !el.dataset.filled) {
      el.innerHTML = `
        <ol class="launch-steps">
          <li><code>cd LLMTrader && source .venv/bin/activate</code></li>
          <li><code>cp .env.example .env</code> — ключ RouterAI</li>
          <li><code>python scripts/preprocess_orderlog_bars.py</code> (если нет parquet)</li>
          <li><code>python main.py</code> или <code>python scripts/web_server.py</code></li>
          <li>Дашборд: <a href="/dashboard.html">/dashboard.html</a></li>
        </ol>
        <p class="muted">NFT: <code>NFT=1 ./scripts/run_intraday.sh</code> (~5 торг. дней)</p>`;
      el.dataset.filled = "1";
    }
  }

  function updateHeader(state, ids) {
    const badge = document.getElementById(ids.statusBadge);
    if (badge) {
      badge.textContent = state.status || "—";
      badge.className = "badge " + (state.halted ? "halted" : state.status || "");
    }
    const tickEl = document.getElementById(ids.tickInfo);
    if (tickEl) {
      const total = state.total_ticks != null ? state.total_ticks : "—";
      tickEl.textContent = `tick ${state.tick ?? 0} / ${total}`;
    }
    const dateEl = document.getElementById(ids.dateInfo);
    if (dateEl) {
      const dt = state.datetime || state.date || "—";
      const interval = state.bar_interval ? ` · ${state.bar_interval}` : "";
      const phase = state.micro_phase ? ` · ${state.micro_phase}` : "";
      const dayN = state.sim_trading_days || 0;
      const dayM = state.sim_total_days || 0;
      const dayPart = dayM ? ` · день ${dayN}/${dayM}` : "";
      dateEl.textContent = dt + interval + phase + dayPart;
    }
    const benchEl = document.getElementById(ids.benchInfo);
    if (benchEl) benchEl.textContent = `bench ${state.benchmark_price ?? "—"}`;
  }

  return {
    initCandleTabs,
    initCandleChart,
    updateCandleChart,
    initEventFilters,
    renderEventLog,
    renderShockList,
    flashLatestShock,
    renderLaunchPanel,
    updateHeader,
    renderAgentPanels,
    agentLabel,
    getActiveTicker: () => activeTicker,
    setActiveTicker: (t) => {
      activeTicker = t;
    },
  };
})();
