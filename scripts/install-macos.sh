#!/bin/bash
# macOS Installation Script for OT Lab Agent
# This script removes the quarantine flag and sets proper permissions

set -e

SCRIPT_VERSION="install-macos.sh 2026-04-24r4"
AGENT_NAME="otlab-agent-macos-amd64"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_PATH="$SCRIPT_DIR/$AGENT_NAME"
CHECK_LOG="$SCRIPT_DIR/.otlab-agent-check.log"
MANIFEST_PATH="$SCRIPT_DIR/otlab-bundle-manifest.json"

echo "=================================================="
echo "  OT Lab Agent - macOS Installation"
echo "=================================================="
echo "  Script version: $SCRIPT_VERSION"
echo ""

if [ -f "$MANIFEST_PATH" ]; then
    echo "📦 Bundle manifest found:"
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$MANIFEST_PATH" <<'PY'
import json, sys
p = sys.argv[1]
try:
    with open(p, "r", encoding="utf-8") as f:
        m = json.load(f)
except Exception as e:
    print(f"   ⚠ Could not parse manifest: {e}")
    raise SystemExit(0)

print(f"   generated_at: {m.get('generated_at', '-')}")
print(f"   server_build_id: {m.get('server_build_id', '-')}")
print(f"   platform: {m.get('platform', '-')}")
print(f"   binary_source: {m.get('binary_source', '-')}")
print(f"   binary_release_tag: {m.get('binary_release_tag', '-')}")
print(f"   binary_asset_name: {m.get('binary_asset_name', '-')}")
print(f"   binary_sha256: {m.get('binary_sha256', '-')}")
PY
    else
        echo "   ⚠ python3 not found, cannot parse manifest JSON."
    fi
    echo ""
fi

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

# Optional integrity check against manifest hash
if [ -f "$MANIFEST_PATH" ] && command -v shasum >/dev/null 2>&1; then
    EXPECTED_SHA="$(grep -E '"binary_sha256"' "$MANIFEST_PATH" | head -n1 | sed -E 's/.*"binary_sha256"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
    if [ -n "$EXPECTED_SHA" ]; then
        CURRENT_SHA="$(shasum -a 256 "$AGENT_PATH" | awk '{print $1}')"
        echo "🔐 Integrity check (SHA256):"
        echo "   expected: $EXPECTED_SHA"
        echo "   current : $CURRENT_SHA"
        if [ "$EXPECTED_SHA" = "$CURRENT_SHA" ]; then
            echo "   ✓ Hash match"
        else
            echo "   ❌ Hash mismatch! This binary does not match the downloaded bundle."
            exit 1
        fi
        echo ""
    fi
fi

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
