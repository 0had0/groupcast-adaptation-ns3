#!/usr/bin/env bash
# run_slinky_mcs_sweep.sh — Scenario A: CACC Slinky / Accordion
#
# 9-node platoon (leader + 8 followers); gaps oscillate 13–37 m (period 30 s).
# Worst-RX distance cycles 104–296 m → optimal MCS cycles between 14/20 and 7/10.
# 3 complete cycles in 95 s sim.  NO NLOSv blockers (numBlockers=0).
#
# Usage: bash run_slinky_mcs_sweep.sh [--seeds N] [--jobs J] [--mcs m1,m2,…] [--binary PATH]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NS3_ROOT="${REPO_ROOT}/ns-3-dev"
SUMO_DIR="${REPO_ROOT}/sumo_scenarios"
OUT_ROOT="${SCRIPT_DIR}/results_mcs_sweep"

SCENARIO="ccam_platoon_slinky"
TRACE="${SUMO_DIR}/ccam_platoon_slinky/ccam_platoon_slinky.mob.csv"

BINARY=""
for prof in debug optimized default; do
    cand="${NS3_ROOT}/build/scratch/platoon_baseline_eval/ns3.42-platoon_baseline_eval-${prof}"
    [[ -x "${cand}" ]] && BINARY="${cand}" && break
done

N_SEEDS=10
SIM_TIME=95
WARMUP=2
PARALLEL_JOBS=$(( $(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 8) / 2 ))
(( PARALLEL_JOBS < 4 )) && PARALLEL_JOBS=4
ALL_MCS=(7 10 12 14 20)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds)   N_SEEDS="$2";       shift 2 ;;
        --jobs)    PARALLEL_JOBS="$2"; shift 2 ;;
        --simTime) SIM_TIME="$2";      shift 2 ;;
        --mcs)     IFS=',' read -ra ALL_MCS <<< "$2"; shift 2 ;;
        --binary)  BINARY="$2";        shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--seeds N] [--jobs J] [--simTime S] [--mcs m1,m2,…] [--binary PATH]"
            exit 0 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [[ -z "${BINARY}" || ! -x "${BINARY}" ]]; then
    echo "ERROR: platoon_baseline_eval binary not found. Build it first."
    exit 1
fi

[[ -f "${TRACE}" ]] || { echo "ERROR: trace not found: ${TRACE}"; exit 1; }

total_runs=$(( ${#ALL_MCS[@]} * N_SEEDS ))
echo "========================================================================"
echo "  Slinky/Accordion MCS sweep (Scenario A)"
echo "  MCS: ${ALL_MCS[*]}  Seeds: ${N_SEEDS}  Total: ${total_runs}"
echo "  simTime=${SIM_TIME}s  warmup=${WARMUP}s  jobs=${PARALLEL_JOBS}"
echo "  Out: ${OUT_ROOT}/${SCENARIO}/"
echo "========================================================================"

run_one() {
    local mcs="$1" seed="$2"
    local tag="${SCENARIO}_mcs${mcs}_seed${seed}"
    local outdir="${OUT_ROOT}/${SCENARIO}"
    mkdir -p "${outdir}"

    local rundir; rundir=$(mktemp -d)
    (cd "${rundir}" && "${BINARY}" \
        --runId="${tag}" \
        --seed="${seed}" \
        --scenario="${SCENARIO}" \
        --trafficClass=periodic2 \
        --mobilityTrace="${TRACE}" \
        --baseScenario=V2V_Highway \
        --shadowing=true \
        --numUEs=9 \
        --numBlockers=0 \
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
        > "${outdir}/${tag}.log" 2>&1)
    rm -rf "${rundir}"
    echo "    ${tag} done"
}
export -f run_one
export BINARY OUT_ROOT SIM_TIME WARMUP SCENARIO TRACE

outdir="${OUT_ROOT}/${SCENARIO}"; mkdir -p "${outdir}"

for mcs in "${ALL_MCS[@]}"; do
    echo ""
    echo "[MCS=${mcs}  ${SCENARIO}] launching ${N_SEEDS} seeds …"
    seq 1 "${N_SEEDS}" | xargs -P "${PARALLEL_JOBS}" -I{} bash -c 'run_one "$@"' _ "${mcs}" {}
done

ALL_RUNS="${outdir}/all_runs.csv"
echo ""
echo "[concat] building ${ALL_RUNS} ..."
first=1; : > "${ALL_RUNS}"
while IFS= read -r -d '' csv; do
    if [[ ${first} -eq 1 ]]; then cat "${csv}" >> "${ALL_RUNS}"; first=0
    else tail -n +2 "${csv}" >> "${ALL_RUNS}"; fi
done < <(find "${outdir}" -name '*_run.csv' -print0 | sort -z)

total=$(( $(wc -l < "${ALL_RUNS}") - 1 ))
echo "========================================================================"
echo "  Slinky sweep complete.  ${total} runs → ${ALL_RUNS}"
echo "========================================================================"
