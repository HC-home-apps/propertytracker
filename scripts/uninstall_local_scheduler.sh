#!/bin/zsh
set -euo pipefail

JOB_LABEL="com.propertytracker.weekly"
PLIST_PATH="$HOME/Library/LaunchAgents/$JOB_LABEL.plist"

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
rm -f "$PLIST_PATH"

echo "Removed launchd job: $JOB_LABEL"
echo "Clearing pmset repeat wake schedule..."
if ! sudo -n pmset repeat cancel >/dev/null 2>&1; then
  osascript -e 'do shell script "pmset repeat cancel" with administrator privileges'
else
  sudo pmset repeat cancel
fi
echo "Done."
