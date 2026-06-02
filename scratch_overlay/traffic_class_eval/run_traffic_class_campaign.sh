#!/usr/bin/env bash
# run_traffic_class_campaign.sh — per-RX KPI characterisation across
# TrafficClass profiles on the intersection_nlosv platoon trace.
#
# Sweep: 5 traffic classes × N seeds (default 20).
#   periodic1, periodic2, periodic3, aperiodic1, aperiodic2.
#
# Per-run outputs land in:
#   results_traffic_class_eval/<class>/<class>_seed<N>.db
#   results_traffic_class_eval/<class>/<class>_seed<N>_run.csv
#   results_traffic_class_eval/<class>/<class>_seed<N>.log
#
# Supports --rri flag to override TrafficClass-default Resource Reservation Interval.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NS3_ROOT="${REPO_ROOT}/ns-3-dev"
TRACE_CSV="${REPO_ROOT}/experiment_ra_apc/traces/intersection_nlosv.mob.csv"
OUT_ROOT="${SCRIPT_DIR}/results_traffic_class_eval"

# Pick debug then optimized binary (prefer freshly-built debug over stale optimized)
BINARY=""
for prof in debug optimized default; do
    cand="${NS3_ROOT}/build/scratch/traffic_class_eval/ns3.42-traffic_class_eval-${prof}"
    [[ -x "${cand}" ]] && BINARY="${cand}" && break
done

# Default RRI override (0 means use TrafficClass default)
RRI_OVERRIDE=0
N_SEEDS=20
SIM_TIME=60
WARMUP=2
PARALLEL_JOBS=$(( $(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 8) / 2 ))
(( PARALLEL_JOBS < 4 )) && PARALLEL_JOBS=4

CLASSES=(periodic1 periodic2 periodic3 aperiodic1 aperiodic2)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds)    N_SEEDS="$2";       shift 2 ;;
        --jobs)     PARALLEL_JOBS="$2"; shift 2 ;;
        --simTime)  SIM_TIME="$2";      shift 2 ;;
        --warmup)   WARMUP="$2";        shift 2 ;;
        --classes)  IFS=',' read -ra CLASSES <<< "$2"; shift 2 ;;
        --binary)   BINARY="$2";        shift 2 ;;
        -h|--help)
            cat <<EOF
Usage: $0 [--seeds N] [--jobs J] [--simTime S] [--warmup W]
          [--classes c1,c2,...] [--rri N] [--binary PATH]

  --seeds     Seeds per traffic class (default 20)
  --jobs      Parallel run pool (default nproc/2, min 4)
  --simTime   Per-run traffic duration in seconds (default 60)
  --warmup    Warm-up seconds discarded by V2xKpi (default 2)
  --classes   Comma-separated subset of {periodic1..3,aperiodic1..2}
  --binary    Override binary path
EOF
            exit 0 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

if [[ -z "${BINARY}" || ! -x "${BINARY}" ]]; then
    echo "ERROR: traffic_class_eval binary not found under ${NS3_ROOT}/build/scratch/traffic_class_eval/"
    echo "       build it first:  cd ns-3-dev && ./ns3 build traffic_class_eval"
    exit 1
fi
[[ -f "${TRACE_CSV}" ]] || { echo "ERROR: trace not found: ${TRACE_CSV}"; exit 1; }

echo "========================================================"
echo "  Traffic-class KPI campaign"
echo "  Classes:  ${CLASSES[*]}"
echo "  Seeds:    ${N_SEEDS}"
echo "  simTime:  ${SIM_TIME}s (warmup ${WARMUP}s)"
echo "  Parallel: ${PARALLEL_JOBS}"
echo "  Binary:   ${BINARY}"
echo "  Trace:    ${TRACE_CSV}"
echo "  Out:      ${OUT_ROOT}"
echo "========================================================"

run_one() {
    local class="$1" seed="$2"
    local tag="${class}_seed${seed}"
    local outdir="${OUT_ROOT}/${class}"
    mkdir -p "${outdir}"

    local logfile="${outdir}/${tag}.log"

    "${BINARY}" \
        --runId="${tag}" \
        --seed="${seed}" \
        --trafficClass="${class}" \
        --mobilityTrace="${TRACE_CSV}" \
        --baseScenario=V2V_Highway \
        --shadowing=true \
        --numUEs=9 \
        --simTime="${SIM_TIME}" \
        --warmup="${WARMUP}" \
        --mcs=14 \
        --bwPRBs=10 \
        --harq=true \
        --psfchPeriod=4 \
        --maxNumTx=2 \
        --outputDir="${outdir}/" \
        --outRunCsv="${outdir}/${tag}_run.csv" \
        --simTag="${tag}" \
        > "${logfile}" 2>&1

    echo "    ${tag} done"
}
export -f run_one
export BINARY TRACE_CSV OUT_ROOT SIM_TIME WARMUP

mkdir -p "${OUT_ROOT}"

for class in "${CLASSES[@]}"; do
    echo ""
    echo "[${class}] launching ${N_SEEDS} seeds (jobs=${PARALLEL_JOBS}) ..."
    seq 1 "${N_SEEDS}" | xargs -P "${PARALLEL_JOBS}" -I{} bash -c \
        'run_one "$@"' _ "${class}" {}
done

# Concatenate per-run summary CSVs
ALL_RUNS="${OUT_ROOT}/all_runs.csv"
echo ""
echo "[concat] building ${ALL_RUNS} ..."
first=1
: > "${ALL_RUNS}"
while IFS= read -r -d '' csv; do
    if [[ ${first} -eq 1 ]]; then
        cat "${csv}" >> "${ALL_RUNS}"
        first=0
    else
        tail -n +2 "${csv}" >> "${ALL_RUNS}"
    fi
done < <(find "${OUT_ROOT}" -name '*_run.csv' -print0 | sort -z)

total=$(( $(wc -l < "${ALL_RUNS}") - 1 ))
echo ""
echo "========================================================"
echo "  Campaign complete. ${total} runs → ${ALL_RUNS}"
echo "  Next: python3 ${SCRIPT_DIR}/extract_kpis.py"
echo "        jupyter notebook ${SCRIPT_DIR}/evaluation.ipynb"
echo "========================================================"
