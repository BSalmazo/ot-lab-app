#!/bin/bash
# Linux Installation Script for OT Lab Local Runtime
# This script sets proper permissions and verifies libpcap

set -e

AGENT_NAME="otlab-agent-linux-amd64"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_PATH="$SCRIPT_DIR/$AGENT_NAME"

echo "=================================================="
echo "  OT Lab Local Runtime - Linux Installation"
echo "=================================================="
echo ""

# Check if agent exists
if [ ! -f "$AGENT_PATH" ]; then
    echo "❌ Error: Runtime binary not found at $AGENT_PATH"
    echo ""
    echo "Please download the Local Runtime first from the OT Lab dashboard:"
    echo "  Open the OT Lab App dashboard and use the Download Runtime button"
    exit 1
fi

echo "✓ Found Local Runtime at: $AGENT_PATH"
echo ""

# Step 1: Set executable permissions
echo "📋 Step 1: Setting executable permissions..."
chmod +x "$AGENT_PATH"
echo "   ✓ Permissions set"
echo ""

# Step 2: Detect package manager and libpcap status
echo "📋 Step 2: Checking packet capture library (libpcap)..."

LIBPCAP_FOUND=false

if command -v apt-get &> /dev/null; then
    # Debian/Ubuntu
    if dpkg -l | grep -q libpcap0.8; then
        echo "   ✓ libpcap is installed (apt-based system)"
        LIBPCAP_FOUND=true
    else
        echo "   ⚠ libpcap not found"
        echo ""
        echo "   To install on Ubuntu/Debian:"
        echo "     sudo apt-get update"
        echo "     sudo apt-get install libpcap0.8"
    fi
elif command -v yum &> /dev/null; then
    # RHEL/CentOS/Fedora
    if rpm -q libpcap &> /dev/null; then
        echo "   ✓ libpcap is installed (yum-based system)"
        LIBPCAP_FOUND=true
    else
        echo "   ⚠ libpcap not found"
        echo ""
        echo "   To install on CentOS/RHEL:"
        echo "     sudo yum install libpcap"
fi
elif command -v pacman &> /dev/null; then
    # Arch Linux
    if pacman -Q libpcap &> /dev/null; then
        echo "   ✓ libpcap is installed (Arch Linux)"
        LIBPCAP_FOUND=true
    else
        echo "   ⚠ libpcap not found"
        echo ""
        echo "   To install on Arch:"
        echo "     sudo pacman -S libpcap"
    fi
else
    echo "   ℹ Could not detect package manager"
fi

if [ "$LIBPCAP_FOUND" = false ]; then
    echo ""
    echo "   ℹ libpcap is required for packet capture"
fi

echo ""

# Step 3: Verify it works
echo "📋 Step 3: Verifying installation..."
if [ -x "$AGENT_PATH" ]; then
    echo "   ✓ Local Runtime is ready to use"
else
    echo "   ❌ Error: Local Runtime is not executable"
    exit 1
fi
echo ""

echo "=================================================="
echo "  Installation Complete! 🎉"
echo "=================================================="
echo ""
echo "To run the Local Runtime:"
echo "  $AGENT_PATH"
echo ""
echo "For more information, visit:"
echo "  Open the OT Lab App dashboard"
echo ""

echo "Starting Local Runtime UI now..."
exec "$AGENT_PATH" --gui
