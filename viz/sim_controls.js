/**
 * Shared simulation control toolbar (web API or read-only batch mode).
 */
window.SimControls = (function () {
  const BTN_IDS = ["btn-start", "btn-pause", "btn-step", "btn-play", "btn-stop", "btn-reset"];
  let apiAvailable = null;
  let ws = null;
  let pollTimer = null;

  function api(path, opts = {}) {
    return fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  }

  async function detectApi() {
    if (apiAvailable !== null) return apiAvailable;
    try {
      const r = await api("/api/state");
      apiAvailable = r.ok;
    } catch (e) {
      apiAvailable = false;
    }
    return apiAvailable;
  }

  function bindButtons(handlers) {
    BTN_IDS.forEach((id) => {
      const el = document.getElementById(id);
      if (el && handlers[id]) el.onclick = handlers[id];
    });
    const speed = document.getElementById("speed");
    if (speed) {
      speed.addEventListener("input", (e) => {
        const val = document.getElementById("speed-val");
        if (val) val.textContent = e.target.value + "ms";
      });
    }
  }

  function updateButtons(state) {
    const status = state?.status || "idle";
    const tick = state?.tick ?? state?.current_tick ?? 0;
    const running = status === "running";
    const paused = status === "paused" || status === "idle" || status === "initializing";
    const finished = status === "finished" || status === "stopped";

    const map = {
      "btn-start": !running && !finished,
      "btn-pause": running,
      "btn-step": !finished,
      "btn-play": !running && !finished,
      "btn-stop": !finished,
      "btn-reset": true,
    };
    Object.entries(map).forEach(([id, enabled]) => {
      const el = document.getElementById(id);
      if (el) el.disabled = !enabled;
    });
    const apply = document.getElementById("btn-apply-config");
    if (apply) apply.disabled = !(paused && tick === 0);
  }

  async function initWebControls(onState) {
    if (!(await detectApi())) {
      document.querySelectorAll(".sim-controls-api").forEach((el) => {
        el.title = "Запустите python scripts/web_server.py для управления";
      });
      return false;
    }

    bindButtons({
      "btn-start": () => api("/api/sim/start", { method: "POST" }),
      "btn-pause": () => api("/api/sim/pause", { method: "POST" }),
      "btn-step": () => api("/api/sim/step", { method: "POST" }),
      "btn-play": () => {
        const ms = parseInt(document.getElementById("speed")?.value || "500", 10);
        return api("/api/sim/play", {
          method: "POST",
          body: JSON.stringify({ interval_ms: ms }),
        });
      },
      "btn-stop": () => api("/api/sim/stop", { method: "POST" }),
      "btn-reset": () => api("/api/sim/reset", { method: "POST" }),
    });

    function connectWs() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onmessage = (ev) => {
        try {
          const state = JSON.parse(ev.data);
          updateButtons(state);
          if (onState) onState(state);
        } catch (e) {}
      };
      ws.onclose = () => setTimeout(connectWs, 2000);
    }
    connectWs();

    api("/api/state")
      .then((r) => r.json())
      .then((s) => {
        updateButtons(s);
        if (onState) onState(s);
      })
      .catch(() => {});

    return true;
  }

  async function applyPreset(presetPath) {
    const cfg = await (await api("/api/config")).json();
    const body = { ...cfg, strategies_config: presetPath };
    if (presetPath.includes("hft")) {
      body.portfolio_config = "config/portfolio_intraday_hft.yaml";
      body.market_mode = "orderlog_intraday";
    }
    if (presetPath.includes("nft")) {
      body.portfolio_config = "config/portfolio_intraday_nft.yaml";
      body.market_mode = "orderlog_intraday";
    }
    const res = await api("/api/config", { method: "PUT", body: JSON.stringify(body) });
    return res.ok;
  }

  return {
    initWebControls,
    updateButtons,
    applyPreset,
    detectApi,
  };
})();
