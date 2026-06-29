#!/usr/bin/env bash
# run_relay_l2id_compare.sh — matched head-to-head sweep for the four relay designs.
#
# Reuses the relay_storm_eval operating points so the arms are directly
# comparable to results_relay_storm_eval. Arms per (speed,ivd,mcd,seed):
#   - none        : 1-hop baseline, fixed MCS-14
#   - none+adapt  : 1-hop baseline, worst-RX NACK-driven adaptive MCS  (ADAPTIVE=1)
#   - sameL2 @{4,6}: 2-hop shared-L2ID relay      (the storm design)
#   - fixed  @{4,6}: 2-hop distinct-L2ID partition (storm -> collision)
#   - broadcast@{4,6}: 2-hop distinct-L2ID broadcast hop-2 (no hop-2 HARQ)
#
# Usage:
#   ./run_relay_l2id_compare.sh [outputDir] [simTime] [seeds] [nParallel]
# Env toggles:
#   QUICK=1     reduced first pass (MCD {80,300}, 2 operating points, modes x {4,6})
#   ADAPTIVE=1  add the worst-RX adaptive-MCS strong baseline (none+adapt)
#
# Examples:
#   QUICK=1 ./run_relay_l2id_compare.sh ./results_quick 5.0 "$(seq -s' ' 1 10)" 10
#   ADAPTIVE=1 ./run_relay_l2id_compare.sh ./results 5.0 "$(seq -s' ' 1 30)" 10

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3_DIR="$(cd "$SCRIPT_DIR/../../ns-3-dev" && pwd)"
BIN_DIR="$NS3_DIR/cmake-cache-opt/scratch/relay_l2id_compare"
KPI_SCRIPT="$SCRIPT_DIR/../../scratch_overlay/common/kpi/kpi_postprocess.py"

# Auto-detect binary (build profile may be -default or -optimized).
BINARY="$(ls "$BIN_DIR"/ns3.*-relay_l2id_compare-* 2>/dev/null | head -1 || true)"
if [ -z "$BINARY" ]; then
    echo "ERROR: relay_l2id_compare binary not found in $BIN_DIR"
    echo "Build it first:  cd $NS3_DIR && ./ns3 build relay_l2id_compare"
    exit 1
fi

OUTPUT_DIR="$(mkdir -p "${1:-./results}" && cd "${1:-./results}" && pwd)"
SIM_TIME="${2:-5.0}"
SEEDS="${3:-1 2 3}"
NPAR="${4:-$(sysctl -n hw.logicalcpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"
QUICK="${QUICK:-0}"
ADAPTIVE="${ADAPTIVE:-0}"

# Operating points (speed_kmh ivd_m) — safe-headway coupled, matched to storm.
if [ "$QUICK" = "1" ]; then
    SPEED_IVD_PAIRS=("80 25" "140 39")   # storm-onset-prone + wide-spacing
    MCD_VALUES=(80 300)                  # min (no storm) + storm regime
else
    SPEED_IVD_PAIRS=("80 25" "90 27" "100 29" "110 32" "120 34" "130 37" "140 39")
    MCD_VALUES=(80 100 150 300 350)
fi
RELAY_NODES=(4 6)
RELAY_MODES=(sameL2 fixed broadcast)

echo "=== relay_l2id_compare sweep ==="
echo "Binary:      $BINARY"
echo "Output:      $OUTPUT_DIR"
echo "SimTime:     $SIM_TIME s   Seeds: $SEEDS   Parallel: $NPAR"
echo "Operating:   ${#SPEED_IVD_PAIRS[@]} pairs   MCD: ${MCD_VALUES[*]}"
echo "Modes:       none ${RELAY_MODES[*]}   positions: ${RELAY_NODES[*]}   QUICK=$QUICK ADAPTIVE=$ADAPTIVE"
echo ""

# --- Build run-list: tag relayMode relayNode adaptiveMcs speed ivd mcd seed ---
RUNLIST="$OUTPUT_DIR/runlist.txt"
> "$RUNLIST"
for speedivd in "${SPEED_IVD_PAIRS[@]}"; do
    speed=$(echo "$speedivd" | awk '{print $1}')
    ivd=$(echo "$speedivd"   | awk '{print $2}')
    for mcd in "${MCD_VALUES[@]}"; do
        for seed in $SEEDS; do
            # 1-hop baseline (fixed MCS)
            echo "none_s${speed}_ivd${ivd}_mcd${mcd}_seed${seed} none 4 false $speed $ivd $mcd $seed" >> "$RUNLIST"
            # 1-hop strong baseline (adaptive MCS)
            if [ "$ADAPTIVE" = "1" ]; then
                echo "adapt_s${speed}_ivd${ivd}_mcd${mcd}_seed${seed} none 4 true $speed $ivd $mcd $seed" >> "$RUNLIST"
            fi
            # 2-hop relay arms
            for mode in "${RELAY_MODES[@]}"; do
                for relay in "${RELAY_NODES[@]}"; do
                    echo "${mode}${relay}_s${speed}_ivd${ivd}_mcd${mcd}_seed${seed} $mode $relay false $speed $ivd $mcd $seed" >> "$RUNLIST"
                done
            done
        done
    done
done
echo "Total runs: $(wc -l < "$RUNLIST")"
echo ""

run_one() {
    read -r tag mode relay adapt speed ivd mcd seed <<< "$1"
    local outDir="$OUTPUT_DIR/$tag"
    mkdir -p "$outDir"
    "$BINARY" \
        --runId="$tag" --seed="$seed" \
        --relayMode="$mode" --relayNode="$relay" --adaptiveMcs="$adapt" \
        --speed="$speed" --ivd="$ivd" --mcd="$mcd" \
        --simTime="$SIM_TIME" --outputDir="$outDir" --simTag="$tag" \
        >"$outDir/sim.log" 2>&1 \
    && echo "OK   $tag" || echo "FAIL $tag (see $outDir/sim.log)"
}
export -f run_one
export BINARY OUTPUT_DIR SIM_TIME

START=$(date +%s)
xargs -P "$NPAR" -I '{}' bash -c 'run_one "$@"' _ '{}' < "$RUNLIST"
echo ""
echo "=== sims done in $(( $(date +%s) - START ))s ==="

# --- KPI post-processing (same engine as the storm campaign) ---
if [ -f "$KPI_SCRIPT" ]; then
    echo "KPI postprocess ($NPAR parallel)..."
    kpi_one() {
        local db="$1" tag seed_n
        tag=$(basename "$db" .db)
        seed_n=$(echo "$tag" | grep -oE 'seed[0-9]+$' | grep -oE '[0-9]+'); [ -z "$seed_n" ] && seed_n=1
        python3 "$KPI_SCRIPT" --db "$db" --out "${db%.db}_kpi.csv" \
            --warmup 1.0 --window 1.0 --step 0.5 --seed 1 --run "$seed_n" 2>&1 | tail -1
    }
    export -f kpi_one; export KPI_SCRIPT
    find "$OUTPUT_DIR" -name "*.db" -print0 | xargs -0 -P "$NPAR" -I '{}' bash -c 'kpi_one "$@"' _ '{}'
fi
echo ""
echo "=== All done. Results in $OUTPUT_DIR ==="
