#!/usr/bin/env bash
# run_coop_followup.sh -- cooperative-HARQ follow-up campaign (README "Cooperative HARQ:
# follow-up"). Branch coop-harq-nr, after `./setup.sh --force` and a rebuild of
# platoon_harq_robustness_eval. Every step is resumable (runs with a _run.csv are skipped).
#
#   ./run_coop_followup.sh [step ...]      steps: 1 2 3 3n45 (default: 1 2 3)
#   JOBS=24 ./run_coop_followup.sh 1 2 3
#
# step 1    the 2026-10-11 cells (roundabout IVD 30 N 0, highway IVD 30 N 45 / 60) x
#           periodic1/2 x seeds 1-10, helper 4, into results/coop2_rlc0: split, additive and
#           leaderRes / ownRes again (the leader no longer NACKs relayed copies of its own
#           TB, which had triggered spurious 2nd relays); none is unaffected (no relays) and
#           reused from results/coop_rlc0 of this machine through a symlink
#           -> 240 runs (highway heavy)
# step 2    roundabout footprint test: roundabout IVD 30 N 45 / 60, arms none leaderRes
#           ownRes split additive, --coll-cond (link conditions), helper 4
#           -> results/coop_rbt_rlc0, 200 heavy runs
# step 3    helper sweep: roundabout IVD 30 N 0, helpers 2 4 6 7, arms leaderRes ownRes
#           split additive -> results/coop_sweep_rlc0/h<H>, 320 light runs
# step 3n45 the same at N 45 (only after step 2) -> results/coop_sweep_n45_rlc0/h<H>,
#           320 heavy runs
#
# Analysis (laptop or server):
#   python3 run_coop.py --analyze-only --results results/coop2_rlc0       # step 1
#   python3 run_coop.py --analyze-only --results results/coop_rbt_rlc0    # step 2 arms
#   python3 analyze_coop_followup.py footprint                            # step 2
#   python3 analyze_coop_followup.py sweep                                # step 3
set -euo pipefail
cd "$(dirname "$0")"
JOBS=${JOBS:-24}
STEPS=("$@")
[ ${#STEPS[@]} -eq 0 ] && STEPS=(1 2 3)
ARMS_STEP1="none leaderRes ownRes split additive"
ARMS_ALL="none leaderRes ownRes split additive"
ARMS_SWEEP="leaderRes ownRes split additive"

for step in "${STEPS[@]}"; do
  echo "=== step $step ($(date +%H:%M:%S))"
  case "$step" in
    1)
      mkdir -p results/coop2_rlc0
      if [ -d results/coop_rlc0/none ] && [ ! -e results/coop2_rlc0/none ]; then
        ln -s ../coop_rlc0/none results/coop2_rlc0/none   # none: no relays, unchanged
      fi
      python3 run_coop.py --arms $ARMS_STEP1 --jobs "$JOBS" --results results/coop2_rlc0
      python3 run_coop.py --analyze-only --results results/coop2_rlc0
      ;;
    2)
      python3 run_coop.py --cells roundabout:45 roundabout:60 --arms $ARMS_ALL --coll-cond \
          --jobs "$JOBS" --results results/coop_rbt_rlc0
      python3 run_coop.py --analyze-only --cells roundabout:45 roundabout:60 \
          --results results/coop_rbt_rlc0
      python3 analyze_coop_followup.py footprint --rbt results/coop_rbt_rlc0 \
          --hwy results/coop2_rlc0
      ;;
    3)
      for H in 2 4 6 7; do
        python3 run_coop.py --cells roundabout:0 --arms $ARMS_SWEEP --helper "$H" \
            --jobs "$JOBS" --results "results/coop_sweep_rlc0/h$H"
      done
      python3 analyze_coop_followup.py sweep --sweep results/coop_sweep_rlc0 \
          --base results/coop2_rlc0
      ;;
    3n45)
      for H in 2 4 6 7; do
        python3 run_coop.py --cells roundabout:45 --arms $ARMS_SWEEP --helper "$H" \
            --jobs "$JOBS" --results "results/coop_sweep_n45_rlc0/h$H"
      done
      python3 analyze_coop_followup.py sweep --sweep results/coop_sweep_n45_rlc0 \
          --base results/coop_rbt_rlc0
      ;;
    *) echo "unknown step $step"; exit 1 ;;
  esac
done
echo "=== done ($(date +%H:%M:%S))"
