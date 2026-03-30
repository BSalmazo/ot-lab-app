(function initModbusFunctionInfo(global) {
  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function shortLabel(label) {
    const mapped = {
      Reading: "Read",
      Writing: "Write",
      Diagnostics: "Diag",
      Identification: "ID",
    };
    return mapped[label] || label;
  }

  function renderCatalog(container, functions, onExpandedChange) {
    const groups = {};
    for (const item of functions || []) {
      const key = item.category || "Other";
      if (!groups[key]) groups[key] = [];
      groups[key].push(item);
    }

    const categories = Object.keys(groups);
    let activeCategory = null;

    function renderPanel() {
      const bar = categories
        .map((category) => {
          const isActive = category === activeCategory;
          return `
            <button type="button" class="modbus-cat-btn ${isActive ? "active" : ""}" data-category="${esc(category)}">
              ${esc(shortLabel(category))}
            </button>
          `;
        })
        .join("");

      let panelHtml = '<div class="modbus-catalog-hint">Choose a category to expand details.</div>';
      if (activeCategory && groups[activeCategory]) {
        const cards = groups[activeCategory]
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

        panelHtml = `
          <div class="modbus-catalog-panel-head">
            <strong>${esc(activeCategory)}</strong>
            <span>${groups[activeCategory].length} functions</span>
          </div>
          <div class="modbus-fc-grid">${cards}</div>
        `;
      }

      container.innerHTML = `
        <div class="modbus-cat-bar">${bar}</div>
        <div class="modbus-catalog-panel">${panelHtml}</div>
      `;

      container.querySelectorAll("[data-category]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const next = btn.dataset.category;
          activeCategory = activeCategory === next ? null : next;
          if (typeof onExpandedChange === "function") {
            onExpandedChange(Boolean(activeCategory));
          }
          renderPanel();
        });
      });
    }

    if (!categories.length) {
      container.innerHTML = '<div class="status slim">No Modbus functions available.</div>';
      return;
    }

    if (typeof onExpandedChange === "function") {
      onExpandedChange(false);
    }
    renderPanel();
  }

  global.OTLabModbusFunctionInfo = {
    renderCatalog,
  };
})(window);
