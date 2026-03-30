(function initModbusValidators(global) {
  function parseCommaList(raw, type) {
    const items = String(raw || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);

    if (items.length === 0) return [];

    if (type === "coils") {
      return items.map((item) => {
        const normalized = item.toLowerCase();
        if (["1", "true", "on"].includes(normalized)) return 1;
        if (["0", "false", "off"].includes(normalized)) return 0;
        return Number(item);
      });
    }

    return items.map((item) => Number(item));
  }

  function validateFields(fields, values) {
    const errors = [];

    for (const field of fields || []) {
      const value = values[field.key];
      const required = field.required !== false;

      if (required && (value === undefined || value === null || String(value).trim() === "")) {
        errors.push(`${field.label} is required`);
        continue;
      }

      if (value === undefined || value === null || value === "") {
        continue;
      }

      if (field.key === "values" || field.key === "write_values") {
        const parsed = parseCommaList(value, "values");
        if (!parsed.length || parsed.some((item) => Number.isNaN(item))) {
          errors.push(`${field.label} must be a comma-separated integer list`);
        }
        continue;
      }

      if (field.key === "coils") {
        const parsed = parseCommaList(value, "coils");
        if (!parsed.length || parsed.some((item) => Number.isNaN(item) || (item !== 0 && item !== 1))) {
          errors.push(`${field.label} must contain only 0/1 values`);
        }
        continue;
      }

      if (field.type === "text" || field.type === "select") {
        continue;
      }

      const num = Number(value);
      if (Number.isNaN(num)) {
        errors.push(`${field.label} must be a number`);
        continue;
      }
      if (field.min !== null && field.min !== undefined && num < field.min) {
        errors.push(`${field.label} must be >= ${field.min}`);
      }
      if (field.max !== null && field.max !== undefined && num > field.max) {
        errors.push(`${field.label} must be <= ${field.max}`);
      }
    }

    return errors;
  }

  global.OTLabModbusValidators = {
    validateFields,
    parseCommaList,
  };
})(window);
