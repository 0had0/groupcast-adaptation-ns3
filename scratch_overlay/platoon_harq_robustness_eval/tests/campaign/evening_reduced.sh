#!/bin/bash
# Start the reduced profile at a given hour (default 21:00) when the laptop is free.
# Waits, archives the pilot outputs (tests/out -> tests/out_pilot_v4, keeping A03b and traces),
# then runs all stages with sleep disabled.  Usage: evening_reduced.sh [HH:MM] [JOBS]
set -u
START="${1:-21:00}"
JOBS="${2:-8}"
TESTS="$(cd "$(dirname "$0")/.." && pwd)"
target=$(date -j -f "%H:%M" "$START" +%s)
now=$(date +%s)
[ "$target" -le "$now" ] && target=$((target + 86400))
echo "waiting until $(date -r "$target") to start the reduced profile"
while [ "$(date +%s)" -lt "$target" ]; do sleep 60; done
while pgrep -f 'platoon_harq_robustness_eval-default|run_matrix.py|run_tests.sh' > /dev/null; do
    echo "$(date +%H:%M) another simulation or test stage is running; waiting"; sleep 300
done
cd "$TESTS" || exit 2
if [ -d out ] && [ ! -d out_pilot_v4 ]; then
    mv out out_pilot_v4 && mkdir -p out/logs && cp -R out_pilot_v4/A03b out_pilot_v4/traces out/
fi
echo "$(date) starting: run_tests.sh --profile reduced --jobs $JOBS"
env -u MAGIC_HELPER_ID -u NUM_MEMBERS caffeinate -i ./run_tests.sh --profile reduced --jobs "$JOBS" \
    > out/logs/reduced_profile_v4.txt 2>&1
rc=$?
echo "$(date) finished with exit $rc"
tail -45 out/logs/reduced_profile_v4.txt
exit $rc
