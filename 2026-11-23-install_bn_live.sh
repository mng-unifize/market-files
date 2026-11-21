#!/bin/bash
# One-time setup on the Mac mini. Safe to run again. Installs the 3 PM backup job and the 9:10 ready check, then shows the state.
cd /Users/ng/ibt/market-files || exit 1
for j in com.ibt.bnbackup com.ibt.bnready; do
  cp "$j.plist" ~/Library/LaunchAgents/
  launchctl bootout "gui/$(id -u)/$j" 2>/dev/null
  launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/$j.plist
done
echo "--- loaded jobs (expect watcher, bnbackup, bnready)"; launchctl list | grep -E "com.ibt"
echo "--- go-live state"; /Users/ng/ibt-env/bin/python 2026-11-22-order_exec_bn.py --status
echo "--- sleep setting (sleep should be 0)"; pmset -g | grep -E "^ sleep|womp"
echo "--- time zone (must be Asia/Kolkata)"; readlink /etc/localtime
