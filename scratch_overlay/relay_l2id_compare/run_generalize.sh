#!/usr/bin/env bash
# run_generalize.sh — external-validity sweep: do the storm (sameL2) and the
# distinct-L2ID fix (fixed) hold across platoon size and scenario?
#
# Axes:  numUEs N x scenario {highway,urban} x 2 operating points.
# Relay positions scale with N (close ~0.45*(N-1), far ~0.7*(N-1)).
# MCD: sameL2 swept {80,300} (the only MCD-sensitive arm); others at 80.
#
# Usage:  ./run_generalize.sh [outDir] [simTime] [seeds] [nPar]
# Env:    NS="9 16"   SCENS="V2V_Highway V2V_Urban"
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3_DIR="$(cd "$SCRIPT_DIR/../../ns-3-dev" && pwd)"
KPI_SCRIPT="$SCRIPT_DIR/../../scratch_overlay/common/kpi/kpi_postprocess.py"
BINARY="$(ls "$NS3_DIR"/cmake-cache-opt/scratch/relay_l2id_compare/ns3.*-relay_l2id_compare-* 2>/dev/null | head -1 || true)"
[ -z "$BINARY" ] && { echo "Build first: cd $NS3_DIR && ./ns3 build relay_l2id_compare"; exit 1; }

OUTPUT_DIR="$(mkdir -p "${1:-./results_gen}" && cd "${1:-./results_gen}" && pwd)"
SIM_TIME="${2:-5.0}"; SEEDS="${3:-$(seq -s' ' 1 20)}"
NPAR="${4:-$(sysctl -n hw.logicalcpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"
NS="${NS:-9 16}"; SCENS="${SCENS:-V2V_Highway V2V_Urban}"
SAMEL2_MCDS=(80 300); BASE_MCD=80
SPEED_IVD=("80 25" "140 39")
pos(){ awk -v n="$1" -v f="$2" 'BEGIN{p=int(f*(n-1)+0.5); if(p<1)p=1; if(p>n-2)p=n-2; print p}'; }

RUNLIST="$OUTPUT_DIR/runlist.txt"; > "$RUNLIST"
# tag mode relay adapt numUEs scen speed ivd mcd
for N in $NS; do
  CLOSE=$(pos "$N" 0.45); FAR=$(pos "$N" 0.70)
  for SC in $SCENS; do
    sct=$(echo "$SC" | tr 'A-Z_' 'a-z-')
    for si in "${SPEED_IVD[@]}"; do read -r sp iv <<< "$si"
      for sd in $SEEDS; do
        b="N${N}_${sct}_s${sp}_seed${sd}"
        echo "none_${b} none 4 false $N $SC $sp $iv $BASE_MCD $sd"   >> "$RUNLIST"
        echo "adapt_${b} none 4 true $N $SC $sp $iv $BASE_MCD $sd"   >> "$RUNLIST"
        for p in "$CLOSE" "$FAR"; do
          echo "fixed${p}_${b} fixed $p false $N $SC $sp $iv $BASE_MCD $sd"         >> "$RUNLIST"
          echo "broadcast${p}_${b} broadcast $p false $N $SC $sp $iv $BASE_MCD $sd" >> "$RUNLIST"
          for m in "${SAMEL2_MCDS[@]}"; do
            echo "sameL2${p}_${b}_mcd${m} sameL2 $p false $N $SC $sp $iv $m $sd"    >> "$RUNLIST"
          done
        done
      done
    done
  done
done
echo "generalization: $(wc -l < "$RUNLIST") runs (N={$NS} x scen={$SCENS})"

run_one(){ read -r tag mode relay adapt numu scen sp iv mcd seed <<< "$1"
  local d="$OUTPUT_DIR/$tag"; mkdir -p "$d"
  "$BINARY" --runId="$tag" --seed="$seed" --relayMode="$mode" --relayNode="$relay" \
    --adaptiveMcs="$adapt" --numUEs="$numu" --scenario="$scen" \
    --speed="$sp" --ivd="$iv" --mcd="$mcd" --simTime="$SIM_TIME" \
    --outputDir="$d" --simTag="$tag" >"$d/sim.log" 2>&1 \
    && echo "OK   $tag" || echo "FAIL $tag"; }
export -f run_one; export BINARY OUTPUT_DIR SIM_TIME
xargs -P "$NPAR" -I{} bash -c 'run_one "$@"' _ {} < "$RUNLIST"

if [ -f "$KPI_SCRIPT" ]; then
  kpi_one(){ local db="$1"; local sn; sn=$(basename "$db" .db | grep -oE 'seed[0-9]+' | grep -oE '[0-9]+'); \
    python3 "$KPI_SCRIPT" --db "$db" --out "${db%.db}_kpi.csv" --warmup 1.0 --window 1.0 --step 0.5 --seed 1 --run "${sn:-1}" 2>&1 | tail -1; }
  export -f kpi_one; export KPI_SCRIPT
  find "$OUTPUT_DIR" -name '*.db' -print0 | xargs -0 -P "$NPAR" -I{} bash -c 'kpi_one "$@"' _ {}
fi
echo "=== generalization done -> $OUTPUT_DIR ==="
