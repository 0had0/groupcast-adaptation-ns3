#!/usr/bin/env bash
# run_phase2b_mcd_sweep.sh — Phase 2b: storm/MCD threshold (R-D) sweep
#
# For each (speed, ivd) pair, at the chosen r*, sweep MCD over
# {(r*-1), r*, (r*+1), (r*+2)} * ivd.
#
# Input: phase2 selection frontier produced by aggregate.py phase2.
# Usage:
#   ./run_phase2b_mcd_sweep.sh <phase2_sweep_csv> [outputDir] [simTime] [seeds] [nParallel]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3_DIR="$(cd "$SCRIPT_DIR/../../ns-3-dev" && pwd)"
BINARY="$NS3_DIR/cmake-cache-opt/scratch/relay_mcs_resource_eval/ns3.42-relay_mcs_resource_eval-default"
KPI_SCRIPT="$SCRIPT_DIR/../common/kpi/kpi_postprocess.py"
FRONTIER_CSV="${1:?usage: $0 <phase2_sweep_csv> [outputDir] [simTime] [seeds] [nParallel]}"
OUTPUT_DIR="$(mkdir -p "${2:-./results_relay_mcs_resource_eval/phase2b}" && cd "${2:-./results_relay_mcs_resource_eval/phase2b}" && pwd)"
SIM_TIME="${3:-5.0}"
SEEDS="${4:-1 2 3 4 5}"
NPAR="${5:-$(sysctl -n hw.logicalcpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"

mkdir -p "$OUTPUT_DIR"

echo "=== Phase 2b: MCD threshold (R-D) sweep ==="
echo "Frontier:   $FRONTIER_CSV"
echo "Output:     $OUTPUT_DIR"
echo "SimTime:    $SIM_TIME s"
echo "Seeds:      $SEEDS"
echo "Parallel:   $NPAR jobs"
echo ""

# Build run-list: tag speed ivd r mcs1 mcs2 mcd seed
# Each row in FRONTIER_CSV is produced by aggregate.py phase2.
# Expected format: speed,ivd,r_star,mcsHop1,mcsHop2,r_star_resource,...
# We parse just the first 5 cols.
RUNLIST="$OUTPUT_DIR/runlist.txt"
> "$RUNLIST"

while IFS=, read -r speed ivd r_star mcs1 mcs2 _rest; do
    [ "$speed" = "speed" ] && continue   # header
    [ -z "$speed" ] && continue
    r_star=$((r_star))   # numeric
    mcs1=$((mcs1))
    mcs2=$((mcs2))
    for k in -1 0 1 2; do
        mcd=$((r_star + k))
        mcd=$((mcd * ivd))
        for seed in $SEEDS; do
            tag="relay${r_star}_s${speed}_ivd${ivd}_mcs1_${mcs1}_mcs2_${mcs2}_mcd${mcd}_seed${seed}"
            echo "$tag $speed $ivd $r_star $mcs1 $mcs2 $mcd $seed" >> "$RUNLIST"
        done
    done
done < "$FRONTIER_CSV"

TOTAL=$(wc -l < "$RUNLIST")
echo "Total runs:  $TOTAL"
echo ""

# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------
run_one() {
    local tag speed ivd r mcs1 mcs2 mcd seed
    read -r tag speed ivd r mcs1 mcs2 mcd seed <<< "$1"

    local outDir="$OUTPUT_DIR/$tag"
    mkdir -p "$outDir"
    local logFile="$outDir/sim.log"

    "$BINARY" \
        --runId="$tag" \
        --seed="$seed" \
        --numUEs=8 \
        --mode=relay \
        --speed="$speed" \
        --ivd="$ivd" \
        --mcsBaseline="$mcs1" \
        --mcsHop2="$mcs2" \
        --mcd="$mcd" \
        --relayNode="$r" \
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
echo "=== Phase 2b simulation sweep done in $((END-START))s ==="

# ---------------------------------------------------------------------------
# KPI post-processing
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
echo "=== Phase 2b done. Results in $OUTPUT_DIR ==="
