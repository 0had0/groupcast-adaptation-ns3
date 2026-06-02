#!/usr/bin/env python3
"""
fcd_to_mob.py — Convert a SUMO FCD XML output into the ns-3 mobility trace format
expected by scratch_overlay/csi_groupcast_eval/mobility.cc.

Output format (comma-separated, no spaces):
    # SUMO FCD mobility trace — node ID mapping:
    #   node_0 = <vehicle_id>
    #   ...
    time,node_id,x,y,speed

Usage:
    python3 fcd_to_mob.py \\
        --fcd    fcd_output.xml \\
        --output ccam_platoon_shadowing.mob.csv \\
        --order  platoon_leader,platoon_follower_1,...,platoon_follower_9 \\
        [--extra-order platoon_blocker_truck]

The --order list assigns node IDs starting at 0.  Any vehicle found in the
FCD that is NOT in --order is appended in first-seen order, starting at the
next available ID.  This ensures platoon vehicles are always node_0 .. node_9.
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fcd",    required=True, help="SUMO FCD XML output file")
    ap.add_argument("--output", required=True, help="Destination mob.csv path")
    ap.add_argument("--order",  required=True,
                    help="Comma-separated SUMO vehicle IDs in desired node-index order")
    ap.add_argument("--extra-order", default="",
                    help="Additional IDs to append (e.g. blocker truck)")
    args = ap.parse_args()

    explicit_order = [v.strip() for v in args.order.split(",") if v.strip()]
    if args.extra_order:
        explicit_order += [v.strip() for v in args.extra_order.split(",") if v.strip()]

    # Pre-seed ID map with the explicit order
    id_map: dict[str, int] = {vid: i for i, vid in enumerate(explicit_order)}
    ordered_ids: list[str] = list(explicit_order)

    # Parse FCD XML
    print(f"[fcd_to_mob] Parsing {args.fcd} …", flush=True)
    tree = ET.parse(args.fcd)
    root = tree.getroot()

    rows: list[tuple] = []
    for ts in root.iter("timestep"):
        t = float(ts.attrib["time"])
        for veh in ts.iter("vehicle"):
            vid = veh.attrib["id"]
            if vid not in id_map:
                # Append in first-seen order
                id_map[vid] = len(ordered_ids)
                ordered_ids.append(vid)
            nid = id_map[vid]
            x   = float(veh.attrib.get("x",     0.0))
            y   = float(veh.attrib.get("y",     0.0))
            spd = float(veh.attrib.get("speed", 0.0))
            rows.append((t, nid, x, y, spd))

    # Write output
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        f.write("# SUMO FCD mobility trace — node ID mapping:\n")
        for i, vid in enumerate(ordered_ids):
            f.write(f"#   node_{i} = {vid}\n")
        for (t, nid, x, y, spd) in rows:
            f.write(f"{t:.3f},{nid},{x:.4f},{y:.4f},{spd:.4f}\n")

    print(f"[fcd_to_mob] Wrote {len(rows)} rows → {out}", flush=True)
    print(f"[fcd_to_mob] Vehicle → node mapping:", flush=True)
    for i, vid in enumerate(ordered_ids):
        print(f"  node_{i:3d} = {vid}")


if __name__ == "__main__":
    main()
