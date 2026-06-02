#!/usr/bin/env bash
# run_nlosv_model_eval.sh — campaign comparing probabilistic (density-based)
# vs geometric (position-exact) NLOSv determination.
#
# Sweep: 2 traces x 3 density tiers x N seeds.
#   highway_intersection.mob.csv -> baseScenario V2V_Highway
#   intersection_nlosv.mob.csv   -> baseScenario V2V_Urban
#
# Per-run CSVs land in results_nlosv_model_eval/<trace>/<density>/seed<N>.csv
# and are concatenated into results_nlosv_model_eval/all_runs.csv.
#
# Usage:
#   bash scratch_overlay/nlosv_model_eval/run_nlosv_model_eval.sh [--seeds N] [--jobs N] [--draws N]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NS3_ROOT="${REPO_ROOT}/ns-3-dev"
TRACE_DIR="${REPO_ROOT}/experiment_ra_apc/traces"
OUT_ROOT="${SCRIPT_DIR}/results_nlosv_model_eval"

BINARY=""
for prof in optimized debug default; do
    cand="${NS3_ROOT}/build/scratch/nlosv_model_eval/ns3.42-nlosv_model_eval-${prof}"
    [[ -x "${cand}" ]] && BINARY="${cand}" && break
done

N_SEEDS=5
N_DRAWS=2000
PARALLEL_JOBS=$(( $(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 8) / 2 ))
(( PARALLEL_JOBS < 4 )) && PARALLEL_JOBS=4

while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds) N_SEEDS="$2";       shift 2 ;;
        --jobs)  PARALLEL_JOBS="$2"; shift 2 ;;
        --draws) N_DRAWS="$2";       shift 2 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

[[ -n "${BINARY}" && -x "${BINARY}" ]] || {
    echo "ERROR: nlosv_model_eval binary not found under ${NS3_ROOT}/build/scratch/nlosv_model_eval/"
    echo "       build it first:  cd ns-3-dev && ./ns3 build nlosv_model_eval"
    exit 1
}

# trace -> baseScenario
TRACES=(
    "highway_intersection.mob.csv:V2V_Highway"
    "intersection_nlosv.mob.csv:V2V_Urban"
)
DENSITIES=(LOW MEDIUM HIGH)

echo "========================================================"
echo "  NLOSv model comparison — probabilistic vs geometric"
echo "  Seeds:    ${N_SEEDS}"
echo "  Draws:    ${N_DRAWS}"
echo "  Parallel: ${PARALLEL_JOBS}"
echo "  Binary:   ${BINARY}"
echo "  Out:      ${OUT_ROOT}"
echo "========================================================"

run_one() {
    local trace_csv="$1" scenario="$2" density="$3" seed="$4"
    local trace_tag="${trace_csv%.mob.csv}"
    local outdir="${OUT_ROOT}/${trace_tag}/${density}"
    local outcsv="${outdir}/seed${seed}.csv"
    local logfile="${outdir}/seed${seed}.log"
    mkdir -p "${outdir}"

    "${BINARY}" \
        --tracePath="${TRACE_DIR}/${trace_csv}" \
        --baseScenario="${scenario}" \
        --density="${density}" \
        --numDraws="${N_DRAWS}" \
        --sampleEverySec=0.5 \
        --rngSeed=1 \
        --rngRun="${seed}" \
        --outCsv="${outcsv}" \
        > "${logfile}" 2>&1
    echo "    ${trace_tag} ${density} seed=${seed} done"
}
export -f run_one
export BINARY TRACE_DIR OUT_ROOT N_DRAWS

mkdir -p "${OUT_ROOT}"
for entry in "${TRACES[@]}"; do
    trace_csv="${entry%%:*}"
    scenario="${entry##*:}"
    [[ -f "${TRACE_DIR}/${trace_csv}" ]] || { echo "ERROR: trace not found: ${TRACE_DIR}/${trace_csv}"; exit 1; }
    for density in "${DENSITIES[@]}"; do
        echo ""
        echo "[${trace_csv} | ${density}] launching ${N_SEEDS} seeds (jobs=${PARALLEL_JOBS}) ..."
        seq 1 "${N_SEEDS}" | xargs -P "${PARALLEL_JOBS}" -I{} bash -c \
            'run_one "$@"' _ "${trace_csv}" "${scenario}" "${density}" {}
    done
done

# Concatenate every per-seed CSV into one file (header once).
ALL="${OUT_ROOT}/all_runs.csv"
echo ""
echo "[concat] building ${ALL} ..."
first=1
: > "${ALL}"
while IFS= read -r -d '' csv; do
    if [[ ${first} -eq 1 ]]; then
        cat "${csv}" >> "${ALL}"
        first=0
    else
        tail -n +2 "${csv}" >> "${ALL}"
    fi
done < <(find "${OUT_ROOT}" -name 'seed*.csv' -print0 | sort -z)

rows=$(( $(wc -l < "${ALL}") - 1 ))
echo ""
echo "========================================================"
echo "  Campaign complete. ${rows} link-sample rows -> ${ALL}"
echo "  Next: python3 ${SCRIPT_DIR}/analyse_nlosv_model_eval.py"
echo "        python3 ${SCRIPT_DIR}/plot_nlosv_model_eval.py"
echo "========================================================"
