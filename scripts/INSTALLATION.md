# OT Lab Local Runtime Installation and Operation

This guide explains how to run the Local Runtime package downloaded from:
`https://otlab.salmazo.org`

The package contains:

- runtime binary
- startup script for your operating system
- `agent-config.json` generated for your web session

## 1. Quick Start

### 1.1 macOS

```bash
bash install-macos.sh
```

Verbose mode:

```bash
bash install-macos.sh -v
```

### 1.2 Windows

```bat
install-windows.bat
```

Verbose mode:

```bat
install-windows.bat -v
```

### 1.3 Linux

```bash
bash install-linux.sh
```

Verbose mode:

```bash
bash install-linux.sh -v
```

## 2. Runtime UI Controls

The Local Runtime UI exposes:

- `Start Runtime` / `Stop Runtime`
- `Start Monitor` / `Stop Monitor` (enabled only while runtime is running)

Behavior:

- Runtime can execute process simulation and local Modbus commands
- Monitor mode enables packet-based traffic inspection in addition to runtime services

## 3. Platform Prerequisites

### 3.1 Windows

- `Npcap` must be installed:
  - https://nmap.org/npcap/

### 3.2 macOS and Linux

- `libpcap` must be installed and available

Common install commands:

- macOS: `brew install libpcap`
- Ubuntu/Debian: `sudo apt-get install libpcap0.8`
- RHEL/CentOS: `sudo yum install libpcap`
- Arch: `sudo pacman -S libpcap`

## 4. What the Scripts Do

### 4.1 macOS script

- verifies binary integrity when bundle manifest is present
- removes quarantine metadata
- validates executable startup
- launches runtime UI

### 4.2 Windows script

- clears `Zone.Identifier` metadata when present
- checks runtime executable availability
- launches runtime UI

### 4.3 Linux script

- sets executable permissions
- checks runtime executable availability
- launches runtime UI

## 5. Troubleshooting

### 5.1 Runtime does not start

- confirm `agent-config.json` exists in the same folder
- run script in verbose mode (`-v`) and inspect terminal output

### 5.2 Monitor mode cannot inspect traffic

- validate `Npcap`/`libpcap` installation
- run monitor mode and verify capture interfaces are detected

### 5.3 Download says build is not ready

- refresh the web interface and retry download
- verify latest release assets in:
  - `https://github.com/BSalmazo/ot-lab-app/releases`

## 6. Support

- Issues: `https://github.com/BSalmazo/ot-lab-app/issues`
