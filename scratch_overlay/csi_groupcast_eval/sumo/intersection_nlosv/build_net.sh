#!/usr/bin/env bash
# build_net.sh — generate intersection_nlosv.net.xml from plain XML sources.
# Run once before using the SUMO scenario.
#
# Requires: SUMO ≥ 1.14 (netconvert in PATH)
set -euo pipefail
cd "$(dirname "$0")"

netconvert \
  --node-files=nodes.nod.xml \
  --edge-files=edges.edg.xml \
  --connection-files=connections.con.xml \
  --output-file=intersection_nlosv.net.xml \
  --no-turnarounds \
  --junctions.corner-detail=5 \
  --geometry.remove \
  --verbose

echo "Network written: intersection_nlosv.net.xml"
