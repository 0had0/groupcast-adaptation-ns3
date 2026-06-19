#!/usr/bin/env bash
# run_platoon_baseline_campaign.sh — fixed-MCS-14 baseline across three SUMO platoon scenarios.
#
# Sweep: 3 scenarios × 30 seeds (default) = 90 runs.
#
#   Scenario configs
#   ─────────────────────────────────────────────────────────────────────────
#   shadowing_blockage     : 10 V2X platoon UEs + 1 NLOSv truck
#                            blockerL=15, blockerW=6.4  (truck AABB reaches lane 0)
#   high_density           : 10 V2X platoon UEs + 75 NLOSv background vehicles
#                            blockerL=4.5, blockerW=2.0
#   variable_packet_size   : 10 V2X platoon UEs, no extra obstacles
#
#   All scenarios: periodic2, MCS=14, 10 PRBs, psfchPeriod=4, maxNumTx=3, 60 s
#
# Per-run outputs:
#   results_platoon_baseline/<scenario>/<scenario>_seed<N>.db
#   results_platoon_baseline/<scenario>/<scenario>_seed<N>_run.csv
#   results_platoon_baseline/<scenario>/<scenario>_seed<N>.log
#
# Usage:
#   bash run_platoon_baseline_campaign.sh [--seeds N] [--jobs J] [--simTime S]
#        [--scenarios s1,s2,...] [--binary PATH]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NS3_ROOT="${REPO_ROOT}/ns-3-dev"
SUMO_DIR="${REPO_ROOT}/sumo_scenarios"
OUT_ROOT="${SCRIPT_DIR}/results_platoon_baseline"

# ── Mob CSV paths ────────────────────────────────────────────────────────────
TRACE_SHADOW="${SUMO_DIR}/ccam_platoon_shadowing_blockage/ccam_platoon_shadowing.mob.csv"
TRACE_HD="${SUMO_DIR}/ccam_platoon_high_density_interference/ccam_platoon_hd_interference.mob.csv"
TRACE_VPS="${SUMO_DIR}/ccam_platoon_variable_packet_size/ccam_platoon_vps_variable_packet.mob.csv"

# ── Binary auto-detect ───────────────────────────────────────────────────────
BINARY=""
for prof in debug optimized default; do
    cand="${NS3_ROOT}/build/scratch/platoon_baseline_eval/ns3.42-platoon_baseline_eval-${prof}"
    [[ -x "${cand}" ]] && BINARY="${cand}" && break
done

# ── Defaults ─────────────────────────────────────────────────────────────────
N_SEEDS=30
SIM_TIME=60
WARMUP=2
PARALLEL_JOBS=$(( $(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 8) / 2 ))
(( PARALLEL_JOBS < 4 )) && PARALLEL_JOBS=4

ALL_SCENARIOS=(shadowing_blockage high_density variable_packet_size)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds)     N_SEEDS="$2";       shift 2 ;;
        --jobs)      PARALLEL_JOBS="$2"; shift 2 ;;
        --simTime)   SIM_TIME="$2";      shift 2 ;;
        --warmup)    WARMUP="$2";        shift 2 ;;
        --scenarios) IFS=',' read -ra ALL_SCENARIOS <<< "$2"; shift 2 ;;
        --binary)    BINARY="$2";        shift 2 ;;
        -h|--help)
            cat <<EOF
Usage: $0 [OPTIONS]

  --seeds N       Seeds per scenario (default 30)
  --jobs J        Parallel jobs (default nproc/2, min 4)
  --simTime S     Per-run traffic duration in seconds (default 60)
  --warmup W      Warm-up seconds discarded by V2xKpi (default 2)
  --scenarios s,… Comma-separated subset of {shadowing_blockage,
                   high_density,variable_packet_size}
  --binary PATH   Override binary path
EOF
            exit 0 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# ── Pre-flight ────────────────────────────────────────────────────────────────
if [[ -z "${BINARY}" || ! -x "${BINARY}" ]]; then
    echo "ERROR: platoon_baseline_eval binary not found."
    echo "       Build it first:  cd ns-3-dev && ./ns3 build platoon_baseline_eval"
    exit 1
fi

for scenario in "${ALL_SCENARIOS[@]}"; do
    case "$scenario" in
        shadowing_blockage)   trace="$TRACE_SHADOW" ;;
        high_density)         trace="$TRACE_HD" ;;
        variable_packet_size) trace="$TRACE_VPS" ;;
        *) echo "ERROR: unknown scenario '$scenario'"; exit 1 ;;
    esac
    if [[ ! -f "$trace" ]]; then
        echo "ERROR: mobility trace not found for '$scenario':"
        echo "       $trace"
        echo "       Run the corresponding SUMO simulation first:"
        echo "         bash ${SUMO_DIR}/${scenario%%_*}*/run_simulation.sh"
        exit 1
    fi
done

echo "========================================================================"
echo "  Platoon baseline campaign (fixed MCS=14, periodic2)"
echo "  Scenarios: ${ALL_SCENARIOS[*]}"
echo "  Seeds:     ${N_SEEDS}"
echo "  simTime:   ${SIM_TIME}s  warmup=${WARMUP}s"
echo "  Parallel:  ${PARALLEL_JOBS}"
echo "  Binary:    ${BINARY}"
echo "  Out:       ${OUT_ROOT}"
echo "========================================================================"

# ── Per-scenario config ───────────────────────────────────────────────────────
# Returns: trace numUEs numBlockers blockerL blockerW
scenario_config() {
    local scen="$1"
    case "$scen" in
        shadowing_blockage)
            echo "$TRACE_SHADOW 10 1 15 6.4"
            ;;
        high_density)
            echo "$TRACE_HD 10 75 4.5 2.0"
            ;;
        variable_packet_size)
            echo "$TRACE_VPS 10 0 4.5 2.0"
            ;;
    esac
}
export -f scenario_config
export TRACE_SHADOW TRACE_HD TRACE_VPS

# ── run_one ──────────────────────────────────────────────────────────────────
run_one() {
    local scenario="$1" seed="$2"
    local tag="${scenario}_seed${seed}"
    local outdir="${OUT_ROOT}/${scenario}"
    mkdir -p "${outdir}"
    local logfile="${outdir}/${tag}.log"

    # Run each instance from a unique per-run tmpdir so that ns-3's global
    # side-effect files (NrDlMacStats.txt, NrUlMacStats.txt) don't collide
    # across parallel jobs sharing the same CWD.
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
        --mcs=14 \
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

for scenario in "${ALL_SCENARIOS[@]}"; do
    echo ""
    echo "[${scenario}] launching ${N_SEEDS} seeds (jobs=${PARALLEL_JOBS}) ..."
    seq 1 "${N_SEEDS}" | xargs -P "${PARALLEL_JOBS}" -I{} bash -c \
        'run_one "$@"' _ "${scenario}" {}
done

# ── Concatenate per-run CSVs ──────────────────────────────────────────────────
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
echo "========================================================================"
echo "  Campaign complete.  ${total} runs → ${ALL_RUNS}"
echo "  Next: python3 ${SCRIPT_DIR}/extract_kpis.py"
echo "        jupyter notebook ${SCRIPT_DIR}/evaluation.ipynb"
echo "========================================================================"
