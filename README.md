# OT Lab App

OT Lab App is an in-progress OT/ICS cybersecurity lab platform designed to support visibility and experimentation in Modbus/TCP environments.

It combines a web-based interface, protocol-aware backend logic, and a local capture agent to monitor traffic, manage events and alerts, and interact with Modbus client/server components in a controlled lab setup.

## Current scope

- Modbus/TCP monitoring interface
- Local agent download and configuration flow
- Session-based event, alert, and log handling
- Modbus client and server controls from the web UI
- Traffic summaries focused on industrial communication behavior
- Cross-platform agent distribution (Windows, macOS, Linux)

## Main goals

- Provide a simple OT lab environment for Modbus/TCP testing
- Support visibility into industrial communication flows
- Help explore monitoring and detection logic in ICS environments
- Serve as a base for future OT cybersecurity experiments and features

## Tech stack

- Python
- FastAPI
- Jinja2 templates
- JavaScript / HTML / CSS
- Modbus/TCP-related parsing and validation components

## Project structure

- `app.py` — main FastAPI application and session/state handling
- `agent/` — local agent logic, runtime, sniffing, protocol-related modules
- `templates/` — web interface templates
- `static/` — frontend assets
- `downloads/` — local downloadable agent files
- `.github/workflows/` — automation workflows

## Status

This project is currently **under development**.

It is being actively refactored and expanded, so features, structure, and workflows may change over time.

## Notes

This repository is intended for lab, educational, and research-oriented use in OT/ICS contexts. It is not a finished production-ready platform.

## Planned improvements

- Improved installation/setup documentation
- Architecture overview
- Better screenshots and usage examples
- Expanded protocol analysis and detection features
- More robust agent management and observability

## Author

Bruno Salmazo
