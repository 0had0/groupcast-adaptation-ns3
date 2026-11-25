#!/bin/bash
# Stage runner of launch_genie.sh (run it through launch_genie.sh): genie_stages.sh JOBS "STAGES"
# One stage after the other; each stage's own log is out/genie_<stage>.log, its start / end lines go to stdout
# (out/genie_campaign.log). Every stage is resumable: finished runs are skipped.
set -u
JOBS="$1"; STAGES="$2"
cd "$(dirname "$0")/.." || exit 2   # harq_helper_study/
stage() {
  local name="$1"; shift
  echo "$(date '+%F %T') stage $name: start"
  "$@" >> "out/genie_$name.log" 2>&1
  echo "$(date '+%F %T') stage $name: end (exit $?)"
}
for s in $STAGES; do
  case "$s" in
    load)   stage load bash -c "python3 scripts/study.py run --profile v3_genie --seeds 1-10 \
                --only '^(R4_bsc|R4_bsc_fbs|R6)_genie/' --jobs $JOBS --out out/v3_genie && \
                python3 scripts/study.py check --profile v3_genie --seeds 1-10 \
                --only '^(R4_bsc|R4_bsc_fbs|R6)_genie/' --out out/v3_genie";;
    hmiss)  stage hmiss python3 helper_rx_campaign/campaign.py --set genie --jobs "$JOBS";;
    timing) stage timing bash -c "python3 scripts/timing_sweep.py run --genie --stage A --jobs $JOBS && \
                python3 scripts/timing_sweep.py run --genie --stage B --seeds 1-10 --jobs $JOBS";;
    period) stage period bash -c "python3 scripts/study.py run --profile psfch_genie --seeds 1-10 --jobs $JOBS \
                --out out/psfch_genie && python3 scripts/study.py check --profile psfch_genie --seeds 1-10 \
                --out out/psfch_genie";;
    psfch)  stage psfch python3 scripts/psfch_err_sweep.py run --genie --jobs "$JOBS";;
    *) echo "unknown stage $s";;
  esac
done
echo "$(date '+%F %T') all stages done"
