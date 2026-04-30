# OT Lab App

OT Lab App is a web-based OT/ICS laboratory focused on Modbus/TCP visibility, local process simulation, and practical monitoring workflows for research and teaching.

Official web interface:
`https://otlab.salmazo.org`

## 1. What This Platform Provides

- Web dashboard for OT lab operation
- Local Runtime (Windows, macOS, Linux) distributed from GitHub Releases
- Optional traffic monitoring mode (packet-based Modbus/TCP observation)
- Local process simulation (PLC + HMI model) controlled from the web interface
- Modbus server/client command and status control
- Event, alert, and system log timeline
- Session-based operation, including per-session runtime download configuration

## 2. Runtime Model

The platform separates responsibilities between cloud and local execution:

- **Web application (Railway)**:
  - UI, session state, command orchestration, event/alert presentation
- **Local Runtime (user machine)**:
  - Executes PLC/HMI process simulation
  - Executes local Modbus server/client actions
  - Optionally enables monitoring mode for network packet inspection

This separation allows process simulation to run locally even when monitoring is intentionally disabled.

## 3. Repository Structure

- `app.py`: FastAPI backend, orchestration, API endpoints, state management
- `agent/`: Local Runtime and monitoring implementation
- `static/`: Frontend JavaScript/CSS
- `templates/`: Jinja2 HTML templates
- `scripts/`: Cross-platform runtime start/install scripts and operator guide
- `studies/`: Research materials, checkpoint evidence, paper/TRP support content
- `.github/workflows/`: Build/release/deploy automation

## 4. Installation and Usage

### 4.1 Access the platform

Open:
`https://otlab.salmazo.org`

Then use the `Download` button in the Local Runtime card.

### 4.2 macOS

```bash
bash install-macos.sh
```

Verbose mode:

```bash
bash install-macos.sh -v
```

### 4.3 Windows

```bat
install-windows.bat
```

Verbose mode:

```bat
install-windows.bat -v
```

### 4.4 Linux

```bash
bash install-linux.sh
```

Verbose mode:

```bash
bash install-linux.sh -v
```

### 4.5 Runtime operation model

After startup, Local Runtime UI provides:

- `Start Runtime` / `Stop Runtime`
- `Start Monitor` / `Stop Monitor`

Notes:

- Runtime can execute PLC/HMI simulation and local Modbus operations.
- Monitor mode enables packet-based traffic visibility.

### 4.6 Platform prerequisites

- Windows: install `Npcap` (https://nmap.org/npcap/)
- macOS/Linux: ensure `libpcap` is available

### 4.7 Full operator guide

Detailed reference:
[`scripts/INSTALLATION.md`](./scripts/INSTALLATION.md)

## 5. Research Scope and Intended Use

This repository is designed for:

- doctoral/research environments
- OT security experimentation and validation
- teaching and laboratory demonstrations

It is not positioned as a certified industrial control product.

## 6. Maintainer

Bruno Salmazo
