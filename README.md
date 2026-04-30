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

See:
`scripts/INSTALLATION.md`

This guide covers Windows, macOS, and Linux, including:

- Download flow
- Runtime startup
- Monitor mode behavior
- Platform prerequisites (`Npcap` or `libpcap`)
- Troubleshooting

## 5. Release and Deployment Flow

### 5.1 Build/Release

GitHub Actions workflow:
`.github/workflows/build-and-release.yml`

On updates to `refactor/codex-setup`, the workflow:

1. builds runtime binaries for Windows, macOS, and Linux
2. publishes assets to `dev-latest` release
3. optionally triggers Railway deployment through a deploy hook

### 5.2 Railway

Recommended production flow:

- Configure Railway to deploy from `refactor/codex-setup`
- Configure `RAILWAY_DEPLOY_HOOK_URL` in GitHub repository secrets
- Keep runtime release generation before deployment to minimize binary/web mismatch windows

## 6. Branching Strategy (Current Baseline + Next Version)

Current stable baseline:

- `refactor/codex-setup`

Recommended next-step model:

1. Create a new branch for the next major cycle (for example `feature/v2`)
2. Keep `refactor/codex-setup` as operational baseline
3. Merge to baseline only after integrated validation (web + runtime + release pipeline)

## 7. Research Scope and Intended Use

This repository is designed for:

- doctoral/research environments
- OT security experimentation and validation
- teaching and laboratory demonstrations

It is not positioned as a certified industrial control product.

## 8. Maintainer

Bruno Salmazo
