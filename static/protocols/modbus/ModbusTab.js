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

      <section class="actions-section">
        <div class="actions-section-head">
          <h3>Modbus Function Catalog</h3>
          <div class="actions-section-sub">Compact technical view by category</div>
        </div>
        <div class="modbus-catalog" data-modbus-catalog></div>
      </section>
    `;

    catalog.renderCatalog(container.querySelector("[data-modbus-catalog]"), functions);

    const formContainer = container.querySelector("[data-modbus-form]");
    const functionSelect = container.querySelector("[data-function-select]");

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
        return;
      }

      resultEl.textContent = `Queued ${data.function.code_label} ${data.function.name} (command ${data.queued_command_id})`;
      if (data.preview) {
        previewEl.textContent = JSON.stringify(data.preview, null, 2);
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
  }

  global.OTLabModbusTab = {
    mount,
  };
})(window);
