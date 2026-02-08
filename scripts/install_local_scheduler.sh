#!/bin/zsh
set -euo pipefail

JOB_LABEL="com.propertytracker.weekly"
WEEKDAY_NUM="5"  # launchd weekday: 1=Monday ... 5=Friday, 6=Saturday, 0/7=Sunday
RUN_HOUR="7"
RUN_MINUTE="0"
WAKE_DAY="F"     # pmset: M T W R F S U
WAKE_TIME="06:50:00"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_SCRIPT="$REPO_DIR/scripts/run_weekly_local.sh"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_DIR/$JOB_LABEL.plist"
STDOUT_LOG="$REPO_DIR/logs/launchd-weekly.out.log"
STDERR_LOG="$REPO_DIR/logs/launchd-weekly.err.log"

mkdir -p "$LAUNCH_DIR" "$REPO_DIR/logs"

if [[ ! -x "$RUN_SCRIPT" ]]; then
  chmod +x "$RUN_SCRIPT"
fi

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$JOB_LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$RUN_SCRIPT</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$REPO_DIR</string>

  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>$WEEKDAY_NUM</integer>
    <key>Hour</key>
    <integer>$RUN_HOUR</integer>
    <key>Minute</key>
    <integer>$RUN_MINUTE</integer>
  </dict>

  <key>StandardOutPath</key>
  <string>$STDOUT_LOG</string>
  <key>StandardErrorPath</key>
  <string>$STDERR_LOG</string>

  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
PLIST

# Ensure old definition is replaced cleanly.
launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/$JOB_LABEL"

echo "Installed launchd job: $JOB_LABEL"
echo "Plist: $PLIST_PATH"

echo "Configuring weekly wake schedule with pmset..."
if ! sudo -n pmset repeat wakeorpoweron "$WAKE_DAY" "$WAKE_TIME" >/dev/null 2>&1; then
  osascript -e "do shell script \"pmset repeat wakeorpoweron $WAKE_DAY $WAKE_TIME\" with administrator privileges"
else
  sudo pmset repeat wakeorpoweron "$WAKE_DAY" "$WAKE_TIME"
fi
echo "pmset wake schedule set: $WAKE_DAY $WAKE_TIME"

echo
echo "Verify:"
echo "  launchctl print gui/$(id -u)/$JOB_LABEL"
echo "  pmset -g sched"
