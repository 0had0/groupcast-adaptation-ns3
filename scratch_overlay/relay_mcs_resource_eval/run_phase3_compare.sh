#!/usr/bin/env bash
# run_phase3_compare.sh — Phase 3: head-to-head baseline vs relay
#
# For each (speed, ivd) pair:
#   baseline run at mcsBaseline = m*_7, no relay.
#   relay run at r*, mcsHop1=m*_{r*}, mcsHop2=m*_{7-r*}, MCD=r**ivd.
# 10 seeds each.
#
# Input: phase1_baseline.csv (for m*_7) and phase2 frontier (for r*, mcs1, mcs2, mcd).
# Usage:
#   ./run_phase3_compare.sh <phase1_baseline_csv> <phase2_sweep_csv> [outputDir] [simTime] [seeds] [nParallel]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3_DIR="$(cd "$SCRIPT_DIR/../../ns-3-dev" && pwd)"
BINARY="$NS3_DIR/cmake-cache-opt/scratch/relay_mcs_resource_eval/ns3.42-relay_mcs_resource_eval-default"
KPI_SCRIPT="$SCRIPT_DIR/../common/kpi/kpi_postprocess.py"
BASE_CSV="${1:?usage: $0 <phase1_baseline_csv> <phase2_sweep_csv> [outputDir] [simTime] [seeds] [nParallel]}"
RELAY_CSV="${2:?usage: $0 <phase1_baseline_csv> <phase2_sweep_csv> [outputDir] [simTime] [seeds] [nParallel]}"
OUTPUT_DIR="$(mkdir -p "${3:-./results_relay_mcs_resource_eval/phase3}" && cd "${3:-./results_relay_mcs_resource_eval/phase3}" && pwd)"
SIM_TIME="${4:-5.0}"
SEEDS="${5:-1 2 3 4 5 6 7 8 9 10}"
NPAR="${6:-$(sysctl -n hw.logicalcpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"

mkdir -p "$OUTPUT_DIR"

echo "=== Phase 3: head-to-head baseline vs relay ==="
echo "Baseline CSV: $BASE_CSV"
echo "Relay CSV:    $RELAY_CSV"
echo "Output:       $OUTPUT_DIR"
echo "SimTime:      $SIM_TIME s"
echo "Seeds:        $SEEDS"
echo "Parallel:     $NPAR jobs"
echo ""

RUNLIST="$OUTPUT_DIR/runlist.txt"
> "$RUNLIST"

# Read baseline (speed,ivd,mcsBaseline) into a map.
declare -A BASE
while IFS=, read -r speed ivd mcsBase kind p_hit res_mean res_std n_seeds; do
    [ "$speed" = "speed" ] && continue
    BASE["${speed},${ivd}"]="$mcsBase"
done < "$BASE_CSV"

# Read relay frontier (speed,ivd,r_star,mcs1,mcs2,...) — we use
# r**ivd as the MCD since the selection CSV doesn't carry MCD.
declare -A RSTAR RELAY1 RELAY2
while IFS=, read -r speed ivd r_star mcs1 mcs2 _rest; do
    [ "$speed" = "speed" ] && continue
    [ -z "$speed" ] && continue
    RSTAR["${speed},${ivd}"]="$r_star"
    RELAY1["${speed},${ivd}"]="$mcs1"
    RELAY2["${speed},${ivd}"]="$mcs2"
done < "$RELAY_CSV"

for key in "${!BASE[@]}"; do
    speed=$(echo "$key" | cut -d, -f1)
    ivd=$(echo "$key"   | cut -d, -f2)
    mcsBase="${BASE[$key]}"
    r="${RSTAR[$key]:-4}"
    mcs1="${RELAY1[$key]:-$mcsBase}"
    mcs2="${RELAY2[$key]:-14}"
    mcd=$((r * ivd))   # default MCD = r * ivd

    for seed in $SEEDS; do
        # Baseline run.
        tag="baseline_s${speed}_ivd${ivd}_mcs${mcsBase}_seed${seed}"
        echo "$tag $speed $ivd baseline $mcsBase $mcsBase 0 0 $seed" >> "$RUNLIST"
        # Relay run.
        tag="relay${r}_s${speed}_ivd${ivd}_mcs1_${mcs1}_mcs2_${mcs2}_mcd${mcd}_seed${seed}"
        echo "$tag $speed $ivd relay $mcs1 $mcs2 $mcd $r $seed" >> "$RUNLIST"
    done
done

TOTAL=$(wc -l < "$RUNLIST")
echo "Total runs:  $TOTAL"
echo ""

# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------
run_one() {
    local tag speed ivd mode mcs1 mcs2 mcd r seed
    read -r tag speed ivd mode mcs1 mcs2 mcd r seed <<< "$1"

    local outDir="$OUTPUT_DIR/$tag"
    mkdir -p "$outDir"
    local logFile="$outDir/sim.log"

    "$BINARY" \
        --runId="$tag" \
        --seed="$seed" \
        --numUEs=8 \
        --mode="$mode" \
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
echo "=== Phase 3 simulation sweep done in $((END-START))s ==="

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
echo "=== Phase 3 done. Results in $OUTPUT_DIR ==="
