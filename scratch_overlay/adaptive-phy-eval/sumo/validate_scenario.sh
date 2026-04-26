#!/bin/bash
# Validate SUMO scenario in GUI
# Usage: ./validate_scenario.sh <scenario> <condition>
# Example: ./validate_scenario.sh s1 nrx08

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ADAPTIVE_PHY_EVAL="$(dirname "$SCRIPT_DIR")"
SUMO_DIR="$ADAPTIVE_PHY_EVAL/sumo"
SUMO_BIN="/Users/hadih/Documents/groupcast-adaptation-ns3/sumo/bin"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

if [ $# -lt 2 ]; then
    echo "Usage: $0 <scenario> <condition>"
    echo ""
    echo "Examples:"
    echo "  $0 s1 nrx08      # Validate S1, N_rx=8"
    echo "  $0 s2 ivd050m     # Validate S2, IVD=50m"
    echo "  $0 s3 spd100kmh   # Validate S3, speed=100km/h"
    echo "  $0 s4 urban_nlos  # Validate S4 urban"
    exit 1
fi

SCENARIO="$1"
CONDITION="$2"

# Map condition to folder name
case "$SCENARIO" in
    s1)
        FOLDER="$SUMO_DIR/s1_group_size/s1_$CONDITION"
        ;;
    s2)
        FOLDER="$SUMO_DIR/s2_channel_heterogeneity/s2_$CONDITION"
        ;;
    s3)
        FOLDER="$SUMO_DIR/s3_speed_doppler/s3_$CONDITION"
        ;;
    s4)
        FOLDER="$SUMO_DIR/s4_propagation/s4_$CONDITION"
        ;;
    *)
        echo -e "${RED}Error: Unknown scenario $SCENARIO${NC}"
        exit 1
        ;;
esac

# Check folder exists
if [ ! -d "$FOLDER" ]; then
    echo -e "${RED}Error: Scenario folder not found: $FOLDER${NC}"
    echo "Did you run generate_scenarios.py first?"
    exit 1
fi

# Check SUMO config exists
SUMO_CFG="$FOLDER/highway.sumocfg"
if [ ! -f "$SUMO_CFG" ]; then
    echo -e "${RED}Error: SUMO config not found: $SUMO_CFG${NC}"
    exit 1
fi

echo -e "${GREEN}Starting SUMO GUI for $SCENARIO / $CONDITION${NC}"
echo "Config: $SUMO_CFG"
echo ""

# Print checklist
echo "=========================================================="
echo "=== VISUAL VALIDATION CHECKLIST ========================"
echo "=========================================================="
echo -e "${YELLOW}[ ] Correct number of vehicles visible${NC}"
echo -e "${YELLOW}[ ] TX vehicle (tx_0) is at the expected position${NC}"
echo -e "${YELLOW}[ ] RX vehicles are behind TX at correct spacing${NC}"
echo -e "${YELLOW}[ ] All vehicles moving in the same direction${NC}"
echo -e "${YELLOW}[ ] All vehicles at the expected speed${NC}"
echo -e "${YELLOW}[ ] No vehicles outside the road / stuck / colliding${NC}"
echo -e "${YELLOW}[ ] Simulation runs for full duration without SUMO errors${NC}"
echo "=========================================================="
echo ""
echo "Press Ctrl+C when done."
echo ""

# Launch SUMO GUI
"$SUMO_BIN/sumo-gui" -c "$SUMO_CFG"