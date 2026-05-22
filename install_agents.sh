#!/bin/bash
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/trading_agent_logs"

echo "=== Trading Agent Installer ==="
echo ""

# ── Create logs directory ──────────────────────────────────────────────────────
echo "Creating log directory: $LOG_DIR"
mkdir -p "$LOG_DIR"

# ── Unload old momentumbot plist if present ────────────────────────────────────
if launchctl list | grep -q "com.momentumbot"; then
    echo "Unloading old com.momentumbot agent..."
    launchctl unload "$LAUNCH_AGENTS/com.momentumbot.plist" 2>/dev/null || true
fi

# ── Install each plist ─────────────────────────────────────────────────────────
PLISTS=(
    "com.tradingagent.eod.plist"
    "com.tradingagent.monitor.plist"
    "com.tradingagent.intraday.plist"
)

for plist in "${PLISTS[@]}"; do
    src="$REPO_DIR/$plist"
    dst="$LAUNCH_AGENTS/$plist"

    if [ ! -f "$src" ]; then
        echo "ERROR: $src not found — aborting"
        exit 1
    fi

    # Unload first if already loaded
    if launchctl list | grep -q "${plist%.plist}"; then
        echo "Unloading existing $plist..."
        launchctl unload "$dst" 2>/dev/null || true
    fi

    echo "Copying $plist → $LAUNCH_AGENTS/"
    cp "$src" "$dst"

    echo "Loading $plist..."
    launchctl load "$dst"

    # Verify
    label="${plist%.plist}"
    if launchctl list | grep -q "$label"; then
        echo "  ✓  $label is loaded"
    else
        echo "  ✗  $label failed to load — check $LOG_DIR/${label#com.tradingagent.}_error.log"
    fi
    echo ""
done

echo "=== Installation complete ==="
echo ""
echo "Agents loaded:"
launchctl list | grep "com.tradingagent"
echo ""
echo "Logs will appear in: $LOG_DIR/"
echo "  eod.log         — 4:30 PM ET pipeline"
echo "  monitor.log     — every 30 min, market hours"
echo "  intraday.log    — 10:00 AM ET morning brief"
