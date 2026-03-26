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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderList(containerId, items, formatter) {
  const container = byId(containerId);
  if (!container) return;

  container.innerHTML = "";

  if (!items || items.length === 0) {
    container.innerHTML = `<div class="log-item">No data available.</div>`;
    return;
  }

  [...items].reverse().forEach((item) => {
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

function openWindow(id) {
  const el = byId(id);
  if (!el) return;
  el.classList.remove("hidden");
  bringWindowToFront(el);
}

function closeWindow(id) {
  const el = byId(id);
  if (!el) return;
  el.classList.add("hidden");
}

let floatingZ = 1300;

function bringWindowToFront(el) {
  floatingZ += 1;
  el.style.zIndex = String(floatingZ);
}

function makeWindowDraggable(windowEl) {
  if (!windowEl) return;

  const head = windowEl.querySelector(".window-head");
  if (!head) return;

  let dragging = false;
  let offsetX = 0;
  let offsetY = 0;

  const startDrag = (clientX, clientY) => {
    const rect = windowEl.getBoundingClientRect();
    dragging = true;
    offsetX = clientX - rect.left;
    offsetY = clientY - rect.top;
    bringWindowToFront(windowEl);
  };

  const onMove = (clientX, clientY) => {
    if (!dragging) return;

    const maxLeft = Math.max(0, window.innerWidth - windowEl.offsetWidth);
    const maxTop = Math.max(0, window.innerHeight - windowEl.offsetHeight);

    let left = clientX - offsetX;
    let top = clientY - offsetY;

    left = Math.max(0, Math.min(left, maxLeft));
    top = Math.max(0, Math.min(top, maxTop));

    windowEl.style.left = `${left}px`;
    windowEl.style.top = `${top}px`;
  };

  const stopDrag = () => {
    dragging = false;
  };

  head.addEventListener("mousedown", (e) => {
    if (e.target.closest("button")) return;
    startDrag(e.clientX, e.clientY);
    e.preventDefault();
  });

  document.addEventListener("mousemove", (e) => {
    onMove(e.clientX, e.clientY);
  });

  document.addEventListener("mouseup", stopDrag);

  head.addEventListener(
    "touchstart",
    (e) => {
      if (e.target.closest("button")) return;
      const touch = e.touches[0];
      if (!touch) return;
      startDrag(touch.clientX, touch.clientY);
    },
    { passive: true }
  );

  document.addEventListener(
    "touchmove",
    (e) => {
      const touch = e.touches[0];
      if (!touch) return;
      onMove(touch.clientX, touch.clientY);
    },
    { passive: true }
  );

  document.addEventListener("touchend", stopDrag);

  windowEl.addEventListener("mousedown", () => bringWindowToFront(windowEl));
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

  (interfaces || []).forEach((iface) => {
    const option = document.createElement("option");
    option.value = iface;
    option.textContent = iface;
    if (iface === selectedValue) {
      option.selected = true;
    }
    select.appendChild(option);
  });
}

function formatAgentStatus(data) {
  const connected = !!data.agent?.connected;
  const iface = data.agent_config?.iface || data.agent?.iface || "-";
  const mode = data.agent_config?.mode || data.agent?.mode || "-";

  return [
    `STATUS: ${connected ? "CONNECTED" : "DISCONNECTED"}`,
    `INTERFACE: ${iface}`,
    `MODE: ${mode}`
  ].join("\n");
}

function formatServerStatus(server) {
  return [
    `STATUS: ${server?.running ? "RUNNING" : "STOPPED"}`,
    `HOST: ${server?.host || "-"}`,
    `PORT: ${server?.port || "-"}`
  ].join("\n");
}

function formatClientStatus(client) {
  return [
    `STATUS: ${client?.running ? "RUNNING" : "STOPPED"}`,
    `HOST: ${client?.host || "-"}`,
    `PORT: ${client?.port || "-"}`,
    `POLL: ${client?.poll_interval ?? "-"}s`,
    `START: ${client?.poll_start ?? "-"}`,
    `QTY: ${client?.poll_quantity ?? "-"}`
  ].join("\n");
}

function formatPolling(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${Number(value).toFixed(1)} s`;
}

function formatFunctions(functionsSeen) {
  if (!Array.isArray(functionsSeen) || functionsSeen.length === 0) {
    return `<div class="event-value">-</div>`;
  }

  return `
    <div class="fc-list">
      ${functionsSeen
        .slice()
        .sort((a, b) => a - b)
        .map((fc) => `<span class="fc-badge">FC${escapeHtml(fc)}</span>`)
        .join("")}
    </div>
  `;
}

function hasRealCommunication(summary, events) {
  if (!summary || !summary.detected) return false;
  const stateLabel = String(summary.state || "").toLowerCase();
  if (stateLabel === "inactive") return false;
  return Array.isArray(events) && events.length > 0;
}

function renderEventsPanel(summary, events = []) {
  const el = byId("eventsPanel");
  if (!el) return;

  if (!hasRealCommunication(summary, events)) {
    el.className = "event-summary empty";
    el.innerHTML = `
      <div class="event-empty-title">No communication identified</div>
      <div class="event-empty-subtitle">Waiting for Modbus/TCP traffic...</div>
    `;
    return;
  }

  const writesDetected = !!summary.writes_detected;
  const stateLabel = summary.state || "Active";
  const stateClass = stateLabel.toLowerCase() === "inactive" ? "inactive" : "";

  el.className = "event-summary";
  el.innerHTML = `
    <div class="event-title-row">
      <div class="event-title">Communication detected</div>
      <div class="event-state ${stateClass}">${escapeHtml(stateLabel)}</div>
    </div>

    <div class="event-grid">
      <div class="event-item">
        <div class="event-label">Protocol</div>
        <div class="event-value">Modbus/TCP</div>
      </div>

      <div class="event-item">
        <div class="event-label">Interface</div>
        <div class="event-value soft">${escapeHtml(summary.interface || "-")}</div>
      </div>

      <div class="event-item">
        <div class="event-label">Port</div>
        <div class="event-value">${escapeHtml(summary.port ?? "-")}</div>
      </div>

      <div class="event-item">
        <div class="event-label">Client</div>
        <div class="event-value soft">${escapeHtml(stripPort(summary.client_ip || "-"))}</div>
      </div>

      <div class="event-item">
        <div class="event-label">Server</div>
        <div class="event-value soft">${escapeHtml(stripPort(summary.server_ip || "-"))}</div>
      </div>

      <div class="event-item">
        <div class="event-label">Average polling</div>
        <div class="event-value">${escapeHtml(formatPolling(summary.avg_polling_s))}</div>
      </div>

      <div class="event-item">
        <div class="event-label">Writes detected</div>
        <div class="event-value ${writesDetected ? "write-yes" : "write-no"}">
          ${writesDetected ? "Yes" : "No"}
        </div>
      </div>

      <div class="event-item wide">
        <div class="event-label">Observed functions</div>
        ${formatFunctions(summary.functions_seen)}
      </div>
    </div>
  `;
}

function stripPort(value) {
  if (!value) return "-";
  const str = String(value);
  const idx = str.lastIndexOf(":");
  if (idx > 0) return str.slice(0, idx);
  return str;
}

function buildReadableSnapshot(snapshot) {
  if (!snapshot || Object.keys(snapshot).length === 0) {
    return "No IDS data available yet.";
  }

  const overview = snapshot.traffic_overview || {};
  const functionCodes =
    Array.isArray(snapshot.function_codes_seen) && snapshot.function_codes_seen.length
      ? snapshot.function_codes_seen.join(", ")
      : "-";

  const readPatterns =
    Array.isArray(snapshot.read_patterns) && snapshot.read_patterns.length
      ? snapshot.read_patterns
          .map((p) => {
            const avg = p.avg_period == null ? "-" : `${Number(p.avg_period).toFixed(3)}s`;
            return `${p.server} | start=${p.start} qty=${p.quantity} | count=${p.count} | avg=${avg}`;
          })
          .join("\n")
      : "-";

  const writeRegisters =
    Array.isArray(snapshot.write_registers) && snapshot.write_registers.length
      ? snapshot.write_registers
          .map(
            (w) =>
              `reg=${w.register} | count=${w.count} | last=${w.last_value} | seen=[${(w.values_seen || []).join(", ")}]`
          )
          .join("\n")
      : "-";

  return [
    `Agent ID: ${snapshot.agent_id || "-"}`,
    `Host: ${snapshot.hostname || "-"}`,
    `Interface: ${snapshot.iface || "-"}`,
    `Mode: ${snapshot.mode || "-"}`,
    ``,
    `Traffic Overview`,
    `Clients Identified: ${overview.clients_identified ?? 0}`,
    `Servers Identified: ${overview.servers_identified ?? 0}`,
    `Function Codes Identified: ${
      Array.isArray(overview.function_codes_identified) && overview.function_codes_identified.length
        ? overview.function_codes_identified.join(", ")
        : functionCodes
    }`,
    `Read Patterns Identified: ${overview.read_pattern_count ?? 0}`,
    `Write Registers Identified: ${overview.write_register_count ?? 0}`,
    ``,
    `Read Patterns`,
    `${readPatterns}`,
    ``,
    `Write Activity`,
    `${writeRegisters}`,
  ].join("\n");
}

async function refreshStatus() {
  const data = await apiGet("/api/status");

  const agentConnected = !!data.agent?.connected;

  setText("agentStatus", formatAgentStatus(data));
  setText("serverStatus", formatServerStatus(data.server));
  setText("clientStatus", formatClientStatus(data.client));

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

  setText("monitorSnapshot", buildReadableSnapshot(data.monitor?.snapshot || {}));
}

function formatSummaryBlock(summary) {
  if (!summary) return "";
  return `<div><strong>Summary:</strong> ${escapeHtml(summary)}</div>`;
}

function formatEventDetails(event) {
  const type = event.type || "UNKNOWN";
  const src = `${event.src_ip || "-"}:${event.src_port || "-"}`;
  const dst = `${event.dst_ip || "-"}:${event.dst_port || "-"}`;
  const functionCode = event.function_code ?? "-";
  const txId = event.transaction_id ?? "-";
  const summary = event.summary || "";

  if (type === "READ_REQUEST") {
    return `
      ${formatSummaryBlock(summary)}
      <div><strong>Action:</strong> Read Holding Registers</div>
      <div><strong>Client:</strong> ${escapeHtml(event.client || src)}</div>
      <div><strong>Server:</strong> ${escapeHtml(event.server || dst)}</div>
      <div><strong>Function:</strong> FC${functionCode}</div>
      <div><strong>Start:</strong> ${event.start_addr ?? "-"}</div>
      <div><strong>Quantity:</strong> ${event.quantity ?? "-"}</div>
      <div><strong>Transaction ID:</strong> ${txId}</div>
    `;
  }

  if (type === "READ_RESPONSE") {
    return `
      ${formatSummaryBlock(summary)}
      <div><strong>Action:</strong> Read Response</div>
      <div><strong>Server:</strong> ${escapeHtml(event.server || src)}</div>
      <div><strong>Client:</strong> ${escapeHtml(event.client || dst)}</div>
      <div><strong>Function:</strong> FC${functionCode}</div>
      <div><strong>Values:</strong> ${escapeHtml(JSON.stringify(event.register_values || []))}</div>
      <div><strong>RTT:</strong> ${event.rtt ?? "-"} s</div>
      <div><strong>Transaction ID:</strong> ${txId}</div>
    `;
  }

  if (type === "WRITE_REQUEST") {
    return `
      ${formatSummaryBlock(summary)}
      <div><strong>Action:</strong> Write Single Register</div>
      <div><strong>Client:</strong> ${escapeHtml(event.client || src)}</div>
      <div><strong>Server:</strong> ${escapeHtml(event.server || dst)}</div>
      <div><strong>Function:</strong> FC${functionCode}</div>
      <div><strong>Register:</strong> ${event.register ?? "-"}</div>
      <div><strong>Value:</strong> ${event.value ?? "-"}</div>
      <div><strong>Transaction ID:</strong> ${txId}</div>
    `;
  }

  if (type === "WRITE_RESPONSE") {
    return `
      ${formatSummaryBlock(summary)}
      <div><strong>Action:</strong> Write Response</div>
      <div><strong>Server:</strong> ${escapeHtml(event.server || src)}</div>
      <div><strong>Client:</strong> ${escapeHtml(event.client || dst)}</div>
      <div><strong>Function:</strong> FC${functionCode}</div>
      <div><strong>Register:</strong> ${event.register ?? "-"}</div>
      <div><strong>Value:</strong> ${event.value ?? "-"}</div>
      <div><strong>RTT:</strong> ${event.rtt ?? "-"} s</div>
      <div><strong>Transaction ID:</strong> ${txId}</div>
    `;
  }

  return `
    ${formatSummaryBlock(summary)}
    <div><strong>Source:</strong> ${escapeHtml(src)}</div>
    <div><strong>Destination:</strong> ${escapeHtml(dst)}</div>
    <div><strong>Function:</strong> FC${functionCode}</div>
    <div><strong>Transaction ID:</strong> ${txId}</div>
  `;
}

function formatEventCard(event) {
  const type = event.type || "UNKNOWN";
  return `
    <div>
      <strong>${escapeHtml(type)}</strong><br>
      ${formatEventDetails(event)}
    </div>
  `;
}

function formatAlertCard(alert) {
  const severity = alert.severity || "INFO";
  const summary = alert.summary || `${alert.event_type || "UNKNOWN"} from ${alert.src || "-"} to ${alert.dst || "-"}`;
  const reasons = Array.isArray(alert.reasons) ? alert.reasons : [];

  return `
    <div class="alert-${escapeHtml(severity)}">
      <strong>${escapeHtml(severity)}</strong><br>
      ${escapeHtml(summary)}
      ${reasons.length ? `<br><span>${escapeHtml(reasons.join(" | "))}</span>` : ""}
    </div>
  `;
}

function simplifyLogLine(log) {
  const line = String(log || "").trim();
  if (!line) return "-";

  if (line.startsWith("Alert: ")) return line;
  if (line.startsWith("Agent connected")) return line;
  if (line.startsWith("Monitor configuration updated")) return line;
  if (line.startsWith("Modbus server")) return line;
  if (line.startsWith("Modbus client")) return line;
  if (line.startsWith("FC")) return line;
  if (line.startsWith("Modbus event detected")) return line;

  return line;
}

async function refreshEvents() {
  const data = await apiGet("/api/events");

  renderEventsPanel(data.modbus_summary, data.events);
  renderList("alertsPanel", data.alerts, (a) => formatAlertCard(a));
  renderList("logsPanel", data.logs, (log) => escapeHtml(simplifyLogLine(log)));
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
    setText("serverStatus", "Failed to start server.");
  }
}

async function stopServer() {
  const result = await apiPost("/api/agent/server/stop");
  if (!result.ok) {
    setText("serverStatus", "Failed to stop server.");
  }
}

async function toggleServer() {
  const status = await apiGet("/api/status");
  if (!status.agent?.connected) {
    setText("serverStatus", "Agent disconnected.");
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
    poll_quantity,
  });

  if (!result.ok) {
    setText("clientStatus", "Failed to start client.");
  }
}

async function stopClient() {
  const result = await apiPost("/api/agent/client/stop");
  if (!result.ok) {
    setText("clientStatus", "Failed to stop client.");
  }
}

async function toggleClient() {
  const status = await apiGet("/api/status");
  if (!status.agent?.connected) {
    setText("clientStatus", "Agent disconnected.");
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
    setText("monitorConfigStatus", "Configuration saved successfully.");
    closeModal("monitorModal");
    await refreshAll();
  } else {
    setText("monitorConfigStatus", `Failed to save: ${result.error || "unknown error"}`);
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
    setText("monitorConfigStatus", "Agent disconnected.");
    populateIfaceSelect([], "");
    return;
  }

  populateIfaceSelect(data.interfaces || [], data.current || "ALL");
  setText("monitorConfigStatus", `Interfaces found: ${(data.interfaces || []).join(", ") || "-"}`);
}

async function openAgentDownloadModal() {
  openModal("agentDownloadModal");
  setText("agentSessionIdValue", "loading...");

  try {
    const status = await apiGet("/api/status");
    setText("agentSessionIdValue", status.session_id || "-");
  } catch (_err) {
    setText("agentSessionIdValue", "unavailable");
  }
}

function disableLegacySections() {
  setDisabled("sendReadBtn", true);
  setDisabled("sendWriteBtn", true);
  setText("actionResult", "Local READ/WRITE actions are disabled in this version.");
}

function initFloatingWindows() {
  byId("openIdsWindowBtn")?.addEventListener("click", () => openWindow("idsWindow"));
  byId("openLogsWindowBtn")?.addEventListener("click", () => openWindow("logsWindow"));
  byId("openActionsWindowBtn")?.addEventListener("click", () => openWindow("actionsWindow"));

  document.querySelectorAll("[data-close-window]").forEach((btn) => {
    btn.addEventListener("click", () => closeWindow(btn.dataset.closeWindow));
  });

  ["idsWindow", "logsWindow", "actionsWindow"].forEach((id, index) => {
    const el = byId(id);
    if (!el) return;

    el.style.left = `${120 + index * 30}px`;
    el.style.top = `${120 + index * 30}px`;

    makeWindowDraggable(el);
  });
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
  byId("openAgentDownloadBtn")?.addEventListener("click", openAgentDownloadModal);

  document.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => closeModal(btn.dataset.close));
  });

  document.querySelectorAll(".modal").forEach((modal) => {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) {
        modal.classList.add("hidden");
      }
    });
  });

  initFloatingWindows();
  disableLegacySections();
  refreshAll();
  setInterval(refreshAll, 1000);
});
