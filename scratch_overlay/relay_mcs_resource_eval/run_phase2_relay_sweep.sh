#!/usr/bin/env bash
# run_phase2_relay_sweep.sh — Phase 2: relay selection (R-B) sweep
#
# Sweeps:
#   - 7 (speed, IVD) pairs
#   - Relay node: {2, 3, 4, 5, 6}
#   - 10 seeds
#   - mode=relay
#   - mcsHop1 = m*_r,    mcsHop2 = m*_{7-r}  (passed in via mcsBaseline/mcsHop2)
#   - MCD = r * ivd  (the minimum that covers hop-2 tail)
#
# m*_r and m*_{7-r} are read from <phase1_mstar.csv> produced by aggregate.py
# phase1.  Per pair, for each r in 2..6:
#   mcsHop1 = mstar at (pair, r)
#   mcsHop2 = mstar at (pair, 7-r)
#   MCD     = r * ivd
#
# Output: results_relay_mcs_resource_eval/phase2/<tag>/*.db
#
# Usage:
#   ./run_phase2_relay_sweep.sh <phase1_mstar_csv> [outputDir] [simTime] [seeds] [nParallel]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3_DIR="$(cd "$SCRIPT_DIR/../../ns-3-dev" && pwd)"
BINARY="$NS3_DIR/cmake-cache-opt/scratch/relay_mcs_resource_eval/ns3.42-relay_mcs_resource_eval-default"
KPI_SCRIPT="$SCRIPT_DIR/../common/kpi/kpi_postprocess.py"
MSTAR_CSV="${1:?usage: $0 <phase1_mstar_csv> [outputDir] [simTime] [seeds] [nParallel]}"
OUTPUT_DIR="$(mkdir -p "${2:-./results_relay_mcs_resource_eval/phase2}" && cd "${2:-./results_relay_mcs_resource_eval/phase2}" && pwd)"
SIM_TIME="${3:-5.0}"
SEEDS="${4:-1 2 3 4 5 6 7 8 9 10}"
NPAR="${5:-$(sysctl -n hw.logicalcpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"

mkdir -p "$OUTPUT_DIR"

# SPEED_IVD_PAIRS must match Phase 1
SPEED_IVD_PAIRS=(
    "80  25"
    "90  27"
    "100 29"
    "110 32"
    "120 34"
    "130 37"
    "140 39"
)

echo "=== Phase 2: relay selection (R-B) sweep ==="
echo "mstar CSV:  $MSTAR_CSV"
echo "Output:     $OUTPUT_DIR"
echo "SimTime:    $SIM_TIME s"
echo "Seeds:      $SEEDS"
echo "Parallel:   $NPAR jobs"
echo ""

# Read mstar into shell map: (speed,ivd,rx) -> mcs
# Format: speed,ivd,rx_node_id,m_star,kind,p_hit_at_mstar,n_mcs_qualifying
declare -A MSTAR
while IFS=, read -r speed ivd rx mstar kind p_hit n_qual; do
    MSTAR["${speed},${ivd},${rx}"]="$mstar"
done < <(tail -n +2 "$MSTAR_CSV")

# Build run-list: tag speed ivd r mcsHop1 mcsHop2 mcd seed
RUNLIST="$OUTPUT_DIR/runlist.txt"
> "$RUNLIST"

for speedivd in "${SPEED_IVD_PAIRS[@]}"; do
    speed=$(echo "$speedivd" | awk '{print $1}')
    ivd=$(echo "$speedivd"   | awk '{print $2}')
    for r in 2 3 4 5 6; do
        mcs1="${MSTAR[${speed},${ivd},${r}]:-?}"
        mcs2="${MSTAR[${speed},${ivd},$((7-r))]:-?}"
        if [[ "$mcs1" == "?" || "$mcs2" == "?" ]]; then
            echo "WARN: missing mstar for (speed=$speed, ivd=$ivd, r=$r) or (7-r=$((7-r))) -- skipping" >&2
            continue
        fi
        mcd=$((r * ivd))
        for seed in $SEEDS; do
            tag="relay${r}_s${speed}_ivd${ivd}_mcs1_${mcs1}_mcs2_${mcs2}_mcd${mcd}_seed${seed}"
            echo "$tag $speed $ivd $r $mcs1 $mcs2 $mcd $seed" >> "$RUNLIST"
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
echo "=== Phase 2 simulation sweep done in $((END-START))s ==="

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
echo "=== Phase 2 done. Results in $OUTPUT_DIR ==="
