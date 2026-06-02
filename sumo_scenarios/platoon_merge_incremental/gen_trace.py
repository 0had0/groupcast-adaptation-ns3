#!/usr/bin/env python3
"""
Platoon Merge Incremental — SUMO TraCI controller + mob.csv generator.

Scenario:
  8-vehicle initial platoon (v0..v7) on a 4-lane highway.
  At 5 junctions, vehicles join after position 6 in the platoon:
    Junction 1 (x=1500):  1 joiner  → size 9
    Junction 2 (x=4000):  2 joiners → size 11
    Junction 3 (x=7000):  3 joiners → size 14
    Junction 4 (x=10500): 4 joiners → size 18
    Junction 5 (x=14000): 5 joiners → size 23

  v0..v6 always hold their front positions.
  At each junction the n_now vehicles immediately after v6 slow to open a gap;
  joiners are inserted using dynamic sub-routes so departPos is edge-relative.
  30 s stable phase between junctions.

Output:
  fcd_output.xml                      — for sumo-gui visual validation
  platoon_merge_incremental.mob.csv   — ns-3 mobility trace
"""

import sys
import subprocess
import csv
import xml.etree.ElementTree as ET
from pathlib import Path
from enum import Enum, auto

SCRIPT_DIR = Path(__file__).parent.resolve()
SUMO_BIN   = Path("/Users/hadih/Documents/groupcast-adaptation-ns3/sumo/bin")
SUMO_EXE   = SUMO_BIN / "sumo"
NETCONVERT = SUMO_BIN / "netconvert"
SUMO_TOOLS = Path("/Users/hadih/Documents/groupcast-adaptation-ns3/sumo/tools")

VEH_LENGTH  = 4.5
PLATOON_IVD = 5.0
SPACING     = VEH_LENGTH + PLATOON_IVD   # 9.5 m
V_HIGHWAY   = 33.333
T_SIM       = 650.0
DT          = 0.1
LOOKAHEAD   = 400.0
STABLE_DUR  = 30.0

JUNCTIONS = [
    {"jct_x": 1500.0,  "n_joiners": 1},
    {"jct_x": 4000.0,  "n_joiners": 2},
    {"jct_x": 7000.0,  "n_joiners": 3},
    {"jct_x": 10500.0, "n_joiners": 4},
    {"jct_x": 14000.0, "n_joiners": 5},
]

# Cumulative x layout: (x_start, x_end, edge_id)
EDGE_LAYOUT = [
    (0.0,     1500.0,  "hw_0_1"),
    (1500.0,  4000.0,  "hw_1_2"),
    (4000.0,  7000.0,  "hw_2_3"),
    (7000.0,  10500.0, "hw_3_4"),
    (10500.0, 14000.0, "hw_4_5"),
    (14000.0, 18000.0, "hw_5_e"),
]
ALL_HW_EDGES = [e[2] for e in EDGE_LAYOUT]

KP = 1.0
KD = 2.5
MAX_ACCEL =  3.0
MAX_DECEL = -6.0


def edge_for_abs_x(abs_x):
    for x0, x1, eid in EDGE_LAYOUT:
        if x0 <= abs_x < x1:
            return eid, abs_x - x0
    return "hw_5_e", max(0.0, abs_x - 14000.0)


def sub_route_edges(eid):
    """All edges from eid to end of highway."""
    idx = next(i for i, e in enumerate(EDGE_LAYOUT) if e[2] == eid)
    return [e[2] for e in EDGE_LAYOUT[idx:]]


def get_abs_x(vid, traci):
    try:
        edge = traci.vehicle.getRoadID(vid)
        lpos = traci.vehicle.getLanePosition(vid)
        for x0, _, eid in EDGE_LAYOUT:
            if eid == edge:
                return x0 + lpos
        return -1.0   # junction internal edge — position unknown
    except Exception:
        return -1.0


class State(Enum):
    NORMAL      = auto()
    GAP_OPENING = auto()
    INSERTING   = auto()
    SETTLING    = auto()
    STABLE      = auto()


def build_network():
    net_file = SCRIPT_DIR / "net.net.xml"
    if net_file.exists():
        return
    print("[netconvert] Building…")
    subprocess.run([str(NETCONVERT),
                    "--node-files", str(SCRIPT_DIR / "nodes.nod.xml"),
                    "--edge-files", str(SCRIPT_DIR / "edges.edg.xml"),
                    "--output-file", str(net_file), "--no-turnarounds"],
                   check=True)
    print("[netconvert] Done")


def run_simulation():
    sys.path.insert(0, str(SUMO_TOOLS))
    import traci

    traci.start([str(SUMO_EXE), "-c",
                 str(SCRIPT_DIR / "scenario.sumocfg"), "--no-step-log"])

    platoon_order = [f"v{i}" for i in range(8)]
    node_id_map   = {f"v{i}": i for i in range(8)}
    next_nid      = [8]
    known_routes  = set()

    def assign_nid(vid):
        if vid not in node_id_map:
            node_id_map[vid] = next_nid[0]
            next_nid[0] += 1

    def alive():
        return set(traci.vehicle.getIDList())

    def set_all_modes():
        for vid in alive():
            try:
                traci.vehicle.setSpeedMode(vid, 0)
                traci.vehicle.setLaneChangeMode(vid, 0)
            except Exception:
                pass

    def cacc(platoon):
        live = alive()
        for idx, vid in enumerate(platoon):
            if vid not in live:
                continue
            if idx == 0:
                try:
                    traci.vehicle.setSpeed(vid, V_HIGHWAY)
                except Exception:
                    pass
                continue
            pid = None
            for k in range(idx - 1, -1, -1):
                if platoon[k] in live:
                    pid = platoon[k]
                    break
            if pid is None:
                continue
            try:
                xs = get_abs_x(vid, traci)
                xp = get_abs_x(pid, traci)
                vs = traci.vehicle.getSpeed(vid)
                vp = traci.vehicle.getSpeed(pid)
            except Exception:
                continue
            if xs < 0 or xp < 0:
                continue   # vehicle on junction internal edge — skip
            gap = xp - xs - VEH_LENGTH
            acc = KP * (gap - PLATOON_IVD) + KD * (vp - vs)
            acc = max(MAX_DECEL, min(MAX_ACCEL, acc))
            new_spd = max(0.0, min(V_HIGHWAY * 1.5, vs + acc * DT))
            try:
                traci.vehicle.setSpeed(vid, new_spd)
            except Exception:
                pass

    state        = State.NORMAL
    jct_idx      = 0
    stable_timer = 0.0
    settle_timer = 0.0
    gap_slow_set = set()
    n_now        = 0
    # Vehicles added in INSERTING state, teleported at start of SETTLING
    pending_tp   = []   # list of (jid, edge_id, lane_pos)

    step = 0
    while traci.simulation.getMinExpectedNumber() > 0 and step * DT < T_SIM:
        t = round(step * DT, 1)
        set_all_modes()

        if state == State.NORMAL:
            cacc(platoon_order)

            if jct_idx >= len(JUNCTIONS):
                traci.simulationStep(); step += 1; continue

            jct  = JUNCTIONS[jct_idx]
            x_v0 = get_abs_x("v0", traci)
            if x_v0 > jct["jct_x"] - LOOKAHEAD:
                n_now = jct["n_joiners"]
                # Vehicles immediately after v6 open the gap
                gap_slow_set = set(platoon_order[7: 7 + n_now])
                state = State.GAP_OPENING
                print(f"[t={t:.1f}] GAP_OPENING jct {jct_idx+1} "
                      f"({n_now} joiners), slowing: {list(gap_slow_set)}")

        elif state == State.GAP_OPENING:
            jct   = JUNCTIONS[jct_idx]
            t_op  = 8.0 + n_now * 4.0
            # Slow vehicles must open (n_now+1)*SPACING gap (n_now joiners + own IVD)
            v_sl  = max(8.0, V_HIGHWAY - (n_now + 1) * SPACING / t_op)

            for idx, vid in enumerate(platoon_order):
                tgt = v_sl if vid in gap_slow_set else V_HIGHWAY
                try:
                    traci.vehicle.setSpeed(vid, tgt)
                except Exception:
                    pass
            # Vehicles after the slow zone follow CACC relative to their predecessor
            slow_end = 7 + n_now
            for idx in range(slow_end, len(platoon_order)):
                vid = platoon_order[idx]
                pid = platoon_order[idx - 1]
                try:
                    xs = get_abs_x(vid, traci)
                    xp = get_abs_x(pid, traci)
                    vs = traci.vehicle.getSpeed(vid)
                    vp = traci.vehicle.getSpeed(pid)
                except Exception:
                    continue
                if xs < 0 or xp < 0:
                    continue
                gap = xp - xs - VEH_LENGTH
                acc = KP * (gap - PLATOON_IVD) + KD * (vp - vs)
                acc = max(MAX_DECEL, min(MAX_ACCEL, acc))
                try:
                    traci.vehicle.setSpeed(vid, max(0.0, min(V_HIGHWAY * 1.5, vs + acc * DT)))
                except Exception:
                    pass

            # Gap check: between v6 (idx=6) and the first slow vehicle (idx=7)
            # Need room for n joiners PLUS the slow vehicle's own 5 m gap:
            #   v6 → j_0 → j_1 → ... → j_{n-1} → first_slow → ...
            # so total gap from v6 to first_slow = (n_now + 1) * SPACING
            x_v6        = get_abs_x("v6", traci)
            x_first_sl  = get_abs_x(platoon_order[7], traci)
            gap_need    = (n_now + 1) * SPACING

            x_v0 = get_abs_x("v0", traci)
            if (x_v0 > 0 and x_v6 > 0 and x_first_sl > 0
                    and x_v0 > jct["jct_x"]):
                gap_avail = x_v6 - x_first_sl - VEH_LENGTH
                if gap_avail >= gap_need * 0.88:
                    state = State.INSERTING
                    print(f"[t={t:.1f}] INSERTING {n_now} joiners "
                          f"(gap={gap_avail:.1f}≥{gap_need*0.88:.1f}m)")

        elif state == State.INSERTING:
            x_v6     = get_abs_x("v6", traci)
            new_vids = []

            for k in range(n_now):
                jid     = f"j{jct_idx+1}_{k}"
                abs_x   = x_v6 - (k + 1) * SPACING
                eid, lp = edge_for_abs_x(abs_x)

                # Build sub-route from target edge if not already created
                sr_id = f"sr_{eid}"
                if sr_id not in known_routes:
                    traci.route.add(sr_id, sub_route_edges(eid))
                    known_routes.add(sr_id)

                try:
                    traci.vehicle.add(
                        jid,
                        routeID      = sr_id,
                        typeID       = "joiner",
                        depart       = "now",
                        departLane   = "0",
                        departPos    = str(round(lp, 2)),
                        departSpeed  = str(round(V_HIGHWAY, 3)),
                    )
                    traci.vehicle.setSpeedMode(jid, 0)
                    new_vids.append(jid)
                    assign_nid(jid)
                    print(f"  add {jid} → {eid} lp={lp:.1f}")
                except Exception as e:
                    print(f"  [warn] add {jid}: {e}")

            # Insert new vehicles after v6 in platoon order
            platoon_order = platoon_order[:7] + new_vids + platoon_order[7:]
            gap_slow_set  = set()
            settle_timer  = 0.0
            state         = State.SETTLING
            print(f"[t={t:.1f}] platoon={len(platoon_order)}: "
                  f"{platoon_order[:min(14,len(platoon_order))]}")

        elif state == State.SETTLING:
            cacc(platoon_order)
            settle_timer += DT

            # Check convergence (first 12 gaps to keep it manageable)
            n_check = min(len(platoon_order), 12)
            converged = all(
                abs(get_abs_x(platoon_order[i-1], traci)
                    - get_abs_x(platoon_order[i],  traci)
                    - VEH_LENGTH - PLATOON_IVD) < 1.5
                for i in range(1, n_check)
            )
            if converged or settle_timer >= 45.0:
                stable_timer = 0.0
                state        = State.STABLE
                print(f"[t={t:.1f}] STABLE jct {jct_idx+1} "
                      f"(settle={settle_timer:.1f}s, n={len(platoon_order)})")

        elif state == State.STABLE:
            cacc(platoon_order)
            stable_timer += DT
            if stable_timer >= STABLE_DUR:
                jct_idx += 1
                state    = State.NORMAL
                if jct_idx < len(JUNCTIONS):
                    print(f"[t={t:.1f}] → junction {jct_idx+1}")
                else:
                    print(f"[t={t:.1f}] all junctions done, cruising.")

        traci.simulationStep()
        step += 1

    traci.close()
    print(f"[sumo] done. steps={step}, platoon={len(platoon_order)}")
    print(f"[sumo] node_id_map: {node_id_map}")


def fcd_to_mob_csv():
    fcd_file = SCRIPT_DIR / "fcd_output.xml"
    out_file = SCRIPT_DIR / "platoon_merge_incremental.mob.csv"
    print(f"[fcd→csv] {fcd_file} …")

    tree = ET.parse(fcd_file)
    root = tree.getroot()

    nid_map = {f"v{i}": i for i in range(8)}
    nid_ctr = [8]

    def get_nid(vid):
        if vid not in nid_map:
            nid_map[vid] = nid_ctr[0]; nid_ctr[0] += 1
        return nid_map[vid]

    with open(out_file, "w", newline="") as f:
        f.write("# time,node_id,x,y,speed\n")
        w = csv.writer(f)
        for ts in root.iter("timestep"):
            t = float(ts.attrib["time"])
            for veh in ts.iter("vehicle"):
                vid = veh.attrib["id"]
                x   = float(veh.attrib["x"])
                y   = float(veh.attrib["y"])
                spd = float(veh.attrib["speed"])
                w.writerow([f"{t:.3f}", get_nid(vid),
                            f"{x:.3f}", f"{y:.3f}", f"{spd:.3f}"])

    print(f"[fcd→csv] → {out_file}")
    print(f"[fcd→csv] mapping: {nid_map}")


if __name__ == "__main__":
    build_network()
    run_simulation()
    fcd_to_mob_csv()
    print("All done.")
