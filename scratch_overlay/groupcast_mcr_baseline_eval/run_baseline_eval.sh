#!/usr/bin/env bash
# run_baseline_eval.sh — campaign runner for groupcast_mcr_baseline_eval.
#
# Cross-product sweep:
#   N           ∈ {8, 10, 16, 20}
#   harqOption  ∈ {1, 2}
#   traffic     ∈ {periodic1, periodic2, aperiodic1, aperiodic2}
#   speed-IVD   ∈ 7 safe-headway coupled pairs (verbatim from run_relay_storm.sh)
#   MCR         ∈ per-(N,IVD) grid from gen_mcr_grid.py
#   seeds       ∈ configurable (default 1..3)
#
# Each cell = (N, harqOption, traffic, speed, ivd, mcr) expanded into M seed runs.
# After all M seeds complete, aggregate_baseline.py finalizes the cell:
#   1. extracts per-seed KPIs,
#   2. computes the cell aggregate,
#   3. deletes M-1 .db files (keeps seed1.db for inspection).
#
# Usage:
#   ./run_baseline_eval.sh [outputDir] [simTime] [seeds] [nParallel]
#
# Env toggles:
#   QUICK=1   Reduced first-pass subset (periodic2 only; smaller MCR grid).
#   SEEDS=<list of ints>  Override default seed list.
#
# Examples:
#   QUICK=1 ./run_baseline_eval.sh ./results_quick 5.0 "1 2 3" 10
#   ./run_baseline_eval.sh ./results 5.0 "$(seq -s' ' 1 10)" 10

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3_DIR="$(cd "$SCRIPT_DIR/../../ns-3-dev" && pwd)"
KPI_SCRIPT="$SCRIPT_DIR/../../scratch_overlay/common/kpi/kpi_postprocess.py"

# Auto-detect binary (build profile may be -default or -optimized).
BIN_DIR="$NS3_DIR/cmake-cache-opt/scratch/groupcast_mcr_baseline_eval"
BINARY="$(ls "$BIN_DIR"/ns3.*-groupcast_mcr_baseline_eval-* 2>/dev/null | head -1 || true)"
if [ -z "$BINARY" ]; then
    echo "ERROR: groupcast_mcr_baseline_eval binary not found in $BIN_DIR"
    echo "Build it first:  cd $NS3_DIR && ./ns3 build groupcast_mcr_baseline_eval"
    exit 1
fi

OUTPUT_DIR="$(mkdir -p "${1:-./results}" && cd "${1:-./results}" && pwd)"
SIM_TIME="${2:-5.0}"
SEEDS="${3:-1 2 3}"
NPAR="${4:-$(sysctl -n hw.logicalcpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"
QUICK="${QUICK:-0}"

# Generate the MCR grid for this run.
GRID_JSON="$OUTPUT_DIR/mcr_grid.json"
if [ "$QUICK" = "1" ]; then
    QUICK_FLAG="--quick"
else
    QUICK_FLAG=""
fi
python3 "$SCRIPT_DIR/gen_mcr_grid.py" --out "$GRID_JSON" $QUICK_FLAG >/dev/null
echo "MCR grid: $GRID_JSON (QUICK=$QUICK)"

# ---------------------------------------------------------------------------
# Build run-list. One line per run, format:
#   tag N speed ivd mcr harqOption traffic seed
# ---------------------------------------------------------------------------
RUNLIST="$OUTPUT_DIR/runlist.txt"
> "$RUNLIST"

# HARQ options + traffic classes.
# NOTE: periodic*/aperiodic* classes require CustomTrafficApplication which
# currently has a pre-existing registration bug in this codebase (see
# README.md). The default campaign uses only the {onoff} class, matching the
# relay_storm_eval baseline so the harness is directly comparable. The
# parser still accepts the other class names for callers who fix the
# upstream bug; aggregate_baseline.py tolerates a missing class gracefully
# by skipping cells whose traffic class failed to run.
HARQ_OPTIONS=(1 2)
TRAFFIC=(onoff)

python3 - "$GRID_JSON" "$RUNLIST" "$SEEDS" <<'PY' || { echo "runlist build failed"; exit 1; }
import json, sys, os
grid_path, runlist_path, seeds_env = sys.argv[1], sys.argv[2], sys.argv[3]
seeds = [int(s) for s in seeds_env.split()]
harq_options = [1, 2]
traffic = ["onoff"]

with open(grid_path) as f:
    g = json.load(f)

lines = []
for row in g["grid"]:
    N = row["N"]; spd = row["speed"]; ivd = row["ivd"]
    for mcr, label in zip(row["mcr_values"], row["mcr_labels"]):
        for harq in harq_options:
            for tc in traffic:
                for seed in seeds:
                    # tag: N{n}_s{spd}_ivd{ivd}_mcr{mcr}_h{harq}_{tc}_seed{seed}
                    mcr_str = ("%.1f" % mcr).replace(".", "p")
                    tag = f"N{N}_s{spd}_ivd{ivd}_mcr{mcr_str}_{label}_h{harq}_{tc}_seed{seed}"
                    lines.append(f"{tag} {N} {spd} {ivd} {mcr:.4f} {harq} {tc} {seed}\n")
with open(runlist_path, "w") as f:
    f.writelines(lines)
print(f"  -> {len(lines)} runs")
PY

TOTAL=$(wc -l < "$RUNLIST")
echo "Total runs: $TOTAL"
echo "Output:     $OUTPUT_DIR"
echo "Binary:     $BINARY"
echo "SimTime:    $SIM_TIME s   Seeds: $SEEDS   Parallel: $NPAR"
echo ""

# ---------------------------------------------------------------------------
# Per-run worker.
# ---------------------------------------------------------------------------
run_one() {
    local line="$1"
    read -r tag N speed ivd mcr harqOption traffic seed <<< "$line"

    local outDir="$OUTPUT_DIR/$tag"
    mkdir -p "$outDir"
    local logFile="$outDir/sim.log"

    "$BINARY" \
        --runId="$tag" --seed="$seed" \
        --numUEs="$N" \
        --speed="$speed" --ivd="$ivd" \
        --mcr="$mcr" \
        --harqOption="$harqOption" \
        --traffic="$traffic" \
        --simTime="$SIM_TIME" \
        --outputDir="$outDir" --simTag="$tag" \
        >"$logFile" 2>&1 \
    && echo "OK   $tag" \
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
# KPI post-processing — defer to aggregate_baseline.py which performs the
# per-cell finalize-and-prune logic. The plain kpi_postprocess.py would run
# on every seed but the finalizer handles extraction + pruning atomically.
# ---------------------------------------------------------------------------
echo "Aggregating & finalizing cells..."
python3 "$SCRIPT_DIR/aggregate_baseline.py" "$OUTPUT_DIR" "$SEEDS" "$SIM_TIME" || {
    echo "WARN: aggregate_baseline.py failed; raw .db files retained."
}
echo ""
echo "=== All done. Results in $OUTPUT_DIR ==="