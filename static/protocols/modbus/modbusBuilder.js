(function initModbusBuilder(global) {
  function toHexByte(value) {
    return Number(value & 0xff)
      .toString(16)
      .toUpperCase()
      .padStart(2, "0");
  }

  function toHexWord(value) {
    return Number(value & 0xffff)
      .toString(16)
      .toUpperCase()
      .padStart(4, "0");
  }

  function buildPreview(functionDef, values) {
    const code = functionDef?.code;
    const common = {
      function_code: code,
      function_label: functionDef?.code_label,
      function_name: functionDef?.name,
      host: values.host,
      port: Number(values.port),
      unit_id: Number(values.unit_id),
    };

    if ([1, 2, 3, 4].includes(code)) {
      return {
        ...common,
        pdu_preview: `${toHexByte(code)} ${toHexWord(Number(values.start_addr))} ${toHexWord(Number(values.quantity))}`,
      };
    }

    if (code === 5) {
      const coilWord = String(values.value).toUpperCase() === "ON" ? 0xff00 : 0x0000;
      return {
        ...common,
        pdu_preview: `${toHexByte(code)} ${toHexWord(Number(values.address))} ${toHexWord(coilWord)}`,
      };
    }

    if (code === 6) {
      return {
        ...common,
        pdu_preview: `${toHexByte(code)} ${toHexWord(Number(values.address))} ${toHexWord(Number(values.value))}`,
      };
    }

    return {
      ...common,
      pdu_preview: `${toHexByte(code)} ...`,
    };
  }

  global.OTLabModbusBuilder = {
    buildPreview,
  };
})(window);
