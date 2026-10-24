#!/usr/bin/env python3
"""
gen_mobility.py -- SUMO/TraCI mobility traces for platoon_harq_robustness_eval.

Two scenarios, both built from the scenarios already used in this repo:

  highway     the 3-lane highway of highway_kuo_eval (sumo/highway/), extended
              to a two-carriageway 7 km road (3 lanes per direction). The
              platoon drives the 3 km test stretch in the middle lane of the
              eastbound carriageway at a constant 30 m/s.
  roundabout  the synthetic 4-arm roundabout of roundabout_rsu_eval
              (sumo/roundabout/roundabout.net.xml, 98 buildings). The platoon
              enters from the northern arm (N_in) and leaves through the
              western exit (W_out) -- route N2W, lane 0 all the way.

Platoon (nodes 0..P-1, node 0 = leader at the front)
  Ideal constant-spacing platooning: member i keeps an arc-length distance of
  i * (IVD + L) behind the leader along the leader's own path, where IVD is the
  bumper-to-bumper gap and L the vehicle length. Positions are the SUMO front
  bumper (same convention as the FCD output of the background vehicles).
    * highway    : analytic (x = x0 + v t), the platoon lane is reserved.
    * roundabout : the leader is driven by SUMO (it yields at the entry, slows
                   down on the ring, ...). The members are SUMO vehicles too
                   (so the background traffic reacts to them), but they are
                   moved every step with TraCI to the exact arc-length
                   position computed from the leader's recorded path.

Background vehicles ("surrounding vehicles")
  SUMO keeps a large population; ns-3 only simulates the vehicles that are
  inside the AREA OF INTEREST around the platoon:
    * highway    : |x - x_platoon_centre| <= --hwy-window (default 1000 m),
                   all lanes except the platoon lane. SUMO density per lane is
                   set so that the mean count in the window equals --nbg.
    * roundabout : inside the circle of radius --rbt-radius (500 m) around the
                   roundabout centre; a TraCI controller (same scheme as the
                   original run_density_sim.py) keeps exactly --nbg vehicles in
                   the area.
  Every background vehicle in the area occupies an ns-3 "seat" (node id
  P, P+1, ...). When it leaves the area the seat is freed and parked far away
  (> 1000 km away, beyond ns-3's SpectrumChannel MaxLossDb); the next vehicle
  that enters takes the free seat. The number of seats is therefore the
  maximum number of simultaneous vehicles in the area, not the total number of
  SUMO vehicles.

Output: gzip CSV, one row per (time step, node):
    time,node_id,x,y,speed
  preceded by '# key=value' metadata lines (read by the ns-3 program) and the
  node -> SUMO vehicle mapping is written to the side-car <out>.json.
  Simulation time 0 == start of the trace; --warmup seconds of the platoon run
  precede the measured part (KPI window starts at t = warmup).

Examples
  python3 gen_mobility.py --scenario highway    --ivd 15 --nbg 30 --seed 1 --out traces/hwy_ivd15_n30_s1.csv.gz
  python3 gen_mobility.py --scenario roundabout --ivd 5  --nbg 45 --seed 2 --out traces/rbt_ivd5_n45_s2.csv.gz
"""

import argparse
import gzip
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
# SUMO: $SUMO_HOME if set, else the repo's sumo/ submodule (when built); the
# binaries fall back to the PATH (e.g. apt / pip "eclipse-sumo" installs).
SUMO_HOME = os.environ.get("SUMO_HOME", str(REPO / "sumo"))
if (Path(SUMO_HOME) / "tools").is_dir():
    sys.path.insert(0, str(Path(SUMO_HOME) / "tools"))
import traci  # noqa: E402
import sumolib  # noqa: E402

V = traci.constants


def _sumo_tool(name):
    p = Path(SUMO_HOME) / "bin" / name
    if p.exists():
        return str(p)
    w = shutil.which(name)
    if not w:
        raise SystemExit(f"{name} not found: set SUMO_HOME or put SUMO on the PATH")
    return w


SUMO_BIN = _sumo_tool("sumo")
NETCONVERT = _sumo_tool("netconvert")

PARK_X0 = -1.0e6      # parked (idle) seats: > 1000 km away, 200 km apart, so the
PARK_Y = -1.0e6       # path loss exceeds ns-3's SpectrumChannel MaxLossDb (150 dB)
PARK_DX = 2.0e5


# =============================================================================
# shared helpers
# =============================================================================
class SeatManager:
    """Maps background vehicles inside the area of interest onto ns-3 seats."""

    def __init__(self, first_node):
        self.first = first_node
        self.seat_of = {}       # vid -> seat index
        self.occupant = []      # seat index -> vid or None
        self.history = []       # (seat, vid, t_in, t_out)
        self._t_in = {}

    def update(self, t, inside):
        """inside: dict vid -> (x, y, speed). Returns (released, assigned)."""
        released, assigned = [], []
        for vid in list(self.seat_of):
            if vid not in inside:
                s = self.seat_of.pop(vid)
                self.occupant[s] = None
                released.append(s)
                self.history.append((s, vid, self._t_in.pop(vid), t))
        for vid in sorted(inside):
            if vid in self.seat_of:
                continue
            try:
                s = self.occupant.index(None)
            except ValueError:
                s = len(self.occupant)
                self.occupant.append(None)
            self.occupant[s] = vid
            self.seat_of[vid] = s
            self._t_in[vid] = t
            assigned.append(s)
        return released, assigned

    def close(self, t):
        for vid, s in self.seat_of.items():
            self.history.append((s, vid, self._t_in[vid], t))

    @property
    def n_seats(self):
        return len(self.occupant)

    def node(self, seat):
        return self.first + seat


def write_atomic(path, text):
    """Write a shared generated file safely when traces are built in parallel."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def park_xy(seat):
    return PARK_X0 - PARK_DX * seat, PARK_Y


class TraceWriter:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rows = []          # (t, nid, x, y, v)

    def add(self, t, nid, x, y, v):
        self.rows.append((t, nid, x, y, v))

    def write(self, meta):
        self.rows.sort(key=lambda r: (r[1], r[0]))
        with gzip.open(self.path, "wt") as fh:
            for k, v in meta.items():
                fh.write(f"# {k}={v}\n")
            fh.write("time,node_id,x,y,speed\n")
            for t, nid, x, y, v in self.rows:
                fh.write(f"{t:.3f},{nid},{x:.3f},{y:.3f},{v:.3f}\n")


def emit_seat_rows(tw, seats, t, dt, inside, released, assigned):
    """Write the background rows of one step, with parking around seat swaps."""
    for s in released:
        # left the area after the previous sample: park right after it
        px, py = park_xy(s)
        tw.add(t - dt + 0.001, seats.node(s), px, py, 0.0)
    for s in assigned:
        if t > 0.0:
            px, py = park_xy(s)
            tw.add(t - 0.001, seats.node(s), px, py, 0.0)
    for vid, s in seats.seat_of.items():
        x, y, v = inside[vid]
        tw.add(t, seats.node(s), x, y, v)


def finish_seats(tw, seats, t):
    for s in range(seats.n_seats):
        if seats.occupant[s] is not None:
            continue
        px, py = park_xy(s)
        tw.add(t, seats.node(s), px, py, 0.0)


# =============================================================================
# HIGHWAY
# =============================================================================
HWY_LEN = 7000.0          # road length [m]
HWY_X_START = 2000.0      # x of the leader when the measured 3 km begins
HWY_LANES = 3
HWY_SPEED_LIMIT = 30.0    # [m/s]; background desired speed = limit * N(1, 0.08)


def build_highway_net(net_dir):
    net = net_dir / "highway_2dir.net.xml"
    if net.exists():
        return net
    net_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{os.getpid()}"
    nod, edg = net_dir / f".hwy2.{tag}.nod.xml", net_dir / f".hwy2.{tag}.edg.xml"
    tmp_net = net_dir / f".highway_2dir.{tag}.net.xml"
    nod.write_text(
        '<nodes>\n'
        '    <node id="A" x="0.0" y="0.0" type="priority"/>\n'
        f'    <node id="B" x="{HWY_LEN:.1f}" y="0.0" type="priority"/>\n'
        '</nodes>\n')
    edg.write_text(
        '<edges>\n'
        f'    <edge id="E" from="A" to="B" numLanes="{HWY_LANES}" speed="{HWY_SPEED_LIMIT}"/>\n'
        f'    <edge id="W" from="B" to="A" numLanes="{HWY_LANES}" speed="{HWY_SPEED_LIMIT}"/>\n'
        '</edges>\n')
    subprocess.run([NETCONVERT, "--node-files", str(nod), "--edge-files", str(edg),
                    "--no-turnarounds", "true", "--output-file", str(tmp_net)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.replace(tmp_net, net)   # atomic: parallel builders never see a partial file
    nod.unlink()
    edg.unlink()
    return net


def run_highway(a):
    net_dir = HERE / "sumo" / "highway"
    net_path = build_highway_net(net_dir)
    net = sumolib.net.readNet(str(net_path))
    lane_y = {}
    for e in ("E", "W"):
        for i in range(HWY_LANES):
            shp = net.getEdge(e).getLane(i).getShape()
            lane_y[(e, i)] = shp[0][1]
    y_platoon = lane_y[("E", 1)]

    rng = random.Random(a.seed * 7919 + int(a.ivd) * 31 + a.nbg)
    P = a.platoon
    spacing = a.ivd + a.veh_len
    v = a.hwy_speed
    t_end = a.warmup + a.hwy_pass / v
    x0 = HWY_X_START - v * a.warmup
    W = a.hwy_window
    bg_lanes = [("E", 0), ("E", 2), ("W", 0), ("W", 1), ("W", 2)]
    # mean count in the 2W window over the 5 background lanes == nbg
    k_per_m = a.nbg / (len(bg_lanes) * 2.0 * W) if a.nbg > 0 else 0.0

    vtypes = net_dir / "vtypes_bg.add.xml"
    write_atomic(vtypes, 
        '<additional>\n'
        '  <vType id="bg" vClass="passenger" length="5.0" width="1.8" minGap="2.5" '
        'accel="2.6" decel="4.5" sigma="0.5" tau="1.0" maxSpeed="45" '
        'speedFactor="normc(1.0,0.08,0.85,1.15)"/>\n'
        '</additional>\n')
    routes = net_dir / "routes_2dir.rou.xml"
    write_atomic(routes, '<routes>\n  <route id="rE" edges="E"/>\n  <route id="rW" edges="W"/>\n</routes>\n')

    cmd = [SUMO_BIN, "-n", str(net_path), "-r", str(routes), "--additional-files", str(vtypes),
           "--begin", "0", "--step-length", str(a.dt), "--no-step-log", "true",
           "--no-warnings", "true", "--seed", str(a.seed), "--time-to-teleport", "-1"]
    label = f"hwy_{a.ivd}_{a.nbg}_{a.seed}_{os.getpid()}"
    traci.start(cmd, label=label)
    tw = TraceWriter(a.out)
    seats = SeatManager(P)
    n_in_samples = []
    seq = [0]

    def add_bg(edge, lane, pos):
        seq[0] += 1
        vid = f"bg{seq[0]}"
        traci.vehicle.add(vid, "r" + edge, "bg", depart="now", departLane=str(lane),
                          departPos=f"{pos:.1f}", departSpeed="desired")
        return vid

    def on_departed():
        for vid in traci.simulation.getSubscriptionResults().get(V.VAR_DEPARTED_VEHICLES_IDS, ()):
            if traci.vehicle.getRouteID(vid) == "rE":
                traci.vehicle.setLaneChangeMode(vid, 0)   # never enters the platoon lane
            traci.vehicle.subscribe(vid, [V.VAR_POSITION, V.VAR_SPEED])

    try:
        traci.simulation.subscribe([V.VAR_DEPARTED_VEHICLES_IDS])
        # initial population: jittered regular spacing over the whole road
        if k_per_m > 0:
            sp = 1.0 / k_per_m
            for (e, l) in bg_lanes:
                p = rng.uniform(0, sp)
                while p < HWY_LEN - 10.0:
                    pos = min(max(5.0, p + rng.uniform(-0.3, 0.3) * sp), HWY_LEN - 10.0)
                    add_bg(e, l, pos)
                    p += sp
        # inflow per lane = density * mean speed (Poisson)
        rate = k_per_m * HWY_SPEED_LIMIT
        n_steps = int(round(t_end / a.dt))
        tw_t = 0.0
        for step in range(n_steps + 1):
            traci.simulationStep()
            on_departed()
            t = step * a.dt
            if k_per_m > 0:
                for (e, l) in bg_lanes:
                    if rng.random() < rate * a.dt:
                        add_bg(e, l, 0.0)
            res = traci.vehicle.getAllSubscriptionResults()
            # platoon (analytic)
            xl = x0 + v * t
            for i in range(P):
                tw.add(t, i, xl - i * spacing, y_platoon, v)
            xc = xl - 0.5 * (P - 1) * spacing
            inside = {}
            for vid, r in res.items():
                p = r.get(V.VAR_POSITION)
                if p is None:
                    continue
                if abs(p[0] - xc) <= W:
                    inside[vid] = (p[0], p[1], r.get(V.VAR_SPEED, 0.0))
            n_in_samples.append(len(inside))
            rel, asg = seats.update(t, inside)
            emit_seat_rows(tw, seats, t, a.dt, inside, rel, asg)
            tw_t = t
        seats.close(tw_t)
        finish_seats(tw, seats, tw_t + 0.001)
    finally:
        traci.close()

    meta = {
        "scenario": "highway", "ivd_m": a.ivd, "veh_len_m": a.veh_len, "platoon": P,
        "nbg_target": a.nbg, "seats": seats.n_seats,
        "bg_in_area_mean": round(sum(n_in_samples) / len(n_in_samples), 2),
        "bg_in_area_min": min(n_in_samples), "bg_in_area_max": max(n_in_samples),
        "t_end": round(t_end, 3), "kpi_start": a.warmup, "dt": a.dt, "seed": a.seed,
        "platoon_speed_mps": v, "pass_length_m": a.hwy_pass,
        "area": f"|x - x_platoon_centre| <= {W} m, 5 non-platoon lanes",
        "bg_density_veh_per_km_lane": round(k_per_m * 1000.0, 3),
    }
    tw.write(meta)
    return meta, seats


# =============================================================================
# ROUNDABOUT
# =============================================================================
RBT_CENTER = (0.0, 0.0)
RBT_ARMS = ["N", "E", "S", "W"]
RBT_ARM_END = 990.0
RBT_ENTRY_EDGE = "N_in"
RBT_EXIT_EDGE = "W_out"
RBT_ROUTE = "N2W"
RBT_LANE = 0
RBT_NIN_LEN = 990.49
RBT_BG_LC_MODE = 0b11000000101   # SUMO default 1621 minus speed-gain (16) and keep-right (64)


def run_roundabout(a):
    global RBT_EXIT_EDGE, RBT_ROUTE
    RBT_EXIT_EDGE, RBT_ROUTE = f"{a.rbt_exit}_out", f"N2{a.rbt_exit}"
    net_dir = HERE / "sumo" / "roundabout"
    net_path = net_dir / "roundabout.net.xml"
    rou_path = net_dir / "roundabout.rou.xml"
    rng = random.Random(a.seed * 104729 + int(a.ivd) * 17 + a.nbg)
    P = a.platoon
    spacing = a.ivd + a.veh_len
    R = a.rbt_radius

    vtypes = net_dir / f"vtypes_platoon_L{a.veh_len:g}.add.xml"
    write_atomic(vtypes, 
        '<additional>\n'
        '  <vType id="car" vClass="passenger" length="4.5" width="2.0" minGap="2.5" '
        'accel="2.6" decel="4.5" emergencyDecel="6.0" sigma="0.5" tau="1.0" maxSpeed="55" '
        'speedFactor="normc(1.0,0.1,0.9,1.0)" lcStrategic="10" lcCooperative="5" '
        'lcSpeedGain="1" lcKeepRight="1" carFollowModel="Krauss" junctionModel="SL2015"/>\n'
        f'  <vType id="platoon" vClass="passenger" length="{a.veh_len}" width="1.8" '
        'minGap="2.5" accel="2.6" decel="4.5" sigma="0.0" tau="1.0" maxSpeed="55" '
        'speedFactor="1.0" carFollowModel="Krauss" junctionModel="SL2015" '
        'lcStrategic="0" lcCooperative="0" lcSpeedGain="0" lcKeepRight="0"/>\n'
        '</additional>\n')

    cmd = [SUMO_BIN, "-n", str(net_path), "-r", str(rou_path), "--additional-files", str(vtypes),
           "--begin", "0", "--step-length", str(a.dt), "--no-step-log", "true",
           "--no-warnings", "true", "--seed", str(a.seed), "--time-to-teleport", "300",
           "--collision.action", "none"]
    label = f"rbt_{a.ivd}_{a.nbg}_{a.seed}_{os.getpid()}"
    traci.start(cmd, label=label)
    tw = TraceWriter(a.out)
    seats = SeatManager(P)
    n_in_samples = []
    seq = [0]
    platoon_ids = [f"p{i}" for i in range(P)]
    pset = set(platoon_ids)
    gone = set()

    def lane_free(results, edge, pos, lane=None, gap=25.0):
        for vid, r in results.items():
            if r.get(V.VAR_ROAD_ID) != edge:
                continue
            if lane is not None and r.get(V.VAR_LANE_INDEX) != lane:
                continue
            if abs(r.get(V.VAR_LANEPOSITION, -1e9) - pos) < gap:
                return False
        return True

    def subscribe(vid):
        traci.vehicle.subscribe(vid, [V.VAR_POSITION, V.VAR_SPEED, V.VAR_ROAD_ID,
                                      V.VAR_LANE_INDEX, V.VAR_LANEPOSITION, V.VAR_ANGLE,
                                      V.VAR_DISTANCE])

    def control_density(results, target):
        """Top up / trim the background population inside the circle."""
        in_area = []
        for vid, r in results.items():
            if vid in pset or vid in gone:
                continue
            p = r.get(V.VAR_POSITION)
            if p is not None and math.dist(p, RBT_CENTER) <= R:
                in_area.append((vid, p))
        n_in = len(in_area)
        attempts = 0
        while n_in < target and attempts < 32:
            attempts += 1
            arm = rng.choice(RBT_ARMS)
            edge = f"{arm}_in"
            pos = rng.uniform(RBT_ARM_END * 0.45, RBT_ARM_END - 20.0)
            if not lane_free(results, edge, pos):
                continue
            if platoon_footprint and edge == RBT_ENTRY_EDGE and \
                    platoon_footprint[0] - 30.0 <= pos <= platoon_footprint[1] + 30.0:
                continue
            seq[0] += 1
            vid = f"c{seq[0]}"
            dst = rng.choice([x for x in RBT_ARMS if x != arm])
            traci.vehicle.add(vid, f"{arm}2{dst}", "car", depart="now", departLane="free",
                              departPos=f"{pos:.1f}", departSpeed="max")
            n_in += 1
        while n_in > target:
            in_area.sort(key=lambda vp: abs(math.dist(vp[1], RBT_CENTER) - R))
            vid = in_area.pop(0)[0]
            try:
                traci.vehicle.remove(vid)
            except traci.TraCIException:
                pass
            gone.add(vid)
            n_in -= 1

    platoon_footprint = None
    t_cold = a.rbt_cold if a.nbg > 0 else 1.0
    try:
        traci.simulation.subscribe([V.VAR_ARRIVED_VEHICLES_IDS, V.VAR_DEPARTED_VEHICLES_IDS])

        def step_sim():
            traci.simulationStep()
            sub = traci.simulation.getSubscriptionResults()
            for vid in sub.get(V.VAR_ARRIVED_VEHICLES_IDS, ()):
                gone.add(vid)
            for vid in sub.get(V.VAR_DEPARTED_VEHICLES_IDS, ()):
                subscribe(vid)
                if vid not in pset:
                    # strategic + cooperative lane changes only (no keep-right,
                    # no speed-gain): stops background cars from cutting into
                    # the gaps of the platoon on the ring / exit
                    traci.vehicle.setLaneChangeMode(vid, RBT_BG_LC_MODE)

        sim_t = 0.0
        # ------------------------------------------------ cold start (bg only)
        while sim_t < t_cold:
            step_sim()
            sim_t = traci.simulation.getTime()
            if a.nbg > 0 and int(round(sim_t / a.dt)) % max(1, int(round(0.1 / a.dt))) == 0:
                control_density(traci.vehicle.getAllSubscriptionResults(), a.nbg)

        # ------------------------------------------------ insert the platoon
        d_start = a.rbt_approach + a.warmup * 13.89     # leader distance to give-way at t=0
        pos0 = RBT_NIN_LEN - d_start
        if pos0 - (P - 1) * spacing < 5.0:
            raise SystemExit("platoon does not fit on N_in; reduce --rbt-approach")
        platoon_footprint = (pos0 - (P - 1) * spacing - a.veh_len, pos0)
        res = traci.vehicle.getAllSubscriptionResults()
        for vid, r in res.items():   # clear the platoon's footprint on lane 0
            if r.get(V.VAR_ROAD_ID) == RBT_ENTRY_EDGE and r.get(V.VAR_LANE_INDEX) == RBT_LANE:
                lp = r.get(V.VAR_LANEPOSITION, -1e9)
                if platoon_footprint[0] - 40.0 <= lp <= platoon_footprint[1] + 40.0:
                    traci.vehicle.remove(vid)
                    gone.add(vid)

        def clear_footprint_fresh():
            """retry path: current positions (not last step's subscription results, which miss
            a vehicle that departed in the last step) plus vehicles still waiting for insertion
            on the entry edge"""
            for vid in traci.vehicle.getIDList():
                if vid in pset:
                    continue
                if traci.vehicle.getRoadID(vid) == RBT_ENTRY_EDGE and \
                        traci.vehicle.getLaneIndex(vid) == RBT_LANE and \
                        platoon_footprint[0] - 40.0 <= traci.vehicle.getLanePosition(vid) <= platoon_footprint[1] + 40.0:
                    traci.vehicle.remove(vid)
                    gone.add(vid)
            for vid in traci.simulation.getPendingVehicles():
                if vid not in pset and traci.vehicle.getRoute(vid)[:1] == (RBT_ENTRY_EDGE,):
                    traci.vehicle.remove(vid)
                    gone.add(vid)

        # attempt 0 is the original insertion (traces that inserted at once are unchanged); if
        # SUMO cannot insert every member (a blocker the clearing missed), the whole platoon is
        # removed, the footprint cleared again from current positions and the insertion repeated
        for attempt in range(6):
            if attempt > 0:
                for pid in platoon_ids:
                    try:
                        traci.vehicle.remove(pid)
                    except traci.TraCIException:
                        pass
                clear_footprint_fresh()
                step_sim()
                for pid in platoon_ids:
                    gone.discard(pid)
            for i, pid in enumerate(platoon_ids):
                traci.vehicle.add(pid, RBT_ROUTE, "platoon", depart="now", departLane=str(RBT_LANE),
                                  departPos=f"{pos0 - i * spacing:.2f}",
                                  departSpeed=("13.89" if i == 0 else "0"))
            step_sim()
            missing = [p for p in platoon_ids if p not in traci.vehicle.getIDList()]
            if not missing:
                break
            print(f"platoon insertion attempt {attempt + 1}: SUMO could not insert {missing}, retrying",
                  file=sys.stderr)
        if missing:
            raise SystemExit(f"platoon insertion failed for {missing}")
        insert_attempts = attempt + 1
        t_ins = traci.simulation.getTime()
        missing = [p for p in platoon_ids if p not in traci.vehicle.getIDList()]
        if missing:
            raise SystemExit(f"platoon insertion failed for {missing}")
        for i, pid in enumerate(platoon_ids):
            traci.vehicle.setLaneChangeMode(pid, 0)
            if i > 0:
                traci.vehicle.setSpeedMode(pid, 0)

        lane_shape = traci.lane.getShape(f"{RBT_ENTRY_EDGE}_{RBT_LANE}")
        (sx0, sy0), (sx1, sy1) = lane_shape[0], lane_shape[-1]
        lane_len = math.dist(lane_shape[0], lane_shape[-1])
        ux, uy = (sx1 - sx0) / lane_len, (sy1 - sy0) / lane_len

        def entry_xy(lane_pos):
            return sx0 + ux * lane_pos, sy0 + uy * lane_pos

        hist = []          # leader path samples: (odo, x, y, angle, speed)
        odo_exit = None
        t_rel = 0.0
        max_steps = int(round(a.rbt_max_time / a.dt))
        step = 0
        overlaps = 0
        while True:
            res = traci.vehicle.getAllSubscriptionResults()
            rl = res[platoon_ids[0]]
            odo_l = rl[V.VAR_DISTANCE]          # odometer since insertion (0 at insertion)
            xl, yl = rl[V.VAR_POSITION]
            vl = rl[V.VAR_SPEED]
            hist.append((odo_l, xl, yl, rl[V.VAR_ANGLE], vl))
            if odo_exit is None and rl[V.VAR_ROAD_ID] == RBT_EXIT_EDGE:
                odo_exit = odo_l

            # ---- platoon rows + move the members
            ppos = []
            for i, pid in enumerate(platoon_ids):
                if i == 0:
                    x, y, ang, sp = xl, yl, rl[V.VAR_ANGLE], vl
                else:
                    target = odo_l - i * spacing
                    if target < hist[0][0]:
                        x, y = entry_xy(pos0 + (target - hist[0][0]))
                        ang, sp = hist[0][3], vl
                    else:
                        lo, hi = 0, len(hist) - 1
                        while hi - lo > 1:
                            mid = (lo + hi) // 2
                            if hist[mid][0] <= target:
                                lo = mid
                            else:
                                hi = mid
                        h0, h1 = hist[lo], hist[hi]
                        w = 0.0 if h1[0] - h0[0] < 1e-9 else (target - h0[0]) / (h1[0] - h0[0])
                        w = min(max(w, 0.0), 1.0)
                        x = h0[1] + w * (h1[1] - h0[1])
                        y = h0[2] + w * (h1[2] - h0[2])
                        ang = h0[3] if w < 0.5 else h1[3]
                        sp = h0[4] + w * (h1[4] - h0[4])
                    try:
                        traci.vehicle.moveToXY(pid, "", -1, x, y, ang, keepRoute=1)
                        traci.vehicle.setSpeed(pid, max(sp, 0.0))
                    except traci.TraCIException:
                        pass
                tw.add(t_rel, i, x, y, sp)
                ppos.append((x, y))

            # ---- background in the circle -> seats
            inside = {}
            for vid, r in res.items():
                if vid in pset or vid in gone:
                    continue
                p = r.get(V.VAR_POSITION)
                if p is None or math.dist(p, RBT_CENTER) > R:
                    continue
                inside[vid] = (p[0], p[1], r.get(V.VAR_SPEED, 0.0))
                for (px, py) in ppos:
                    if math.dist(p, (px, py)) < 3.0:
                        overlaps += 1
                        break
            n_in_samples.append(len(inside))
            rel, asg = seats.update(t_rel, inside)
            emit_seat_rows(tw, seats, t_rel, a.dt, inside, rel, asg)

            # ---- stop when the tail is past the exit
            if odo_exit is not None and odo_l - (P - 1) * spacing >= odo_exit + a.rbt_exit_margin:
                break
            step += 1
            if step > max_steps:
                raise SystemExit(f"platoon did not clear the roundabout within {a.rbt_max_time}s "
                                 f"(odo={odo_l:.0f} m) -- gridlock?")
            step_sim()
            t_rel = round(traci.simulation.getTime() - t_ins, 6)
            if a.nbg > 0 and step % max(1, int(round(0.1 / a.dt))) == 0:
                control_density(traci.vehicle.getAllSubscriptionResults(), a.nbg)
        seats.close(t_rel)
        finish_seats(tw, seats, t_rel + 0.001)
        leader_speeds = [h[4] for h in hist]
    finally:
        traci.close()

    meta = {
        "scenario": "roundabout", "ivd_m": a.ivd, "veh_len_m": a.veh_len, "platoon": P,
        "nbg_target": a.nbg, "seats": seats.n_seats,
        "bg_in_area_mean": round(sum(n_in_samples) / len(n_in_samples), 2) if n_in_samples else 0,
        "bg_in_area_min": min(n_in_samples) if n_in_samples else 0,
        "bg_in_area_max": max(n_in_samples) if n_in_samples else 0,
        "t_end": round(t_rel, 3), "kpi_start": a.warmup, "dt": a.dt, "seed": a.seed,
        "route": f"N_in(lane0) -> ring -> {RBT_EXIT_EDGE}(lane0)",
        "leader_start_to_giveway_m": round(d_start, 1),
        "leader_mean_speed_mps": round(sum(leader_speeds) / len(leader_speeds), 2),
        "leader_min_speed_mps": round(min(leader_speeds), 2),
        "area": f"circle r={R} m around the roundabout centre",
        "bg_platoon_overlap_samples": overlaps,
        "cold_start_s": t_cold,
        "platoon_insert_attempts": insert_attempts,
    }
    tw.write(meta)
    return meta, seats


# =============================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=["highway", "roundabout"], required=True)
    ap.add_argument("--ivd", type=float, required=True, help="bumper-to-bumper gap [m]")
    ap.add_argument("--nbg", type=int, required=True, help="background vehicles in the area")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--platoon", type=int, default=9)
    ap.add_argument("--veh-len", type=float, default=5.0)
    ap.add_argument("--dt", type=float, default=0.1)
    ap.add_argument("--warmup", type=float, default=3.0, help="s before the measured part")
    ap.add_argument("--hwy-speed", type=float, default=30.0)
    ap.add_argument("--hwy-pass", type=float, default=3000.0)
    ap.add_argument("--hwy-window", type=float, default=1000.0)
    ap.add_argument("--rbt-radius", type=float, default=500.0)
    ap.add_argument("--rbt-cold", type=float, default=120.0)
    ap.add_argument("--rbt-approach", type=float, default=250.0,
                    help="leader distance to the give-way line at the start of the KPI window")
    ap.add_argument("--rbt-exit-margin", type=float, default=100.0,
                    help="the run ends when the tail is this far past the W_out start")
    ap.add_argument("--rbt-exit", choices=["W", "S", "E"], default="W",
                    help="platoon exit from N_in: W = 1/4 of the ring (default), S = 1/2, E = 3/4")
    ap.add_argument("--rbt-max-time", type=float, default=400.0)
    a = ap.parse_args()

    t0 = time.time()
    if a.scenario == "highway":
        meta, seats = run_highway(a)
    else:
        meta, seats = run_roundabout(a)
    meta["wall_clock_s"] = round(time.time() - t0, 1)
    side = {"meta": meta,
            "seat_history": [{"node": seats.node(s), "veh": vid, "t_in": round(ti, 3),
                              "t_out": round(to, 3)} for (s, vid, ti, to) in seats.history]}
    Path(str(a.out).replace(".csv.gz", ".json")).write_text(json.dumps(side, indent=1))
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
