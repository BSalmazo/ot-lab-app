# OT Lab Agent - Installation Scripts

These scripts handle platform-specific requirements for running the OT Lab Agent after download.

## Why These Scripts?

When you download binaries from the internet, operating systems add security restrictions:

- **macOS**: Adds quarantine flag, requires `xattr` to remove
- **Windows**: Windows Defender SmartScreen warns about unverified executables
- **Linux**: File needs executable permissions set

These scripts automate the setup process.

## Installation Instructions

### macOS 🍎

```bash
# Option 1: Run the installation script
bash ~/Downloads/install-macos.sh

# Option 2: Manual commands
xattr -d com.apple.quarantine ~/Downloads/otlab-agent-macos-amd64
chmod +x ~/Downloads/otlab-agent-macos-amd64
~/Downloads/otlab-agent-macos-amd64
```

**Requirements:**
- libpcap (usually pre-installed, or: `brew install libpcap`)

---

### Windows 🪟

```batch
# Run the batch file (double-click or in Command Prompt)
"%USERPROFILE%\Downloads\install-windows.bat"

# Or manually:
# 1. Right-click agent.exe → Properties → Unblock → Apply
# 2. Double-click to run
```

**Requirements:**
- Npcap (download from https://nmap.org/npcap/)

---

### Linux 🐧

```bash
# Option 1: Run the installation script
bash ~/Downloads/install-linux.sh

# Option 2: Manual commands
chmod +x ~/Downloads/otlab-agent-linux-amd64
~/Downloads/otlab-agent-linux-amd64
```

**Requirements:**
- libpcap: 
  - Ubuntu/Debian: `sudo apt-get install libpcap0.8`
  - CentOS/RHEL: `sudo yum install libpcap`
  - Arch: `sudo pacman -S libpcap`

---

## What Each Script Does

### `install-macos.sh`
1. ✓ Removes macOS quarantine flag
2. ✓ Sets executable permissions
3. ✓ Verifies installation
4. ✓ Validates packet-capture runtime by running `agent --help`
5. ✓ If needed, attempts `brew install libpcap` automatically
6. ✓ Aborts with clear error if runtime is still unavailable

### `install-windows.bat`
1. ✓ Removes Zone.Identifier (SmartScreen)
2. ✓ Sets file attributes
3. ✓ Verifies installation
4. ℹ Checks Npcap status

### `install-linux.sh`
1. ✓ Sets executable permissions
2. ✓ Auto-detects package manager
3. ✓ Checks libpcap status
4. ✓ Provides installation commands if missing

---

## Troubleshooting

### "Permission Denied" (macOS/Linux)
```bash
chmod +x ~/Downloads/install-macos.sh
bash ~/Downloads/install-macos.sh
```

### "Cannot verify identity" (macOS)
If you still see the warning after running the script:
1. Click "Cancel" on the warning
2. Open System Settings → Privacy & Security
3. Scroll down and click "Open Anyway" next to the agent
4. Click "Trust" when prompted

### "Npcap not installed" (Windows/macOS)
The agent will show a clear error message on startup if Npcap/libpcap is missing.
Follow the prompts to install the required driver.

### "Command not found" (Linux)
If `libpcap` is in a non-standard location, you may need to set:
```bash
export LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH
./otlab-agent-linux-amd64
```

---

## Auto-Update Available

We're working on automated updates. For now, download new versions from the dashboard:
Open the OT Lab App dashboard and use the Download Agent button

---

## Support

For issues or questions:
- Email: the project maintainer
- Issues: https://github.com/BSalmazo/ot-lab-app/issues
