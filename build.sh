#!/usr/bin/env bash
# build.sh — configure and build the groupcast_eval + adaptive-phy-eval NS-3 targets.
# Run from the project root after ./setup.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS3="$ROOT/ns-3-dev"

[[ -d "$NS3" ]] || { echo "ERROR: ns-3-dev not found. Run ./setup.sh first."; exit 1; }

echo "[build] Configuring NS-3..."
cd "$NS3"
./ns3 configure --enable-examples --disable-werror

echo "[build] Building groupcast_eval..."
./ns3 build groupcast_eval

echo ""
echo "Build complete. To run a dry-sweep:"
echo "  python3 scratch/adaptive-phy-eval/scripts/run_sweep.py --dry-run --scenarios s1 --mu 1 --w 50 --mcs 14 --seeds 1"
