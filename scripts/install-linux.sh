#!/bin/bash
# OT Lab Local Runtime - Linux setup & launch

set -e

AGENT_NAME="otlab-agent-linux-amd64"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_PATH="$SCRIPT_DIR/$AGENT_NAME"

VERBOSE=0
for arg in "$@"; do
    case "$arg" in
        -v|--verbose) VERBOSE=1 ;;
    esac
done

if [ $VERBOSE -eq 1 ]; then
    echo "=================================================="
    echo "  OT Lab Local Runtime - Linux Setup & Launch"
    echo "=================================================="
    echo ""
else
    echo ""
    echo "  OT Lab Local Runtime"
fi

if [ ! -f "$AGENT_PATH" ]; then
    echo "❌ Error: Runtime binary not found at $AGENT_PATH"
    echo "   Download it from the OT Lab dashboard."
    exit 1
fi

chmod +x "$AGENT_PATH"

if [ ! -x "$AGENT_PATH" ]; then
    echo "❌ Error: Runtime is not executable after chmod."
    exit 1
fi

echo "  ✓ Runtime ready"

if [ $VERBOSE -eq 1 ]; then
    echo ""
    echo "Checking packet capture library (libpcap)..."

    LIBPCAP_FOUND=false

    if command -v apt-get &> /dev/null; then
        if dpkg -l 2>/dev/null | grep -q libpcap0.8; then
            echo "   ✓ libpcap installed (apt)"
            LIBPCAP_FOUND=true
        else
            echo "   ⚠ libpcap not found"
            echo "     sudo apt-get install libpcap0.8"
        fi
    elif command -v yum &> /dev/null; then
        if rpm -q libpcap &> /dev/null; then
            echo "   ✓ libpcap installed (yum)"
            LIBPCAP_FOUND=true
        else
            echo "   ⚠ libpcap not found"
            echo "     sudo yum install libpcap"
        fi
    elif command -v pacman &> /dev/null; then
        if pacman -Q libpcap &> /dev/null; then
            echo "   ✓ libpcap installed (pacman)"
            LIBPCAP_FOUND=true
        else
            echo "   ⚠ libpcap not found"
            echo "     sudo pacman -S libpcap"
        fi
    else
        echo "   ℹ Could not detect package manager"
    fi
fi

echo ""
echo "  Starting Local Runtime UI..."
echo ""
if [ $VERBOSE -eq 1 ]; then
  exec "$AGENT_PATH" --gui --verbose
else
  exec "$AGENT_PATH" --gui
fi
