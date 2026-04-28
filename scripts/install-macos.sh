#!/bin/bash
# OT Lab Local Runtime - macOS setup & launch

set -e

SCRIPT_VERSION="install-macos.sh 2026-04-28r1"
AGENT_NAME="otlab-agent-macos-amd64"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_PATH="$SCRIPT_DIR/$AGENT_NAME"
CHECK_LOG="$SCRIPT_DIR/.otlab-agent-check.log"
MANIFEST_PATH="$SCRIPT_DIR/otlab-bundle-manifest.json"

VERBOSE=0
for arg in "$@"; do
    case "$arg" in
        -v|--verbose) VERBOSE=1 ;;
    esac
done

if [ $VERBOSE -eq 1 ]; then
    echo "=================================================="
    echo "  OT Lab Local Runtime - macOS Setup & Launch"
    echo "=================================================="
    echo "  Script version: $SCRIPT_VERSION"
    echo ""
else
    echo ""
    echo "  OT Lab Local Runtime"
fi

if [ $VERBOSE -eq 1 ] && [ -f "$MANIFEST_PATH" ]; then
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

if [ ! -f "$AGENT_PATH" ]; then
    echo "❌ Error: Runtime binary not found at $AGENT_PATH"
    echo "   Download it from the OT Lab dashboard."
    exit 1
fi

if [ $VERBOSE -eq 1 ]; then
    echo "✓ Found Local Runtime at: $AGENT_PATH"
    echo ""
fi

if [ -f "$MANIFEST_PATH" ] && command -v shasum >/dev/null 2>&1; then
    EXPECTED_SHA="$(grep -E '"binary_sha256"' "$MANIFEST_PATH" | head -n1 | sed -E 's/.*"binary_sha256"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
    if [ -n "$EXPECTED_SHA" ]; then
        CURRENT_SHA="$(shasum -a 256 "$AGENT_PATH" | awk '{print $1}')"
        if [ "$EXPECTED_SHA" != "$CURRENT_SHA" ]; then
            echo "❌ Integrity check failed: hash mismatch."
            if [ $VERBOSE -eq 1 ]; then
                echo "   expected: $EXPECTED_SHA"
                echo "   current : $CURRENT_SHA"
            fi
            exit 1
        fi
        if [ $VERBOSE -eq 1 ]; then
            echo "🔐 Integrity check (SHA256):"
            echo "   expected: $EXPECTED_SHA"
            echo "   current : $CURRENT_SHA"
            echo "   ✓ Hash match"
            echo ""
        fi
    fi
fi

echo "  ✓ Integrity verified"

if [ $VERBOSE -eq 1 ]; then
    echo "📋 Removing macOS quarantine flag..."
fi
if xattr -d com.apple.quarantine "$AGENT_PATH" 2>/dev/null; then
    [ $VERBOSE -eq 1 ] && echo "   ✓ Quarantine flag removed"
else
    [ $VERBOSE -eq 1 ] && echo "   ℹ No quarantine flag found"
fi

chmod +x "$AGENT_PATH"

if [ ! -x "$AGENT_PATH" ]; then
    echo "❌ Error: Runtime is not executable after chmod."
    exit 1
fi

set +e
"$AGENT_PATH" --help >"$CHECK_LOG" 2>&1
CHECK_RC=$?
set -e

if [ $CHECK_RC -ne 0 ]; then
    if command -v brew &> /dev/null; then
        [ $VERBOSE -eq 1 ] && echo "   ↻ Trying: brew install libpcap"
        if brew install libpcap >/dev/null 2>&1; then
            set +e
            "$AGENT_PATH" --help >"$CHECK_LOG" 2>&1
            CHECK_RC=$?
            set -e
        fi
    fi
    if [ $CHECK_RC -ne 0 ]; then
        echo "❌ Runtime check failed (possible libpcap issue)."
        echo "   Fix: brew install libpcap"
        echo "   Details: $CHECK_LOG"
        [ $VERBOSE -eq 1 ] && cat "$CHECK_LOG"
        exit 1
    fi
fi

echo "  ✓ Runtime ready"
echo ""
echo "  Starting Local Runtime UI..."
echo ""
exec "$AGENT_PATH" --gui
