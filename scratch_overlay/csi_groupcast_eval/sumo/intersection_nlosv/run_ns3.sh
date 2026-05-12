#!/usr/bin/env bash
# run_ns3.sh — run csi_groupcast_eval with the intersection_nlosv SUMO scenario.
#
# The platoon: tx_0 (front) + rx_1..rx_8 (rear).
# All 9 vehicles are both communicating UEs and NLOSv blockers.
#
# Key phases during the 60s active window:
#   t≈ 2s  : tx_0 enters int1 (left turn, slows to ~15 km/h)
#   t≈ 5-20s : platoon traversing first corner, IVDs compress to 15-18 m
#   t≈15-25s : platoon on vertical 300m segment — maximum NLOSv
#   t≈25-35s : tx_0..rx_4 exiting int2 (right turn), tx_0 200m ahead of rx_8
#   t≈35+   : full platoon on exit road, IVDs settle
#
# Expected NLOSv behaviour:
#   - On e_vertical  all 9 vehicles at x≈0 in a N-S line → every pair blocked
#   - At turns      geometry creates transient LOS/NLOSv transitions
#   - On exit road   standard LOS platoon (no blocker vehicles crossing path)
#
# Usage:
#   bash run_ns3.sh [--outputDir /tmp/nlosv_results/]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NS3_ROOT="$(cd "${SCRIPT_DIR}/../../../../.." && pwd)/ns-3-dev"
BINARY="${NS3_ROOT}/build/scratch/csi_groupcast_eval/ns3.42-csi_groupcast_eval-optimized"

OUTPUT_DIR="${1:-/tmp/nlosv_results}"
mkdir -p "${OUTPUT_DIR}"

RUN_ID="nlosv_int"

"${BINARY}" \
  --runId="${RUN_ID}" \
  --seed=1 \
  --numUEs=9 \
  --mu=1 \
  --bwPRBs=10 \
  --mcs=14 \
  --simTime=60 \
  --warmup=2 \
  --harq=true \
  --maxNumTx=5 \
  --psfchPeriod=0 \
  --baseScenario=V2V_Urban \
  --shadowing=true \
  --sumoCfg="${SCRIPT_DIR}/intersection_nlosv.sumocfg" \
  --sumoBinary="" \
  --sumoSync=0.1 \
  --blockerIds="tx_0,rx_1,rx_2,rx_3,rx_4,rx_5,rx_6,rx_7,rx_8" \
  --blockerL=4.5 \
  --blockerW=2.0 \
  --outputDir="${OUTPUT_DIR}/" \
  --outRunCsv="${OUTPUT_DIR}/${RUN_ID}_run.csv" \
  --outPacketsCsv="${OUTPUT_DIR}/${RUN_ID}_packets.csv"

echo ""
echo "Done. KPI database: ${OUTPUT_DIR}/${RUN_ID}.db"
echo ""
echo "Analyse SINR evolution:"
echo "  python3 ${SCRIPT_DIR}/../../sinr_evolution.py \\"
echo "    ${OUTPUT_DIR}/${RUN_ID}.db --window_ms 500 --all-pairs"
echo ""
echo "Estimate CSI reports:"
echo "  python3 ${SCRIPT_DIR}/../../csi_report_estimator.py \\"
echo "    ${OUTPUT_DIR}/${RUN_ID}.db --window_ms 500 --all-pairs"
