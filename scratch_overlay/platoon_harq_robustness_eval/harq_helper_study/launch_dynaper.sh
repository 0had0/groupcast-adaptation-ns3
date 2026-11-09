#!/bin/bash
# Aperiodic traffic with the leader on dynamic grants, on the remote: every Kuo arm (R1, R1_bsc, R2, R3, R4,
# R4_bsc, R4.1, R4.2, R5, R6), highway + roundabout (W and E exit, curve window), aperiodic1tb only, 0 / 15 / 30 /
# 45 / 60 cars at the moderate per-car load, 10 seeds (1500 runs), plus R4_bsc periodic1tb at 15 / 30 / 45 cars (90 runs,
# SB-SPS as before: full_v2 has R4_bsc at 0 / 60 cars only). Every command equals the full_v2 / density_v2 /
# map_fill_v2 run of the same cell and seed except that --aperiodicRri=20 is dropped: the leader then selects one
# dynamic grant per packet (periodic traffic keeps SB-SPS; it is not re-run).
#   1. (optional) update the checkout:   --pull BRANCH
#   2. (optional) setup.sh --force; build the simulator (always, ninja is a no-op when up to date)
#   3. check that every trace exists (the earlier campaigns used them: nothing is generated here)
#   4. smoke: 33 short runs (profile dynaper_smoke) + invariant checks, including "leader on dynamic grants
#      only"; aborts the launch on any failure
#   5. study.py run --profile dynaper in the background (nohup, resumable: re-run this script to continue)
# Usage: launch_dynaper.sh [--pull BRANCH] [--setup] [--jobs N] [--seeds 1-10] [--out DIR] [--only REGEX] [--no-smoke]
#          --out DIR     output folder (default out/dynaper_v2), log out/<basename DIR>_run.log; the commit
#                        launched is written to DIR/COMMIT
#          --only REGEX  passed to study.py (regex on "arm/tag")
#        launch_dynaper.sh [--out DIR] --stop
# Progress: ./monitor_study.sh dynaper --log out/dynaper_v2_run.log --watch 300
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
NCPU=$(nproc 2>/dev/null || sysctl -n hw.ncpu)
JOBS=$NCPU; SEEDS=1-10; PULL=""; SETUP=0; STOP=0; SMOKE=1; OUT=""; ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --pull) PULL="$2"; shift 2;;
    --setup) SETUP=1; shift;;
    --jobs) JOBS="$2"; shift 2;;
    --seeds) SEEDS="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    --only) ONLY="$2"; shift 2;;
    --no-smoke) SMOKE=0; shift;;
    --stop) STOP=1; shift;;
    *) echo "unknown option $1"; exit 2;;
  esac
done
PROFILE=dynaper
[ -z "$OUT" ] && OUT=out/${PROFILE}_v2
case "$OUT" in /*) OUTABS="$OUT";; *) OUTABS="$HERE/$OUT";; esac
OUTABS="${OUTABS%/}"
LOG="out/$(basename "$OUTABS")_run.log"
ONLYARG=(); [ -n "$ONLY" ] && ONLYARG=(--only "$ONLY")
# processes of this output folder only (study.py and the simulators get its absolute path)
STUDY_PAT="study.py run --profile $PROFILE .*--out $OUTABS( |\$)"
SIM_PAT="ns3.42-platoon_harq_robustness_eval-.*--out=$OUTABS/"
if [ "$STOP" = 1 ]; then
  pkill -f "$STUDY_PAT" && echo "stopped study.py ($OUTABS)"
  pkill -f "$SIM_PAT" && echo "stopped simulator processes"
  sleep 2
  pgrep -fl "$STUDY_PAT|$SIM_PAT" || echo "nothing running for $OUTABS"
  exit 0
fi
if pgrep -f "$STUDY_PAT" > /dev/null; then
  echo "a $PROFILE study is already running into $OUTABS: launch_dynaper.sh --out $OUT --stop first"; exit 7
fi
cd "$REPO" || exit 2
if [ -n "$PULL" ]; then
  git fetch origin && git checkout "$PULL" && git pull --ff-only origin "$PULL" || exit 3
fi
echo "commit $(git rev-parse --short HEAD) ($(git rev-parse --abbrev-ref HEAD))"
git merge-base --is-ancestor stale-ndi-fix HEAD || { echo "HEAD does not contain stale-ndi-fix"; exit 9; }
if [ "$SETUP" = 1 ]; then
  ./setup.sh --force || exit 4          # applies patches/nr/local.patch to ns-3-dev/contrib/nr
fi
ninja -C ns-3-dev/cmake-cache scratch_platoon_harq_robustness_eval_platoon_harq_robustness_eval || exit 5

cd "$HERE" || exit 2
mkdir -p out
unset MAGIC_HELPER_ID NUM_MEMBERS
MISSING=$(python3 -c "
import os, sys; sys.path.insert(0, '$HERE'); import study
print(*sorted({r['trace'] for r in study.plan('$PROFILE', '$SEEDS') if not os.path.exists(r['trace'])}), sep='\n')")
if [ -n "$MISSING" ]; then
  echo "missing traces (generate them with launch_density.sh / launch_full.sh first):"; echo "$MISSING"; exit 8
fi
if [ "$SMOKE" = 1 ]; then
  python3 study.py run --profile dynaper_smoke --jobs "$JOBS" --out out/dynaper_smoke > out/dynaper_smoke_run.log 2>&1 && \
    python3 study.py check --profile dynaper_smoke --out out/dynaper_smoke > out/dynaper_smoke_check.log 2>&1
  if [ $? -ne 0 ]; then
    echo "smoke test FAILED: see $HERE/out/dynaper_smoke_run.log and out/dynaper_smoke_check.log"; exit 6
  fi
  echo "smoke test passed"
fi
mkdir -p "$OUTABS"
git -C "$REPO" rev-parse HEAD > "$OUTABS/COMMIT"
echo "starting profile $PROFILE (seeds $SEEDS, $JOBS jobs) -> $OUTABS (commit $(cut -c1-12 "$OUTABS/COMMIT"))"
ONLYQ=""; [ -n "$ONLY" ] && ONLYQ="--only $(printf '%q' "$ONLY")"
nohup bash -c "python3 study.py run --profile $PROFILE --seeds $SEEDS --jobs $JOBS --out $OUTABS $ONLYQ" \
  >> "$LOG" 2>&1 &
echo "running in background (pid $!). Progress: ./monitor_study.sh $PROFILE --log $LOG --watch 300"
echo "planned runs: $(python3 study.py list --profile "$PROFILE" --seeds "$SEEDS" ${ONLYARG[@]+"${ONLYARG[@]}"} | wc -l)"
