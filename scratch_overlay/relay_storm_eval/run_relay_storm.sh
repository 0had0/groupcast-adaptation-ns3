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
#   ./run_relay_storm.sh [outputDir] [simTime] [seeds]
#
# Example:
#   ./run_relay_storm.sh ./results 5.0 "1 2 3"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3_DIR="$(cd "$SCRIPT_DIR/../../ns-3-dev" && pwd)"
SIM="scratch/relay_storm_eval/relay_storm_eval"

OUTPUT_DIR="${1:-./results}"
SIM_TIME="${2:-5.0}"
SEEDS="${3:-1 2 3}"

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
echo "Output: $OUTPUT_DIR"
echo "SimTime: $SIM_TIME s"
echo "Seeds: $SEEDS"
echo "Speed/IVD pairs: ${#SPEED_IVD_PAIRS[@]}"
echo "MCD values: ${MCD_VALUES[*]}"
echo "Relay nodes: ${RELAY_NODES[*]}"
echo ""

total=0
done_=0

# Pre-compute total runs.
for _ in "${SPEED_IVD_PAIRS[@]}"; do
    for _ in "${MCD_VALUES[@]}"; do
        for _ in "${RELAY_NODES[@]}"; do
            for _ in $SEEDS; do total=$((total+1)); done
        done
        # +1 for no-relay baseline per (speed,ivd,mcd,seed).
        for _ in $SEEDS; do total=$((total+1)); done
    done
done
echo "Total runs: $total"
echo ""

run_sim() {
    local runId="$1"
    local speed="$2"
    local ivd="$3"
    local mcd="$4"
    local relay="$5"
    local relayEnabled="$6"
    local seed="$7"

    local outDir="$OUTPUT_DIR/$runId"
    mkdir -p "$outDir"

    local logFile="$outDir/sim.log"

    "$NS3_DIR/ns3" run "$SIM" -- \
        --runId="$runId" \
        --seed="$seed" \
        --speed="$speed" \
        --ivd="$ivd" \
        --mcd="$mcd" \
        --relayNode="$relay" \
        --relayEnabled="$relayEnabled" \
        --simTime="$SIM_TIME" \
        --outputDir="$outDir" \
        --simTag="$runId" \
        > "$logFile" 2>&1 && echo "  OK: $runId" || echo "  FAIL: $runId (see $logFile)"
}

for speedivd in "${SPEED_IVD_PAIRS[@]}"; do
    speed=$(echo "$speedivd" | awk '{print $1}')
    ivd=$(echo "$speedivd" | awk '{print $2}')

    for mcd in "${MCD_VALUES[@]}"; do
        # No-relay baseline runs.
        for seed in $SEEDS; do
            runId="baseline_s${speed}_ivd${ivd}_mcd${mcd}_seed${seed}"
            run_sim "$runId" "$speed" "$ivd" "$mcd" "0" "false" "$seed"
            done_=$((done_+1))
            echo "  Progress: $done_/$total"
        done

        # Relay runs.
        for relay in "${RELAY_NODES[@]}"; do
            for seed in $SEEDS; do
                runId="relay${relay}_s${speed}_ivd${ivd}_mcd${mcd}_seed${seed}"
                run_sim "$runId" "$speed" "$ivd" "$mcd" "$relay" "true" "$seed"
                done_=$((done_+1))
                echo "  Progress: $done_/$total"
            done
        done
    done
done

echo ""
echo "=== Sweep complete. Running KPI postprocess ==="

# Run kpi_postprocess.py on each DB.
# Note: our sim uses SetSeed(1)/SetRun(seed), so SEED=1,RUN=seed_number in the DB.
KPI_SCRIPT="$(cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../scratch_overlay/common/kpi" && pwd)/kpi_postprocess.py"

find "$OUTPUT_DIR" -name "*.db" | while read -r db; do
    tag=$(basename "$db" .db)
    # Extract seed number from tag suffix "_seed<N>" → --run N
    seed_n=$(echo "$tag" | grep -oE 'seed[0-9]+$' | grep -oE '[0-9]+')
    [ -z "$seed_n" ] && seed_n=1
    outCsv="${db%.db}_kpi.csv"
    python3 "$KPI_SCRIPT" \
        --db "$db" \
        --out "$outCsv" \
        --warmup 1.0 \
        --window 1.0 \
        --step 0.5 \
        --seed 1 \
        --run "$seed_n" \
        2>&1 | tail -3
done

echo ""
echo "=== All done. Results in $OUTPUT_DIR ==="
