#!/usr/bin/env python3
"""
Platoon IVD Sweep — SUMO TraCI controller + mob.csv generator.

Scenario:
  8-vehicle platoon on a single-lane highway.
  IVD sequence: [2, 4, 8, 10] m, each held 60 s.
  Transitions: 30 s smooth (smooth-step) between consecutive IVDs.
  Speed: 120 km/h = 33.333 m/s.

  Control: leader-anchored PFL — every follower i tracks position
  x_leader - i*(VEH_LENGTH + ivd_target(t)), so all gaps change together.

Output:
  fcd_output.xml             — SUMO FCD for sumo-gui
  platoon_ivd_sweep.mob.csv  — ns-3 mobility trace
"""

import sys
import subprocess
import csv
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
SUMO_BIN   = Path("/Users/hadih/Documents/groupcast-adaptation-ns3/sumo/bin")
SUMO_EXE   = SUMO_BIN / "sumo-gui"
NETCONVERT = SUMO_BIN / "netconvert"
SUMO_TOOLS = Path("/Users/hadih/Documents/groupcast-adaptation-ns3/sumo/tools")

N_VEHICLES   = 8
VEH_LENGTH   = 4.5
IVD_SEQUENCE = [2.0, 4.0, 8.0, 10.0]
T_STABLE     = 60.0   # s per phase
TRANS_DUR    = 30.0   # s per transition

# Phase timeline (same for every vehicle — no per-hop delay):
# t=  0..60:  IVD=2   t=60..90:  transition 2→4
# t= 90..150: IVD=4   t=150..180 transition 4→8
# t=180..240: IVD=8   t=240..270 transition 8→10
# t=270..330: IVD=10
T_STABLE_START = [0.0]
for _ in range(len(IVD_SEQUENCE) - 1):
    T_STABLE_START.append(T_STABLE_START[-1] + T_STABLE + TRANS_DUR)
T_TRANS_START = [ts + T_STABLE for ts in T_STABLE_START[:-1]]  # [60, 150, 240]

T_SIM     = T_STABLE_START[-1] + T_STABLE + 5.0   # ~335 s
V_HIGHWAY = 33.333   # m/s

KP = 2.0
KD = 0.5
MAX_ACCEL =  3.0
MAX_DECEL = -6.0


def smooth_step(t, t0, t1):
    if t <= t0: return 0.0
    if t >= t1: return 1.0
    x = (t - t0) / (t1 - t0)
    return x * x * (3.0 - 2.0 * x)


def ivd_target(t):
    """Global IVD target — identical for every follower at time t."""
    ivd = IVD_SEQUENCE[0]
    for j, ts in enumerate(T_TRANS_START):
        if t >= ts + TRANS_DUR:
            ivd = IVD_SEQUENCE[j + 1]
        elif t >= ts:
            ivd = IVD_SEQUENCE[j] + smooth_step(t, ts, ts + TRANS_DUR) * (
                IVD_SEQUENCE[j + 1] - IVD_SEQUENCE[j])
            break
    return ivd


def build_network():
    net_file = SCRIPT_DIR / "net.net.xml"
    if net_file.exists():
        return
    print("[netconvert] Building network…")
    cmd = [
        str(NETCONVERT),
        "--node-files", str(SCRIPT_DIR / "nodes.nod.xml"),
        "--edge-files", str(SCRIPT_DIR / "edges.edg.xml"),
        "--output-file", str(net_file),
        "--no-turnarounds",
    ]
    subprocess.run(cmd, check=True)
    print("[netconvert] Done →", net_file)


def run_simulation():
    sys.path.insert(0, str(SUMO_TOOLS))
    import traci

    cfg = SCRIPT_DIR / "scenario.sumocfg"
    traci.start([str(SUMO_EXE), "-c", str(cfg), "--no-step-log"])

    veh_ids = [f"v{i}" for i in range(N_VEHICLES)]
    dt = 0.1
    step = 0

    while traci.simulation.getMinExpectedNumber() > 0:
        t = round(step * dt, 1)
        if t > T_SIM:
            break

        for vid in veh_ids:
            try:
                traci.vehicle.setSpeedMode(vid, 0)
                traci.vehicle.setLaneChangeMode(vid, 0)
            except Exception:
                pass

        # Leader: constant highway speed
        try:
            traci.vehicle.setSpeed("v0", V_HIGHWAY)
            x0 = traci.vehicle.getLanePosition("v0")
            v0 = traci.vehicle.getSpeed("v0")
        except Exception:
            traci.simulationStep(); step += 1; continue

        ivd = ivd_target(t)
        spacing = VEH_LENGTH + ivd   # front-to-front distance

        # PFL: each follower i targets x0 - i*spacing
        for i in range(1, N_VEHICLES):
            vid = f"v{i}"
            try:
                x_self = traci.vehicle.getLanePosition(vid)
                v_self = traci.vehicle.getSpeed(vid)
            except Exception:
                continue

            desired_x = x0 - i * spacing
            pos_err   = desired_x - x_self
            spd_err   = v0 - v_self

            accel = KP * pos_err + KD * spd_err
            accel = max(MAX_DECEL, min(MAX_ACCEL, accel))
            traci.vehicle.setSpeed(vid, max(0.0, v_self + accel * dt))

        traci.simulationStep()
        step += 1

    traci.close()
    print(f"[sumo] Simulation done. Steps: {step}")


def fcd_to_mob_csv():
    """Convert fcd_output.xml → platoon_ivd_sweep.mob.csv"""
    fcd_file = SCRIPT_DIR / "fcd_output.xml"
    out_file = SCRIPT_DIR / "platoon_ivd_sweep.mob.csv"

    print(f"[fcd→csv] Parsing {fcd_file} …")
    tree = ET.parse(fcd_file)
    root = tree.getroot()

    # Build node-id mapping: vehicle string id → integer node id
    node_id = {f"v{i}": i for i in range(N_VEHICLES)}

    with open(out_file, "w", newline="") as f:
        f.write("# time,node_id,x,y,speed\n")
        writer = csv.writer(f)
        for ts in root.iter("timestep"):
            t = float(ts.attrib["time"])
            for veh in ts.iter("vehicle"):
                vid = veh.attrib["id"]
                if vid not in node_id:
                    continue
                x   = float(veh.attrib["x"])
                y   = float(veh.attrib["y"])
                spd = float(veh.attrib["speed"])
                writer.writerow([f"{t:.3f}", node_id[vid], f"{x:.3f}", f"{y:.3f}", f"{spd:.3f}"])

    print(f"[fcd→csv] Written → {out_file}")


if __name__ == "__main__":
    build_network()
    run_simulation()
    fcd_to_mob_csv()
    print("All done.")
