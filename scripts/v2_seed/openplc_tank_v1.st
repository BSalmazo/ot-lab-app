PROGRAM plc_prg
VAR
    // HMI commands (written by Modbus client / HMI)
    PUMP_CMD AT %QX0.0 : BOOL;            // Coil 00001
    VALVE_CMD AT %QX0.1 : BOOL;           // Coil 00002

    // Alarm status bits (read by HMI)
    ALARM_HI_ACTIVE AT %QX0.2 : BOOL;     // Coil 00003
    ALARM_LO_ACTIVE AT %QX0.3 : BOOL;     // Coil 00004

    // HMI setpoints (holding registers)
    PUMP_FLOW_SP AT %QW0 : INT;           // HR 40001 (0..100 %)
    VALVE_FLOW_SP AT %QW1 : INT;          // HR 40002 (0..100 %)
    ALARM_HI_SP AT %QW2 : INT;            // HR 40003 (0..100)
    ALARM_LO_SP AT %QW3 : INT;            // HR 40004 (0..100)

    // Process readback (holding registers, more stable in OpenPLC/FUXA mapping)
    LEVEL_AI AT %QW4 : INT;               // HR 40005 (0..100)
    PUMP_RATE_AI AT %QW5 : INT;           // HR 40006 (0..100)
    VALVE_RATE_AI AT %QW6 : INT;          // HR 40007 (0..100)

    // Internal process state
    LEVEL_R : REAL := 0.0;
    INFLOW_R : REAL := 0.0;
    OUTFLOW_R : REAL := 0.0;

    // Tunable model constants
    DT_S : REAL := 0.10;                  // Task cycle = 100 ms
    MAX_FILL_RATE : REAL := 12.0;         // % tank / s at PUMP_FLOW_SP=100
    MAX_DRAIN_RATE : REAL := 10.0;        // % tank / s at VALVE_FLOW_SP=100

    INIT_DONE : BOOL := FALSE;
END_VAR

// One-shot defaults (first scan)
IF NOT INIT_DONE THEN
    IF PUMP_FLOW_SP <= 0 THEN
        PUMP_FLOW_SP := 35;
    END_IF;
    IF VALVE_FLOW_SP <= 0 THEN
        VALVE_FLOW_SP := 20;
    END_IF;
    IF ALARM_HI_SP <= 0 THEN
        ALARM_HI_SP := 85;
    END_IF;
    IF ALARM_LO_SP <= 0 THEN
        ALARM_LO_SP := 15;
    END_IF;
    INIT_DONE := TRUE;
END_IF;

// Clamp operator setpoints
IF PUMP_FLOW_SP < 0 THEN PUMP_FLOW_SP := 0; END_IF;
IF PUMP_FLOW_SP > 100 THEN PUMP_FLOW_SP := 100; END_IF;

IF VALVE_FLOW_SP < 0 THEN VALVE_FLOW_SP := 0; END_IF;
IF VALVE_FLOW_SP > 100 THEN VALVE_FLOW_SP := 100; END_IF;

IF ALARM_LO_SP < 0 THEN ALARM_LO_SP := 0; END_IF;
IF ALARM_LO_SP > 99 THEN ALARM_LO_SP := 99; END_IF;

IF ALARM_HI_SP < 1 THEN ALARM_HI_SP := 1; END_IF;
IF ALARM_HI_SP > 100 THEN ALARM_HI_SP := 100; END_IF;

// Keep valid alarm band: HI must stay above LO
IF ALARM_HI_SP <= ALARM_LO_SP THEN
    ALARM_HI_SP := ALARM_LO_SP + 1;
    IF ALARM_HI_SP > 100 THEN
        ALARM_HI_SP := 100;
        ALARM_LO_SP := 99;
    END_IF;
END_IF;

// Convert commands + flow setpoints into process rates
IF PUMP_CMD THEN
    INFLOW_R := (INT_TO_REAL(PUMP_FLOW_SP) / 100.0) * MAX_FILL_RATE;
ELSE
    INFLOW_R := 0.0;
END_IF;

IF VALVE_CMD THEN
    OUTFLOW_R := (INT_TO_REAL(VALVE_FLOW_SP) / 100.0) * MAX_DRAIN_RATE;
ELSE
    OUTFLOW_R := 0.0;
END_IF;

// Process integration
LEVEL_R := LEVEL_R + (INFLOW_R - OUTFLOW_R) * DT_S;

// Physical bounds
IF LEVEL_R < 0.0 THEN LEVEL_R := 0.0; END_IF;
IF LEVEL_R > 100.0 THEN LEVEL_R := 100.0; END_IF;

// Publish process values
LEVEL_AI := REAL_TO_INT(LEVEL_R);
PUMP_RATE_AI := REAL_TO_INT((INFLOW_R / MAX_FILL_RATE) * 100.0);
VALVE_RATE_AI := REAL_TO_INT((OUTFLOW_R / MAX_DRAIN_RATE) * 100.0);

// Alarm evaluation
ALARM_HI_ACTIVE := LEVEL_AI >= ALARM_HI_SP;
ALARM_LO_ACTIVE := LEVEL_AI <= ALARM_LO_SP;

END_PROGRAM

CONFIGURATION Config0
    RESOURCE Res0 ON PLC
        TASK Main(INTERVAL := T#100ms, PRIORITY := 0);
        PROGRAM Inst0 WITH Main : plc_prg;
    END_RESOURCE
END_CONFIGURATION
