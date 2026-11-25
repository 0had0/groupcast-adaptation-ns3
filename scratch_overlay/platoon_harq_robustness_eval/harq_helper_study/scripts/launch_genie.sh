#!/bin/bash
# Genie (oracle) helper campaign: the SCHEMES_COMPARISON data with the per-window best helper (schedule replay,
# scripts/genie.py) in place of Kuo's selection. R3 has no helper: its existing runs stay the reference.
# Five stages, one after the other, in one background job (each is resumable: re-run this script to continue):
#   load    study.py --profile v3_genie, R4_bsc / R4_bsc_fbs / R6 _genie       900 runs   out/v3_genie
#   hmiss   helper_rx_campaign/campaign.py --set genie                         900 runs   out/helperrx_genie
#   timing  timing_sweep.py --genie, stage A then stage B (seeds 1-10)         810 runs   out/timing_genie
#   period  study.py --profile psfch_genie (R6_genie at PSFCH period 2, 4)     240 runs   out/psfch_genie
#   psfch   psfch_err_sweep.py --genie (miss / NACK->ACK / power)             1320 runs   out/psfch_err_genie
# Needs: the 150 schedules in out/genie_probe_all/R3_probe and the preflight references in out/genie_preflight_ref
# (both copied from the laptop), the campaign-v3 commands in out/v3 and out/v3_fb (templates of the sweeps).
# Before the stages: build, input checks, and a preflight: R6_genie, 3 routes, 0 cars, seed 1, which must reproduce
# the laptop's runs bit for bit (out/genie_preflight_ref) or the launch stops.
# Usage: launch_genie.sh [--jobs N] [--stages "load hmiss timing period psfch"] [--no-build] [--no-preflight]
#        launch_genie.sh --stop
# Progress: scripts/monitor_genie.sh [--watch 300]
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"   # harq_helper_study/ (this script: scripts/)
REPO="$(cd "$HERE/../../.." && pwd)"
JOBS=$(nproc 2>/dev/null || sysctl -n hw.ncpu); STAGES="load hmiss timing period psfch"; BUILD=1; PREFLIGHT=1; STOP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --jobs) JOBS="$2"; shift 2;;
    --stages) STAGES="$2"; shift 2;;
    --no-build) BUILD=0; shift;;
    --no-preflight) PREFLIGHT=0; shift;;
    --stop) STOP=1; shift;;
    *) echo "unknown option $1"; exit 2;;
  esac
done
LOG="$HERE/out/genie_campaign.log"
if [ "$STOP" = 1 ]; then
  pkill -f "genie_stages.sh" && echo "stopped the stage runner"
  pkill -f "study.py run --profile (v3_genie|psfch_genie)" ; pkill -f "campaign.py --set genie"
  pkill -f "(timing_sweep|psfch_err_sweep).py run .*--genie"
  pkill -f "ns3.42-platoon_harq_robustness_eval-.*--helperSelect=schedule" && echo "stopped simulator processes"
  sleep 2; pgrep -fl "genie_stages.sh|helperSelect=schedule" || echo "nothing of the genie campaign running"
  exit 0
fi
if pgrep -f "genie_stages.sh" > /dev/null; then
  echo "the genie campaign is already running: launch_genie.sh --stop first"; exit 7
fi
cd "$REPO" || exit 2
echo "commit $(git rev-parse --short HEAD) ($(git rev-parse --abbrev-ref HEAD))"
if [ "$BUILD" = 1 ]; then
  ninja -C ns-3-dev/cmake-cache scratch_platoon_harq_robustness_eval_platoon_harq_robustness_eval || exit 5
fi
cd "$HERE" || exit 2
mkdir -p out
# inputs
NS=$(ls out/genie_probe_all/R3_probe/*_genie.csv 2>/dev/null | wc -l)
[ "$NS" -eq 150 ] || { echo "need the 150 schedules in out/genie_probe_all/R3_probe (found $NS)"; exit 8; }
for f in out/v3/R6/hwy_ivd30_periodic1tb_none_s1.cmd.json out/v3/R4_bsc/hwy_ivd30_periodic1tb_none_s1.cmd.json \
         out/v3_fb/R4_bsc_fb/hwy_ivd30_periodic1tb_none_s1.cmd.json; do
  [ -f "$f" ] || { echo "missing campaign-v3 command $f (templates of hmiss / timing / psfch)"; exit 8; }
done
MISSING=$(python3 -c "
import os, sys; sys.path.insert(0, 'scripts'); import study
print(*sorted({r['trace'] for p in ('v3_genie', 'psfch_genie') for r in study.plan(p, '1-10') if not os.path.exists(r['trace'])}), sep='\n')")
[ -n "$MISSING" ] && { echo "missing traces:"; echo "$MISSING"; exit 8; }
# preflight: this build and these schedules reproduce the laptop's genie runs bit for bit
if [ "$PREFLIGHT" = 1 ]; then
  [ "$(ls out/genie_preflight_ref/*_run.csv 2>/dev/null | wc -l)" -eq 3 ] || { echo "need out/genie_preflight_ref/*_run.csv (3 files from the laptop)"; exit 8; }
  echo "preflight: R6_genie, 3 routes, 0 cars, seed 1 ..."
  python3 scripts/study.py run --profile v3_genie --seeds 1 --only '^R6_genie/(hwy|rbt|rbtE)_ivd30_periodic1tb_none_s1$' \
    --jobs 3 --out out/genie_preflight > out/genie_preflight.log 2>&1 || { echo "preflight runs FAILED: out/genie_preflight.log"; exit 6; }
  python3 - <<'EOF' || { echo "preflight FAILED: the server does not reproduce the laptop's genie runs"; exit 6; }
import glob, os, sys
import pandas as pd
bad = 0
for ref in sorted(glob.glob('out/genie_preflight_ref/*_run.csv')):
    new = os.path.join('out/genie_preflight/R6_genie', os.path.basename(ref))
    a, b = pd.read_csv(ref), pd.read_csv(new)
    d = [c for c in a.columns if c in b and c not in ('wall_s', 'sel_cpu_us_mean') and str(a[c][0]) != str(b[c][0])]
    print(f"  {os.path.basename(ref)}: {'identical' if not d else 'DIFFERS in ' + ', '.join(d[:8])}")
    bad += bool(d)
sys.exit(1 if bad else 0)
EOF
  echo "preflight passed"
fi
for d in v3_genie helperrx_genie timing_genie psfch_genie psfch_err_genie; do
  mkdir -p "out/$d"; git -C "$REPO" rev-parse HEAD > "out/$d/COMMIT"
done
echo "$(date '+%F %T') launch: stages [$STAGES], $JOBS jobs, commit $(git -C "$REPO" rev-parse --short HEAD)" >> "$LOG"
nohup bash scripts/genie_stages.sh "$JOBS" "$STAGES" >> "$LOG" 2>&1 &
echo "running in background (pid $!): stages [$STAGES], $JOBS jobs. Log: $LOG"
echo "progress: scripts/monitor_genie.sh --watch 300     stop: scripts/launch_genie.sh --stop"
