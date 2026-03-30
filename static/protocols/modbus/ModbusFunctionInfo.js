(function initModbusFunctionInfo(global) {
  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function renderCatalog(container, functions) {
    const groups = {};
    for (const item of functions || []) {
      const key = item.category || "Other";
      if (!groups[key]) groups[key] = [];
      groups[key].push(item);
    }

    const groupHtml = Object.entries(groups)
      .map(([category, items]) => {
        const cards = items
          .map(
            (item) => `
              <article class="modbus-fc-card">
                <div class="modbus-fc-top">
                  <span class="modbus-fc-code">FC ${esc(item.code_label || item.code)}</span>
                  <span class="modbus-fc-target">${esc(item.acts_on || "-")}</span>
                </div>
                <div class="modbus-fc-name">${esc(item.name)}</div>
                <div class="modbus-fc-desc">${esc(item.description || "")}</div>
                <div class="modbus-fc-note">${esc(item.support_note || "")}</div>
              </article>
            `
          )
          .join("");

        return `
          <section class="modbus-fc-group">
            <h4>${esc(category)}</h4>
            <div class="modbus-fc-grid">${cards}</div>
          </section>
        `;
      })
      .join("");

    container.innerHTML = groupHtml || '<div class="status slim">No Modbus functions available.</div>';
  }

  global.OTLabModbusFunctionInfo = {
    renderCatalog,
  };
})(window);
