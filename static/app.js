async function apiGet(url) {
  const res = await fetch(url, {
    credentials: "same-origin",
  });
  return res.json();
}

async function apiPost(url, data = {}) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(data),
  });
  return res.json();
}

function byId(id) {
  return document.getElementById(id);
}

function setText(id, text) {
  const el = byId(id);
  if (el) el.textContent = text;
}

function setHtml(id, html) {
  const el = byId(id);
  if (el) el.innerHTML = html;
}

function setBadge(id, label, running) {
  const el = byId(id);
  if (!el) return;
  el.textContent = `${label}: ${running ? "RUNNING" : "STOPPED"}`;
  el.style.color = running ? "var(--ok)" : "var(--muted)";
}

function setToggleButton(id, running) {
  const btn = byId(id);
  if (!btn) return;
  btn.textContent = running ? "Stop" : "Start";
  btn.classList.toggle("danger", running);
}

function setDisabled(id, disabled) {
  const el = byId(id);
  if (el) el.disabled = disabled;
}

function renderList(containerId, items, formatter) {
  const container = byId(containerId);
  if (!container) return;

  container.innerHTML = "";

  if (!items || items.length === 0) {
    container.innerHTML = `<div class="log-item">Sem dados.</div>`;
    return;
  }

  [...items].reverse().forEach(item => {
    const div = document.createElement("div");
    div.className = "log-item";
    div.innerHTML = formatter(item);
    container.appendChild(div);
  });
}

function openModal(id) {
  const el = byId(id);
  if (el) el.classList.remove("hidden");
}

function closeModal(id) {
  const el = byId(id);
  if (el) el.classList.add("hidden");
}

function populateIfaceSelect(interfaces, selectedValue) {
  const select = byId("ifaceSelect");
  if (!select) return;

  select.innerHTML = "";

  const allOption = document.createElement("option");
  allOption.value = "ALL";
  allOption.textContent = "ALL";
  if ((selectedValue || "ALL") === "ALL") {
    allOption.selected = true;
  }
  select.appendChild(allOption);

  (interfaces || []).forEach(iface => {
    const option = document.createElement("option");
    option.value = iface;
    option.textContent = iface;
    if (iface === selectedValue) {
      option.selected = true;
    }
    select.appendChild(option);
  });
}

async function refreshStatus() {
  const data = await apiGet("/api/status");

  const agentConnected = !!data.agent?.connected;
  const lastSeen = data.agent?.last_seen
    ? new Date(data.agent.last_seen * 1000).toLocaleTimeString()
    : "-";

  setText(
    "agentStatus",
    `${agentConnected ? "CONNECTED" : "DISCONNECTED"} | id=${data.agent?.agent_id || "-"} | host=${data.agent?.hostname || "-"} | iface=${data.agent?.iface || "-"} | mode=${data.agent?.mode || "-"} | running=${data.agent?.running ? "YES" : "NO"} | last_seen=${lastSeen}`
  );

  setText(
    "monitorStatus",
    `${data.monitor?.running ? "RUNNING" : "STOPPED"} | current_iface=${data.monitor?.iface || "-"} | current_mode=${data.monitor?.mode || "-"} | target_iface=${data.agent_config?.iface || "-"} | target_mode=${data.agent_config?.mode || "-"}`
  );

  setText(
    "serverStatus",
    `${data.server?.running ? "RUNNING" : "STOPPED"} | ${data.server?.host || "-"}:${data.server?.port || "-"}`
  );

  setText(
    "clientStatus",
    `${data.client?.running ? "RUNNING" : "STOPPED"} | ${data.client?.host || "-"}:${data.client?.port || "-"} | poll=${data.client?.poll_interval ?? "-"}s`
  );

  setBadge("globalMonitorBadge", "MONITOR", !!data.monitor?.running);
  setBadge("globalServerBadge", "SERVER", !!data.server?.running);
  setBadge("globalClientBadge", "CLIENT", !!data.client?.running);

  setToggleButton("toggleServerBtn", !!data.server?.running);
  setToggleButton("toggleClientBtn", !!data.client?.running);

  setDisabled("toggleServerBtn", !agentConnected);
  setDisabled("toggleClientBtn", !agentConnected);
  setDisabled("openMonitorConfigBtn", !agentConnected);
  setDisabled("openServerConfigBtn", !agentConnected);
  setDisabled("openClientConfigBtn", !agentConnected);

  if (byId("serverHost")) byId("serverHost").value = data.server?.host || "127.0.0.1";
  if (byId("serverPort")) byId("serverPort").value = data.server?.port || 5020;

  if (byId("clientHost")) byId("clientHost").value = data.client?.host || "127.0.0.1";
  if (byId("clientPort")) byId("clientPort").value = data.client?.port || 5020;
  if (byId("pollInterval")) byId("pollInterval").value = data.client?.poll_interval ?? 1.0;
  if (byId("pollStart")) byId("pollStart").value = data.client?.poll_start ?? 0;
  if (byId("pollQuantity")) byId("pollQuantity").value = data.client?.poll_quantity ?? 4;

  setText("monitorSnapshot", JSON.stringify(data.monitor?.snapshot || {}, null, 2));

  // Como o backend novo não tem mais processo local, deixamos a área como placeholder.
  setText("levelValue", "-");
  setHtml("pumpValue", `<span class="off">-</span>`);
  setHtml("valveValue", `<span class="off">-</span>`);
  setHtml("alarmValue", `<span class="off">-</span>`);
}

async function refreshEvents() {
  const data = await apiGet("/api/events");

  renderList("eventsPanel", data.events, (e) => {
    const base = `[${e.type || "UNKNOWN"}] ${e.src_ip || "-"}:${e.src_port || "-"} -> ${e.dst_ip || "-"}:${e.dst_port || "-"}`;
    if (e.type === "READ_REQUEST") {
      return `${base} | start=${e.start_addr} qty=${e.quantity}`;
    }
    if (e.type === "READ_RESPONSE") {
      return `${base} | values=${JSON.stringify(e.register_values || [])} | rtt=${e.rtt ?? "-"}`;
    }
    if (e.type === "WRITE_REQUEST" || e.type === "WRITE_RESPONSE") {
      return `${base} | reg=${e.register} value=${e.value} | rtt=${e.rtt ?? "-"}`;
    }
    return base;
  });

  renderList("alertsPanel", data.alerts, (a) => {
    return `<div class="alert-${a.severity || "INFO"}">
      <strong>${a.severity || "INFO"}</strong> score=${a.score ?? "-"}<br>
      ${a.event_type || "-"} | ${a.src || "-"} -> ${a.dst || "-"}<br>
      ${(a.reasons || []).join(" | ")}
    </div>`;
  });

  renderList("logsPanel", data.logs, (log) => `${log}`);
}

async function refreshAll() {
  try {
    await refreshStatus();
    await refreshEvents();
  } catch (err) {
    console.error(err);
  }
}

async function startServer() {
  const host = byId("serverHost")?.value || "127.0.0.1";
  const port = Number(byId("serverPort")?.value || 5020);

  const result = await apiPost("/api/agent/server/start", { host, port });
  if (!result.ok) {
    setText("serverStatus", `Erro ao iniciar servidor.`);
  }
}

async function stopServer() {
  const result = await apiPost("/api/agent/server/stop");
  if (!result.ok) {
    setText("serverStatus", `Erro ao parar servidor.`);
  }
}

async function toggleServer() {
  const status = await apiGet("/api/status");
  if (!status.agent?.connected) {
    setText("serverStatus", "Agente desconectado.");
    return;
  }

  if (status.server?.running) {
    await stopServer();
  } else {
    await startServer();
  }

  await refreshAll();
}

async function startClient() {
  const host = byId("clientHost")?.value || "127.0.0.1";
  const port = Number(byId("clientPort")?.value || 5020);
  const poll_interval = Number(byId("pollInterval")?.value || 1.0);
  const poll_start = Number(byId("pollStart")?.value || 0);
  const poll_quantity = Number(byId("pollQuantity")?.value || 4);

  const result = await apiPost("/api/agent/client/start", {
    host,
    port,
    poll_interval,
    poll_start,
    poll_quantity
  });

  if (!result.ok) {
    setText("clientStatus", `Erro ao iniciar cliente.`);
  }
}

async function stopClient() {
  const result = await apiPost("/api/agent/client/stop");
  if (!result.ok) {
    setText("clientStatus", `Erro ao parar cliente.`);
  }
}

async function toggleClient() {
  const status = await apiGet("/api/status");
  if (!status.agent?.connected) {
    setText("clientStatus", "Agente desconectado.");
    return;
  }

  if (status.client?.running) {
    await stopClient();
  } else {
    await startClient();
  }

  await refreshAll();
}

async function resetSystem() {
  await apiPost("/api/reset");
  await refreshAll();
}

async function saveMonitorConfig() {
  const iface = byId("ifaceSelect")?.value || "ALL";
  const mode = byId("monitorMode")?.value || "MONITORING";

  const result = await apiPost("/api/agent/config", { iface, mode });

  if (result.ok) {
    setText("monitorConfigStatus", "Configuração salva com sucesso.");
    closeModal("monitorModal");
    await refreshAll();
  } else {
    setText("monitorConfigStatus", `Erro ao salvar: ${result.error || "desconhecido"}`);
  }
}

async function openMonitorConfig() {
  const status = await apiGet("/api/status");

  if (byId("monitorMode")) {
    byId("monitorMode").value = status.agent_config?.mode || "MONITORING";
  }

  openModal("monitorModal");
  await scanInterfaces();

  populateIfaceSelect(
    status.agent?.available_ifaces || [],
    status.agent_config?.iface || "ALL"
  );
}

async function scanInterfaces() {
  const data = await apiGet("/api/agent/interfaces");

  if (!data.connected) {
    setText("monitorConfigStatus", "Agente desconectado.");
    populateIfaceSelect([], "");
    return;
  }

  populateIfaceSelect(data.interfaces || [], data.current || "ALL");
  setText("monitorConfigStatus", `Interfaces encontradas: ${(data.interfaces || []).join(", ") || "-"}`);
}

function disableLegacySections() {
  setDisabled("sendReadBtn", true);
  setDisabled("sendWriteBtn", true);
  setDisabled("togglePumpBtn", true);
  setDisabled("toggleValveBtn", true);
  setDisabled("resetProcessBtn", true);
  setDisabled("setLevelBtn", true);

  setText("actionResult", "Ações READ/WRITE locais desativadas nesta versão.");
}

window.addEventListener("DOMContentLoaded", () => {
  byId("toggleServerBtn")?.addEventListener("click", toggleServer);
  byId("toggleClientBtn")?.addEventListener("click", toggleClient);
  byId("resetSystemBtn")?.addEventListener("click", resetSystem);

  byId("openMonitorConfigBtn")?.addEventListener("click", openMonitorConfig);
  byId("saveMonitorConfigBtn")?.addEventListener("click", saveMonitorConfig);
  byId("scanIfacesBtn")?.addEventListener("click", scanInterfaces);

  byId("openServerConfigBtn")?.addEventListener("click", () => openModal("serverModal"));
  byId("openClientConfigBtn")?.addEventListener("click", () => openModal("clientModal"));
  byId("openAgentDownloadBtn")?.addEventListener("click", () => openModal("agentDownloadModal"));

  document.querySelectorAll("[data-close]").forEach(btn => {
    btn.addEventListener("click", () => closeModal(btn.dataset.close));
  });

  document.querySelectorAll(".modal").forEach(modal => {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) {
        modal.classList.add("hidden");
      }
    });
  });

  disableLegacySections();
  refreshAll();
  setInterval(refreshAll, 1000);
});