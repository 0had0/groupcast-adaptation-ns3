#!/usr/bin/env bash
# Stop the helper-reception campaign: close the tmux session and the ns-3 runs it started.
# Finished runs are kept; ./launch.sh resumes with the rest. Unfinished outputs are rewritten on resume.
set -uo pipefail
SESSION="${SESSION:-helperrx}"
tmux kill-session -t "$SESSION" 2>/dev/null && echo "tmux session '$SESSION' closed" || echo "no tmux session '$SESSION'"
pkill -f "ns3.42-platoon_harq_robustness_eval-default.*out/local_helperrx" && echo "ns-3 runs stopped" || echo "no ns-3 run left"
