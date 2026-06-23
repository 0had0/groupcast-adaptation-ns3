#!/usr/bin/env bash
# run_regime_study.sh — relay_storm_eval REGIME sweep driver
#
# A controlled regime experiment: pin the relay position to node 4 or 6,
# sweep MCD *relative to IVD* so that runs land cleanly in each of the
# three hop-1 feedback regimes:
#   - out       : MCD < relay_node * IVD
#   - worst     : relay_node*IVD <= MCD < (relay_node+1)*IVD
#   - not_worst : MCD >= (relay_node+1)*IVD
#
# Sweep:
#   - 2 (speed, IVD) operating points: 100 km/h @ 29 m, 120 km/h @ 34 m
#   - MCD: 11 fine-grained values straddling the regime boundaries
#           (80, 100, 110, 115, 120, 130, 140, 150, 175, 200, 250 m)
#   - Relay node: {4, 6}  (separate runs, never both at once)
#   - No-relay baseline at every MCD x speed
#   - Seeds: 1, 2, 3
#
# Usage:
#   ./run_regime_study.sh [outputDir] [simTime] [seeds] [nParallel]
#
# Example (3 seeds, all 10 cores):
#   ./run_regime_study.sh ./results_regime_study 30 "1 2 3" 10

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3_DIR="$(cd "$SCRIPT_DIR/../../ns-3-dev" && pwd)"
BINARY="$NS3_DIR/cmake-cache-opt/scratch/relay_storm_eval/ns3.42-relay_storm_eval-default"
KPI_SCRIPT="$SCRIPT_DIR/../../scratch_overlay/common/kpi/kpi_postprocess.py"

# Resolve output dir to absolute path (ns3 run executes from ns-3-dev/).
OUTPUT_DIR="$(mkdir -p "${1:-./results_regime_study}" && cd "${1:-./results_regime_study}" && pwd)"
SIM_TIME="${2:-30.0}"
SEEDS="${3:-1 2 3}"
NPAR="${4:-$(sysctl -n hw.logicalcpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"

mkdir -p "$OUTPUT_DIR"

# Two operating points only — keep the wall time bounded and the regime
# comparison statistically clean (one IVD ~= 30 m, so 11 MCD values cleanly
# cover the three regime windows for both relay positions).
SPEED_IVD_PAIRS=(
    "100 29"
    "120 34"
)

# 11 MCD values straddling the regime boundaries for both op-points:
#   relay4: dist=4*IVD ∈ {116, 136}     →  out  boundary 116 ; worst band 116-145 / 136-170 ; not_worst above
#   relay6: dist=6*IVD ∈ {174, 204}     →  out  boundary 174 / 204 ; worst band 174-203 / 204-238 ; not_worst above
# A 80 m value is well "out" for both; 250 m is well "not_worst" for both.
MCD_VALUES=(80 100 110 115 120 130 140 150 175 200 250)
RELAY_NODES=(4 6)

echo "=== relay_storm_eval REGIME sweep ==="
echo "Output:      $OUTPUT_DIR"
echo "Binary:      $BINARY"
echo "SimTime:     $SIM_TIME s"
echo "Seeds:       $SEEDS"
echo "Parallel:    $NPAR jobs"
echo "Speed/IVD:   ${#SPEED_IVD_PAIRS[@]} pairs (${SPEED_IVD_PAIRS[*]})"
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
EXPECTED=$(( ${#SPEED_IVD_PAIRS[@]} * ${#MCD_VALUES[@]} * (1 + ${#RELAY_NODES[@]}) * $(echo $SEEDS | wc -w) ))
echo "Total runs: $TOTAL  (expected: $EXPECTED)"
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
