(function initActionsWindow(global) {
  const modbusTab = global.OTLabModbusTab;

  async function mountActionsWindow(containerId) {
    const root = document.getElementById(containerId);
    if (!root) return;

    root.innerHTML = `
      <div class="actions-shell">
        <div class="actions-tabs" role="tablist" aria-label="Protocols">
          <button class="actions-tab active" data-protocol-tab="modbus" type="button">Modbus</button>
        </div>
        <div class="actions-tab-panel" data-protocol-panel="modbus"></div>
      </div>
    `;

    const modbusPanel = root.querySelector("[data-protocol-panel='modbus']");
    await modbusTab.mount(modbusPanel);
  }

  global.OTLabActions = {
    mountActionsWindow,
  };
})(window);
