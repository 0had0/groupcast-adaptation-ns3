#!/usr/bin/env bash
# run_c4_campaign.sh — the C4 head-to-head: does the distinct-L2ID relay beat a
# worst-RX-adaptive single hop?  Full 7 operating points, statistics-grade seeds.
#
# MCD-trim (validated): only `sameL2` responds to MCD, so it is swept over
# {80,300}; every other arm runs at a single MCD (80) — cuts the grid ~half.
#
# Arms per (op-point, seed):           MCD
#   none        1-hop, fixed MCS-14    80
#   adapt       1-hop, worst-RX adaptive MCS   80      <- the C4 competitor
#   sameL2 @{4,6}  shared-L2ID (storm)  80, 300
#   fixed  @{4,6}  distinct-L2ID part.  80
#   broadcast@{4,6} distinct-L2ID bcast 80
#
# Usage:  ./run_c4_campaign.sh [outDir] [simTime] [seeds] [nPar]
#   ./run_c4_campaign.sh ./results_c4 5.0 "$(seq -s' ' 1 30)" 10
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3_DIR="$(cd "$SCRIPT_DIR/../../ns-3-dev" && pwd)"
KPI_SCRIPT="$SCRIPT_DIR/../../scratch_overlay/common/kpi/kpi_postprocess.py"
BINARY="$(ls "$NS3_DIR"/cmake-cache-opt/scratch/relay_l2id_compare/ns3.*-relay_l2id_compare-* 2>/dev/null | head -1 || true)"
[ -z "$BINARY" ] && { echo "Build first: cd $NS3_DIR && ./ns3 build relay_l2id_compare"; exit 1; }

OUTPUT_DIR="$(mkdir -p "${1:-./results_c4}" && cd "${1:-./results_c4}" && pwd)"
SIM_TIME="${2:-5.0}"; SEEDS="${3:-$(seq -s' ' 1 30)}"
NPAR="${4:-$(sysctl -n hw.logicalcpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"
NUMUES=9; SCENARIO="V2V_Highway"
POS=(4 6); SAMEL2_MCDS=(80 300); BASE_MCD=80
SPEED_IVD=("80 25" "90 27" "100 29" "110 32" "120 34" "130 37" "140 39")

RUNLIST="$OUTPUT_DIR/runlist.txt"; > "$RUNLIST"
emit(){ echo "$1 $2 $3 $4 $5 $6 $7 $8" >> "$RUNLIST"; }   # tag mode relay adapt speed ivd mcd seed
for si in "${SPEED_IVD[@]}"; do read -r sp iv <<< "$si"
  for sd in $SEEDS; do
    emit "none_s${sp}_seed${sd}"  none 4 false $sp $iv $BASE_MCD $sd
    emit "adapt_s${sp}_seed${sd}" none 4 true  $sp $iv $BASE_MCD $sd
    for p in "${POS[@]}"; do
      emit "fixed${p}_s${sp}_seed${sd}"     fixed     $p false $sp $iv $BASE_MCD $sd
      emit "broadcast${p}_s${sp}_seed${sd}" broadcast $p false $sp $iv $BASE_MCD $sd
      for m in "${SAMEL2_MCDS[@]}"; do
        emit "sameL2${p}_s${sp}_mcd${m}_seed${sd}" sameL2 $p false $sp $iv $m $sd
      done
    done
  done
done
echo "C4 campaign: $(wc -l < "$RUNLIST") runs | $BINARY"

run_one(){ read -r tag mode relay adapt sp iv mcd seed <<< "$1"
  local d="$OUTPUT_DIR/$tag"; mkdir -p "$d"
  "$BINARY" --runId="$tag" --seed="$seed" --relayMode="$mode" --relayNode="$relay" \
    --adaptiveMcs="$adapt" --numUEs="$NUMUES" --scenario="$SCENARIO" \
    --speed="$sp" --ivd="$iv" --mcd="$mcd" --simTime="$SIM_TIME" \
    --outputDir="$d" --simTag="$tag" >"$d/sim.log" 2>&1 \
    && echo "OK   $tag" || echo "FAIL $tag"; }
export -f run_one; export BINARY OUTPUT_DIR SIM_TIME NUMUES SCENARIO
xargs -P "$NPAR" -I{} bash -c 'run_one "$@"' _ {} < "$RUNLIST"

if [ -f "$KPI_SCRIPT" ]; then
  kpi_one(){ local db="$1"; local sn; sn=$(basename "$db" .db | grep -oE 'seed[0-9]+' | grep -oE '[0-9]+'); \
    python3 "$KPI_SCRIPT" --db "$db" --out "${db%.db}_kpi.csv" --warmup 1.0 --window 1.0 --step 0.5 --seed 1 --run "${sn:-1}" 2>&1 | tail -1; }
  export -f kpi_one; export KPI_SCRIPT
  find "$OUTPUT_DIR" -name '*.db' -print0 | xargs -0 -P "$NPAR" -I{} bash -c 'kpi_one "$@"' _ {}
fi
echo "=== C4 campaign done -> $OUTPUT_DIR ==="
