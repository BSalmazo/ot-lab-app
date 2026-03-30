(function initActionsWindow(global) {
  let mounted = false;

  async function mountActionsWindow(containerId) {
    const root = document.getElementById(containerId);
    if (!root) return;
    if (mounted) return;
    mounted = true;

    const modbusTab = global.OTLabModbusTab;
    if (!modbusTab || typeof modbusTab.mount !== "function") {
      root.innerHTML = '<div class="status slim">Failed to load Modbus tab module.</div>';
      return;
    }

    root.innerHTML = `
      <div class="actions-shell">
        <div class="actions-tabs" role="tablist" aria-label="Protocols">
          <button class="actions-tab active" data-protocol-tab="modbus" type="button">Modbus</button>
        </div>
        <div class="actions-tab-panel" data-protocol-panel="modbus"></div>
      </div>
    `;

    const modbusPanel = root.querySelector("[data-protocol-panel='modbus']");
    try {
      await modbusTab.mount(modbusPanel);
    } catch (_err) {
      root.innerHTML = '<div class="status slim">Failed to initialize Actions tab.</div>';
    }
  }

  function bootstrap() {
    mountActionsWindow("actionsWindowBody").catch(() => {
      const root = document.getElementById("actionsWindowBody");
      if (root) {
        root.innerHTML = '<div class="status slim">Failed to mount Actions window.</div>';
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
  } else {
    bootstrap();
  }

  global.OTLabActions = {
    mountActionsWindow,
  };
})(window);
