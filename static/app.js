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
  byId(id).textContent = text;
}

function setBadge(id, label, running) {
  const el = byId(id);
  el.textContent = `${label}: ${running ? "RUNNING" : "STOPPED"}`;
  el.style.color = running ? "var(--ok)" : "var(--muted)";
}

function setToggleButton(id, running) {
  const btn = byId(id);
  btn.textContent = running ? "Stop" : "Start";
  btn.classList.toggle("danger", running);
}

function renderList(containerId, items, formatter) {
  const container = byId(containerId);
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
  byId(id).classList.remove("hidden");
}

function closeModal(id) {
  byId(id).classList.add("hidden");
}

async function refreshStatus() {
  const data = await apiGet("/api/status");

  if (data.agent) {
    const lastSeen = data.agent.last_seen
      ? new Date(data.agent.last_seen * 1000).toLocaleTimeString()
      : "-";

    setText(
      "agentStatus",
      `${data.agent.connected ? "CONNECTED" : "DISCONNECTED"} | id=${data.agent.agent_id || "-"} | host=${data.agent.hostname || "-"} | iface=${data.agent.iface || "-"} | mode=${data.agent.mode || "-"} | running=${data.agent.running ? "YES" : "NO"} | last_seen=${lastSeen}`
    );
  }

  setText(
    "monitorStatus",
    `${data.monitor.running ? "RUNNING" : "STOPPED"} | current_iface=${data.monitor.iface} | current_mode=${data.monitor.mode} | target_iface=${data.agent_config?.iface || "-"} | target_mode=${data.agent_config?.mode || "-"}`
  );

  setText(
    "serverStatus",
    `${data.server.running ? "RUNNING" : "STOPPED"} | ${data.server.host}:${data.server.port}`
  );

  setText(
    "clientStatus",
    `${data.client.running ? "RUNNING" : "STOPPED"} | ${data.client.host}:${data.client.port} | poll=${data.client.poll_interval}s`
  );

  setBadge("globalMonitorBadge", "MONITOR", data.monitor.running);
  setBadge("globalServerBadge", "SERVER", data.server.running);
  setBadge("globalClientBadge", "CLIENT", data.client.running);

  setToggleButton("toggleServerBtn", data.server.running);
  setToggleButton("toggleClientBtn", data.client.running);

  setText("levelValue", data.process.level);
  byId("pumpValue").innerHTML = `<span class="${data.process.pump_on ? "on" : "off"}">${data.process.pump_on ? "ON" : "OFF"}</span>`;
  byId("valveValue").innerHTML = `<span class="${data.process.valve_open ? "on" : "off"}">${data.process.valve_open ? "OPEN" : "CLOSED"}</span>`;
  byId("alarmValue").innerHTML = `<span class="${data.process.alarm_high ? "on" : "off"}">${data.process.alarm_high ? "ON" : "OFF"}</span>`;

  byId("monitorSnapshot").textContent = JSON.stringify(data.monitor.snapshot, null, 2);
}

async function refreshEvents() {
  const data = await apiGet("/api/events");

  renderList("eventsPanel", data.events, (e) => {
    const base = `[${e.type}] ${e.src_ip}:${e.src_port} -> ${e.dst_ip}:${e.dst_port}`;
    if (e.type === "READ_REQUEST") {
      return `${base} | start=${e.start_addr} qty=${e.quantity}`;
    }
    if (e.type === "READ_RESPONSE") {
      return `${base} | values=${JSON.stringify(e.register_values)} | rtt=${e.rtt}`;
    }
    if (e.type === "WRITE_REQUEST" || e.type === "WRITE_RESPONSE") {
      return `${base} | reg=${e.register} value=${e.value} | rtt=${e.rtt ?? "-"}`;
    }
    return base;
  });

  renderList("alertsPanel", data.alerts, (a) => {
    return `<div class="alert-${a.severity}">
      <strong>${a.severity}</strong> score=${a.score}<br>
      ${a.event_type} | ${a.src} -> ${a.dst}<br>
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
  const host = byId("serverHost").value;
  const port = Number(byId("serverPort").value);
  await apiPost("/api/server/start", { host, port });
}

async function stopServer() {
  await apiPost("/api/server/stop");
}

async function toggleServer() {
  const status = await apiGet("/api/status");
  if (status.server.running) {
    await stopServer();
  } else {
    await startServer();
  }
  refreshAll();
}

async function startClient() {
  const host = byId("clientHost").value;
  const port = Number(byId("clientPort").value);
  const poll_interval = Number(byId("pollInterval").value);
  const poll_start = Number(byId("pollStart").value);
  const poll_quantity = Number(byId("pollQuantity").value);

  await apiPost("/api/client/start", {
    host,
    port,
    poll_interval,
    poll_start,
    poll_quantity
  });
}

async function stopClient() {
  await apiPost("/api/client/stop");
}

async function toggleClient() {
  const status = await apiGet("/api/status");
  if (status.client.running) {
    await stopClient();
  } else {
    await startClient();
  }
  refreshAll();
}

async function sendRead() {
  const start = Number(byId("readStart").value);
  const quantity = Number(byId("readQty").value);
  const result = await apiPost("/api/client/read", { start, quantity });
  setText("actionResult", result.ok ? `READ OK: ${JSON.stringify(result.values)}` : `ERRO: ${result.error}`);
  refreshAll();
}

async function sendWrite() {
  const register = Number(byId("writeRegister").value);
  const value = Number(byId("writeValue").value);
  const result = await apiPost("/api/client/write", { register, value });
  setText("actionResult", result.ok ? `WRITE OK: ${JSON.stringify(result.result)}` : `ERRO: ${result.error}`);
  refreshAll();
}

async function togglePump() {
  const status = await apiGet("/api/status");
  await apiPost("/api/process/set", { pump_on: !status.process.pump_on });
  refreshAll();
}

async function toggleValve() {
  const status = await apiGet("/api/status");
  await apiPost("/api/process/set", { valve_open: !status.process.valve_open });
  refreshAll();
}

async function resetProcess() {
  await apiPost("/api/process/reset");
  refreshAll();
}

async function setLevel() {
  const level = Number(byId("forceLevel").value);
  await apiPost("/api/process/set", { level });
  refreshAll();
}

async function resetSystem() {
  await apiPost("/api/reset");
  refreshAll();
}

async function saveMonitorConfig() {
  const iface = byId("ifaceSelect").value;
  const mode = byId("monitorMode").value;

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

  if (status.agent_config) {
    byId("monitorMode").value = status.agent_config.mode || "MONITORING";
  }

  openModal("monitorModal");
  await scanInterfaces();

  if (status.agent_config) {
    populateIfaceSelect(
      status.agent?.available_ifaces || [],
      status.agent_config.iface || ""
    );
  }
}

function populateIfaceSelect(interfaces, selectedValue) {
  const select = byId("ifaceSelect");
  select.innerHTML = "";

  const allOption = document.createElement("option");
  allOption.value = "ALL";
  allOption.textContent = "ALL";
  if (selectedValue === "ALL") {
    allOption.selected = true;
  }
  select.appendChild(allOption);

  if (!interfaces || interfaces.length === 0) {
    return;
  }

  interfaces.forEach(iface => {
    const option = document.createElement("option");
    option.value = iface;
    option.textContent = iface;
    if (iface === selectedValue) {
      option.selected = true;
    }
    select.appendChild(option);
  });
}

async function scanInterfaces() {
  const data = await apiGet("/api/agent/interfaces");

  if (!data.connected) {
    setText("monitorConfigStatus", "Agente desconectado.");
    populateIfaceSelect([], "");
    return;
  }

  populateIfaceSelect(data.interfaces || [], data.current || "");
  setText("monitorConfigStatus", `Interfaces encontradas: ${(data.interfaces || []).join(", ") || "-"}`);
}

function setupAgentDownloadButtons() {
  const buttons = document.querySelectorAll("[data-agent-platform]");
  buttons.forEach(btn => {
    btn.addEventListener("click", () => {
      window.location.href = "/api/agent/download";
      closeModal("agentDownloadModal");
    });
  });
}

window.addEventListener("DOMContentLoaded", () => {
  byId("toggleServerBtn").addEventListener("click", toggleServer);
  byId("toggleClientBtn").addEventListener("click", toggleClient);
  byId("resetSystemBtn").addEventListener("click", resetSystem);

  byId("sendReadBtn").addEventListener("click", sendRead);
  byId("sendWriteBtn").addEventListener("click", sendWrite);

  byId("togglePumpBtn").addEventListener("click", togglePump);
  byId("toggleValveBtn").addEventListener("click", toggleValve);
  byId("resetProcessBtn").addEventListener("click", resetProcess);
  byId("setLevelBtn").addEventListener("click", setLevel);

  byId("openMonitorConfigBtn").addEventListener("click", openMonitorConfig);
  byId("saveMonitorConfigBtn").addEventListener("click", saveMonitorConfig);
  byId("scanIfacesBtn").addEventListener("click", scanInterfaces);

  byId("openServerConfigBtn").addEventListener("click", () => openModal("serverModal"));
  byId("openClientConfigBtn").addEventListener("click", () => openModal("clientModal"));
  byId("openAgentDownloadBtn").addEventListener("click", () => openModal("agentDownloadModal"));

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

  setupAgentDownloadButtons();

  refreshAll();
  setInterval(refreshAll, 1000);
});