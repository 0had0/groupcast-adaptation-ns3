#!/usr/bin/env python3
"""
replay_scenario.py — Visualise a .mob.csv platoon trace in sumo-gui via TraCI.

Usage
-----
    cd sumo_scenarios
    python3 replay_scenario.py slinky
    python3 replay_scenario.py gap_open  --delay 200 --follow 8
    python3 replay_scenario.py merge     --delay 150 --zoom 600

Options
-------
  --delay  MS    ms delay between simulation steps in the GUI (default: 100 = real-time)
                 Increase to slow down playback (e.g. 300), decrease to speed up (e.g. 30).
  --follow ID    Node ID to track in the camera (default: 4, mid-platoon).
  --zoom   Z     Initial zoom level (default: 500).
  --regen        Force regeneration of SUMO network/route/config files.

Legend
------
  Red   = TX leader (node 0)
  Blue  = platoon followers (nodes 1–4, or all followers in slinky/gap_open)
  Green = joining vehicles (nodes 5–8, merge scenario only)
"""

import argparse
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Locate SUMO
# ---------------------------------------------------------------------------
def _find_sumo_home() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        repo_root / "sumo",                                   # local build next to repo
        Path(os.environ.get("SUMO_HOME", "__unset__")),
        Path("/opt/homebrew/opt/sumo"),
        Path("/usr/local/opt/sumo"),
        Path("/usr/share/sumo"),
    ]
    for c in candidates:
        if c.is_dir() and (c / "bin" / "sumo-gui").is_file():
            return c
    raise FileNotFoundError(
        "Cannot find sumo-gui.  Set SUMO_HOME, or place SUMO at <repo_root>/sumo/.\n"
        "  repo_root tried: " + str(repo_root)
    )

SUMO_HOME   = _find_sumo_home()
SUMO_GUI    = SUMO_HOME / "bin" / "sumo-gui"
NETCONVERT  = SUMO_HOME / "bin" / "netconvert"

sys.path.insert(0, str(SUMO_HOME / "tools"))
import traci                              # noqa: E402 (path must be set first)
import traci.exceptions as traci_exc     # noqa: E402

# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent

SCENARIOS: dict[str, dict] = {
    "slinky": dict(
        trace   = HERE / "ccam_platoon_slinky"   / "ccam_platoon_slinky.mob.csv",
        label   = "Scenario A — Slinky/Accordion  (sinusoidal gap oscillation, 3 cycles)",
        joining = [],        # no joining vehicles
    ),
    "gap_open": dict(
        trace   = HERE / "ccam_platoon_gap_open" / "ccam_platoon_gap_open.mob.csv",
        label   = "Scenario B — CACC Gap Opening  (tight → expanded → tight, 2 s/hop delay)",
        joining = [],
    ),
    "merge": dict(
        trace   = HERE / "ccam_platoon_merge"    / "ccam_platoon_merge.mob.csv",
        label   = "Scenario C — Platoon Merge  (4 joining vehicles close from 200–380 m)",
        joining = [5, 6, 7, 8],   # nodes that are green
    ),
}

# ---------------------------------------------------------------------------
# Helpers: vehicle type/colour by node ID
# ---------------------------------------------------------------------------
def _vtype(nid: int, joining: list) -> str:
    if nid == 0:
        return "leader"
    if nid in joining:
        return "joining"
    return "follower"


# ---------------------------------------------------------------------------
# SUMO file generators  (called once; result is cached next to the trace)
# ---------------------------------------------------------------------------
def _gen_network(out_dir: Path) -> Path:
    net = out_dir / "highway.net.xml"
    if net.exists():
        return net

    nod = out_dir / "_tmp.nod.xml"
    edg = out_dir / "_tmp.edg.xml"
    nod.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<nodes>\n'
        '    <node id="A" x="-1000.00" y="-8.00"/>\n'
        '    <node id="B" x="6000.00"  y="-8.00"/>\n'
        '</nodes>\n'
    )
    edg.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<edges>\n'
        '    <edge id="highway" from="A" to="B" numLanes="1"\n'
        '          speed="55.56" priority="3" spreadType="center"/>\n'
        '</edges>\n'
    )
    subprocess.run(
        [str(NETCONVERT),
         "--node-files", str(nod),
         "--edge-files", str(edg),
         "--no-internal-links",
         "--no-warnings",
         "-o", str(net)],
        check=True, capture_output=True,
    )
    nod.unlink(missing_ok=True)
    edg.unlink(missing_ok=True)
    print(f"  [setup] generated {net.name}")
    return net


def _get_net_offset(net: Path) -> tuple:
    """
    Parse the coordinate offset netconvert applied so we can convert
    trace world coordinates (x, y) to SUMO internal coordinates:
        sumo_x = world_x + offset_x
        sumo_y = world_y + offset_y
    """
    tree = ET.parse(net)
    loc  = tree.getroot().find("location")
    ox, oy = map(float, loc.get("netOffset").split(","))
    return ox, oy


def _gen_routes(out_dir: Path, node_ids: list, joining: list) -> Path:
    rou = out_dir / "platoon.rou.xml"
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<routes>\n',
        '    <vType id="leader"   color="220,50,50"  length="4.5" width="2.0"'
        ' maxSpeed="60" minGap="0.5" guiShape="passenger"/>\n',
        '    <vType id="follower" color="50,100,220" length="4.5" width="2.0"'
        ' maxSpeed="60" minGap="0.5" guiShape="passenger"/>\n',
        '    <vType id="joining"  color="50,180,60"  length="4.5" width="2.0"'
        ' maxSpeed="60" minGap="0.5" guiShape="passenger"/>\n',
        '    <route id="r0" edges="highway"/>\n',
    ]
    # NOTE: no <vehicle> elements here — vehicles are added at runtime via TraCI
    lines.append('</routes>\n')
    rou.write_text(''.join(lines))
    print(f"  [setup] generated {rou.name}")
    return rou


def _gen_cfg(out_dir: Path, net: Path, rou: Path) -> Path:
    cfg = out_dir / "run.sumocfg"
    cfg.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<configuration>\n'
        '    <input>\n'
        f'        <net-file value="{net.resolve()}"/>\n'
        f'        <route-files value="{rou.resolve()}"/>\n'
        '    </input>\n'
        '    <time>\n'
        '        <begin value="0"/>\n'
        '        <step-length value="0.1"/>\n'
        '    </time>\n'
        '    <processing>\n'
        '        <ignore-route-errors value="true"/>\n'
        '        <collision.action value="none"/>\n'
        '    </processing>\n'
        '    <gui_only>\n'
        '        <start value="true"/>\n'
        '        <quit-on-end value="false"/>\n'
        '    </gui_only>\n'
        '</configuration>\n'
    )
    print(f"  [setup] generated {cfg.name}")
    return cfg


# ---------------------------------------------------------------------------
# Main replay
# ---------------------------------------------------------------------------
def replay(scenario_key: str, delay_ms: int, follow_node: int,
           zoom: float, regen: bool) -> None:

    scen      = SCENARIOS[scenario_key]
    trace     = scen["trace"]
    joining   = scen["joining"]

    if not trace.exists():
        sys.exit(f"ERROR: trace not found:\n  {trace}")

    # ---- Load trace --------------------------------------------------------
    df = pd.read_csv(trace, comment="#", header=None,
                     names=["time", "node_id", "x", "y", "speed"])
    df["time"]    = df["time"].round(3)
    df["node_id"] = df["node_id"].astype(int)

    timesteps = sorted(df["time"].unique())
    node_ids  = sorted(df["node_id"].unique())
    step0     = round(timesteps[1] - timesteps[0], 3)

    print()
    print("=" * 65)
    print(f"  {scen['label']}")
    print("=" * 65)
    print(f"  Vehicles  : {len(node_ids)}  (IDs {node_ids[0]}..{node_ids[-1]})")
    print(f"  Duration  : {timesteps[-1]:.1f} s  ({len(timesteps)} steps × {step0:.2f} s)")
    print(f"  X range   : {df['x'].min():.0f} – {df['x'].max():.0f} m")
    print(f"  GUI delay : {delay_ms} ms/step")
    print(f"  Tracking  : node v{follow_node}")
    print()
    if delay_ms == 100:
        print("  Tip: use --delay 300 to slow down, --delay 30 to speed up.")
    print()

    # ---- Pre-index: time → {node_id: (x, y, speed)} -----------------------
    by_time: dict[float, dict[int, tuple]] = {}
    for t, grp in df.groupby("time"):
        by_time[round(float(t), 3)] = {
            int(r.node_id): (float(r.x), float(r.y), float(r.speed))
            for r in grp.itertuples()
        }

    # ---- Generate SUMO files (cached next to the trace) -------------------
    out_dir = trace.parent
    if regen:
        for fn in ["highway.net.xml", "platoon.rou.xml", "run.sumocfg"]:
            (out_dir / fn).unlink(missing_ok=True)

    net = _gen_network(out_dir)
    rou = _gen_routes(out_dir, node_ids, joining)
    cfg = _gen_cfg(out_dir, net, rou)

    # netconvert shifts coordinates; parse the offset so we can convert
    # trace world coords → SUMO internal coords for moveToXY / setOffset
    ox, oy = _get_net_offset(net)
    print(f"  [setup] net offset: dx={ox:.2f}  dy={oy:.2f}")

    # ---- Launch sumo-gui ---------------------------------------------------
    print(f"  Launching sumo-gui …  (close the window to exit)\n")
    traci.start([
        str(SUMO_GUI), "-c", str(cfg),
        "--start",
        f"--delay={delay_ms}",
    ])

    try:
        VIEW = traci.gui.getIDList()[0]

        # ---- Add all vehicles at t=0 ------------------------------------------
        # depart="0"  → vehicles are scheduled for sim-time 0.0
        # departLane="first" → use the only lane (lane 0)
        # keepRoute=2 in moveToXY lets us teleport freely, so departPos just
        # needs to be any valid position on the edge.
        first_frame = by_time[timesteps[0]]
        for nid in node_ids:
            vt = _vtype(nid, joining)
            x0, y0, spd0 = first_frame[nid]
            # departPos is distance along edge in SUMO internal coords
            dep_pos = max(1.0, min(6999.0, x0 + ox))
            traci.vehicle.add(
                f"v{nid}", "r0",
                typeID=vt,
                depart="0",
                departPos=str(dep_pos),
                departSpeed=str(round(spd0, 3)),
                departLane="first",
            )

        # First simulationStep → vehicles depart at their departPos
        traci.simulationStep()

        # Set initial view — convert mid-platoon world coords → SUMO internal
        traci.gui.setZoom(VIEW, zoom)
        if f"v{follow_node}" in traci.vehicle.getIDList():
            traci.gui.trackVehicle(VIEW, f"v{follow_node}")
        x_mid = first_frame.get(follow_node, first_frame[0])[0]
        y_mid = first_frame.get(follow_node, first_frame[0])[1]
        traci.gui.setOffset(VIEW, x_mid + ox, y_mid + oy)

        # ---- Main replay loop ----------------------------------------------
        prev_progress = -1
        for i, t in enumerate(timesteps):
            frame = by_time[t]

            # Progress indicator every 10 sim-seconds
            progress = int(t // 10) * 10
            if progress != prev_progress:
                prev_progress = progress
                active = traci.vehicle.getIDList()
                print(f"  t = {t:6.1f} s  |  active vehicles: {len(active)}")

            for nid in node_ids:
                vid = f"v{nid}"
                if nid not in frame:
                    continue
                x, y, spd = frame[nid]
                # moveToXY uses SUMO internal coordinates → apply netOffset
                traci.vehicle.moveToXY(vid, "highway", 0,
                                       x + ox, y + oy,
                                       angle=90.0, keepRoute=2)
                traci.vehicle.setSpeed(vid, spd)

            traci.simulationStep()

        print(f"\n  Trace finished at t = {timesteps[-1]:.1f} s.")
        print("  Close sumo-gui window when ready.")
        # Keep the window open until user closes it
        while True:
            traci.simulationStep()

    except traci_exc.FatalTraCIError:
        print("\n  sumo-gui window closed.")
    except KeyboardInterrupt:
        print("\n  Interrupted by user.")
    except Exception as exc:
        import traceback
        print(f"\n  ERROR during replay: {exc}")
        traceback.print_exc()
    finally:
        try:
            traci.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Replay a .mob.csv platoon scenario in sumo-gui.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 replay_scenario.py slinky\n"
            "  python3 replay_scenario.py gap_open  --delay 200  --follow 8\n"
            "  python3 replay_scenario.py merge     --delay 150  --zoom 700\n"
            "\n"
            "Legend in sumo-gui:\n"
            "  Red   = leader  (node 0)\n"
            "  Blue  = follower nodes\n"
            "  Green = joining vehicles (merge scenario only)\n"
        ),
    )
    ap.add_argument(
        "scenario", choices=list(SCENARIOS.keys()),
        help="Which platoon scenario to replay",
    )
    ap.add_argument(
        "--delay", type=int, default=100, metavar="MS",
        help="ms delay per GUI step (default: 100 ≈ real-time; "
             "300 = slow-motion; 30 = 3× fast)",
    )
    ap.add_argument(
        "--follow", type=int, default=4, metavar="NODE",
        help="Node ID to keep centred in the view (default: 4)",
    )
    ap.add_argument(
        "--zoom", type=float, default=500, metavar="LEVEL",
        help="Initial zoom level (default: 500; higher = more zoomed in)",
    )
    ap.add_argument(
        "--regen", action="store_true",
        help="Force regeneration of highway.net.xml / platoon.rou.xml / run.sumocfg",
    )
    args = ap.parse_args()

    replay(
        scenario_key=args.scenario,
        delay_ms=args.delay,
        follow_node=args.follow,
        zoom=args.zoom,
        regen=args.regen,
    )


if __name__ == "__main__":
    main()
