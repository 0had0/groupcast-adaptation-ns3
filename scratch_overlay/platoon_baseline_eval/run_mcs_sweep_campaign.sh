#!/usr/bin/env bash
# run_mcs_sweep_campaign.sh
#
# 4-point MCS sweep × 3 scenarios × 10 seeds = 120 runs.
#
#   MCS values : 7  10  12  20
#   Scenarios  : shadowing_blockage | high_density | variable_packet_size
#   Seeds      : 1..10  (default)
#
# Per-run outputs:
#   results_mcs_sweep/<scenario>/<scenario>_mcs<M>_seed<N>.{db,log,_run.csv}
#
# Usage:
#   bash run_mcs_sweep_campaign.sh [--seeds N] [--jobs J] [--simTime S]
#        [--mcs m1,m2,...] [--scenarios s1,s2,...] [--binary PATH]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NS3_ROOT="${REPO_ROOT}/ns-3-dev"
SUMO_DIR="${REPO_ROOT}/sumo_scenarios"
OUT_ROOT="${SCRIPT_DIR}/results_mcs_sweep"

TRACE_SHADOW="${SUMO_DIR}/ccam_platoon_shadowing_blockage/ccam_platoon_shadowing.mob.csv"
TRACE_HD="${SUMO_DIR}/ccam_platoon_high_density_interference/ccam_platoon_hd_interference.mob.csv"
TRACE_VPS="${SUMO_DIR}/ccam_platoon_variable_packet_size/ccam_platoon_vps_variable_packet.mob.csv"

BINARY=""
for prof in debug optimized default; do
    cand="${NS3_ROOT}/build/scratch/platoon_baseline_eval/ns3.42-platoon_baseline_eval-${prof}"
    [[ -x "${cand}" ]] && BINARY="${cand}" && break
done

N_SEEDS=10
SIM_TIME=60
WARMUP=2
PARALLEL_JOBS=$(( $(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 8) / 2 ))
(( PARALLEL_JOBS < 4 )) && PARALLEL_JOBS=4

ALL_MCS=(7 10 12 20)
ALL_SCENARIOS=(shadowing_blockage high_density variable_packet_size)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds)     N_SEEDS="$2";       shift 2 ;;
        --jobs)      PARALLEL_JOBS="$2"; shift 2 ;;
        --simTime)   SIM_TIME="$2";      shift 2 ;;
        --warmup)    WARMUP="$2";        shift 2 ;;
        --mcs)       IFS=',' read -ra ALL_MCS       <<< "$2"; shift 2 ;;
        --scenarios) IFS=',' read -ra ALL_SCENARIOS <<< "$2"; shift 2 ;;
        --binary)    BINARY="$2";        shift 2 ;;
        -h|--help)
            cat <<EOF
Usage: $0 [OPTIONS]

  --seeds N       Seeds per (scenario × MCS) combination (default 10)
  --jobs J        Parallel jobs (default nproc/2, min 4)
  --simTime S     Simulation duration in seconds (default 60)
  --mcs m1,m2,…   MCS values to sweep (default 7,10,12,20)
  --scenarios s,… Comma-separated subset of scenarios
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

scenario_config() {
    case "$1" in
        shadowing_blockage)   echo "$TRACE_SHADOW 10 1 15 6.4" ;;
        high_density)         echo "$TRACE_HD 10 75 4.5 2.0"   ;;
        variable_packet_size) echo "$TRACE_VPS 10 0 4.5 2.0"   ;;
    esac
}
export -f scenario_config
export TRACE_SHADOW TRACE_HD TRACE_VPS

total_runs=$(( ${#ALL_MCS[@]} * ${#ALL_SCENARIOS[@]} * N_SEEDS ))
echo "========================================================================"
echo "  Platoon MCS sweep campaign"
echo "  MCS values:  ${ALL_MCS[*]}"
echo "  Scenarios:   ${ALL_SCENARIOS[*]}"
echo "  Seeds:       ${N_SEEDS}"
echo "  Total runs:  ${total_runs}"
echo "  simTime:     ${SIM_TIME}s  warmup=${WARMUP}s"
echo "  Parallel:    ${PARALLEL_JOBS}"
echo "  Binary:      ${BINARY}"
echo "  Out:         ${OUT_ROOT}"
echo "========================================================================"

run_one() {
    local scenario="$1" mcs="$2" seed="$3"
    local tag="${scenario}_mcs${mcs}_seed${seed}"
    local outdir="${OUT_ROOT}/${scenario}"
    mkdir -p "${outdir}"
    local logfile="${outdir}/${tag}.log"

    local rundir
    rundir=$(mktemp -d)

    read -r trace numUEs numBlockers blockerL blockerW < <(scenario_config "$scenario")

    (cd "${rundir}" && "${BINARY}" \
        --runId="${tag}" \
        --seed="${seed}" \
        --scenario="${scenario}" \
        --trafficClass=periodic2 \
        --mobilityTrace="${trace}" \
        --baseScenario=V2V_Highway \
        --shadowing=true \
        --numUEs="${numUEs}" \
        --numBlockers="${numBlockers}" \
        --blockerL="${blockerL}" \
        --blockerW="${blockerW}" \
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
export BINARY OUT_ROOT SIM_TIME WARMUP

mkdir -p "${OUT_ROOT}"

for mcs in "${ALL_MCS[@]}"; do
    for scenario in "${ALL_SCENARIOS[@]}"; do
        echo ""
        echo "[MCS=${mcs}  ${scenario}] launching ${N_SEEDS} seeds (jobs=${PARALLEL_JOBS}) ..."
        seq 1 "${N_SEEDS}" | xargs -P "${PARALLEL_JOBS}" -I{} bash -c \
            'run_one "$@"' _ "${scenario}" "${mcs}" {}
    done
done

# Concatenate per-run CSVs
ALL_RUNS="${OUT_ROOT}/all_runs.csv"
echo ""
echo "[concat] building ${ALL_RUNS} ..."
first=1
: > "${ALL_RUNS}"
while IFS= read -r -d '' csv; do
    if [[ ${first} -eq 1 ]]; then cat "${csv}" >> "${ALL_RUNS}"; first=0
    else tail -n +2 "${csv}" >> "${ALL_RUNS}"; fi
done < <(find "${OUT_ROOT}" -name '*_run.csv' -print0 | sort -z)

total=$(( $(wc -l < "${ALL_RUNS}") - 1 ))
echo ""
echo "========================================================================"
echo "  Sweep complete.  ${total} runs → ${ALL_RUNS}"
echo "  Next: python3 ${SCRIPT_DIR}/extract_mcs_sweep.py"
echo "========================================================================"
