#!/bin/bash
# Full HARQ x Kuo-helper study on a remote machine.
#   1. (optional) update the checkout:   --pull BRANCH   (git fetch + checkout + pull)
#   2. re-assemble contrib/nr from patches/nr/local.patch (setup.sh --force) and build the simulator
#   3. smoke test (12 short runs + invariant checks): aborts the launch on any failure
#   4. full profile in the background (nohup, resumable: re-run this script to continue)
#   5. when it ends: build and execute STUDY_RESULTS_full.ipynb
# Usage: launch_full.sh [--pull BRANCH] [--jobs N] [--seeds 1-5] [--no-setup] [--no-smoke]
#        launch_full.sh --stop     # kill a running full study (study.py + simulator processes) and exit
# Restarting is resumable: finished runs whose command is unchanged are skipped; the
# roundabout runs are re-done (their KPI window is now the curve only, see study.curve_window).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
JOBS=8; SEEDS=1-5; PULL=""; SETUP=1; SMOKE=1; STOP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --pull) PULL="$2"; shift 2;;
    --jobs) JOBS="$2"; shift 2;;
    --seeds) SEEDS="$2"; shift 2;;
    --no-setup) SETUP=0; shift;;
    --no-smoke) SMOKE=0; shift;;
    --stop) STOP=1; shift;;
    *) echo "unknown option $1"; exit 2;;
  esac
done
stop_study() {
  pkill -f "study.py run --profile full" && echo "stopped study.py"
  pkill -f "build_notebook.py --profile full"
  pkill -f "ns3.42-platoon_harq_robustness_eval-" && echo "stopped simulator processes"
  sleep 2
  pgrep -fl "ns3.42-platoon_harq_robustness_eval-|study.py run --profile full" || echo "nothing running"
}
if [ "$STOP" = 1 ]; then stop_study; exit 0; fi
if pgrep -f "study.py run --profile full" > /dev/null; then
  echo "a full study is already running: launch_full.sh --stop first"; exit 7
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
echo "starting the full profile (seeds $SEEDS, $JOBS jobs)"
nohup bash -c "python3 study.py run --profile full --seeds $SEEDS --jobs $JOBS && \
               python3 build_notebook.py --profile full" >> out/full_run.log 2>&1 &
echo "running in background (pid $!). Progress: tail -f $HERE/out/full_run.log"
echo "planned runs: $(python3 study.py list --profile full --seeds "$SEEDS" | wc -l)"
