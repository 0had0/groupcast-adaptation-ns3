#!/usr/bin/env bash
# run_traffic_class_compress_campaign.sh
#
# Adaptive application-layer compression on top of the fixed-MCS=14 setup.
# Sweep: COMP_MODES × 5 traffic classes × N seeds.
#
# Per-run outputs land in:
#   results_traffic_class_compress_eval/<mode>/<class>/<class>_seed<N>.{db,log,_run.csv}
#
# Env-tunable:
#   COMP_MODES        space-separated list of {none, fixed_lz4, fixed_zstd1,
#                     fixed_zstd3, adaptive}. Default: "adaptive".
#   COMP_NACK_THR     overrides --compNackHighThr (default 0.10)
#   COMP_DIST_THR     overrides --compDistFarThr  (default 80)
#   COMP_ENT_THR      overrides --compEntropyHighThr (default 0.92)
#   COMP_PDB_SLACK    overrides --compDeadlineSlackMs (default 2.0)
#   COMP_PDB_MS       overrides per-class PDB default

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NS3_ROOT="${REPO_ROOT}/ns-3-dev"
TRACE_CSV="${REPO_ROOT}/experiment_ra_apc/traces/intersection_nlosv.mob.csv"
OUT_ROOT="${SCRIPT_DIR}/results_traffic_class_compress_eval"

BINARY=""
for prof in debug optimized default; do
    cand="${NS3_ROOT}/build/scratch/traffic_class_compress_eval/ns3.42-traffic_class_compress_eval-${prof}"
    [[ -x "${cand}" ]] && BINARY="${cand}" && break
done

N_SEEDS=20
SIM_TIME=60
WARMUP=2
PARALLEL_JOBS=$(( $(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 8) / 2 ))
(( PARALLEL_JOBS < 4 )) && PARALLEL_JOBS=4

CLASSES=(periodic1 periodic2 periodic3 aperiodic1 aperiodic2)
MODES=(${COMP_MODES:-adaptive})

COMP_NACK_THR="${COMP_NACK_THR:-0.10}"
COMP_DIST_THR="${COMP_DIST_THR:-80}"
COMP_ENT_THR="${COMP_ENT_THR:-0.92}"
COMP_PDB_SLACK="${COMP_PDB_SLACK:-2.0}"
COMP_PDB_MS="${COMP_PDB_MS:-0}"   # 0 → per-class default in the binary

while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds)    N_SEEDS="$2";       shift 2 ;;
        --jobs)     PARALLEL_JOBS="$2"; shift 2 ;;
        --simTime)  SIM_TIME="$2";      shift 2 ;;
        --warmup)   WARMUP="$2";        shift 2 ;;
        --classes)  IFS=',' read -ra CLASSES <<< "$2"; shift 2 ;;
        --modes)    IFS=',' read -ra MODES <<< "$2"; shift 2 ;;
        --binary)   BINARY="$2";        shift 2 ;;
        -h|--help)
            cat <<EOF
Usage: $0 [--seeds N] [--jobs J] [--simTime S] [--warmup W]
          [--classes c1,c2,...] [--modes m1,m2,...] [--binary PATH]

  --seeds     Seeds per (mode,class) (default 20)
  --jobs      Parallel run pool (default nproc/2, min 4)
  --simTime   Per-run traffic duration in seconds (default 60)
  --warmup    Warm-up seconds discarded by V2xKpi (default 2)
  --classes   Comma-separated subset of {periodic1..3, aperiodic1..2}
  --modes     Comma-separated subset of {none, fixed_lz4, fixed_zstd1,
              fixed_zstd3, adaptive}. Default: adaptive.
  --binary    Override binary path

Env overrides: COMP_NACK_THR COMP_DIST_THR COMP_ENT_THR
               COMP_PDB_SLACK COMP_PDB_MS
EOF
            exit 0 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

if [[ -z "${BINARY}" || ! -x "${BINARY}" ]]; then
    echo "ERROR: traffic_class_compress_eval binary not found under"
    echo "       ${NS3_ROOT}/build/scratch/traffic_class_compress_eval/"
    echo "       build it first:  cd ns-3-dev && ./ns3 build traffic_class_compress_eval"
    exit 1
fi
[[ -f "${TRACE_CSV}" ]] || { echo "ERROR: trace not found: ${TRACE_CSV}"; exit 1; }

echo "========================================================"
echo "  Adaptive-compression KPI campaign"
echo "  Modes:    ${MODES[*]}"
echo "  Classes:  ${CLASSES[*]}"
echo "  Seeds:    ${N_SEEDS}"
echo "  simTime:  ${SIM_TIME}s (warmup ${WARMUP}s)"
echo "  Parallel: ${PARALLEL_JOBS}"
echo "  Binary:   ${BINARY}"
echo "  Trace:    ${TRACE_CSV}"
echo "  Out:      ${OUT_ROOT}"
echo "  Adaptive: nackThr=${COMP_NACK_THR} distThr=${COMP_DIST_THR}"
echo "            entThr=${COMP_ENT_THR}   slackMs=${COMP_PDB_SLACK}"
echo "            pdbMs=${COMP_PDB_MS} (0=class default)"
echo "========================================================"

run_one() {
    local mode="$1" class="$2" seed="$3"
    local tag="${class}_seed${seed}"
    local outdir="${OUT_ROOT}/${mode}/${class}"
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
        --compMode="${mode}" \
        --compNackHighThr="${COMP_NACK_THR}" \
        --compDistFarThr="${COMP_DIST_THR}" \
        --compEntropyHighThr="${COMP_ENT_THR}" \
        --compDeadlineSlackMs="${COMP_PDB_SLACK}" \
        --compPdbMs="${COMP_PDB_MS}" \
        --outputDir="${outdir}/" \
        --outRunCsv="${outdir}/${tag}_run.csv" \
        --simTag="${tag}" \
        > "${logfile}" 2>&1

    echo "    [${mode}] ${tag} done"
}
export -f run_one
export BINARY TRACE_CSV OUT_ROOT SIM_TIME WARMUP
export COMP_NACK_THR COMP_DIST_THR COMP_ENT_THR COMP_PDB_SLACK COMP_PDB_MS

mkdir -p "${OUT_ROOT}"

for mode in "${MODES[@]}"; do
    for class in "${CLASSES[@]}"; do
        echo ""
        echo "[${mode}/${class}] launching ${N_SEEDS} seeds (jobs=${PARALLEL_JOBS}) ..."
        seq 1 "${N_SEEDS}" | xargs -P "${PARALLEL_JOBS}" -I{} bash -c \
            'run_one "$@"' _ "${mode}" "${class}" {}
    done
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
echo "        jupyter notebook ${SCRIPT_DIR}/comparison.ipynb"
echo "========================================================"
