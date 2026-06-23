#!/usr/bin/env bash
# run_co_tx.sh — co_tx_interference sweep driver
#
# 2x2 design (relay4 only) testing the hypothesis that the relay's own TB
# co-TX interference (on the same sidelink pool as the leader) is what
# inflates the leader's hop-1 reTX count when the relay enters the MCD
# feedback group.
#
# Sweep matrix (per (speed, ivd, mcd)):
#   |              | MCD on (e.g. 100..300) | MCD off (mcd=1e9) |
#   | relayMode=forward     | C2  storm headline   | C1  co-TX only   |
#   | relayMode=noForward   | B1  feedback NACK only | B2  true baseline |
#
# Per (speed, ivd) we sample 3 MCDs that bracket the relay-entry threshold
# (4·IVD) to expose the switch-on cleanly without scanning the full grid.
#
# Usage:
#   ./run_co_tx.sh [outputDir] [seeds] [nParallel] [simTime]
#
# Defaults: outputDir=./results_co_tx_interference, seeds=1..10, nParallel=10, simTime=12

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3_DIR="$(cd "$SCRIPT_DIR/../../ns-3-dev" && pwd)"
BINARY="$NS3_DIR/cmake-cache-opt/scratch/co_tx_interference/ns3.42-co_tx_interference-default"

OUTPUT_DIR="$(mkdir -p "${1:-./results_co_tx_interference}" && cd "${1:-./results_co_tx_interference}" && pwd)"
SEEDS="${2:-$(seq 1 10)}"
NPAR="${3:-$(sysctl -n hw.logicalcpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"
SIM_TIME="${4:-12.0}"

# (speed_kmh, ivd_m) — same pairs as relay_storm_eval
SPEED_IVD_PAIRS=(
    "80  25"
    "90  27"
    "100 29"
    "110 32"
    "120 34"
    "130 37"
    "140 39"
)

# Per (spd, ivd), pick 3 MCDs that bracket the relay-entry threshold (4·IVD):
#   - floor(4·IVD / 50) * 50  → MCD well below threshold
#   - 4·IVD itself            → MCD at threshold (rounded to nearest 50)
#   - next grid step up        → MCD just above threshold
# (MCD grid is 80, 100, 150, 300, 350 — same as relay_storm_eval.)
MCD_GRID=(80 100 150 300 350)

echo "=== co_tx_interference sweep ==="
echo "Output:      $OUTPUT_DIR"
echo "Binary:      $BINARY"
echo "SimTime:     $SIM_TIME s"
echo "Seeds:       $SEEDS"
echo "Parallel:    $NPAR jobs"
echo "Speed/IVD:   ${#SPEED_IVD_PAIRS[@]} pairs"
echo "MCD grid:    ${MCD_GRID[*]}"
echo ""

# ---------------------------------------------------------------------------
# Build run-list
# ---------------------------------------------------------------------------
RUNLIST="$OUTPUT_DIR/runlist.txt"
> "$RUNLIST"

for speedivd in "${SPEED_IVD_PAIRS[@]}"; do
    speed=$(echo "$speedivd" | awk '{print $1}')
    ivd=$(echo   "$speedivd" | awk '{print $2}')
    relay_dist_m=$((4 * ivd))   # relay4 sits at distance 4·IVD from leader
    # Choose MCDs: 1 below threshold, 1 at threshold (rounded to grid), 1 above
    th_mcd=$relay_dist_m
    # Round th_mcd to nearest MCD grid value
    nearest=""
    diff_min=99999
    for g in "${MCD_GRID[@]}"; do
        d=$(( th_mcd > g ? th_mcd - g : g - th_mcd ))
        if [ $d -lt $diff_min ]; then diff_min=$d; nearest=$g; fi
    done
    th_mcd=$nearest
    # Find below and above in grid
    below=""
    above=""
    for i in "${!MCD_GRID[@]}"; do
        g=${MCD_GRID[$i]}
        if [ $g -lt $th_mcd ] && [ -z "$below" ]; then below=$g; fi
        if [ $g -gt $th_mcd ] && [ -z "$above" ]; then above=$g; fi
    done
    # Fallback if at edge
    [ -z "$below" ] && below=$th_mcd
    [ -z "$above" ] && above=$th_mcd
    mcds="$below $th_mcd $above"
    for mcd in $mcds; do
        for mode in forward noForward; do
            for gate in on off; do
                for seed in $SEEDS; do
                    if [ "$gate" = "off" ]; then mcd_arg=1000000000; else mcd_arg=$mcd; fi
                    tag="${mode}_${gate}_s${speed}_ivd${ivd}_mcd${mcd}_seed${seed}"
                    echo "$tag $speed $ivd $mcd_arg $mode $seed" >> "$RUNLIST"
                done
            done
        done
    done
done

TOTAL=$(wc -l < "$RUNLIST")
echo "Total runs: $TOTAL (expected 7 spd × 3 MCDs × 2 modes × 2 gates × N seeds)"
echo ""

# ---------------------------------------------------------------------------
# Parallel execution via xargs -P
# ---------------------------------------------------------------------------
run_one() {
    local tag speed ivd mcd_arg mode seed
    read -r tag speed ivd mcd_arg mode seed <<< "$1"

    local outDir="$OUTPUT_DIR/$tag"
    mkdir -p "$outDir"
    local logFile="$outDir/sim.log"

    "$BINARY" \
        --runId="$tag" \
        --seed="$seed" \
        --speed="$speed" \
        --ivd="$ivd" \
        --mcd="$mcd_arg" \
        --relayNode=4 \
        --relayMode="$mode" \
        --relayStageDelayMs=0 \
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
echo ""
echo "=== All done. Results in $OUTPUT_DIR ==="