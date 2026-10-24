#!/bin/bash
# Density sensitivity sweep on the remote: arms R1 / R3 / R6, background 15 / 30 / 45 cars at the
# moderate per-car load (1.67 flows / car), highway + roundabout (W and E exit, curve window),
# periodic1tb + aperiodic1tb, 10 seeds (540 runs). 0 and 60 cars come from out/full (none / moderate).
#   1. (optional) update the checkout:   --pull BRANCH
#   2. (optional) setup.sh --force; build the simulator (always, ninja is a no-op when up to date)
#   3. generate the missing ivd30 n15 / n30 / n45 traces (hwy, rbt, rbtE; needs SUMO)
#   4. study.py run --profile density in the background (nohup, resumable), then the notebook
# Usage: launch_density.sh [--pull BRANCH] [--jobs N] [--seeds 1-10] [--setup] [--gen-jobs N]
#        launch_density.sh --stop
# Progress: ./monitor_study.sh density
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
EVAL="$HERE/.."
JOBS=8; SEEDS=1-10; PULL=""; SETUP=0; STOP=0; GENJOBS=4
while [ $# -gt 0 ]; do
  case "$1" in
    --pull) PULL="$2"; shift 2;;
    --jobs) JOBS="$2"; shift 2;;
    --seeds) SEEDS="$2"; shift 2;;
    --setup) SETUP=1; shift;;
    --gen-jobs) GENJOBS="$2"; shift 2;;
    --stop) STOP=1; shift;;
    *) echo "unknown option $1"; exit 2;;
  esac
done
if [ "$STOP" = 1 ]; then
  pkill -f "study.py run --profile density" && echo "stopped study.py"
  pkill -f "build_density_notebook.py"
  pkill -f "ns3.42-platoon_harq_robustness_eval-.*out/density/" && echo "stopped simulator processes"
  sleep 2
  pgrep -fl "study.py run --profile density" || echo "nothing running"
  exit 0
fi
if pgrep -f "study.py run --profile density" > /dev/null; then
  echo "a density sweep is already running: launch_density.sh --stop first"; exit 7
fi

cd "$REPO" || exit 2
if [ -n "$PULL" ]; then
  git fetch origin && git checkout "$PULL" && git pull --ff-only origin "$PULL" || exit 3
fi
echo "commit $(git rev-parse --short HEAD) ($(git rev-parse --abbrev-ref HEAD))"
if [ "$SETUP" = 1 ]; then
  ./setup.sh --force || exit 4
fi
ninja -C ns-3-dev/cmake-cache scratch_platoon_harq_robustness_eval_platoon_harq_robustness_eval || exit 5

mkdir -p "$HERE/out"
# ---- traces (git-ignored): generate the missing ones, GENJOBS in parallel
SEEDLIST=$(python3 -c "import sys; sys.path.insert(0, '$HERE'); import study; print(*study.parse_seeds('$SEEDS'))")
TODO=()
for N in 15 30 45; do
  for S in $SEEDLIST; do
    for P in hwy rbt rbtE; do
      T="$EVAL/traces/${P}_ivd30_n${N}_s${S}.csv.gz"
      [ -f "$T" ] && continue
      case $P in
        hwy)  A="--scenario highway";;
        rbt)  A="--scenario roundabout";;
        rbtE) A="--scenario roundabout --rbt-exit E";;
      esac
      TODO+=("python3 $EVAL/gen_mobility.py $A --ivd 30 --nbg $N --seed $S --out $T")
    done
  done
done
echo "traces to generate: ${#TODO[@]}"
if [ ${#TODO[@]} -gt 0 ]; then
  printf '%s\n' "${TODO[@]}" | xargs -P "$GENJOBS" -I{} sh -c '{} > /dev/null 2>&1 || echo "FAILED: {}"' \
    | tee "$HERE/out/density_traces.log"
  grep -q FAILED "$HERE/out/density_traces.log" 2>/dev/null && { echo "trace generation failed (see out/density_traces.log)"; exit 8; }
fi

cd "$HERE" || exit 2
mkdir -p out
unset MAGIC_HELPER_ID NUM_MEMBERS
echo "planned runs: $(python3 study.py list --profile density --seeds "$SEEDS" | wc -l)"
nohup bash -c "python3 study.py run --profile density --seeds $SEEDS --jobs $JOBS && \
               python3 build_density_notebook.py --full-dir out/full --density-dir out/density" \
      >> out/density_run.log 2>&1 &
echo "running in background (pid $!). Progress: ./monitor_study.sh density"
