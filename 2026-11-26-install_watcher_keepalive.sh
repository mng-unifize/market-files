#!/bin/bash
# Reloads the watcher job with restart-after-crash. Safe to run again. The watcher exits by itself after 16:00 and on weekends, and that is not restarted.
cd /Users/ng/ibt/market-files || exit 1
cp com.ibt.watcher.plist ~/Library/LaunchAgents/
launchctl bootout "gui/$(id -u)/com.ibt.watcher" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.ibt.watcher.plist
echo "--- loaded"; launchctl list | grep com.ibt.watcher
echo "--- KeepAlive in the installed file"; grep -A1 KeepAlive ~/Library/LaunchAgents/com.ibt.watcher.plist
