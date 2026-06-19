#!/usr/bin/env bash
# run_overtake_mcs_sweep.sh
#
# MCS sweep for the "Truck Overtake" scenario:
#   MCS values : 7  10  12  14  20   (14 included so no separate baseline run needed)
#   Seeds      : 1..10  (default)
#
# Platoon:  5 V2X nodes (0 = TX leader, 1–4 followers)
# Blocker:  1 NLOSv truck (node 5), L=15 m, W=6.4 m (extends to platoon lane)
# simTime:  62 s  (trace covers 62 s with 0.1 s resolution)
#
# Predicted phase transitions (from gen_overtake_trace.py):
#   t =  0 – 25.0 s : Phase 1 — CLEAR  (truck ahead of TX)     → MCS=14
#   t = 25.0 – 46.7 s : Phase 2 — BLOCKAGE (truck in column)   → MCS=7
#   t = 46.7 – 60 s : Phase 3 — CLEAR  (truck behind all)      → MCS=14
#
# Outputs:
#   results_mcs_sweep/ccam_platoon_overtake/
#     ccam_platoon_overtake_mcs<M>_seed<N>.{db,log,_run.csv}
#
# Usage:
#   bash run_overtake_mcs_sweep.sh [--seeds N] [--jobs J] [--simTime S]
#        [--mcs m1,m2,...] [--binary PATH]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NS3_ROOT="${REPO_ROOT}/ns-3-dev"
SUMO_DIR="${REPO_ROOT}/sumo_scenarios"
OUT_ROOT="${SCRIPT_DIR}/results_mcs_sweep"

TRACE="${SUMO_DIR}/ccam_platoon_overtake/ccam_platoon_overtake.mob.csv"

BINARY=""
for prof in debug optimized default; do
    cand="${NS3_ROOT}/build/scratch/platoon_baseline_eval/ns3.42-platoon_baseline_eval-${prof}"
    [[ -x "${cand}" ]] && BINARY="${cand}" && break
done

SCENARIO="ccam_platoon_overtake"
N_SEEDS=10
SIM_TIME=80
WARMUP=2
PARALLEL_JOBS=$(( $(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 8) / 2 ))
(( PARALLEL_JOBS < 4 )) && PARALLEL_JOBS=4

# Include MCS=14 so no separate baseline run is needed for this scenario
ALL_MCS=(7 10 12 14 20)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds)   N_SEEDS="$2";       shift 2 ;;
        --jobs)    PARALLEL_JOBS="$2"; shift 2 ;;
        --simTime) SIM_TIME="$2";      shift 2 ;;
        --warmup)  WARMUP="$2";        shift 2 ;;
        --mcs)     IFS=',' read -ra ALL_MCS <<< "$2"; shift 2 ;;
        --binary)  BINARY="$2";        shift 2 ;;
        -h|--help)
            cat <<EOF
Usage: $0 [OPTIONS]

  --seeds N       Seeds per MCS value (default 10)
  --jobs J        Parallel jobs (default nproc/2, min 4)
  --simTime S     Simulation duration in seconds (default 62)
  --mcs m1,m2,…   MCS values (default 7,10,12,14,20)
  --binary PATH   Override binary path
EOF
            exit 0 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

if [[ -z "${BINARY}" || ! -x "${BINARY}" ]]; then
    echo "ERROR: platoon_baseline_eval binary not found."
    echo "       Build it: cd ns-3-dev && ./ns3 build platoon_baseline_eval"
    exit 1
fi

if [[ ! -f "${TRACE}" ]]; then
    echo "ERROR: Mobility trace not found: ${TRACE}"
    echo "       Run: python3 ${SUMO_DIR}/ccam_platoon_overtake/gen_overtake_trace.py"
    exit 1
fi

total_runs=$(( ${#ALL_MCS[@]} * N_SEEDS ))
echo "========================================================================"
echo "  Truck Overtake MCS sweep"
echo "  Trace:       ${TRACE}"
echo "  MCS values:  ${ALL_MCS[*]}"
echo "  Seeds:       ${N_SEEDS}"
echo "  Total runs:  ${total_runs}"
echo "  simTime:     ${SIM_TIME}s  warmup=${WARMUP}s"
echo "  Parallel:    ${PARALLEL_JOBS}"
echo "  Binary:      ${BINARY}"
echo "  Out:         ${OUT_ROOT}/${SCENARIO}/"
echo "========================================================================"

run_one() {
    local mcs="$1" seed="$2"
    local tag="${SCENARIO}_mcs${mcs}_seed${seed}"
    local outdir="${OUT_ROOT}/${SCENARIO}"
    mkdir -p "${outdir}"
    local logfile="${outdir}/${tag}.log"

    local rundir
    rundir=$(mktemp -d)

    (cd "${rundir}" && "${BINARY}" \
        --runId="${tag}" \
        --seed="${seed}" \
        --scenario="${SCENARIO}" \
        --trafficClass=periodic2 \
        --mobilityTrace="${TRACE}" \
        --baseScenario=V2V_Highway \
        --shadowing=true \
        --numUEs=9 \
        --numBlockers=1 \
        --blockerL=15 \
        --blockerW=6.4 \
        --simTime="${SIM_TIME}" \
        --warmup="${WARMUP}" \
        --mcs="${mcs}" \
        --bwPRBs=10 \
        --harq=true \
        --psfchPeriod=4 \
        --maxNumTx=3 \
        --outputDir="${outdir}/" \
        --outRunCsv="${outdir}/${tag}_run.csv" \
        --simTag="${tag}" \
        > "${logfile}" 2>&1)

    rm -rf "${rundir}"
    echo "    ${tag} done"
}
export -f run_one
export BINARY OUT_ROOT SIM_TIME WARMUP SCENARIO TRACE

outdir="${OUT_ROOT}/${SCENARIO}"
mkdir -p "${outdir}"

for mcs in "${ALL_MCS[@]}"; do
    echo ""
    echo "[MCS=${mcs}  ${SCENARIO}] launching ${N_SEEDS} seeds (jobs=${PARALLEL_JOBS}) ..."
    seq 1 "${N_SEEDS}" | xargs -P "${PARALLEL_JOBS}" -I{} bash -c \
        'run_one "$@"' _ "${mcs}" {}
done

# Concatenate per-run CSVs
ALL_RUNS="${outdir}/all_runs.csv"
echo ""
echo "[concat] building ${ALL_RUNS} ..."
first=1
: > "${ALL_RUNS}"
while IFS= read -r -d '' csv; do
    if [[ ${first} -eq 1 ]]; then cat "${csv}" >> "${ALL_RUNS}"; first=0
    else tail -n +2 "${csv}" >> "${ALL_RUNS}"; fi
done < <(find "${outdir}" -name '*_run.csv' -print0 | sort -z)

total=$(( $(wc -l < "${ALL_RUNS}") - 1 ))
echo ""
echo "========================================================================"
echo "  Overtake sweep complete.  ${total} runs → ${ALL_RUNS}"
echo "  Next: python3 ${SCRIPT_DIR}/analyse_mcs_timeseries.py"
echo "========================================================================"
