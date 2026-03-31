(function initModbusTab(global) {
  const definitions = global.OTLabModbusDefinitions;
  const catalog = global.OTLabModbusFunctionInfo;
  const form = global.OTLabModbusActionForm;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function mount(container) {
    const protocol = await definitions.fetchProtocolDefinition();
    const functions = Array.isArray(protocol.functions) ? protocol.functions : [];

    if (!functions.length) {
      container.innerHTML = '<div class="status slim">Failed to load Modbus function catalog.</div>';
      return;
    }

    const firstFunction = functions[0];
    const state = {
      selectedId: firstFunction.id,
      valuesByFunction: {},
    };

    container.innerHTML = `
      <section class="actions-section">
        <div class="actions-section-head">
          <h3>Modbus Function Catalog</h3>
          <div class="actions-section-sub">Category shortcuts (expand on click)</div>
        </div>
        <div class="modbus-catalog" data-modbus-catalog></div>
      </section>

      <section class="actions-section">
        <div class="actions-section-head">
          <h3>Interactive Test</h3>
          <div class="actions-section-sub">Select a function and queue a request to the agent</div>
        </div>
        <label class="action-form-item">
          <span>Function</span>
          <select data-function-select>
            ${functions
              .map(
                (item) =>
                  `<option value="${esc(item.id)}">FC ${esc(item.code_label || item.code)} - ${esc(item.name)}</option>`
              )
              .join("")}
          </select>
        </label>

        <div class="modbus-action-form" data-modbus-form></div>
      </section>
    `;

    const popupBody = container.closest(".actions-window-body");
    catalog.renderCatalog(
      container.querySelector("[data-modbus-catalog]"),
      functions,
      (expanded) => {
        if (!popupBody) return;
        popupBody.classList.toggle("catalog-expanded", Boolean(expanded));
        if (!expanded) {
          popupBody.scrollTop = 0;
        }
      }
    );

    const formContainer = container.querySelector("[data-modbus-form]");
    const functionSelect = container.querySelector("[data-function-select]");
    const historyContainer = document.getElementById("actionsHistoryPanel");
    const previewContainer = document.getElementById("actionsPreviewPanel");
    const openHistoryWindow = () => {
      const win = document.getElementById("actionsHistoryWindow");
      if (win) win.classList.remove("hidden");
    };
    const openPreviewWindow = (preview) => {
      const win = document.getElementById("actionsPreviewWindow");
      if (previewContainer) {
        previewContainer.textContent = JSON.stringify(preview || {}, null, 2);
      }
      if (win) win.classList.remove("hidden");
    };

    function formatStatus(status) {
      const normalized = String(status || "").toLowerCase();
      if (normalized === "queued") return "Pending";
      if (normalized === "sent") return "Sent";
      if (normalized === "done") return "Done";
      if (normalized === "error") return "Error";
      return "Pending";
    }

    function renderCommandHistory(commands) {
      const rows = (commands || []).slice(0, 40);
      if (!historyContainer) return;
      if (!rows.length) {
        historyContainer.innerHTML = `<div class="log-item">No actions executed yet.</div>`;
        return;
      }

      historyContainer.innerHTML = `
        <div class="action-command-history-list">
          ${rows
            .map((row) => {
              const badgeClass = `status-${String(row.status || "queued").toLowerCase()}`;
              return `
                <div class="action-command-row">
                  <span class="action-command-name">FC ${esc(row.code_label || "-")} ${esc(row.function_name || row.function_id || "Modbus")}</span>
                  <span class="action-command-badge ${badgeClass}">${esc(formatStatus(row.status))}</span>
                  <span class="action-command-msg">${esc(row.message || "-")}</span>
                </div>
              `;
            })
            .join("")}
        </div>
      `;
    }

    async function fetchCommandHistory() {
      const response = await fetch("/api/actions/modbus/commands", {
        credentials: "same-origin",
      });
      const data = await response.json();
      if (!data.ok) return [];
      return Array.isArray(data.commands) ? data.commands : [];
    }

    async function submit(payload, statusEl, preview) {
      const response = await fetch("/api/actions/modbus/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(payload),
      });
      const data = await response.json();

      if (!data.ok) {
        statusEl.textContent = `Error: ${data.error || "failed to execute action"}`;
        renderCommandHistory(await fetchCommandHistory());
        return;
      }

      const commandId = data.command_id;
      statusEl.textContent = `Pending`;
      if (previewContainer && (data.preview || preview)) {
        previewContainer.textContent = JSON.stringify(data.preview || preview, null, 2);
      }

      const startedAt = Date.now();
      let finalStatus = null;
      const pollDelayMs = 300;

      while (Date.now() - startedAt < 20000) {
        const history = await fetchCommandHistory();
        renderCommandHistory(history);

        const current = history.find((item) => item.id === commandId);
        if (!current) {
          await new Promise((resolve) => setTimeout(resolve, pollDelayMs));
          continue;
        }

        finalStatus = String(current.status || "").toLowerCase();
        if (finalStatus === "queued") {
          statusEl.textContent = "Pending";
          await new Promise((resolve) => setTimeout(resolve, pollDelayMs));
          continue;
        }
        if (finalStatus === "sent") {
          statusEl.textContent = "Sent";
          await new Promise((resolve) => setTimeout(resolve, pollDelayMs));
          continue;
        }
        if (finalStatus === "done") {
          statusEl.textContent = "Done";
          break;
        }
        if (finalStatus === "error") {
          statusEl.textContent = "Error";
          break;
        }
      }

      if (!finalStatus || finalStatus === "queued" || finalStatus === "sent") {
        statusEl.textContent = "Pending";
      }
    }

    function renderSelectedForm() {
      const selectedFunction = functions.find((item) => item.id === state.selectedId) || firstFunction;
      const values = state.valuesByFunction[selectedFunction.id] || {};

      form.renderActionForm({
        container: formContainer,
        functionDef: selectedFunction,
        values,
        onSubmit: async (payload, statusEl, preview) => {
          state.valuesByFunction[selectedFunction.id] = payload.values;
          await submit(payload, statusEl, preview);
        },
        onOpenHistory: openHistoryWindow,
        onOpenPreview: openPreviewWindow,
      });
    }

    functionSelect.addEventListener("change", () => {
      state.selectedId = functionSelect.value;
      renderSelectedForm();
    });

    renderSelectedForm();
    fetchCommandHistory().then(renderCommandHistory).catch(() => {});
  }

  global.OTLabModbusTab = {
    mount,
  };
})(window);
