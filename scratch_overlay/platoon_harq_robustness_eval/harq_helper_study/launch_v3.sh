#!/bin/bash
# Campaign v3 on the remote: the whole study re-run with SCI 1-A on every PSSCH (--sciOnEveryTx=true, every UE,
# TS 38.212 / 38.214: a member that missed the first SCI of a TB can still decode its later copies) and the leader's
# aperiodic traffic on dynamic grants (periodic traffic keeps SB-SPS). Every Kuo arm (R1, R1_bsc, R2, R3, R4,
# R4_bsc, R4.1, R4.2, R5, R6), highway + roundabout (W and E exit, curve window), periodic1tb + aperiodic1tb,
# 0 / 15 / 30 / 45 / 60 cars at the moderate per-car load, 10 seeds (3000 runs). --map: the five map-selection
# arms instead (R4, R4.1, R4.2, R5, R6 with --helperSelect=map, 1500 runs). --psfch: the sl-PSFCH-Period
# sensitivity on the same configuration (R2, R3, R4.2, R6 at period 2 and 4, R1 without PSFCH; 0 and 60 cars,
# 1080 runs; period 1 = out/v3). Run it after v3 (or split the cores with --jobs): both use the same CPUs.
# Needs the NR patch of this commit: run with --setup once after pulling (applies patches/nr/local.patch).
#   1. (optional) update the checkout:   --pull BRANCH
#   2. (optional) setup.sh --force; build the simulator (always, ninja is a no-op when up to date)
#   3. check that every trace exists (the earlier campaigns used them: nothing is generated here)
#   4. smoke: short runs at 15 cars, seed 1 (profile v3_smoke: 60, v3_map_smoke: 30, psfch_smoke: 54) + checks,
#      including "SCI 1-A on every PSSCH" and "leader on dynamic grants" for aperiodic; aborts on any failure
#   5. study.py run in the background (nohup, resumable: re-run this script to continue)
# Usage: launch_v3.sh [--pull BRANCH] [--setup] [--jobs N] [--seeds 1-10] [--out DIR] [--only REGEX] [--map | --psfch]
#                     [--no-smoke]
#          --out DIR     output folder (default out/v3, out/v3_map with --map, out/psfch with --psfch),
#                        log out/<basename DIR>_run.log;
#                        the commit launched is written to DIR/COMMIT
#          --only REGEX  passed to study.py (regex on "arm/tag")
#        launch_v3.sh [--map | --psfch] [--out DIR] --stop
# Progress: ./monitor_study.sh v3 --log out/v3_run.log --watch 300   (--map: v3_map, out/v3_map_run.log;
#           --psfch: psfch, out/psfch_run.log)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
NCPU=$(nproc 2>/dev/null || sysctl -n hw.ncpu)
JOBS=$NCPU; SEEDS=1-10; PULL=""; SETUP=0; STOP=0; SMOKE=1; MAP=0; PSFCH=0; OUT=""; ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --pull) PULL="$2"; shift 2;;
    --setup) SETUP=1; shift;;
    --jobs) JOBS="$2"; shift 2;;
    --seeds) SEEDS="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    --only) ONLY="$2"; shift 2;;
    --map) MAP=1; shift;;
    --psfch) PSFCH=1; shift;;
    --no-smoke) SMOKE=0; shift;;
    --stop) STOP=1; shift;;
    *) echo "unknown option $1"; exit 2;;
  esac
done
[ "$MAP" = 1 ] && [ "$PSFCH" = 1 ] && { echo "--map and --psfch exclude each other"; exit 2; }
PROFILE=v3; [ "$MAP" = 1 ] && PROFILE=v3_map; [ "$PSFCH" = 1 ] && PROFILE=psfch
[ -z "$OUT" ] && OUT=out/${PROFILE}
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
  echo "a $PROFILE study is already running into $OUTABS: launch_v3.sh --out $OUT --stop first"; exit 7
fi
OTHER=$(ps -eo pid,args | grep "[s]tudy.py run --profile " | grep -v -- "--profile $PROFILE " || true)
[ -n "$OTHER" ] && { echo "note: another study is running (its CPUs are shared with this one):"; echo "$OTHER"; }
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
  python3 study.py run --profile ${PROFILE}_smoke --jobs "$JOBS" --out out/${PROFILE}_smoke > out/${PROFILE}_smoke_run.log 2>&1 && \
    python3 study.py check --profile ${PROFILE}_smoke --out out/${PROFILE}_smoke > out/${PROFILE}_smoke_check.log 2>&1
  if [ $? -ne 0 ]; then
    echo "smoke test FAILED: see $HERE/out/${PROFILE}_smoke_run.log and out/${PROFILE}_smoke_check.log"; exit 6
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
