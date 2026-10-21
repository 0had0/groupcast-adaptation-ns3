#!/bin/bash
# Full HARQ x Kuo-helper study on a remote machine.
#   1. (optional) update the checkout:   --pull BRANCH   (git fetch + checkout + pull)
#   2. re-assemble contrib/nr from patches/nr/local.patch (setup.sh --force) and build the simulator
#   3. smoke test (12 short runs + invariant checks): aborts the launch on any failure
#   4. full profile in the background (nohup, resumable: re-run this script to continue)
#   5. when it ends: build and execute STUDY_RESULTS_full.ipynb
# Usage: launch_full.sh [--pull BRANCH] [--jobs N] [--seeds 1-5] [--no-setup]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
JOBS=8; SEEDS=1-5; PULL=""; SETUP=1
while [ $# -gt 0 ]; do
  case "$1" in
    --pull) PULL="$2"; shift 2;;
    --jobs) JOBS="$2"; shift 2;;
    --seeds) SEEDS="$2"; shift 2;;
    --no-setup) SETUP=0; shift;;
    *) echo "unknown option $1"; exit 2;;
  esac
done
cd "$REPO" || exit 2
if [ -n "$PULL" ]; then
  git fetch origin && git checkout "$PULL" && git pull --ff-only origin "$PULL" || exit 3
fi
echo "commit $(git rev-parse --short HEAD) ($(git rev-parse --abbrev-ref HEAD))"
if [ "$SETUP" = 1 ]; then
  ./setup.sh --force || exit 4          # applies patches/nr/local.patch to ns-3-dev/contrib/nr
fi
ninja -C ns-3-dev/cmake-cache scratch_platoon_harq_robustness_eval_platoon_harq_robustness_eval || exit 5

cd "$HERE" || exit 2
mkdir -p out
unset MAGIC_HELPER_ID NUM_MEMBERS
python3 study.py run --profile smoke --jobs "$JOBS" > out/smoke_run.log 2>&1 && \
  python3 study.py check --profile smoke > out/smoke_check.log 2>&1
if [ $? -ne 0 ]; then
  echo "smoke test FAILED: see $HERE/out/smoke_run.log and out/smoke_check.log"; exit 6
fi
echo "smoke test passed; starting the full profile (seeds $SEEDS, $JOBS jobs)"
nohup bash -c "python3 study.py run --profile full --seeds $SEEDS --jobs $JOBS && \
               python3 build_notebook.py --profile full" > out/full_run.log 2>&1 &
echo "running in background (pid $!). Progress: tail -f $HERE/out/full_run.log"
echo "planned runs: $(python3 study.py list --profile full --seeds "$SEEDS" | wc -l)"
