(function initModbusDefinitions(global) {
  const fallback = {
    id: "modbus",
    name: "Modbus",
    functions: [],
  };

  function getDefaultValue(field) {
    if (field.default !== undefined && field.default !== null) return field.default;
    if (field.type === "select" && Array.isArray(field.options) && field.options.length > 0) {
      return field.options[0].value;
    }
    return "";
  }

  async function fetchProtocolDefinition() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 7000);
    let data = null;

    try {
      const response = await fetch("/api/actions/definitions", {
        credentials: "same-origin",
        signal: controller.signal,
      });
      data = await response.json();
    } catch (_err) {
      return fallback;
    } finally {
      clearTimeout(timeout);
    }

    if (!data || !data.ok) return fallback;

    const protocols = Array.isArray(data.protocols) ? data.protocols : [];
    const modbus = protocols.find((protocol) => protocol.id === "modbus");
    return modbus || fallback;
  }

  global.OTLabModbusDefinitions = {
    fetchProtocolDefinition,
    getDefaultValue,
  };
})(window);
