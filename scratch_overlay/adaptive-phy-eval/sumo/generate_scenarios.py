#!/usr/bin/env python3
"""
Generate SUMO configuration files for adaptive PHY evaluation.

Generates ALL scenario families (S1-S4) with all variants.
Use --scenario to select specific family, --all for everything.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Base paths
SCRIPT_DIR = Path(__file__).parent
ADAPTIVE_PHY_EVAL = SCRIPT_DIR.parent
SUMO_DIR = ADAPTIVE_PHY_EVAL / "sumo"
SUMO_BIN = Path("/Users/hadih/Documents/groupcast-adaptation-ns3/sumo/bin")

# Scenario parameters
S1_GROUP_SIZES = [2, 4, 8, 12, 16, 20]
S2_IVDS = [10, 20, 50, 100, 150]
S3_SPEEDS = [30, 60, 100, 140, 200]
S4_ENVS = ["highway_los", "urban_nlos", "mixed"]

# Common road parameters
LANE_WIDTH = 3.2  # meters
NUM_LANES = 3
HIGHWAY_LENGTH = 2000  # meters (default)
TX_POSITION = 1000  # TX at center of highway


def run_netconvert(nod_file: Path, edg_file: Path, net_file: Path) -> bool:
    """Run netconvert to generate .net.xml from .nod.xml and .edg.xml."""
    cmd = [
        str(SUMO_BIN / "netconvert"),
        "-n", str(nod_file),
        "-e", str(edg_file),
        "-o", str(net_file),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except Exception as e:
        print(f"Error running netconvert: {e}")
        return False


def generate_nodes_nod(output_dir: Path, length: int, num_lanes: int = NUM_LANES) -> Path:
    """Generate nodes.nod.xml for a straight highway."""
    
    nod_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<nodes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/nodes_file.xsd">
    <node id="start" x="0" y="0" type="priority"/>
    <node id="end" x="{length}" y="0" type="priority"/>
</nodes>"""
    
    nod_path = output_dir / "nodes.nod.xml"
    nod_path.write_text(nod_xml)
    return nod_path


def generate_edges_edg(output_dir: Path, length: int, num_lanes: int = NUM_LANES) -> Path:
    """Generate edges.edg.xml for a straight highway."""
    
    lanes_str = f'numLanes="{num_lanes}"'
    speed_str = 'speed="27.78"'  # 100 km/h in m/s
    
    # Forward edge (eastbound)
    edge_fwd = f'    <edge id="hw_fwd" from="start" to="end" {lanes_str} {speed_str}/>\n'
    # Reverse edge (westbound)
    edge_rev = f'    <edge id="hw_rev" from="end" to="start" {lanes_str} {speed_str}/>'
    
    edg_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<edges xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/edges_file.xsd">
{edge_fwd}{edge_rev}
</edges>"""
    
    edg_path = output_dir / "edges.edg.xml"
    edg_path.write_text(edg_xml)
    return edg_path


def generate_vehicles_rou(output_dir: Path, num_receivers: int, ivd: float, 
                         speed_kmh: float, edge_id: str = "hw_rev") -> Path:
    """Generate vehicles.rou.xml with TX and RX vehicles."""
    
    speed_ms = speed_kmh / 3.6  # Convert to m/s
    
    # Vehicle type - use high maxSpeed (70 m/s = 252 km/h) to accommodate all speed scenarios
    vtype = """  <vType id="car" vClass="passenger" accel="2.5" decel="4.5" 
                maxSpeed="70" sigma="0.5" length="5" width="2" minGap="2.5"/>"""
    
    # Route
    route = f'  <route id="route0" edges="{edge_id}"/>'
    
    # TX at center
    vehicles = [
        f"""  <vehicle id="tx_0" type="car" route="route0" depart="0" departPos="{TX_POSITION}" 
                departSpeed="{speed_ms}" color="0,1,0">
    <param key="role" value="tx"/>
    <param key="group_id" value="group_0"/>
  </vehicle>"""
    ]
    
    # RXs behind TX (lower positions = behind in direction of travel)
    for i in range(1, num_receivers + 1):
        rx_pos = TX_POSITION - (ivd * i)
        vehicles.append(
            f"""  <vehicle id="rx_{i}" type="car" route="route0" depart="0" departPos="{rx_pos}" 
                departSpeed="{speed_ms}" color="0,0.5,1">
    <param key="role" value="rx"/>
    <param key="group_id" value="group_0"/>
    <param key="rx_index" value="{i}"/>
  </vehicle>"""
        )
    
    rou_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<routes>
{vtype}
{route}
{chr(10).join(vehicles)}
</routes>"""
    
    rou_path = output_dir / "vehicles.rou.xml"
    rou_path.write_text(rou_xml)
    return rou_path


def generate_sumocfg(output_dir: Path, net_file: str = "highway.net.xml",
                   rou_file: str = "vehicles.rou.xml",
                   fcd_file: str = None, duration: int = 30) -> Path:
    """Generate SUMO configuration file."""
    
    input_files = f"""    <net-file value="{net_file}"/>
    <route-files value="{rou_file}"/>"""
    
    output_files = ""
    if fcd_file:
        output_files = f"""    <fcd-output value="{fcd_file}"/>
"""
    
    sumocfg = f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/sumoConfiguration.xsd">
    <input>
{input_files}
    </input>
{output_files}    <time>
        <begin value="0"/>
        <end value="{duration * 1000}"/>
        <step-length value="0.1"/>
    </time>
    <report>
        <verbose value="false"/>
    </report>
</configuration>"""
    
    cfg_path = output_dir / "highway.sumocfg"
    cfg_path.write_text(sumocfg)
    return cfg_path


def generate_scenario(output_dir: Path, scenario: str, condition: str,
                     num_receivers: int = 8, ivd: float = 20.0,
                     speed_kmh: float = 100.0, duration: int = 30,
                     edge_id: str = "hw_rev") -> bool:
    """Generate a single scenario."""
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Use road length 2000m for most scenarios
    road_length = HIGHWAY_LENGTH
    
    # For large IVD, extend road
    if ivd >= 100:
        road_length = 4000
    
    # Generate nodes and edges
    nod_path = generate_nodes_nod(output_dir, road_length)
    edg_path = generate_edges_edg(output_dir, road_length)
    
    # Generate net.xml using netconvert
    net_path = output_dir / "highway.net.xml"
    if not run_netconvert(nod_path, edg_path, net_path):
        print(f"Error: netconvert failed for {condition}")
        return False
    
    # Generate vehicles
    rou_path = generate_vehicles_rou(output_dir, num_receivers, ivd, speed_kmh, edge_id)
    
    # Generate config
    generate_sumocfg(output_dir, net_path.name, rou_path.name, 
                     f"fcd_{condition}.xml", duration)
    
    return True


def generate_s1(output_dir: Path, n_rx: int, dry_run: bool = False) -> None:
    """Generate S1: varying group size scenario."""
    
    subdir = SUMO_DIR / "s1_group_size" / f"s1_nrx{n_rx:02d}"
    condition = f"nrx{n_rx:02d}"
    
    if dry_run:
        print(f"[DRY RUN] Would create S1: N_rx={n_rx}, IVD=20m, speed=100km/h")
        print(f"         → {subdir}")
        return
    
    if generate_scenario(subdir, "s1", condition,
                         num_receivers=n_rx, ivd=20.0,
                         speed_kmh=100.0, duration=30):
        print(f"✓ Generated S1: N_rx={n_rx} → {subdir}")
    else:
        print(f"✗ Failed S1: N_rx={n_rx}")


def generate_s2(output_dir: Path, ivd: int, dry_run: bool = False) -> None:
    """Generate S2: varying inter-vehicle distance."""
    
    subdir = SUMO_DIR / "s2_channel_heterogeneity" / f"s2_ivd{ivd:03d}m"
    condition = f"ivd{ivd:03d}m"
    
    if dry_run:
        print(f"[DRY RUN] Would create S2: IVD={ivd}m, N_rx=8, speed=100km/h")
        print(f"         → {subdir}")
        return
    
    if generate_scenario(subdir, "s2", condition,
                         num_receivers=8, ivd=float(ivd),
                         speed_kmh=100.0, duration=30):
        print(f"✓ Generated S2: IVD={ivd}m → {subdir}")
    else:
        print(f"✗ Failed S2: IVD={ivd}m")


def generate_s3(output_dir: Path, speed_kmh: int, dry_run: bool = False) -> None:
    """Generate S3: varying speed/Doppler."""
    
    subdir = SUMO_DIR / "s3_speed_doppler" / f"s3_spd{speed_kmh:03d}kmh"
    condition = f"spd{speed_kmh:03d}kmh"
    
    if dry_run:
        print(f"[DRY RUN] Would create S3: speed={speed_kmh}km/h, N_rx=8, IVD=20m")
        print(f"         → {subdir}")
        return
    
    if generate_scenario(subdir, "s3", condition,
                         num_receivers=8, ivd=20.0,
                         speed_kmh=float(speed_kmh), duration=30):
        print(f"✓ Generated S3: speed={speed_kmh}km/h → {subdir}")
    else:
        print(f"✗ Failed S3: speed={speed_kmh}km/h")


def generate_s4(output_dir: Path, env_type: str, dry_run: bool = False) -> None:
    """Generate S4: varying propagation environment."""
    
    subdir = SUMO_DIR / "s4_propagation" / f"s4_{env_type}"
    condition = env_type
    
    if dry_run:
        print(f"[DRY RUN] Would create S4: env={env_type}")
        print(f"         → {subdir}")
        return
    
    # For now, all use same SUMO topology (highway)
    # The channel model difference is handled in NS3
    if generate_scenario(subdir, "s4", condition,
                         num_receivers=8, ivd=20.0,
                         speed_kmh=60.0, duration=30):
        print(f"✓ Generated S4: env={env_type} → {subdir}")
    else:
        print(f"✗ Failed S4: env={env_type}")


def generate_all(dry_run: bool = False) -> None:
    """Generate all scenario families."""
    
    print("=" * 60)
    print("Generating SUMO scenarios for adaptive PHY evaluation")
    print("=" * 60)
    
    # S1: Group size
    print("\n[S1] Group Size:")
    for n_rx in S1_GROUP_SIZES:
        generate_s1(None, n_rx, dry_run)
    
    # S2: Channel heterogeneity
    print("\n[S2] Channel Heterogeneity (IVD):")
    for ivd in S2_IVDS:
        generate_s2(None, ivd, dry_run)
    
    # S3: Speed/Doppler
    print("\n[S3] Speed/Doppler:")
    for speed in S3_SPEEDS:
        generate_s3(None, speed, dry_run)
    
    # S4: Propagation
    print("\n[S4] Propagation Environment:")
    for env in S4_ENVS:
        generate_s4(None, env, dry_run)
    
    print("\n" + "=" * 60)
    if dry_run:
        print("Dry run complete. Re-run without --dry-run to create files.")
    else:
        print("All scenarios generated successfully.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Generate SUMO configurations for adaptive PHY evaluation"
    )
    parser.add_argument(
        "--scenario",
        choices=["s1", "s2", "s3", "s4"],
        help="Generate specific scenario family"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate all scenario families"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created without creating files"
    )
    parser.add_argument(
        "--value",
        type=int,
        help="Value for scenario parameter (e.g., 8 for s1 nrx08)"
    )
    
    args = parser.parse_args()
    
    if args.all:
        generate_all(args.dry_run)
    elif args.scenario:
        if args.scenario == "s1":
            if args.value:
                generate_s1(None, args.value, args.dry_run)
            else:
                for n_rx in S1_GROUP_SIZES:
                    generate_s1(None, n_rx, args.dry_run)
        elif args.scenario == "s2":
            if args.value:
                generate_s2(None, args.value, args.dry_run)
            else:
                for ivd in S2_IVDS:
                    generate_s2(None, ivd, args.dry_run)
        elif args.scenario == "s3":
            if args.value:
                generate_s3(None, args.value, args.dry_run)
            else:
                for speed in S3_SPEEDS:
                    generate_s3(None, speed, args.dry_run)
        elif args.scenario == "s4":
            if args.value:
                env_map = {0: "highway_los", 1: "urban_nlos", 2: "mixed"}
                generate_s4(None, env_map[args.value], args.dry_run)
            else:
                for env in S4_ENVS:
                    generate_s4(None, env, args.dry_run)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()