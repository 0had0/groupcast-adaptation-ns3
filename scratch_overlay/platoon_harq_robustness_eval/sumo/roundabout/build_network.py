#!/usr/bin/env python3
"""
build_network.py — synthetic 4-arm roundabout network (plain XML -> netconvert).

Geometry
--------
* one roundabout in the centre (RING_SEGMENTS chords, RING_LANES lanes,
  counter-clockwise = right-hand traffic, entering traffic gives way);
* 4 straight arms (north, east, south, west), ARM_LEN = 1 km each;
* every arm has 4 lanes: ARM_LANES_IN towards the roundabout and
  ARM_LANES_OUT away from it (in right-hand traffic the inbound lanes are the
  two on the right of the centre line, the outbound the two on the left);
* buildings: a block in the middle of the roundabout plus blocks along both
  sides of every arm, injected into the net as <type id="building.N"> with
  <polygon> shapes, exactly like netconvert stores OSM building shapes.

Outputs (next to this script)
  roundabout.nod.xml / .edg.xml / .con.xml   plain-XML input (skipped by git)
  roundabout.net.xml                          final SUMO network (with buildings)
  roundabout.rou.xml                          12 routes: every arm in -> a
                                              different arm out, around the ring
  buildings.csv                               the building polygons (for plotting)
"""

import csv
import math
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUMO_HOME = __import__("os").environ.get(
    "SUMO_HOME", "/Users/hadih/Documents/groupcast-adaptation-ns3/sumo")

# ---------------------------------------------------------------- geometry
CENTER = (0.0, 0.0)
RING_R = 25.0             # radius of the ring carriageway centre line [m]
RING_SEGMENTS = 12        # chords the circle is approximated with
RING_LANES = 2
RING_SPEED = 8.33         # 30 km/h on the circle [m/s]
ARM_LANES_IN = 2          # lanes towards the roundabout
ARM_LANES_OUT = 2         # lanes away from the roundabout
ARM_LEN = 1000.0          # length of every arm [m]
ARM_SPEED = 13.89         # 50 km/h on the arms [m/s]
ARM_PRIORITY = 1
RING_PRIORITY = 10        # higher than the arms -> entering traffic gives way
ARM_NODE_DISTANCES = [RING_R, 40.0, 120.0, 300.0, 550.0, 1000.0]

BUILDING_OFFSET = 8.0     # distance of the buildings from the road edge [m]
BUILDING_W, BUILDING_H = 30.0, 40.0
BUILDING_SPACING = 80.0
BUILDING_START = 60.0


def ring_nodes():
    """(RING_SEGMENTS) nodes on the circle, numbered counter-clockwise.

    Node 0 is the northern junction, 3 west, 6 south, 9 east; the circulation
    follows ring_i -> ring_{i+1}, i.e. counter-clockwise, which is what
    right-hand traffic expects.
    """
    nodes = []
    for i in range(RING_SEGMENTS):
        a = math.radians(90.0 + i * 360.0 / RING_SEGMENTS)
        nodes.append((CENTER[0] + RING_R * math.cos(a), CENTER[1] + RING_R * math.sin(a)))
    return nodes


def arm_index(name, nodes):
    return {"N": 0, "W": 3, "S": 6, "E": 9}[name]


def arm_nodes(name, nodes):
    """Nodes of one arm, from the roundabout outwards."""
    a = arm_index(name, nodes)
    base = nodes[a]
    for arm, ang in (("N", 90.0), ("W", 180.0), ("S", 270.0), ("E", 0.0)):
        if arm == name:
            rad = math.radians(ang)
            d = (math.cos(rad), math.sin(rad))
            break
    return [(base[0] + d[0] * dist, base[1] + d[1] * dist) for dist in ARM_NODE_DISTANCES]


# ------------------------------------------------------------------- output
def write_plain_xml(edges, connections, nodes_all):
    ET.SubElement  # keep the import obviously used
    nod = ET.Element("nodes")
    for nid, (x, y) in nodes_all.items():
        ET.SubElement(nod, "node", id=nid, x=f"{x:.3f}", y=f"{y:.3f}")
    ET.ElementTree(nod).write(HERE / "roundabout.nod.xml", encoding="UTF-8",
                              xml_declaration=True)

    edg = ET.Element("edges")
    for e in edges:
        ET.SubElement(edg, "edge", id=e["id"], **{k: str(v) for k, v in e.items()
                                                  if k != "id"})
    ET.ElementTree(edg).write(HERE / "roundabout.edg.xml", encoding="UTF-8",
                              xml_declaration=True)

    con = ET.Element("connections")
    for c in connections:
        ET.SubElement(con, "connection", **{k: str(v) for k, v in c.items()})
    ET.ElementTree(con).write(HERE / "roundabout.con.xml", encoding="UTF-8",
                              xml_declaration=True)


def buildings():
    """Building polygons: two in the middle of the roundabout, blocks along
    every arm on both sides."""
    out = [("centre", [(-9.0, -9.0), (9.0, -9.0), (9.0, 9.0), (-9.0, 9.0)]),
           ("centre-2", [(-4.0, 10.0), (6.0, 10.0), (6.0, 19.0), (-4.0, 19.0)])]
    half_road = (ARM_LANES_IN + ARM_LANES_OUT) * 3.2 / 2.0
    for name, ang in (("N", 90.0), ("W", 180.0), ("S", 270.0), ("E", 0.0)):
        rad = math.radians(ang)
        u = (math.cos(rad), math.sin(rad))        # along the arm
        n = (-math.sin(rad), math.cos(rad))       # perpendicular
        for side in (-1, 1):
            d = BUILDING_START
            k = 0
            while d + BUILDING_H < ARM_LEN:
                centre = (CENTER[0] + u[0] * (d + BUILDING_H / 2)
                          + n[0] * side * (half_road + BUILDING_OFFSET + BUILDING_W / 2),
                          CENTER[1] + u[1] * (d + BUILDING_H / 2)
                          + n[1] * side * (half_road + BUILDING_OFFSET + BUILDING_W / 2))
                hx, hy = BUILDING_W / 2, BUILDING_H / 2
                pts = [(centre[0] - hx, centre[1] - hy), (centre[0] + hx, centre[1] - hy),
                       (centre[0] + hx, centre[1] + hy), (centre[0] - hx, centre[1] + hy)]
                out.append((f"{name}{'+' if side > 0 else '-'}{k}", pts))
                d += BUILDING_SPACING
                k += 1
    return out


def inject_buildings(net_path, polys, color=(0.62, 0.58, 0.52)):
    """netconvert 1.26 has no building import, but SUMO stores building shapes
    as <type> elements with a <polygon> child - which is exactly what we do."""
    text = Path(net_path).read_text()
    block = []
    for i, (name, pts) in enumerate(polys):
        shape = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts + [pts[0]])
        r, g, b = color
        shade = 1.0 - 0.12 * (i % 4)
        block.append(
            f'    <type id="building.{i}" width="0.0" name="{name}">\n'
            f'        <polygon shape="{shape}" color="{r * shade:.2f},{g * shade:.2f},'
            f'{b * shade:.2f}" fill="true"/>\n    </type>')
    Path(net_path).write_text(text.replace("</net>", "\n".join(block) + "\n</net>"))
    return len(polys)


# ---------------------------------------------------------------------- main
def main():
    ring = ring_nodes()
    nodes, edges, connections = {}, [], []

    # --- roundabout ring (one way, counter-clockwise) ------------------------
    for i in range(RING_SEGMENTS):
        nodes[f"ring_{i}"] = ring[i]
    for i in range(RING_SEGMENTS):
        edges.append(dict(id=f"ring_{i}", **{"from": f"ring_{i}", "to": f"ring_{(i + 1) % RING_SEGMENTS}"},
                          numLanes=RING_LANES, speed=f"{RING_SPEED}",
                          priority=RING_PRIORITY))

    # --- arms ---------------------------------------------------------------
    arms = {}
    for name in ("N", "E", "S", "W"):
        idx = arm_index(name, ring)
        pts = arm_nodes(name, ring)
        for k, p in enumerate(pts):
            nodes[f"{name}_{k}"] = p
        arms[name] = idx
        last = f"{name}_{len(pts) - 1}"
        edges.append(dict(id=f"{name}_in", **{"from": last, "to": f"ring_{idx}"},
                          numLanes=ARM_LANES_IN, speed=f"{ARM_SPEED}", priority=ARM_PRIORITY))
        edges.append(dict(id=f"{name}_out", **{"from": f"ring_{idx}", "to": last},
                          numLanes=ARM_LANES_OUT, speed=f"{ARM_SPEED}", priority=ARM_PRIORITY))

    # --- connections: enter the ring, drive on, leave again; no u-turns ------
    for name, idx in arms.items():
        nxt = (idx + 1) % RING_SEGMENTS
        prv = (idx - 1) % RING_SEGMENTS
        for lane in range(RING_LANES):
            # arm -> ring  (give way, the ring has the higher priority)
            connections.append({"from": f"{name}_in", "to": f"ring_{idx}",
                                "fromLane": lane % ARM_LANES_IN, "toLane": lane,
                                "dir": "s"})
            # ring -> next ring segment (straight on)
            connections.append({"from": f"ring_{prv}", "to": f"ring_{idx}",
                                "fromLane": lane, "toLane": lane, "dir": "s"})
            # ring -> arm (exit): the ring edge that *arrives* at the junction
            connections.append({"from": f"ring_{prv}", "to": f"{name}_out",
                                "fromLane": lane, "toLane": lane % ARM_LANES_OUT,
                                "dir": "s"})
        _ = nxt

    write_plain_xml(edges, connections, nodes)

    # --- netconvert ---------------------------------------------------------
    import subprocess
    cmd = [str(Path(SUMO_HOME) / "bin" / "netconvert"),
           "--node-files", str(HERE / "roundabout.nod.xml"),
           "--edge-files", str(HERE / "roundabout.edg.xml"),
           "--connection-files", str(HERE / "roundabout.con.xml"),
           "--output-file", str(HERE / "roundabout.net.xml"),
           "--no-turnarounds", "true",
           "--offset.disable-normalization", "true",
           "--default.junctions.radius", "6"]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)

    polys = buildings()
    n = inject_buildings(HERE / "roundabout.net.xml", polys)
    with open(HERE / "buildings.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "x1", "y1", "x2", "y2", "x3", "y3", "x4", "y4"])
        for name, p in polys:
            w.writerow([name] + [v for xy in p for v in xy])
    print(f"injected {n} buildings ({len(polys)} polygons) into roundabout.net.xml")

    # --- routes: every arm in, a different arm out --------------------------
    rou = ET.Element("routes")
    written = 0
    for src, si in arms.items():
        for dst, di in arms.items():
            if src == dst:
                continue
            edges_seq = [f"{src}_in"]
            k = si
            while k != di:
                edges_seq.append(f"ring_{k}")
                k = (k + 1) % RING_SEGMENTS
            edges_seq.append(f"{dst}_out")
            ET.SubElement(rou, "route", id=f"{src}2{dst}", edges=" ".join(edges_seq))
            written += 1
    ET.ElementTree(rou).write(HERE / "roundabout.rou.xml", encoding="UTF-8",
                              xml_declaration=True)
    print(f"wrote {written} routes (4 x 3) to roundabout.rou.xml")
    print(f"network: {len(edges)} edges, {len(nodes)} nodes, "
          f"{sum(1 for e in edges if e['id'].endswith('_in'))} arms, "
          f"{RING_SEGMENTS} ring segments")


if __name__ == "__main__":
    main()
