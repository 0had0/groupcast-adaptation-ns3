#!/bin/bash
# Full HARQ x Kuo-helper study on a remote machine.
#   1. (optional) update the checkout:   --pull BRANCH   (git fetch + checkout + pull)
#   2. re-assemble contrib/nr from patches/nr/local.patch (setup.sh --force) and build the simulator
#   3. smoke test (12 short runs + invariant checks): aborts the launch on any failure
#   4. full profile in the background (nohup, resumable: re-run this script to continue)
#   5. when it ends: build and execute STUDY_RESULTS_full.ipynb
# Usage: launch_full.sh [--pull BRANCH] [--jobs N] [--seeds 1-5] [--no-setup] [--no-smoke]
#                       [--out DIR] [--only REGEX]
#          --out DIR     output folder (default out/full), log out/<basename DIR>_run.log; the commit
#                        launched is written to DIR/COMMIT
#          --only REGEX  passed to study.py (regex on "arm/tag", e.g. '^(R1|R3|R6)/')
#        launch_full.sh [--out DIR] --stop     # kill the full study writing to DIR and exit
# Restarting is resumable: finished runs whose command is unchanged are skipped; the
# roundabout runs are re-done (their KPI window is now the curve only, see study.curve_window).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
JOBS=8; SEEDS=1-5; PULL=""; SETUP=1; SMOKE=1; STOP=0; OUT=out/full; ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --pull) PULL="$2"; shift 2;;
    --jobs) JOBS="$2"; shift 2;;
    --seeds) SEEDS="$2"; shift 2;;
    --no-setup) SETUP=0; shift;;
    --no-smoke) SMOKE=0; shift;;
    --stop) STOP=1; shift;;
    --out) OUT="$2"; shift 2;;
    --only) ONLY="$2"; shift 2;;
    *) echo "unknown option $1"; exit 2;;
  esac
done
case "$OUT" in /*) OUTABS="$OUT";; *) OUTABS="$HERE/$OUT";; esac
OUTABS="${OUTABS%/}"
LOG="out/$(basename "$OUTABS")_run.log"
ONLYARG=(); [ -n "$ONLY" ] && ONLYARG=(--only "$ONLY")
# processes of this output folder only (study.py and the simulators get its absolute path)
STUDY_PAT="study.py run --profile full .*--out $OUTABS( |\$)"
SIM_PAT="ns3.42-platoon_harq_robustness_eval-.*--out=$OUTABS/"
stop_study() {
  pkill -f "$STUDY_PAT" && echo "stopped study.py ($OUTABS)"
  pkill -f "build_notebook.py --profile full --out $OUTABS"
  pkill -f "$SIM_PAT" && echo "stopped simulator processes"
  sleep 2
  pgrep -fl "$STUDY_PAT|$SIM_PAT" || echo "nothing running for $OUTABS"
}
if [ "$STOP" = 1 ]; then stop_study; exit 0; fi
if pgrep -f "$STUDY_PAT" > /dev/null; then
  echo "a full study is already running into $OUTABS: launch_full.sh --out $OUT --stop first"; exit 7
fi
cd "$REPO" || exit 2
if [ -n "$PULL" ]; then
  git fetch origin && git checkout "$PULL" && git pull --ff-only origin "$PULL" || exit 3
fi
echo "commit $(git rev-parse --short HEAD) ($(git rev-parse --abbrev-ref HEAD))"
if [ "$SETUP" = 1 ]; then
  ./setup.sh --force || exit 4          # applies patches/nr/local.patch to ns-3-dev/contrib/nr
fi
ninja -C ns-3-dev/cmake-cache scratch_platoon_harq_robustness_eval_platoon_harq_robustness_eval || exit 5

# roundaboutE traces (N -> E exit) are git-ignored: generate the missing ones (needs SUMO)
for N in 0 60; do
  for S in $(python3 -c "import sys; sys.path.insert(0, '$HERE'); import study; print(*study.parse_seeds('$SEEDS'))"); do
    T="$HERE/../traces/rbtE_ivd30_n${N}_s${S}.csv.gz"
    [ -f "$T" ] && continue
    python3 "$HERE/../gen_mobility.py" --scenario roundabout --ivd 30 --nbg "$N" --seed "$S" --rbt-exit E \
      --out "$T" > /dev/null 2>&1 && echo "generated $(basename "$T")" || { echo "trace generation failed: $T"; exit 8; }
  done
done

cd "$HERE" || exit 2
mkdir -p out
unset MAGIC_HELPER_ID NUM_MEMBERS
if [ "$SMOKE" = 1 ]; then
  python3 study.py run --profile smoke --jobs "$JOBS" > out/smoke_run.log 2>&1 && \
    python3 study.py check --profile smoke > out/smoke_check.log 2>&1
  if [ $? -ne 0 ]; then
    echo "smoke test FAILED: see $HERE/out/smoke_run.log and out/smoke_check.log"; exit 6
  fi
  echo "smoke test passed"
fi
mkdir -p "$OUTABS"
git -C "$REPO" rev-parse HEAD > "$OUTABS/COMMIT"
echo "starting the full profile (seeds $SEEDS, $JOBS jobs) -> $OUTABS (commit $(cut -c1-12 "$OUTABS/COMMIT"))"
ONLYQ=""; [ -n "$ONLY" ] && ONLYQ="--only $(printf '%q' "$ONLY")"
nohup bash -c "python3 study.py run --profile full --seeds $SEEDS --jobs $JOBS --out $OUTABS $ONLYQ && \
               python3 build_notebook.py --profile full --out $OUTABS" >> "$LOG" 2>&1 &
echo "running in background (pid $!). Progress: tail -f $HERE/$LOG  or  ./monitor_study.sh full --log $LOG"
echo "planned runs: $(python3 study.py list --profile full --seeds "$SEEDS" ${ONLYARG[@]+"${ONLYARG[@]}"} | wc -l)"
