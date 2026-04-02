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
    if (dragging && windowEl?.id) {
      persistWindowState(windowEl, windowEl.id);
    }
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
  const portMode = data.agent_config?.port_mode || data.agent?.port_mode || "-";
  const customPorts = data.agent_config?.custom_ports || data.agent?.custom_ports || [];
  const customPortText = Array.isArray(customPorts) && customPorts.length ? customPorts.join(",") : "-";

  return [
    `STATUS: ${connected ? "CONNECTED" : "DISCONNECTED"}`,
    `INTERFACE: ${iface}`,
    `MODE: ${mode}`,
    `PORT FILTER: ${portMode}`,
    `CUSTOM PORTS: ${customPortText}`
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

function formatFunctions(functionsSeen, exceptionFunctionsSeen = []) {
  if ((!Array.isArray(functionsSeen) || functionsSeen.length === 0) && (!Array.isArray(exceptionFunctionsSeen) || exceptionFunctionsSeen.length === 0)) {
    return `<div class="event-value">-</div>`;
  }

  const normal = new Set();
  const exceptions = new Set();
  for (const rawFc of functionsSeen || []) {
    const fc = Number(rawFc);
    if (!Number.isFinite(fc)) continue;
    if (fc > 127) {
      exceptions.add(fc & 0x7f);
    } else {
      normal.add(fc);
    }
  }

  const normalList = [...normal].sort((a, b) => a - b);
  for (const rawFc of exceptionFunctionsSeen || []) {
    const fc = Number(rawFc);
    if (!Number.isFinite(fc)) continue;
    if (fc > 0 && fc < 128) {
      exceptions.add(fc);
    }
  }
  const exceptionList = [...exceptions].sort((a, b) => a - b);

  const union = [...new Set([...normalList, ...exceptionList])].sort((a, b) => a - b);
  return `
    <div class="fc-list">
      ${union
        .map((fc) => {
          const hasException = exceptionList.includes(fc);
          if (!hasException) {
            return `<span class="fc-badge">FC${escapeHtml(fc)}</span>`;
          }
          const tip = exceptionInfoByFc.get(fc) || `Exception response detected for FC${fc} (raw frame may appear as FC${fc + 128}).`;
          return `<span class="fc-badge fc-badge-exception" data-fc-exception-tooltip="${escapeHtml(tip)}">FC${escapeHtml(fc)}</span>`;
        })
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
        ${formatFunctions(summary.functions_seen, summary.exception_functions_seen)}
      </div>
    </div>
  `;
  bindFcExceptionTooltips(el);
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
    `Port Filter: ${snapshot.port_mode || "-"}`,
    `Custom Ports: ${
      Array.isArray(snapshot.custom_ports) && snapshot.custom_ports.length
        ? snapshot.custom_ports.join(", ")
        : "-"
    }`,
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

  // Só atualizar inputs se NÃO estão em um modal aberto
  const serverModal = byId("serverModal");
  const clientModal = byId("clientModal");
  
  if (!serverModal || serverModal.classList.contains("hidden")) {
    if (byId("serverHost")) byId("serverHost").value = data.server?.host || "127.0.0.1";
    if (byId("serverPort")) byId("serverPort").value = data.server?.port || 5020;
  }

  if (!clientModal || clientModal.classList.contains("hidden")) {
    if (byId("clientHost")) byId("clientHost").value = data.client?.host || "127.0.0.1";
    if (byId("clientPort")) byId("clientPort").value = data.client?.port || 5020;
    if (byId("pollInterval")) byId("pollInterval").value = data.client?.poll_interval ?? 1.0;
    if (byId("pollStart")) byId("pollStart").value = data.client?.poll_start ?? 0;
    if (byId("pollQuantity")) byId("pollQuantity").value = data.client?.poll_quantity ?? 4;
  }

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

  if (type === "EXCEPTION_RESPONSE") {
    return `
      ${formatSummaryBlock(summary)}
      <div><strong>Action:</strong> Exception Response</div>
      <div><strong>Server:</strong> ${escapeHtml(event.server || src)}</div>
      <div><strong>Client:</strong> ${escapeHtml(event.client || dst)}</div>
      <div><strong>Function:</strong> FC${functionCode}</div>
      <div><strong>Exception Code:</strong> ${event.exception_code ?? "-"}</div>
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

const MODBUS_EXCEPTION_MAP = {
  1: "Illegal Function",
  2: "Illegal Data Address",
  3: "Illegal Data Value",
  4: "Server Device Failure",
  5: "Acknowledge",
  6: "Server Device Busy",
  8: "Memory Parity Error",
  10: "Gateway Path Unavailable",
  11: "Gateway Target Failed to Respond",
};

const MODBUS_FUNCTION_NAME_MAP = {
  1: "Read Coils",
  2: "Read Discrete Inputs",
  3: "Read Holding Registers",
  4: "Read Input Registers",
  5: "Write Single Coil",
  6: "Write Single Register",
  7: "Read Exception Status",
  8: "Diagnostics",
  11: "Get Comm Event Counter",
  12: "Get Comm Event Log",
  15: "Write Multiple Coils",
  16: "Write Multiple Registers",
  17: "Report Server ID",
  20: "Read File Record",
  21: "Write File Record",
  22: "Mask Write Register",
  23: "Read/Write Multiple Registers",
  24: "Read FIFO Queue",
  43: "Read Device Identification",
};

const WINDOW_STATE_KEY_PREFIX = "otlab_window_state_v1_";
const openAlertDetails = new Set();
let lastAlertsFingerprint = "";
let lastAlertsPlain = "";
let lastLogsFingerprint = "";
let lastConnectionsFingerprint = "";
let exceptionInfoByFc = new Map();
let fcTooltipEl = null;

function getAlertKey(alert) {
  return [
    alert.timestamp ?? "-",
    alert.severity ?? "-",
    alert.event_type ?? "-",
    alert.src ?? "-",
    alert.dst ?? "-",
    alert.summary ?? "-",
  ].join("|");
}

function parseExceptionCode(alert) {
  const reasons = Array.isArray(alert.reasons) ? alert.reasons : [];
  for (const reason of reasons) {
    const match = String(reason || "").match(/exception[_ ]code[=: ]+(\d+)/i);
    if (match) return Number(match[1]);
  }
  const summaryMatch = String(alert.summary || "").match(/exception(?:[_ ]response)?(?:[_ ]code)?[=: ]+(\d+)/i);
  if (summaryMatch) return Number(summaryMatch[1]);
  return null;
}

function getExceptionActionHint(exceptionCode) {
  if (exceptionCode === 1) return "Verify whether the PLC supports this function code.";
  if (exceptionCode === 2) return "Check address mapping and register boundaries.";
  if (exceptionCode === 3) return "Validate values/quantity against device limits.";
  if (exceptionCode === 4) return "Check PLC diagnostics for internal device failure.";
  if (exceptionCode === 6) return "Device busy. Retry with backoff or lower request burst.";
  return "Review request payload and PLC-specific Modbus support.";
}

function getFunctionName(functionCode, fallback = "") {
  const fc = Number(functionCode);
  if (Number.isFinite(fc) && MODBUS_FUNCTION_NAME_MAP[fc]) {
    return MODBUS_FUNCTION_NAME_MAP[fc];
  }
  return fallback || "Modbus Function";
}

function inferModbusContext(alert) {
  const summary = String(alert.summary || "");
  const functionMatch = summary.match(/\bFC(\d+)\b/i);
  const fc = functionMatch ? Number(functionMatch[1]) : null;
  const isException = String(alert.event_type || "").toUpperCase() === "EXCEPTION_RESPONSE" || /\bexception\b/i.test(summary);
  const exceptionCode = isException ? parseExceptionCode(alert) : null;
  return {
    fc,
    isException,
    exceptionCode,
    exceptionLabel:
      exceptionCode !== null && MODBUS_EXCEPTION_MAP[exceptionCode]
        ? `${exceptionCode} - ${MODBUS_EXCEPTION_MAP[exceptionCode]}`
        : exceptionCode,
  };
}

function inferOutcome(alert, context) {
  const eventType = String(alert.event_type || "").toUpperCase();
  if (context?.isException || eventType === "EXCEPTION_RESPONSE") {
    return { label: "Rejected", cls: "outcome-rejected" };
  }
  if (eventType === "WRITE_RESPONSE") {
    return { label: "Accepted", cls: "outcome-accepted" };
  }
  if (eventType === "UNKNOWN_REQUEST") {
    return { label: "Unknown", cls: "outcome-unknown" };
  }
  return { label: "Observed", cls: "outcome-observed" };
}

function formatAlertCard(alert) {
  const severity = alert.severity || "INFO";
  const summary = alert.summary || `${alert.event_type || "UNKNOWN"} from ${alert.src || "-"} to ${alert.dst || "-"}`;
  const reasons = Array.isArray(alert.reasons) ? alert.reasons : [];
  const eventType = String(alert.event_type || "UNKNOWN").replaceAll("_", " ");
  const severityClass = `alert-level-${escapeHtml(severity)}`;
  const severityBadgeClass = `sev-${escapeHtml(severity)}`;
  const context = inferModbusContext(alert);
  const outcome = inferOutcome(alert, context);
  const alertKey = getAlertKey(alert);
  const detailsOpen = openAlertDetails.has(alertKey) ? "open" : "";

  const fc = Number(alert.function_code ?? context.fc);
  const functionName = getFunctionName(fc, alert.function_label || "");
  const clientEndpoint = alert.client || alert.src || "-";
  const serverEndpoint = alert.server || alert.dst || "-";
  const register = alert.register ?? alert.address ?? alert.start_addr ?? "-";
  const quantity = alert.quantity ?? alert.read_quantity ?? null;
  const scalarValue = alert.value;
  const listValues = Array.isArray(alert.values) ? alert.values : [];
  const valueText = scalarValue != null ? String(scalarValue) : (listValues.length ? listValues.slice(0, 8).join(", ") : null);

  let readableTitle = summary;
  if (context.isException) {
    readableTitle = context.fc || Number.isFinite(fc)
      ? `FC${context.fc || fc} exception response`
      : "Server returned an exception response";
  } else if (Number.isFinite(fc)) {
    readableTitle = `FC${fc} detected (${functionName})`;
  }
  const readableReason = context.isException
    ? `Reason: ${context.exceptionLabel || "not identified"}`
    : reasons.join(" | ");
  let whatHappened = context.isException
    ? `Server rejected ${context.fc ? `FC${context.fc}` : "the request"}`
    : (eventType || "Modbus event");
  if (!context.isException && Number.isFinite(fc) && String(alert.event_type || "").toUpperCase() === "WRITE_REQUEST") {
    if (valueText !== null && register !== "-") {
      whatHappened = `SCADA/HMI sent value ${valueText} to PLC register ${register}`;
    } else if (register !== "-") {
      whatHappened = `SCADA/HMI issued FC${fc} write to PLC register ${register}`;
    } else {
      whatHappened = `SCADA/HMI issued FC${fc} write to PLC`;
    }
  }
  if (!context.isException && Number.isFinite(fc) && String(alert.event_type || "").toUpperCase() === "WRITE_RESPONSE") {
    if (valueText !== null && register !== "-") {
      whatHappened = `SCADA/HMI requested value ${valueText} for PLC register ${register}, and PLC accepted it`;
    } else if (register !== "-") {
      whatHappened = `SCADA/HMI write to PLC register ${register} was accepted by PLC`;
    } else {
      whatHappened = `SCADA/HMI write request was accepted by PLC`;
    }
  }
  const likelyCause = context.isException
    ? (context.exceptionLabel || "Request not accepted by device")
    : (reasons[0] || `${functionName} observed in live traffic`);
  const operatorAction = context.isException
    ? getExceptionActionHint(context.exceptionCode)
    : "Validate whether this behavior is expected for the process state.";

  const compactTitle = Number.isFinite(fc) ? `FC${fc} detected` : "Modbus event detected";
  const compactValue = valueText != null ? valueText : "-";

  return `
    <div class="alert-card ${severityClass}">
      <div class="alert-top">
        <span class="alert-severity ${severityBadgeClass}">${escapeHtml(severity)}</span>
        <span class="alert-outcome ${escapeHtml(outcome.cls)}">${escapeHtml(outcome.label)}</span>
        <span class="alert-event">${escapeHtml(eventType)}</span>
      </div>
      <div class="alert-summary">${escapeHtml(compactTitle)}</div>
      <div class="alert-srcdst">${escapeHtml(alert.src || "-")} → ${escapeHtml(alert.dst || "-")}</div>
      <div class="alert-brief-grid">
        <div class="alert-brief-row"><span class="alert-brief-k">Outcome</span><span class="alert-brief-v">${escapeHtml(outcome.label)}</span></div>
        <div class="alert-brief-row"><span class="alert-brief-k">What happened</span><span class="alert-brief-v">${escapeHtml(whatHappened)}</span></div>
        <div class="alert-brief-row"><span class="alert-brief-k">Value</span><span class="alert-brief-v">${escapeHtml(compactValue)}</span></div>
      </div>
      <details class="alert-detail" data-alert-key="${escapeHtml(alertKey)}" ${detailsOpen}>
        <summary>Technical details</summary>
        <div class="alert-technical-line"><strong>Function:</strong> ${Number.isFinite(fc) ? `FC${escapeHtml(fc)} - ${escapeHtml(functionName)}` : "-"}</div>
        <div class="alert-technical-line"><strong>Client:</strong> ${escapeHtml(clientEndpoint)}</div>
        <div class="alert-technical-line"><strong>Server:</strong> ${escapeHtml(serverEndpoint)}</div>
        ${register !== "-" ? `<div class="alert-technical-line"><strong>Register:</strong> ${escapeHtml(register)}</div>` : ""}
        ${quantity != null ? `<div class="alert-technical-line"><strong>Quantity:</strong> ${escapeHtml(quantity)}</div>` : ""}
        ${valueText != null ? `<div class="alert-technical-line"><strong>Value:</strong> ${escapeHtml(valueText)}</div>` : ""}
        <div class="alert-technical-line"><strong>Likely cause:</strong> ${escapeHtml(likelyCause)}</div>
        <div class="alert-technical-line"><strong>Operator action:</strong> ${escapeHtml(operatorAction)}</div>
        ${readableReason ? `<div class="alert-technical-line"><strong>Reason:</strong> ${escapeHtml(readableReason)}</div>` : ""}
        <div class="alert-technical-line"><strong>Summary:</strong> ${escapeHtml(summary)}</div>
        ${context.exceptionLabel ? `<div class="alert-technical-line"><strong>Exception:</strong> ${escapeHtml(context.exceptionLabel)}</div>` : ""}
        ${reasons.length ? `<div class="alert-technical-line"><strong>Reasons:</strong> ${escapeHtml(reasons.join(" | "))}</div>` : ""}
        ${alert.unit_id != null ? `<div class="alert-technical-line"><strong>Unit ID:</strong> ${escapeHtml(alert.unit_id)}</div>` : ""}
        ${alert.transaction_id != null ? `<div class="alert-technical-line"><strong>Transaction ID:</strong> ${escapeHtml(alert.transaction_id)}</div>` : ""}
        ${alert.rtt != null ? `<div class="alert-technical-line"><strong>RTT:</strong> ${escapeHtml(alert.rtt)} s</div>` : ""}
      </details>
    </div>
  `;
}

function formatAlertPlain(alert) {
  const severity = alert.severity || "INFO";
  const context = inferModbusContext(alert);
  const outcome = inferOutcome(alert, context);
  const eventType = String(alert.event_type || "UNKNOWN").replaceAll("_", " ");
  const summary = alert.summary || "-";
  const src = alert.src || "-";
  const dst = alert.dst || "-";
  const reasons = Array.isArray(alert.reasons) ? alert.reasons.join(" | ") : "-";
  return `[${severity}] ${eventType} | Outcome: ${outcome.label}\n${summary}\n${src} -> ${dst}\nReasons: ${reasons}`;
}

function formatConnectionHistoryRow(row) {
  const stateBadge = row.active ? "conn-active" : "conn-inactive";
  const stateLabel = row.active ? "Active" : "Closed";
  const normalFcs = Array.isArray(row.functions_seen) ? row.functions_seen : [];
  const exceptionFcs = Array.isArray(row.exception_functions_seen) ? row.exception_functions_seen : [];
  const fcText = normalFcs.map((fc) => `FC${fc}`).join(", ") || "-";
  const excText = exceptionFcs.length ? ` | Exceptions: ${exceptionFcs.map((fc) => `FC${fc}`).join(", ")}` : "";
  const ageText = row.age_s == null ? "-" : `${Number(row.age_s).toFixed(1)}s ago`;
  const durText = row.duration_s == null ? "-" : `${Number(row.duration_s).toFixed(3)}s`;
  const portText = row.port == null ? "-" : row.port;
  const connId = row.connection_id ? String(row.connection_id).slice(0, 8) : "-";
  const instance = row.instance_id == null ? 1 : Number(row.instance_id);
  const reconnects = row.reconnect_count == null ? 0 : Number(row.reconnect_count);
  return `
    <div class="conn-row">
      <div class="conn-top">
        <span class="conn-state ${stateBadge}">${stateLabel}</span>
        <span class="conn-meta">${escapeHtml(row.protocol || "Modbus/TCP")} | iface=${escapeHtml(row.interface || "-")} | port=${escapeHtml(portText)}</span>
      </div>
      <div class="conn-path">${escapeHtml(row.client_ip || "-")} → ${escapeHtml(row.server_ip || "-")}</div>
      <div class="conn-fcs">${escapeHtml(fcText)}${escapeHtml(excText)}</div>
      <div class="conn-extra">id=${escapeHtml(connId)} | instance=${escapeHtml(instance)} | reconnects=${escapeHtml(reconnects)} | events=${escapeHtml(row.event_count || 0)} | duration=${escapeHtml(durText)} | last=${escapeHtml(ageText)}</div>
    </div>
  `;
}

function getAlertSemanticKey(alert) {
  return [
    String(alert.event_type || "").toUpperCase(),
    alert.function_code ?? "-",
    alert.src ?? "-",
    alert.dst ?? "-",
    alert.register ?? "-",
    alert.start_addr ?? "-",
    alert.quantity ?? "-",
    alert.value ?? "-",
    alert.exception_code ?? "-",
    alert.transaction_id ?? "-",
  ].join("|");
}

function dedupeAlerts(alerts) {
  const out = [];
  const seen = new Set();
  for (let i = 0; i < alerts.length; i += 1) {
    const alert = alerts[i];
    const key = getAlertSemanticKey(alert);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(alert);
  }
  return out;
}

function buildExceptionInfoByFc(alerts) {
  const map = new Map();
  for (let i = alerts.length - 1; i >= 0; i -= 1) {
    const alert = alerts[i];
    const ctx = inferModbusContext(alert);
    if (!ctx.isException || !ctx.fc) continue;
    if (map.has(ctx.fc)) continue;
    const reason = ctx.exceptionLabel || "Exception";
    map.set(ctx.fc, `FC${ctx.fc} exception: ${reason}. ${getExceptionActionHint(ctx.exceptionCode)}`);
  }
  return map;
}

function ensureFcTooltip() {
  if (fcTooltipEl) return fcTooltipEl;
  const el = document.createElement("div");
  el.id = "fcExceptionTooltip";
  el.className = "floating-tip hidden";
  document.body.appendChild(el);
  fcTooltipEl = el;
  return el;
}

function hideFcTooltip() {
  const el = ensureFcTooltip();
  el.classList.add("hidden");
}

function showFcTooltip(text, x, y) {
  const el = ensureFcTooltip();
  el.textContent = text || "";
  const left = Math.min(window.innerWidth - 320, Math.max(8, x + 12));
  const top = Math.min(window.innerHeight - 80, Math.max(8, y + 12));
  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
  el.classList.remove("hidden");
}

function bindFcExceptionTooltips(root) {
  if (!root) return;
  root.querySelectorAll("[data-fc-exception-tooltip]").forEach((node) => {
    node.addEventListener("mouseenter", (ev) => {
      showFcTooltip(node.getAttribute("data-fc-exception-tooltip") || "", ev.clientX || 20, ev.clientY || 20);
    });
    node.addEventListener("mousemove", (ev) => {
      showFcTooltip(node.getAttribute("data-fc-exception-tooltip") || "", ev.clientX || 20, ev.clientY || 20);
    });
    node.addEventListener("mouseleave", () => {
      hideFcTooltip();
    });
  });
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
  const rawAlerts = Array.isArray(data.alerts) ? data.alerts : [];
  const alerts = dedupeAlerts(rawAlerts);
  const logs = Array.isArray(data.logs) ? data.logs : [];
  const connections = Array.isArray(data.connection_history) ? data.connection_history : [];
  exceptionInfoByFc = buildExceptionInfoByFc(alerts);

  renderEventsPanel(data.modbus_summary, data.events);
  const alertsFingerprint = alerts
    .map((a) => `${a.timestamp}|${a.severity}|${a.event_type}|${a.src}|${a.dst}|${a.summary}`)
    .join("||");
  const alertsPlain = alerts.map((a) => formatAlertPlain(a)).join("\n\n");
  const logsFingerprint = logs.map((line) => simplifyLogLine(line)).join("\n");

  if (alertsFingerprint !== lastAlertsFingerprint) {
    renderList("alertsPanel", alerts, (a) => formatAlertCard(a));
    renderList("alertsWindowPanel", alerts, (a) => formatAlertCard(a));
    bindAlertDetails("alertsPanel");
    bindAlertDetails("alertsWindowPanel");
    lastAlertsFingerprint = alertsFingerprint;
  }

  if (alertsPlain !== lastAlertsPlain) {
    setText("alertsPlainPanel", alertsPlain);
    lastAlertsPlain = alertsPlain;
  }

  if (logsFingerprint !== lastLogsFingerprint) {
    renderList("logsPanel", logs, (log) => escapeHtml(simplifyLogLine(log)));
    lastLogsFingerprint = logsFingerprint;
  }

  const connectionsFingerprint = connections
    .map((c) => `${c.id}|${c.active}|${c.last_seen}|${(c.functions_seen || []).join(",")}|${(c.exception_functions_seen || []).join(",")}|${c.event_count}`)
    .join("||");
  if (connectionsFingerprint !== lastConnectionsFingerprint) {
    renderList("connectionsHistoryPanel", connections, (row) => formatConnectionHistoryRow(row));
    lastConnectionsFingerprint = connectionsFingerprint;
  }
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
  const port_mode = byId("portModeSelect")?.value || "MODBUS_PORTS";
  const custom_ports_raw = byId("customPortsInput")?.value || "";
  const custom_ports = custom_ports_raw
    .split(",")
    .map((v) => v.trim())
    .filter((v) => v.length > 0);

  const result = await apiPost("/api/agent/config", { iface, mode, port_mode, custom_ports });

  if (result.ok) {
    setText("monitorConfigStatus", "Configuration saved successfully.");
    closeModal("monitorModal");
    await refreshAll();
  } else {
    setText("monitorConfigStatus", `Failed to save: ${result.error || "unknown error"}`);
  }
}

async function saveServerConfig() {
  const host = byId("serverHost")?.value || "127.0.0.1";
  const port = byId("serverPort")?.value || "5020";

  const result = await apiPost("/api/agent/server/configure", { host, port });

  if (result.ok) {
    closeModal("serverModal");
    await refreshAll();
  } else {
    alert(`Failed to save server config: ${result.error || "unknown error"}`);
  }
}

async function saveClientConfig() {
  const host = byId("clientHost")?.value || "127.0.0.1";
  const port = byId("clientPort")?.value || "5020";
  const poll_interval = byId("pollInterval")?.value || "1.0";
  const poll_start = byId("pollStart")?.value || "0";
  const poll_quantity = byId("pollQuantity")?.value || "4";

  const result = await apiPost("/api/agent/client/configure", {
    host,
    port,
    poll_interval,
    poll_start,
    poll_quantity,
  });

  if (result.ok) {
    closeModal("clientModal");
    await refreshAll();
  } else {
    alert(`Failed to save client config: ${result.error || "unknown error"}`);
  }
}

async function openMonitorConfig() {
  const status = await apiGet("/api/status");

  if (byId("monitorMode")) {
    byId("monitorMode").value = status.agent_config?.mode || "MONITORING";
  }
  if (byId("portModeSelect")) {
    byId("portModeSelect").value = status.agent_config?.port_mode || "MODBUS_PORTS";
  }
  if (byId("customPortsInput")) {
    const ports = status.agent_config?.custom_ports || [];
    byId("customPortsInput").value = Array.isArray(ports) ? ports.join(",") : "";
  }
  updateCustomPortsVisibility();

  openModal("monitorModal");
  await scanInterfaces();

  populateIfaceSelect(
    status.agent?.available_ifaces || [],
    status.agent_config?.iface || "ALL"
  );
}

function updateCustomPortsVisibility() {
  const mode = byId("portModeSelect")?.value || "MODBUS_PORTS";
  const row = byId("customPortsRow");
  const input = byId("customPortsInput");
  if (!row) return;
  if (mode === "CUSTOM") {
    row.classList.remove("hidden");
    if (input) input.disabled = false;
  } else {
    row.classList.add("hidden");
    if (input) input.disabled = true;
  }
}

async function scanInterfaces() {
  const data = await apiGet("/api/agent/interfaces");

  if (!data.connected) {
    setText("monitorConfigStatus", "Agent disconnected.");
    populateIfaceSelect([], "");
    return;
  }

  populateIfaceSelect(data.interfaces || [], data.current || "ALL");
  const available = Array.isArray(data.interfaces) ? data.interfaces.length : 0;
  const monitored = Array.isArray(data.monitored_interfaces) ? data.monitored_interfaces.length : 0;
  const unmonitored = Array.isArray(data.unmonitored_interfaces) ? data.unmonitored_interfaces.length : 0;
  setText(
    "monitorConfigStatus",
    `Interfaces: available=${available}, monitored=${monitored}, not_monitored=${unmonitored}`
  );
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

  // Load and render releases
  await loadAndRenderReleases();
}

async function loadAndRenderReleases() {
  const container = document.getElementById("releasesContainer");
  if (!container) return;

  container.innerHTML = '<div class="loading-spinner">Loading releases from GitHub...</div>';

  try {
    const response = await apiGet("/api/releases/agent?refresh=1");

    if (!response.ok && response.releases && response.releases.length === 0) {
      // No GitHub releases, show local fallback
      container.innerHTML = '<div class="info-message">GitHub releases not available. Using local copies.</div>';
      document.querySelector(".download-fallback")?.classList.remove("hidden");
      return;
    }

    // Render releases
    const releases = response.releases || [];
    if (releases.length === 0) {
      container.innerHTML = '<div class="error-message">No releases available.</div>';
      document.querySelector(".download-fallback")?.classList.remove("hidden");
      return;
    }

    let html = "";
    for (const release of releases) {
      const releaseType = release.type === "development" 
        ? '<span class="badge-dev">DEV</span>' 
        : '<span class="badge-stable">STABLE</span>';
      
      const releaseDateRaw = release.updated_at || release.published_at;
      const publishDate = releaseDateRaw
        ? new Date(releaseDateRaw).toLocaleDateString()
        : "-";
      
      html += `
        <div class="release-card">
          <div class="release-header">
            <h4>${release.tag} ${releaseType}</h4>
            <div class="release-date">Updated: ${publishDate}</div>
          </div>
          <div class="release-downloads">
      `;

      const assets = release.assets || {};
      
      if (assets.windows) {
        html += `<a class="download-link" href="${getBundleDownloadUrl("windows")}" download>
          <span class="os-icon">🪟</span> Windows Bundle ZIP (agent + install)
        </a>`;
      }
      if (assets.macos) {
        html += `<a class="download-link" href="${getBundleDownloadUrl("macos")}" download>
          <span class="os-icon">🍎</span> macOS Bundle ZIP (agent + install)
        </a>`;
      }
      if (assets.linux) {
        html += `<a class="download-link" href="${getBundleDownloadUrl("linux")}" download>
          <span class="os-icon">🐧</span> Linux Bundle ZIP (agent + install)
        </a>`;
      }
      
      html += `
          </div>
        </div>
      `;
    }

    container.innerHTML = html;
    document.querySelector(".download-fallback")?.classList.add("hidden");

  } catch (err) {
    console.error("Error loading releases:", err);
    container.innerHTML = '<div class="error-message">Failed to load releases from GitHub.</div>';
    document.querySelector(".download-fallback")?.classList.remove("hidden");
  }
}

function formatFileSize(bytes) {
  if (!bytes) return "unknown";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function getBundleDownloadUrl(platform) {
  if (platform === "windows") return "/api/downloads/agent/windows";
  if (platform === "macos") return "/api/downloads/agent/mac";
  if (platform === "linux") return "/api/downloads/agent/linux";
  return "#";
}

function initFloatingWindows() {
  byId("openIdsWindowBtn")?.addEventListener("click", () => openWindow("idsWindow"));
  byId("openLogsWindowBtn")?.addEventListener("click", () => openWindow("logsWindow"));
  byId("openConnectionsWindowBtn")?.addEventListener("click", () => openWindow("connectionsWindow"));
  byId("openActionsWindowBtn")?.addEventListener("click", () => openWindow("actionsWindow"));
  byId("openAlertsWindowBtn")?.addEventListener("click", () => openWindow("alertsWindow"));

  document.querySelectorAll("[data-close-window]").forEach((btn) => {
    btn.addEventListener("click", () => closeWindow(btn.dataset.closeWindow));
  });

  ["idsWindow", "logsWindow", "connectionsWindow", "actionsWindow", "actionsHistoryWindow", "actionsPreviewWindow", "alertsWindow"].forEach((id, index) => {
    const el = byId(id);
    if (!el) return;

    applyWindowState(el, id, index);
    makeWindowDraggable(el);
    observeWindowResize(el, id);
  });

  byId("copyAlertsBtn")?.addEventListener("click", async () => {
    const plain = byId("alertsPlainPanel")?.textContent || "";
    if (!plain.trim()) return;
    try {
      await navigator.clipboard.writeText(plain);
    } catch (_err) {
      console.error("Failed to copy alerts");
    }
  });

  document.addEventListener("mouseleave", () => hideFcTooltip());
  window.addEventListener("blur", () => hideFcTooltip());
}

function bindAlertDetails(containerId) {
  const container = byId(containerId);
  if (!container) return;
  container.querySelectorAll(".alert-detail[data-alert-key]").forEach((detailsEl) => {
    const key = detailsEl.dataset.alertKey;
    if (!key) return;
    detailsEl.addEventListener("toggle", () => {
      if (detailsEl.open) {
        openAlertDetails.add(key);
      } else {
        openAlertDetails.delete(key);
      }
    });
  });
}

function windowStateStorageKey(id) {
  return `${WINDOW_STATE_KEY_PREFIX}${id}`;
}

function applyWindowState(el, id, index) {
  const fallbackLeft = 120 + index * 30;
  const fallbackTop = 120 + index * 30;

  try {
    const raw = localStorage.getItem(windowStateStorageKey(id));
    if (!raw) {
      el.style.left = `${fallbackLeft}px`;
      el.style.top = `${fallbackTop}px`;
      return;
    }

    const state = JSON.parse(raw);
    if (Number.isFinite(state.left)) el.style.left = `${state.left}px`;
    if (Number.isFinite(state.top)) el.style.top = `${state.top}px`;
    if (Number.isFinite(state.width)) el.style.width = `${state.width}px`;
    if (Number.isFinite(state.height)) el.style.height = `${state.height}px`;
  } catch (_err) {
    el.style.left = `${fallbackLeft}px`;
    el.style.top = `${fallbackTop}px`;
  }
}

function persistWindowState(el, id) {
  if (!el) return;
  const left = parseFloat(el.style.left || "0");
  const top = parseFloat(el.style.top || "0");
  const width = el.offsetWidth;
  const height = el.offsetHeight;
  try {
    localStorage.setItem(
      windowStateStorageKey(id),
      JSON.stringify({ left, top, width, height })
    );
  } catch (_err) {
    // Ignore storage failures.
  }
}

function observeWindowResize(el, id) {
  if (typeof ResizeObserver === "undefined") return;
  let timeout = null;
  const observer = new ResizeObserver(() => {
    if (timeout) clearTimeout(timeout);
    timeout = setTimeout(() => persistWindowState(el, id), 120);
  });
  observer.observe(el);
}

window.addEventListener("DOMContentLoaded", () => {
  byId("toggleServerBtn")?.addEventListener("click", toggleServer);
  byId("toggleClientBtn")?.addEventListener("click", toggleClient);
  byId("resetSystemBtn")?.addEventListener("click", resetSystem);

  byId("openMonitorConfigBtn")?.addEventListener("click", openMonitorConfig);
  byId("saveMonitorConfigBtn")?.addEventListener("click", saveMonitorConfig);
  byId("scanIfacesBtn")?.addEventListener("click", scanInterfaces);
  byId("portModeSelect")?.addEventListener("change", updateCustomPortsVisibility);

  byId("saveServerBtn")?.addEventListener("click", saveServerConfig);
  byId("closeServerBtn")?.addEventListener("click", () => closeModal("serverModal"));

  byId("saveClientBtn")?.addEventListener("click", saveClientConfig);
  byId("closeClientBtn")?.addEventListener("click", () => closeModal("clientModal"));

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
  if (window.OTLabActions?.mountActionsWindow) {
    window.OTLabActions.mountActionsWindow("actionsWindowBody").catch((err) => {
      console.error(err);
      setText("actionsWindowBody", "Failed to load Actions window.");
    });
  }
  refreshAll();
  setInterval(refreshAll, 1000);
});
