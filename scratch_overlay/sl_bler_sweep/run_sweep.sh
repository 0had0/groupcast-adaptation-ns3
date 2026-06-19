#!/usr/bin/env bash
# run_sweep.sh
#
# NR 5G Sidelink groupcast BLER-vs-SNR sweep.
# Sweeps: MCS × distance × channel_condition × speed × HARQ (× N_SEEDS seeds).
# N_blockers is NOT a sweep dimension: NLOSv always uses 1 mid-path blocker
# (to deterministically trigger the geometric CCM); LOS/NLOS use 0 blockers.
#
# Usage:
#   bash run_sweep.sh [--seeds N] [--jobs J] [--simTime S]
#                     [--mcs m1,m2,...] [--distances d1,...] [--conds c1,...]
#                     [--speeds s1,...] [--harq 0|1|both]
#                     [--channelUpdateMs F] [--binary PATH]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NS3_ROOT="${REPO_ROOT}/ns-3-dev"
OUT_ROOT="${SCRIPT_DIR}/results"

# ── Locate binary ─────────────────────────────────────────────────────────────
BINARY=""
for prof in optimized debug default; do
    cand="${NS3_ROOT}/cmake-cache-opt/scratch/sl_bler_sweep/ns3.42-sl_bler_sweep-${prof}"
    [[ -x "${cand}" ]] && BINARY="${cand}" && break
    cand="${NS3_ROOT}/build/scratch/sl_bler_sweep/ns3.42-sl_bler_sweep-${prof}"
    [[ -x "${cand}" ]] && BINARY="${cand}" && break
done

# ── Defaults ──────────────────────────────────────────────────────────────────
N_SEEDS=3
SIM_TIME=12
CHANNEL_UPDATE_MS=-1   # <=0 => per-slot realtime (1/2^mu ms)

ALL_MCS=(5 9 11 15 19 23)
ALL_DISTANCES=(10 25 50 75 100 150 200 300 400)
ALL_CONDS=(los nlos nlosv)
ALL_SPEEDS=(0 30 60 90 120)
ALL_HARQ=(0 1)

PARALLEL_JOBS=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

# ── Arg parsing ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds)           N_SEEDS="$2";             shift 2 ;;
        --jobs)            PARALLEL_JOBS="$2";        shift 2 ;;
        --simTime)         SIM_TIME="$2";             shift 2 ;;
        --channelUpdateMs) CHANNEL_UPDATE_MS="$2";    shift 2 ;;
        --mcs)       IFS=',' read -ra ALL_MCS       <<< "$2"; shift 2 ;;
        --distances) IFS=',' read -ra ALL_DISTANCES <<< "$2"; shift 2 ;;
        --conds)     IFS=',' read -ra ALL_CONDS     <<< "$2"; shift 2 ;;
        --speeds)    IFS=',' read -ra ALL_SPEEDS    <<< "$2"; shift 2 ;;
        --harq)
            case "$2" in
                0)    ALL_HARQ=(0)   ;;
                1)    ALL_HARQ=(1)   ;;
                both) ALL_HARQ=(0 1) ;;
                *) echo "ERROR: --harq must be 0, 1, or both"; exit 1 ;;
            esac
            shift 2 ;;
        --binary) BINARY="$2"; shift 2 ;;
        -h|--help)
            grep '^#' "$0" | head -20 | sed 's/^# \?//'
            exit 0 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

if [[ -z "${BINARY}" || ! -x "${BINARY}" ]]; then
    echo "ERROR: sl_bler_sweep binary not found."
    echo "       Build:  cd ${NS3_ROOT} && ./ns3 configure --build-profile=optimized && ./ns3 build sl_bler_sweep"
    exit 1
fi

# ── Count total runs ──────────────────────────────────────────────────────────
total_runs=$(( ${#ALL_MCS[@]} * ${#ALL_DISTANCES[@]} * ${#ALL_CONDS[@]} * \
               ${#ALL_SPEEDS[@]} * ${#ALL_HARQ[@]} * N_SEEDS ))
echo "========================================================================"
echo "  NR SL BLER Sweep"
echo "  MCS:         ${ALL_MCS[*]}"
echo "  Distances:   ${ALL_DISTANCES[*]} m"
echo "  Conditions:  ${ALL_CONDS[*]}"
echo "  Speeds:      ${ALL_SPEEDS[*]} km/h"
echo "  HARQ:        ${ALL_HARQ[*]}"
echo "  Seeds:       ${N_SEEDS}  (1..${N_SEEDS})"
echo "  simTime:     ${SIM_TIME}s  (>= ~1200 TBs per run)"
echo "  Total runs:  ${total_runs}"
echo "  Parallel:    ${PARALLEL_JOBS} jobs (all CPUs)"
echo "  Binary:      ${BINARY}"
echo "  Out:         ${OUT_ROOT}"
echo "========================================================================"
mkdir -p "${OUT_ROOT}"

# ── Progress tracking ─────────────────────────────────────────────────────────
PROGRESS_FILE="${OUT_ROOT}/.progress"
printf '0' > "${PROGRESS_FILE}"

inc_progress() {
    # Atomically increment the counter and print progress.
    local n
    # flock is available on Linux; on macOS use a temp-file swap.
    n=$(< "${PROGRESS_FILE}")
    n=$(( n + 1 ))
    printf '%d' "${n}" > "${PROGRESS_FILE}"
    local pct=$(( n * 100 / total_runs ))
    printf '\r  [%4d/%4d  %3d%%]  %s' "${n}" "${total_runs}" "${pct}" "$1"
}
export -f inc_progress
export PROGRESS_FILE total_runs

# ── Per-run function ──────────────────────────────────────────────────────────
run_one() {
    local mcs="$1" dist="$2" cond="$3" speed="$4" harqFlag="$5" seed="$6"
    local harqStr; harqStr=$([ "${harqFlag}" -eq 1 ] && echo "harq1" || echo "harq0")
    local tag="mcs${mcs}_d${dist}_${cond}_v${speed}_${harqStr}_s${seed}"
    local outdir="${OUT_ROOT}"
    local outcsv="${outdir}/${tag}.csv"
    local logfile="${outdir}/${tag}.log"

    local tmpdir
    tmpdir=$(mktemp -d)
    (cd "${tmpdir}" && "${BINARY}" \
        --mcs="${mcs}" \
        --distance="${dist}" \
        --channelCondition="${cond}" \
        --speedKmh="${speed}" \
        --harq="${harqFlag}" \
        --channelUpdateMs="${CHANNEL_UPDATE_MS}" \
        --simTime="${SIM_TIME}" \
        --seed="${seed}" \
        --runId="${tag}" \
        --outCsv="${outcsv}" \
        > "${logfile}" 2>&1) || echo "  FAILED: ${tag}" >&2
    rm -rf "${tmpdir}"

    inc_progress "${tag}"
}
export -f run_one
export BINARY CHANNEL_UPDATE_MS SIM_TIME OUT_ROOT

# ── Build the full parameter list and launch in parallel ─────────────────────
echo ""
echo "[sweep] launching ${total_runs} runs across ${PARALLEL_JOBS} cores ..."
echo ""

param_list=()
for mcs in "${ALL_MCS[@]}"; do
  for dist in "${ALL_DISTANCES[@]}"; do
    for cond in "${ALL_CONDS[@]}"; do
      for speed in "${ALL_SPEEDS[@]}"; do
        for harq in "${ALL_HARQ[@]}"; do
          for seed in $(seq 1 "${N_SEEDS}"); do
            param_list+=("${mcs}|${dist}|${cond}|${speed}|${harq}|${seed}")
          done
        done
      done
    done
  done
done

printf '%s\n' "${param_list[@]}" \
  | xargs -P "${PARALLEL_JOBS}" -I{} bash -c \
      'IFS="|" read -r mcs dist cond speed harq seed <<< "$1"
       run_one "${mcs}" "${dist}" "${cond}" "${speed}" "${harq}" "${seed}"' \
      _ {}

echo ""
echo ""

# ── Concatenate per-run CSVs ──────────────────────────────────────────────────
ALL_CSV="${OUT_ROOT}/all_runs.csv"
echo "[concat] building ${ALL_CSV} ..."
rm -f "${ALL_CSV}"
first=1
while IFS= read -r -d '' csv; do
    if [[ ${first} -eq 1 ]]; then
        cat "${csv}" >> "${ALL_CSV}"
        first=0
    else
        tail -n +2 "${csv}" >> "${ALL_CSV}"
    fi
done < <(find "${OUT_ROOT}" -maxdepth 1 -name '*.csv' \
            ! -name 'all_runs.csv' -print0 | sort -z)

total_rows=$(( $(wc -l < "${ALL_CSV}") - 1 ))

# ── Clean up stray .db files ──────────────────────────────────────────────────
db_count=$(find "${OUT_ROOT}" -name '*.db' | wc -l)
if [[ ${db_count} -gt 0 ]]; then
    echo "[cleanup] removing ${db_count} .db file(s) ..."
    find "${OUT_ROOT}" -name '*.db' -delete
fi
rm -f "${PROGRESS_FILE}"

echo ""
echo "========================================================================"
echo "  Sweep complete."
echo "  ${total_rows} rows -> ${ALL_CSV}"
echo "  Next: jupyter lab ${SCRIPT_DIR}/plot_bler.ipynb"
echo "========================================================================"
