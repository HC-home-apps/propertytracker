# Local Weekly Scheduler (launchd + pmset)

This runs PropertyTracker weekly from your Mac (residential IP), instead of GitHub-hosted runners.

## What gets installed

- `launchd` job label: `com.propertytracker.weekly`
- Weekly run time: Friday `07:00` (Mac local timezone)
- Auto-wake time: Friday `06:50`

## One-time setup

1. Ensure dependencies are installed:
   - `.venv` exists
   - `config.yml` exists
   - `.env` contains Telegram/API secrets
2. Install scheduler:
   - `./scripts/install_local_scheduler.sh`
3. Verify:
   - `launchctl print gui/$(id -u)/com.propertytracker.weekly`
   - `pmset -g sched`

## Manual run

- `./scripts/run_weekly_local.sh`
- Dry-run notify only:
  - `LOCAL_DRY_RUN=true ./scripts/run_weekly_local.sh`

## Remove scheduler

- `./scripts/uninstall_local_scheduler.sh`

## Notes

- Your Mac must be powered on and plugged in.
- If FileVault/login policies block background tasks after reboot, log in once after restart.
- `pmset repeat cancel` clears all repeat power events, not only this job.
