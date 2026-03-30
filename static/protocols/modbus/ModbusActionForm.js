(function initModbusActionForm(global) {
  const defs = global.OTLabModbusDefinitions;
  const validators = global.OTLabModbusValidators;
  const builder = global.OTLabModbusBuilder;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function renderField(field, currentValue) {
    const value = currentValue ?? defs.getDefaultValue(field);

    if (field.type === "select") {
      const options = (field.options || [])
        .map((option) => {
          const selected = String(option.value) === String(value) ? "selected" : "";
          return `<option value="${esc(option.value)}" ${selected}>${esc(option.label)}</option>`;
        })
        .join("");
      return `
        <label class="action-form-item">
          <span>${esc(field.label)}</span>
          <select data-field-key="${esc(field.key)}">${options}</select>
        </label>
      `;
    }

    const type = field.type === "number" ? "number" : "text";
    const min = field.min !== null && field.min !== undefined ? `min="${field.min}"` : "";
    const max = field.max !== null && field.max !== undefined ? `max="${field.max}"` : "";

    return `
      <label class="action-form-item">
        <span>${esc(field.label)}</span>
        <input
          type="${type}"
          data-field-key="${esc(field.key)}"
          value="${esc(value)}"
          placeholder="${esc(field.placeholder || "")}" ${min} ${max}
        />
      </label>
    `;
  }

  function collectValues(root) {
    const values = {};
    root.querySelectorAll("[data-field-key]").forEach((element) => {
      values[element.dataset.fieldKey] = element.value;
    });
    return values;
  }

  function renderActionForm({ container, functionDef, values, onSubmit }) {
    const fieldsHtml = (functionDef.fields || [])
      .map((field) => renderField(field, values[field.key]))
      .join("");

    container.innerHTML = `
      <div class="action-host-row">
        <label class="action-form-item">
          <span>Target Host</span>
          <input type="text" data-host value="${esc(values.host || "127.0.0.1")}" placeholder="127.0.0.1" />
        </label>
        <label class="action-form-item">
          <span>Target Port</span>
          <input type="number" data-port min="1" max="65535" value="${esc(values.port || 5020)}" />
        </label>
      </div>
      <div class="action-form-grid">
        ${fieldsHtml}
      </div>
      <div class="action-form-actions">
        <button type="button" data-action-run>Execute Test</button>
      </div>
      <pre class="action-preview" data-action-preview></pre>
      <div class="status slim" data-action-result>-</div>
    `;

    const runBtn = container.querySelector("[data-action-run]");
    const resultEl = container.querySelector("[data-action-result]");
    const previewEl = container.querySelector("[data-action-preview]");

    function updatePreview() {
      const liveValues = {
        host: container.querySelector("[data-host]")?.value || "127.0.0.1",
        port: container.querySelector("[data-port]")?.value || 5020,
        ...collectValues(container),
      };

      const preview = builder.buildPreview(functionDef, liveValues);
      previewEl.textContent = JSON.stringify(preview, null, 2);
    }

    container.querySelectorAll("input,select").forEach((el) => {
      el.addEventListener("input", updatePreview);
    });

    runBtn?.addEventListener("click", async () => {
      const payloadValues = collectValues(container);
      const errors = validators.validateFields(functionDef.fields || [], payloadValues);
      if (errors.length > 0) {
        resultEl.textContent = errors.join(" | ");
        return;
      }

      const payload = {
        function_id: functionDef.id,
        host: container.querySelector("[data-host]")?.value || "127.0.0.1",
        port: Number(container.querySelector("[data-port]")?.value || 5020),
        values: payloadValues,
      };

      runBtn.disabled = true;
      resultEl.textContent = "Queueing request...";

      try {
        await onSubmit(payload, resultEl, previewEl);
      } finally {
        runBtn.disabled = false;
      }
    });

    updatePreview();
  }

  global.OTLabModbusActionForm = {
    renderActionForm,
  };
})(window);
