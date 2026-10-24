#!/bin/bash
# Progress of a study.py profile run: done / total, failures, throughput, ETA, running simulations.
# Usage: monitor_study.sh [profile=density] [--watch [seconds=60]]
#   reads out/<profile>_run.log (density) or out/full_run.log (full)
HERE="$(cd "$(dirname "$0")" && pwd)"
PROFILE="${1:-density}"
WATCH=0; EVERY=60
[ "${2:-}" = "--watch" ] && { WATCH=1; EVERY="${3:-60}"; }

report() {
  local LOG="$HERE/out/${PROFILE}_run.log"
  [ "$PROFILE" = "full" ] && LOG="$HERE/out/full_run.log"
  echo "=== $PROFILE  $(date '+%F %T')  host $(hostname)"
  if [ ! -f "$LOG" ]; then echo "no log yet: $LOG"; return; fi
  python3 - "$LOG" <<'EOF'
import re, sys, time, os
log = sys.argv[1]
lines = open(log, errors='replace').read().splitlines()
# the last "[profile] N runs" header starts the current run (the log is appended to)
start = max((i for i, l in enumerate(lines) if re.match(r'\[\w+\] \d+ runs', l)), default=0)
cur = lines[start:]
m = re.match(r'\[\w+\] (\d+) runs', cur[0]) if cur else None
total = int(m.group(1)) if m else 0
rows = [re.match(r'\[(\d+)/(\d+)\] (\w+)\s+exit\s+(-?\d+)\s+([\d.]+)s (\S+)\s+(\S+)\s+\(elapsed ([\d.]+) min\)', l) for l in cur]
rows = [r for r in rows if r]
ran = [r for r in rows if r.group(3) == 'ran']
skip = [r for r in rows if r.group(3) == 'skip']
fail = [r for r in rows if r.group(4) != '0']
done = len(rows)
el = float(rows[-1].group(8)) if rows else 0.0
print(f'progress  {done}/{total} ({100 * done / max(total, 1):.1f} %)   ran {len(ran)}  skipped {len(skip)}  failed {len(fail)}')
if ran:
    wall = [float(r.group(5)) for r in ran]
    print(f'run time  mean {sum(wall) / len(wall) / 60:.1f} min   max {max(wall) / 60:.1f} min   elapsed {el:.0f} min')
    rate = len(ran) / max(el, 1e-9)                      # runs per minute, current concurrency
    left = total - done
    print(f'ETA       {left / rate / 60:.1f} h for {left} runs  (~{rate * 60:.1f} runs/h)' if rate > 0 else 'ETA n/a')
for r in fail[-10:]:
    print(f'FAILED    {r.group(6)} {r.group(7)} exit {r.group(4)}')
tail = [l for l in cur if l.startswith(('done:', 'Traceback', 'Error', 'ModuleNotFound'))]
for l in tail[-3:]:
    print('log       ' + l)
print(f'log age   {(time.time() - os.path.getmtime(log)) / 60:.1f} min since the last line')
EOF
  local N
  N=$(pgrep -f "ns3.42-platoon_harq_robustness_eval-.*out/$PROFILE/" | wc -l)
  echo "running   $N simulations   load $(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null || uptime | sed 's/.*load averages*: //')"
  pgrep -af "ns3.42-platoon_harq_robustness_eval-.*out/$PROFILE/" 2>/dev/null \
    | grep -o "out/$PROFILE/[^ ]*" | sed "s|out/$PROFILE/|          |" | head -12
  pgrep -f "study.py run --profile $PROFILE" > /dev/null && echo "study.py  alive" || echo "study.py  NOT running"
  free -h 2>/dev/null | awk '/Mem:/ {print "memory    used " $3 " / " $2 ", available " $7}'
  df -h "$HERE/out" | awk 'NR == 2 {print "disk      " $4 " free on " $NF}'
}

if [ "$WATCH" = 1 ]; then
  while true; do clear; report; sleep "$EVERY"; done
else
  report
fi
