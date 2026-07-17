#!/bin/bash
# run_seed_sweep.sh — run mikami_v2v_eval with N seeds in sequence.
#
# Usage:
#   ./run_seed_sweep.sh [N_SEEDS] [SIM_TIME_S] [WARMUP_S]
#
# Defaults: 10 seeds, simTime=20s, warmup=2s
# Outputs: /tmp/mikami/seeds/seed{NN}/mikami_seed{NN}.db
#          /tmp/mikami/seeds/seed{NN}/kpis/*.csv

set -euo pipefail

N_SEEDS="${1:-10}"
SIM_TIME="${2:-20.0}"
WARMUP="${3:-2.0}"
ROOT="${MIKAMI_OUT_ROOT:-/tmp/mikami/seeds}"
BIN="/Users/hadih/Documents/groupcast-adaptation-ns3/ns-3-dev/build/scratch/mikami_v2v_eval/ns3.42-mikami_v2v_eval-debug"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "[mikami-seed-sweep] N_SEEDS=$N_SEEDS  simTime=$SIM_TIME  warmup=$WARMUP  out=$ROOT"
mkdir -p "$ROOT"

for ((s = 1; s <= N_SEEDS; s++)); do
  RUN_ID=$(printf "seed%02d" "$s")
  RUN_DIR="$ROOT/$RUN_ID"
  mkdir -p "$RUN_DIR/kpis"
  DB="$RUN_DIR/${RUN_ID}.db"
  echo "[mikami-seed-sweep] expecting DB at $DB"

  echo ""
  echo "[mikami-seed-sweep] === seed=$s  runId=$RUN_ID ==="

  # 1) run sim (CLI flags: --outputDir, --outRunCsv, --outPacketsCsv, --runId, --seed, --simTime, --warmup)
  PKT_CSV="$RUN_DIR/${RUN_ID}_packets.csv"
  RUN_CSV="$RUN_DIR/${RUN_ID}_run.csv"
  "$BIN" \
    --runId="$RUN_ID" \
    --seed="$s" \
    --simTime="$SIM_TIME" \
    --warmup="$WARMUP" \
    --outputDir="$RUN_DIR/" \
    --outRunCsv="$RUN_CSV" \
    --outPacketsCsv="$PKT_CSV" \
    2>&1 | tail -5

  # 2) post-process (DataCache filters pktTxRx/psschRxUePhy by SEED/RUN; pass them through)
  python3 "$HERE/mikami_postprocess.py" \
    --db "$DB" \
    --out-dir "$RUN_DIR/kpis" \
    --warmup "$WARMUP" \
    --seed "$s" \
    --run 1 \
    --tag "$RUN_ID" \
    2>&1 | tail -10
done

# 3) aggregate
echo ""
echo "[mikami-seed-sweep] Aggregating KPIs across seeds..."
python3 "$HERE/aggregate_seed_sweep.py" --root "$ROOT" --out "$ROOT/summary"

echo ""
echo "[mikami-seed-sweep] Done. Summary at: $ROOT/summary/"
ls -la "$ROOT/summary/"