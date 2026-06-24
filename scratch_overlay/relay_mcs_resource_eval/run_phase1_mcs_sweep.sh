#!/usr/bin/env bash
# run_phase1_mcs_sweep.sh — Phase 1: MCS × distance (R-A) sweep
#
# Sweeps:
#   - 7 (speed, IVD) pairs (same as relay_storm_eval)
#   - MCS: {0, 2, 4, ..., 22} (12 points)
#   - 5 seeds
#   - mode=baseline (no relay)
#   - 5 s simTime, MCD=350 (large enough not to gate any single-hop TX)
#
# Output: results_relay_mcs_resource_eval/phase1/<tag>/*.db
#         + parallel kpi_postprocess on each DB
#
# Builds the P_hit(i, m, pair) surface, the per-vehicle max-decodable MCS m*_i,
# and the baseline MCS = m*_7. Feeds Phase 2 (relay selection).
#
# Usage:
#   ./run_phase1_mcs_sweep.sh [outputDir] [simTime] [seeds] [nParallel]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3_DIR="$(cd "$SCRIPT_DIR/../../ns-3-dev" && pwd)"
BINARY="$NS3_DIR/cmake-cache-opt/scratch/relay_mcs_resource_eval/ns3.42-relay_mcs_resource_eval-default"
KPI_SCRIPT="$SCRIPT_DIR/../common/kpi/kpi_postprocess.py"

OUTPUT_DIR="$(mkdir -p "${1:-./results_relay_mcs_resource_eval/phase1}" && cd "${1:-./results_relay_mcs_resource_eval/phase1}" && pwd)"
SIM_TIME="${2:-5.0}"
SEEDS="${3:-1 2 3 4 5}"
NPAR="${4:-$(sysctl -n hw.logicalcpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"

mkdir -p "$OUTPUT_DIR"

# (speed_kmh, ivd_m) pairs — identical to relay_storm_eval
SPEED_IVD_PAIRS=(
    "80  25"
    "90  27"
    "100 29"
    "110 32"
    "120 34"
    "130 37"
    "140 39"
)

# MCS grid: 12 points {0,2,...,22}
MCS_VALUES=(0 2 4 6 8 10 12 14 16 18 20 22)

# MCD large enough to not gate any single-hop TX (platoon is at most 7*39=273 m).
MCD=350

echo "=== Phase 1: MCS×distance (R-A) sweep ==="
echo "Output:      $OUTPUT_DIR"
echo "Binary:      $BINARY"
echo "SimTime:     $SIM_TIME s"
echo "Seeds:       $SEEDS"
echo "Parallel:    $NPAR jobs"
echo "Speed/IVD:   ${#SPEED_IVD_PAIRS[@]} pairs"
echo "MCS values:  ${MCS_VALUES[*]}"
echo "MCD:         $MCD m"
echo ""

# ---------------------------------------------------------------------------
# Build run-list: tag speed ivd mcs mcd mode seed
# ---------------------------------------------------------------------------
RUNLIST="$OUTPUT_DIR/runlist.txt"
> "$RUNLIST"

for speedivd in "${SPEED_IVD_PAIRS[@]}"; do
    speed=$(echo "$speedivd" | awk '{print $1}')
    ivd=$(echo "$speedivd"   | awk '{print $2}')
    for mcs in "${MCS_VALUES[@]}"; do
        for seed in $SEEDS; do
            tag="baseline_s${speed}_ivd${ivd}_mcs${mcs}_seed${seed}"
            echo "$tag $speed $ivd $mcs $MCD $seed" >> "$RUNLIST"
        done
    done
done

TOTAL=$(wc -l < "$RUNLIST")
echo "Total runs:  $TOTAL"
echo ""

# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------
run_one() {
    local tag speed ivd mcs mcd seed
    read -r tag speed ivd mcs mcd seed <<< "$1"

    local outDir="$OUTPUT_DIR/$tag"
    mkdir -p "$outDir"
    local logFile="$outDir/sim.log"

    "$BINARY" \
        --runId="$tag" \
        --seed="$seed" \
        --numUEs=8 \
        --mode=baseline \
        --speed="$speed" \
        --ivd="$ivd" \
        --mcsBaseline="$mcs" \
        --mcd="$mcd" \
        --relayNode=4 \
        --simTime="$SIM_TIME" \
        --warmup=1.0 \
        --outputDir="$outDir" \
        --simTag="$tag" \
        >"$logFile" 2>&1 \
    && echo "OK  $tag" \
    || echo "FAIL $tag (see $logFile)"
}

export -f run_one
export BINARY OUTPUT_DIR SIM_TIME

START=$(date +%s)
xargs -P "$NPAR" -I '{}' bash -c 'run_one "$@"' _ '{}' < "$RUNLIST"
END=$(date +%s)
echo ""
echo "=== Phase 1 simulation sweep done in $((END-START))s ==="

# ---------------------------------------------------------------------------
# KPI post-processing (parallelised)
# ---------------------------------------------------------------------------
echo "Running KPI postprocess ($NPAR parallel)..."

kpi_one() {
    local db="$1"
    local tag seed_n outCsv
    tag=$(basename "$db" .db)
    seed_n=$(echo "$tag" | grep -oE 'seed[0-9]+$' | grep -oE '[0-9]+')
    [ -z "$seed_n" ] && seed_n=1
    outCsv="${db%.db}_kpi.csv"
    python3 "$KPI_SCRIPT" \
        --db "$db" --out "$outCsv" \
        --warmup 1.0 --sim-end "$SIM_TIME" \
        --window 1.0 --step 0.5 \
        --seed 1 --run "$seed_n" \
        2>&1 | tail -1
}

export -f kpi_one
export KPI_SCRIPT SIM_TIME

find "$OUTPUT_DIR" -name "*.db" -print0 \
    | xargs -0 -P "$NPAR" -I '{}' bash -c 'kpi_one "$@"' _ '{}'

echo ""
echo "=== Phase 1 done. Results in $OUTPUT_DIR ==="
