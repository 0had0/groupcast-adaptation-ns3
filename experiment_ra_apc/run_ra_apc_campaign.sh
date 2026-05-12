#!/usr/bin/env bash
# run_ra_apc_campaign.sh — 4-scheme RA-APC-Groupcast comparison campaign.
#
# Schemes:
#   1. yan_broadcast   MCS=14, bwPRBs=10, no HARQ, no PSFCH         (Jin Yan baseline)
#   2. yan_groupcast   MCS=14, bwPRBs=10, HARQ + PSFCH              (groupcast w/ retx)
#   3. ra_apc_A        Adaptive MCS+N_subch, Strategy A (min)        (worst-case)
#   4. ra_apc_B        Adaptive MCS+N_subch, Strategy B (P5)         (proposed)
#
# All four share:
#   - the composite highway → intersection mobility trace
#   - 9 UEs (1 leader/TX node 0, 8 followers/RX)
#   - 100 kbps CAM @ 20 ms RRI (250 B/packet) on the main platoon flow
#   - 60 s sim time, 2 s warmup
#
# Usage:
#   bash experiment_ra_apc/run_ra_apc_campaign.sh [--seeds N] [--jobs N]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
NS3_ROOT="${REPO_ROOT}/ns-3-dev"
BINARY="${NS3_ROOT}/build/scratch/csi_groupcast_eval/ns3.42-csi_groupcast_eval-optimized"
TRACE_CSV="${SCRIPT_DIR}/traces/highway_intersection.mob.csv"
OUT_ROOT="${SCRIPT_DIR}/results_ra_apc"

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

[[ -x "${BINARY}"   ]] || { echo "ERROR: NS-3 binary not found: ${BINARY}";   exit 1; }
[[ -f "${TRACE_CSV}" ]] || { echo "ERROR: trace not found: ${TRACE_CSV} (run build_composite_trace.py)"; exit 1; }

echo "========================================================"
echo "  RA-APC-Groupcast Campaign"
echo "  Seeds:    ${N_SEEDS}"
echo "  Parallel: ${PARALLEL_JOBS}"
echo "  Trace:    ${TRACE_CSV}"
echo "========================================================"

mkdir -p "${OUT_ROOT}"/yan_broadcast "${OUT_ROOT}"/yan_groupcast \
         "${OUT_ROOT}"/ra_apc_A     "${OUT_ROOT}"/ra_apc_B \
         "${OUT_ROOT}"/nack_rate

FLOW_SPEC="label=platoon;rri_ms=20;rate_kbps=100;tx_nodes=0"

# -----------------------------------------------------------------------
# Helper: run one NS-3 instance.
#   $1 = seed
#   $2 = scheme tag (yan_broadcast | yan_groupcast | ra_apc_A | ra_apc_B | nack_rate)
# -----------------------------------------------------------------------
run_one() {
    local seed="$1"
    local scheme="$2"
    local outdir="${OUT_ROOT}/${scheme}"
    local tag="${scheme}_seed${seed}"
    local logfile="${outdir}/${tag}.log"

    local scheme_args=""
    case "${scheme}" in
        yan_broadcast)
            # Pure broadcast: no HARQ, no PSFCH, fixed MCS=14.
            scheme_args="--mcs=14 --bwPRBs=10 --harq=false --psfchPeriod=0"
            ;;
        yan_groupcast)
            # Groupcast with HARQ + PSFCH at fixed MCS=14.
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
            # NACK-based rate adaptation: fallback to MCS=7 if HARQ feedback shows >20% packet loss.
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
echo "  All ${N_SEEDS} seeds x 5 schemes complete."
echo "  Results: ${OUT_ROOT}/"
echo "  Next: python3 ${SCRIPT_DIR}/analyse_ra_apc.py"
echo "        python3 ${SCRIPT_DIR}/plot_ra_apc.py"
echo "========================================================"
