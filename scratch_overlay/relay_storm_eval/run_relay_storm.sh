#!/usr/bin/env bash
# run_relay_storm.sh — relay_storm_eval sweep driver
#
# Sweeps:
#   - 7 (speed, IVD) pairs: 80..140 km/h × IVD mid-range
#   - MCD: {80, 100, 150, 300, 350} m
#   - Relay node: {4, 6} (separate runs, never both at once)
#   - No-relay baseline (relayEnabled=false)
#   - Seeds: configurable (default 1..3)
#
# Usage:
#   ./run_relay_storm.sh [outputDir] [simTime] [seeds] [nParallel]
#
# Example (10 seeds, all 10 cores):
#   ./run_relay_storm.sh ./results 5.0 "$(seq -s' ' 1 10)" 10

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3_DIR="$(cd "$SCRIPT_DIR/../../ns-3-dev" && pwd)"
BINARY="$NS3_DIR/cmake-cache-opt/scratch/relay_storm_eval/ns3.42-relay_storm_eval-default"
KPI_SCRIPT="$SCRIPT_DIR/../../scratch_overlay/common/kpi/kpi_postprocess.py"

# Resolve output dir to absolute path (ns3 run executes from ns-3-dev/).
OUTPUT_DIR="$(mkdir -p "${1:-./results}" && cd "${1:-./results}" && pwd)"
SIM_TIME="${2:-5.0}"
SEEDS="${3:-1 2 3}"
NPAR="${4:-$(sysctl -n hw.logicalcpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"

mkdir -p "$OUTPUT_DIR"

# (speed_kmh, ivd_m) pairs — one per sweep row
SPEED_IVD_PAIRS=(
    "80  25"
    "90  27"
    "100 29"
    "110 32"
    "120 34"
    "130 37"
    "140 39"
)

MCD_VALUES=(80 100 150 300 350)
RELAY_NODES=(4 6)

echo "=== relay_storm_eval sweep ==="
echo "Output:      $OUTPUT_DIR"
echo "Binary:      $BINARY"
echo "SimTime:     $SIM_TIME s"
echo "Seeds:       $SEEDS"
echo "Parallel:    $NPAR jobs"
echo "Speed/IVD:   ${#SPEED_IVD_PAIRS[@]} pairs"
echo "MCD values:  ${MCD_VALUES[*]}"
echo "Relay nodes: ${RELAY_NODES[*]}"
echo ""

# ---------------------------------------------------------------------------
# Build run-list (one line per run: tag speed ivd mcd relay relayEnabled seed)
# ---------------------------------------------------------------------------
RUNLIST="$OUTPUT_DIR/runlist.txt"
> "$RUNLIST"

for speedivd in "${SPEED_IVD_PAIRS[@]}"; do
    speed=$(echo "$speedivd" | awk '{print $1}')
    ivd=$(echo "$speedivd" | awk '{print $2}')
    for mcd in "${MCD_VALUES[@]}"; do
        # No-relay baseline
        for seed in $SEEDS; do
            tag="baseline_s${speed}_ivd${ivd}_mcd${mcd}_seed${seed}"
            echo "$tag $speed $ivd $mcd 0 false $seed" >> "$RUNLIST"
        done
        # Relay runs
        for relay in "${RELAY_NODES[@]}"; do
            for seed in $SEEDS; do
                tag="relay${relay}_s${speed}_ivd${ivd}_mcd${mcd}_seed${seed}"
                echo "$tag $speed $ivd $mcd $relay true $seed" >> "$RUNLIST"
            done
        done
    done
done

TOTAL=$(wc -l < "$RUNLIST")
echo "Total runs: $TOTAL"
echo ""

# ---------------------------------------------------------------------------
# Parallel execution via xargs -P
# ---------------------------------------------------------------------------
run_one() {
    local tag speed ivd mcd relay relayEnabled seed
    read -r tag speed ivd mcd relay relayEnabled seed <<< "$1"

    local outDir="$OUTPUT_DIR/$tag"
    mkdir -p "$outDir"
    local logFile="$outDir/sim.log"

    "$BINARY" \
        --runId="$tag" \
        --seed="$seed" \
        --speed="$speed" \
        --ivd="$ivd" \
        --mcd="$mcd" \
        --relayNode="$relay" \
        --relayEnabled="$relayEnabled" \
        --simTime="$SIM_TIME" \
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
echo "=== Simulation sweep done in $((END-START))s ==="

# ---------------------------------------------------------------------------
# KPI post-processing (parallelised too)
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
        --warmup 1.0 --window 1.0 --step 0.5 \
        --seed 1 --run "$seed_n" \
        2>&1 | tail -1
}

export -f kpi_one
export KPI_SCRIPT

find "$OUTPUT_DIR" -name "*.db" -print0 \
    | xargs -0 -P "$NPAR" -I '{}' bash -c 'kpi_one "$@"' _ '{}'

echo ""
echo "=== All done. Results in $OUTPUT_DIR ==="
