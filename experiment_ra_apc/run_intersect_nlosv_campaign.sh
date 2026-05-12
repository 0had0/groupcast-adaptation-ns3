#!/usr/bin/env bash
# run_intersect_nlosv_campaign.sh — 5-scheme RA-APC campaign on the SUMO
# intersection_nlosv.mob.csv trace (Z-shaped NLOSv intersection).
#
# Trace geometry:
#   t=0–2.7 s  : platoon approaching corner (pre-NLOSv, LOS)
#   t=2.7–24.4 s: staggered corner crossing — TX already around bend while
#                  tail followers still on straight → NLOSv stress window
#   t=24.4–60 s : all vehicles past corner, LOS restored on new straight
#
# Usage:
#   bash experiment_ra_apc/run_intersect_nlosv_campaign.sh [--seeds N] [--jobs N]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
NS3_ROOT="${REPO_ROOT}/ns-3-dev"
BINARY="${NS3_ROOT}/build/scratch/csi_groupcast_eval/ns3.42-csi_groupcast_eval-optimized"
TRACE_CSV="/Users/hadih/Documents/groupcast-adaptation-ns3/experiment_adaptive_mcs/traces/intersection_nlosv.mob.csv"
OUT_ROOT="${SCRIPT_DIR}/results_intersect_nlosv"

N_SEEDS=30
PARALLEL_JOBS=$(( $(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 8) / 2 ))
(( PARALLEL_JOBS < 4 )) && PARALLEL_JOBS=4

while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds) N_SEEDS="$2";       shift 2 ;;
        --jobs)  PARALLEL_JOBS="$2"; shift 2 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

[[ -x "${BINARY}"    ]] || { echo "ERROR: NS-3 binary not found: ${BINARY}";    exit 1; }
[[ -f "${TRACE_CSV}" ]] || { echo "ERROR: trace not found: ${TRACE_CSV}";        exit 1; }

echo "========================================================"
echo "  RA-APC-Groupcast — intersection_nlosv campaign"
echo "  Seeds:    ${N_SEEDS}"
echo "  Parallel: ${PARALLEL_JOBS}"
echo "  Trace:    ${TRACE_CSV}"
echo "  Out:      ${OUT_ROOT}"
echo "========================================================"

mkdir -p "${OUT_ROOT}"/yan_broadcast "${OUT_ROOT}"/yan_groupcast \
         "${OUT_ROOT}"/ra_apc_A     "${OUT_ROOT}"/ra_apc_B \
         "${OUT_ROOT}"/nack_rate

FLOW_SPEC="label=platoon;rri_ms=20;rate_kbps=100;tx_nodes=0"

run_one() {
    local seed="$1"
    local scheme="$2"
    local outdir="${OUT_ROOT}/${scheme}"
    local tag="${scheme}_seed${seed}"
    local logfile="${outdir}/${tag}.log"

    local scheme_args=""
    case "${scheme}" in
        yan_broadcast)
            scheme_args="--mcs=14 --bwPRBs=10 --harq=false --psfchPeriod=0"
            ;;
        yan_groupcast)
            scheme_args="--mcs=14 --bwPRBs=10 --harq=true --psfchPeriod=4 --maxNumTx=2"
            ;;
        ra_apc_A)
            scheme_args="--mcs=14 --bwPRBs=10 --harq=true --psfchPeriod=4 --maxNumTx=2 \
                         --adaptiveMcs=true --adaptMode=raApcA --etpVariant=avg \
                         --adaptWinMs=500 --adaptEvalMs=500"
            ;;
        ra_apc_B)
            scheme_args="--mcs=14 --bwPRBs=10 --harq=true --psfchPeriod=4 --maxNumTx=2 \
                         --adaptiveMcs=true --adaptMode=raApcB --etpVariant=avg \
                         --adaptWinMs=500 --adaptEvalMs=500"
            ;;
        nack_rate)
            scheme_args="--mcs=14 --bwPRBs=10 --harq=true --psfchPeriod=4 --maxNumTx=2 \
                         --adaptiveMcs=true --adaptMode=nackRate \
                         --adaptWinMs=100 --adaptEvalMs=100 --adaptDownThr=0.05 --adaptUpThr=0.05"
            ;;
        *) echo "Unknown scheme: ${scheme}"; exit 1 ;;
    esac

    # shellcheck disable=SC2086
    "${BINARY}" \
        --runId="${tag}" \
        --seed="${seed}" \
        --numUEs=9 \
        --mu=1 \
        --simTime=60 \
        --warmup=2 \
        --baseScenario=V2V_Urban \
        --shadowing=true \
        --mobilityTrace="${TRACE_CSV}" \
        --blockerL=4.5 \
        --blockerW=2.0 \
        --flows="${FLOW_SPEC}" \
        --outputDir="${outdir}/" \
        --outRunCsv="${outdir}/${tag}_run.csv" \
        --simTag="${tag}" \
        ${scheme_args} \
        > "${logfile}" 2>&1
    echo "    ${scheme} seed=${seed} done → ${outdir}/${tag}.db"
}
export -f run_one
export BINARY TRACE_CSV OUT_ROOT FLOW_SPEC

for scheme in yan_broadcast yan_groupcast ra_apc_A ra_apc_B nack_rate; do
    echo ""
    echo "[${scheme}] launching ${N_SEEDS} seeds (jobs=${PARALLEL_JOBS}) …"
    seq 1 "${N_SEEDS}" | xargs -P "${PARALLEL_JOBS}" -I{} bash -c \
        'run_one "$@"' _ {} "${scheme}"
    echo "[${scheme}] done."
done

echo ""
echo "========================================================"
echo "  All ${N_SEEDS} seeds × 5 schemes complete."
echo "  Results: ${OUT_ROOT}/"
echo "  Next: python3 ${SCRIPT_DIR}/analyse_intersect_nlosv.py"
echo "        python3 ${SCRIPT_DIR}/plot_intersect_nlosv.py"
echo "========================================================"
