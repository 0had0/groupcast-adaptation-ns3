#!/bin/bash
# Build the SUMO network for the platoon_intersection_merge_split scenario.
set -e
cd "$(dirname "$0")"
NETCONVERT="/Users/hadih/Documents/groupcast-adaptation-ns3/sumo/bin/netconvert"
if [ ! -x "$NETCONVERT" ]; then
    echo "Error: netconvert not found at $NETCONVERT"; exit 1
fi
echo "Building network..."
$NETCONVERT \
    --node-files=nodes.nod.xml \
    --edge-files=edges.edg.xml \
    --connection-files=connections.con.xml \
    --output-file=net.net.xml \
    --no-turnarounds \
    --geometry.remove=true \
    --junctions.corner-detail=5
echo "Network built: net.net.xml"
