#!/bin/bash
# macOS Installation Script for OT Lab Agent
# This script removes the quarantine flag and sets proper permissions

set -e

AGENT_NAME="otlab-agent-macos-amd64"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_PATH="$SCRIPT_DIR/$AGENT_NAME"
CHECK_LOG="$SCRIPT_DIR/.otlab-agent-check.log"

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
echo "📋 Step 4: Checking packet capture runtime..."
set +e
"$AGENT_PATH" --help >"$CHECK_LOG" 2>&1
CHECK_RC=$?
set -e

if [ $CHECK_RC -eq 0 ]; then
    echo "   ✓ Packet capture runtime is available"
else
    echo "   ⚠ Agent runtime check failed (possible libpcap issue)"
    if command -v brew &> /dev/null; then
        echo "   ↻ Trying automatic install: brew install libpcap"
        if brew install libpcap; then
            set +e
            "$AGENT_PATH" --help >"$CHECK_LOG" 2>&1
            CHECK_RC=$?
            set -e
            if [ $CHECK_RC -eq 0 ]; then
                echo "   ✓ libpcap installed and runtime check passed"
            else
                echo "   ❌ Runtime check still failing after install attempt"
                echo "   See details in: $CHECK_LOG"
                cat "$CHECK_LOG"
                exit 1
            fi
        else
            echo "   ❌ Failed to install libpcap via Homebrew"
            echo "   See details in: $CHECK_LOG"
            cat "$CHECK_LOG"
            exit 1
        fi
    else
        echo "   ❌ Homebrew not found and runtime check failed"
        echo "   Install Homebrew + libpcap, then retry:"
        echo "     /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        echo "     brew install libpcap"
        echo "   See details in: $CHECK_LOG"
        cat "$CHECK_LOG"
        exit 1
    fi
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

echo "Starting agent now..."
exec "$AGENT_PATH"
