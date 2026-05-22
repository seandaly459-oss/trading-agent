#!/bin/bash

LAUNCH_AGENTS="$HOME/Library/LaunchAgents"

echo "=== Trading Agent Uninstaller ==="
echo ""

PLISTS=(
    "com.tradingagent.eod.plist"
    "com.tradingagent.monitor.plist"
    "com.tradingagent.intraday.plist"
)

for plist in "${PLISTS[@]}"; do
    dst="$LAUNCH_AGENTS/$plist"
    label="${plist%.plist}"

    if launchctl list | grep -q "$label"; then
        echo "Unloading $label..."
        launchctl unload "$dst" 2>/dev/null && echo "  ✓  Unloaded" || echo "  ✗  Failed to unload"
    else
        echo "$label is not loaded — skipping"
    fi

    if [ -f "$dst" ]; then
        rm "$dst"
        echo "  ✓  Removed $dst"
    fi
    echo ""
done

echo "=== Uninstall complete ==="
echo ""
echo "Note: log files at ~/trading_agent_logs/ were not removed."
echo "Delete them manually if no longer needed."
