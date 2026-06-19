#!/usr/bin/env bash
# run_pdb_sweep.sh
#
# NR 5G Sidelink groupcast MCS, packet size & PDB sweep.
# Sweeps: MCS × packet_size × PDB (with RRI = PDB) (× N_SEEDS seeds).
# Other parameters (distance, channel condition, speed) are held fixed.
#
# Usage:
#   bash run_pdb_sweep.sh [--seeds N] [--jobs J] [--simTime S]
#                         [--distance D] [--condition C] [--speed S]
#                         [--mcs m1,m2,...] [--pktSizes p1,p2,...] [--pdbs d1,d2,...]
#                         [--binary PATH]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NS3_ROOT="${REPO_ROOT}/ns-3-dev"
OUT_ROOT="${SCRIPT_DIR}/results_pdb"

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

# Fixed parameters
DISTANCE=100
CONDITION="nlosv"
SPEED=0

# Sweep dimensions
ALL_MCS=(5 9 11 15 19 23)
ALL_PKT_SIZES=(100 500 1000 2000 4000 6000)  # bytes
ALL_PDBS=(10 20 40 100)                       # milliseconds
ALL_PSFCH=(1 2 4)                             # PSFCH periods (only when HARQ enabled)

PARALLEL_JOBS=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

# ── Arg parsing ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --seeds)           N_SEEDS="$2";             shift 2 ;;
        --jobs)            PARALLEL_JOBS="$2";        shift 2 ;;
        --simTime)         SIM_TIME="$2";             shift 2 ;;
        --channelUpdateMs) CHANNEL_UPDATE_MS="$2";    shift 2 ;;
        --distance)        DISTANCE="$2";             shift 2 ;;
        --condition)       CONDITION="$2";            shift 2 ;;
        --speed)           SPEED="$2";                shift 2 ;;
        --mcs)       IFS=',' read -ra ALL_MCS       <<< "$2"; shift 2 ;;
        --pktSizes)  IFS=',' read -ra ALL_PKT_SIZES <<< "$2"; shift 2 ;;
        --pdbs)      IFS=',' read -ra ALL_PDBS      <<< "$2"; shift 2 ;;
        --psfch)     IFS=',' read -ra ALL_PSFCH     <<< "$2"; shift 2 ;;
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
# HARQ off: 1 config per (MCS, pktSize, PDB, seed)
# HARQ on:  3 configs per (MCS, pktSize, PDB, seed) for PSFCH in {1,2,4}
total_harq_configs=$(( 1 + ${#ALL_PSFCH[@]} ))
total_runs=$(( ${#ALL_MCS[@]} * ${#ALL_PKT_SIZES[@]} * ${#ALL_PDBS[@]} * total_harq_configs * N_SEEDS ))
echo "========================================================================"
echo "  NR SL MCS × PDB & Packet Size × HARQ Sweep"
echo "  MCS:          ${ALL_MCS[*]}"
echo "  Packet sizes: ${ALL_PKT_SIZES[*]} bytes"
echo "  PDBs:         ${ALL_PDBS[*]} ms (RRI = PDB)"
echo "  HARQ:         off + on (with PSFCH periods ${ALL_PSFCH[*]})"
echo "  Distance:     ${DISTANCE} m"
echo "  Condition:    ${CONDITION}"
echo "  Speed:        ${SPEED} km/h"
echo "  Seeds:        ${N_SEEDS}  (1..${N_SEEDS})"
echo "  simTime:      ${SIM_TIME}s"
echo "  Total runs:   ${total_runs}"
echo "  Parallel:     ${PARALLEL_JOBS} jobs (all CPUs)"
echo "  Binary:       ${BINARY}"
echo "  Out:          ${OUT_ROOT}"
echo "========================================================================"
mkdir -p "${OUT_ROOT}"

# ── Progress tracking ─────────────────────────────────────────────────────────
PROGRESS_FILE="${OUT_ROOT}/.progress"
printf '0' > "${PROGRESS_FILE}"

inc_progress() {
    # Atomically increment the counter and print progress.
    local n
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
    local mcs="$1" pktSize="$2" pdb="$3" harq="$4" psfch="$5" seed="$6"
    local rri="${pdb}"  # RRI = PDB
    local harqStr; harqStr=$([ "${harq}" -eq 1 ] && echo "harq1_psfch${psfch}" || echo "harq0")
    local tag="mcs${mcs}_pktSize${pktSize}B_pdb${pdb}ms_${harqStr}_d${DISTANCE}_${CONDITION}_v${SPEED}_s${seed}"
    local outdir="${OUT_ROOT}"
    local outcsv="${outdir}/${tag}.csv"
    local logfile="${outdir}/${tag}.log"

    local tmpdir
    tmpdir=$(mktemp -d)
    (cd "${tmpdir}" && "${BINARY}" \
        --mcs="${mcs}" \
        --distance="${DISTANCE}" \
        --channelCondition="${CONDITION}" \
        --speedKmh="${SPEED}" \
        --packetSizeBytes="${pktSize}" \
        --pdbMs="${pdb}" \
        --rriMs="${rri}" \
        --harq="${harq}" \
        --psfchPeriod="${psfch}" \
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
export BINARY DISTANCE CONDITION SPEED CHANNEL_UPDATE_MS SIM_TIME OUT_ROOT

# ── Build the full parameter list and launch in parallel ─────────────────────
echo ""
echo "[sweep] launching ${total_runs} runs across ${PARALLEL_JOBS} cores ..."
echo ""

param_list=()
for mcs in "${ALL_MCS[@]}"; do
  for pktSize in "${ALL_PKT_SIZES[@]}"; do
    for pdb in "${ALL_PDBS[@]}"; do
      for seed in $(seq 1 "${N_SEEDS}"); do
        # HARQ disabled (harq=0, psfch=0 ignored)
        param_list+=("${mcs}|${pktSize}|${pdb}|0|0|${seed}")
        # HARQ enabled with different PSFCH periods
        for psfch in "${ALL_PSFCH[@]}"; do
          param_list+=("${mcs}|${pktSize}|${pdb}|1|${psfch}|${seed}")
        done
      done
    done
  done
done

printf '%s\n' "${param_list[@]}" \
  | xargs -P "${PARALLEL_JOBS}" -I{} bash -c \
      'IFS="|" read -r mcs pktSize pdb harq psfch seed <<< "$1"
       run_one "${mcs}" "${pktSize}" "${pdb}" "${harq}" "${psfch}" "${seed}"' \
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
echo "========================================================================"
