#!/usr/bin/env bash
# Start the helper-reception campaign in its own detached tmux session.
#   ./launch.sh                 # both phases, 4 parallel runs, then extract.py and notebook 10
#   JOBS=2 ./launch.sh --phase fixed
#   SESSION=helperrx_fb HRX_OUT=$PWD/../out/local_helperrx_fb ./launch.sh --set fb   # R4 + C + fallback sweep
# Window "run": the runner (kept awake with caffeinate while it runs). Window "monitor": monitor.py every 30 s.
# Follow:  tmux attach -t helperrx   (detach: Ctrl-b d)   or once:  python3 monitor.py
# Stop:    ./stop.sh   (finished runs are kept; ./launch.sh again resumes)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION="${SESSION:-helperrx}"
JOBS="${JOBS:-4}"
PY="$(command -v python3)"
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "tmux session '$SESSION' already exists: tmux attach -t $SESSION  (or ./stop.sh first)"
    exit 1
fi
tmux new-session -d -s "$SESSION" -n run -c "$HERE" \
    "caffeinate -ims '$PY' '$HERE/campaign.py' --jobs $JOBS $*; echo; echo 'runner finished. Enter closes this window.'; read"
tmux new-window -t "$SESSION" -n monitor -c "$HERE" "SESSION=$SESSION HRX_OUT='${HRX_OUT:-}' '$PY' '$HERE/monitor.py' --watch 30"
echo "started in tmux session '$SESSION' ($JOBS parallel runs)"
echo "  follow:  tmux attach -t $SESSION      status once:  python3 $HERE/monitor.py"
