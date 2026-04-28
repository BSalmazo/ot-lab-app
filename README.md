# OT Lab App

OT Lab App is an in-progress OT/ICS cybersecurity lab platform designed to run as a web interface for Modbus/TCP visibility and experimentation.

It combines a web-based interface, protocol-aware backend logic, and a Local Runtime to monitor traffic, manage events and alerts, and interact with Modbus client/server components in a controlled lab setup.

## Current scope

- Modbus/TCP monitoring interface
- Local Runtime download and configuration flow
- Session-based event, alert, and log handling
- Modbus client and server controls from the web UI
- Traffic summaries focused on industrial communication behavior
- Cross-platform Local Runtime distribution from GitHub release assets (Windows, macOS, Linux)
- Local process simulation and HMI/PLC views for controlled testing

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
- `agent/` — Local Runtime logic, capture agent, runtime services, sniffing, protocol-related modules
- `templates/` — web interface templates
- `static/` — frontend assets
- `scripts/` — installer helpers used when packaging agent downloads
- `studies/` — checkpoint reports, paper/TRP material, and research evidence
- `.github/workflows/` — automation workflows

## Deployment model

Railway runs the FastAPI web app from this repository. Users open the Railway URL, use the dashboard, download a Local Runtime package for their operating system, and connect that runtime back to their web session.

Local Runtime binaries are not stored in the repository. GitHub Actions builds them and publishes them as release assets under `dev-latest`; the web app downloads the release asset that matches the deployed commit and wraps it with the user/session-specific `agent-config.json`.

For the safest Railway flow, configure a Railway deploy hook as the GitHub secret `RAILWAY_DEPLOY_HOOK_URL` and disable Railway's direct branch auto-deploy. The GitHub workflow will then build/publish the agent first and trigger Railway only after the matching binaries are available. If Railway deploys directly from GitHub before the workflow finishes, the app will temporarily block agent downloads for that build instead of serving an incompatible agent.

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
