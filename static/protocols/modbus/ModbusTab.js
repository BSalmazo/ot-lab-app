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
        <div class="action-command-history" data-command-history></div>
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
    const historyContainer = container.querySelector("[data-command-history]");

    function formatStatus(status) {
      const normalized = String(status || "").toLowerCase();
      if (normalized === "queued") return "Pending";
      if (normalized === "sent") return "Sent";
      if (normalized === "done") return "Done";
      if (normalized === "error") return "Error";
      return "Pending";
    }

    function renderCommandHistory(commands) {
      const rows = (commands || []).slice(0, 6);
      if (!rows.length) {
        historyContainer.innerHTML = "";
        return;
      }

      historyContainer.innerHTML = `
        <div class="action-command-history-head">Recent Executions</div>
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

    async function submit(payload, resultEl, previewEl) {
      const response = await fetch("/api/actions/modbus/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(payload),
      });
      const data = await response.json();

      if (!data.ok) {
        resultEl.textContent = `Error: ${data.error || "failed to execute action"}`;
        renderCommandHistory(await fetchCommandHistory());
        return;
      }

      const commandId = data.command_id;
      resultEl.textContent = `Pending: FC ${data.function.code_label} ${data.function.name}`;
      if (data.preview) {
        previewEl.textContent = JSON.stringify(data.preview, null, 2);
      }

      const startedAt = Date.now();
      let finalStatus = null;

      while (Date.now() - startedAt < 20000) {
        const history = await fetchCommandHistory();
        renderCommandHistory(history);

        const current = history.find((item) => item.id === commandId);
        if (!current) {
          await new Promise((resolve) => setTimeout(resolve, 900));
          continue;
        }

        finalStatus = String(current.status || "").toLowerCase();
        if (finalStatus === "queued") {
          resultEl.textContent = "Pending: queued";
          await new Promise((resolve) => setTimeout(resolve, 900));
          continue;
        }
        if (finalStatus === "sent") {
          resultEl.textContent = "Pending: sent to agent";
          await new Promise((resolve) => setTimeout(resolve, 900));
          continue;
        }
        if (finalStatus === "done") {
          resultEl.textContent = `Done: ${current.message || "request executed"}`;
          break;
        }
        if (finalStatus === "error") {
          resultEl.textContent = `Error: ${current.message || "request failed"}`;
          break;
        }
      }

      if (!finalStatus || finalStatus === "queued" || finalStatus === "sent") {
        resultEl.textContent = "Pending: awaiting agent confirmation";
      }
    }

    function renderSelectedForm() {
      const selectedFunction = functions.find((item) => item.id === state.selectedId) || firstFunction;
      const values = state.valuesByFunction[selectedFunction.id] || {};

      form.renderActionForm({
        container: formContainer,
        functionDef: selectedFunction,
        values,
        onSubmit: async (payload, resultEl, previewEl) => {
          state.valuesByFunction[selectedFunction.id] = payload.values;
          await submit(payload, resultEl, previewEl);
        },
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
