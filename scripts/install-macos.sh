#!/bin/bash
# macOS Installation Script for OT Lab Agent
# This script removes the quarantine flag and sets proper permissions

set -e

AGENT_NAME="otlab-agent-macos-amd64"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_PATH="$SCRIPT_DIR/$AGENT_NAME"

echo "=================================================="
echo "  OT Lab Agent - macOS Installation"
echo "=================================================="
echo ""

# Check if agent exists
if [ ! -f "$AGENT_PATH" ]; then
    echo "❌ Error: Agent not found at $AGENT_PATH"
    echo ""
    echo "Please download the agent first from the OT Lab dashboard:"
    echo "  https://your-ot-lab-site.com/downloads"
    exit 1
fi

echo "✓ Found agent at: $AGENT_PATH"
echo ""

# Step 1: Remove quarantine flag
echo "📋 Step 1: Removing macOS quarantine flag..."
if xattr -d com.apple.quarantine "$AGENT_PATH" 2>/dev/null; then
    echo "   ✓ Quarantine flag removed"
else
    echo "   ℹ No quarantine flag found (may already be clean)"
fi
echo ""

# Step 2: Set executable permissions
echo "📋 Step 2: Setting executable permissions..."
chmod +x "$AGENT_PATH"
echo "   ✓ Permissions set"
echo ""

# Step 3: Verify it works
echo "📋 Step 3: Verifying installation..."
if [ -x "$AGENT_PATH" ]; then
    echo "   ✓ Agent is ready to use"
else
    echo "   ❌ Error: Agent is not executable"
    exit 1
fi
echo ""

# Step 4: Optional - Check Npcap/libpcap
echo "📋 Step 4: Checking packet capture library..."
if command -v brew &> /dev/null; then
    if brew list libpcap &> /dev/null; then
        echo "   ✓ libpcap is installed"
    else
        echo "   ⚠ libpcap not found. Install with:"
        echo "     brew install libpcap"
    fi
else
    echo "   ℹ Could not verify libpcap (Homebrew not found)"
fi
echo ""

echo "=================================================="
echo "  Installation Complete! 🎉"
echo "=================================================="
echo ""
echo "To run the agent:"
echo "  $AGENT_PATH"
echo ""
echo "For more information, visit:"
echo "  https://your-ot-lab-site.com"
echo ""
