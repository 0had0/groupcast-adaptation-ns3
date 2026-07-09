#!/usr/bin/env python3
"""
platoon_intersection_merge_split — SUMO TraCI controller + mob.csv generator.

Scenario (mirrors the IVD-sweep and merge-incremental reference scenarios):

  * 8-vehicle platoon v0..v7 starts 20 m back of v0's start on the south
    approach (e_south_in), heading north towards J1.
  * At J1 the platoon turns right onto e_j1_j2.
  * 2 joiners (j0, j1) come from the west (e_west_in) and merge between v3
    and v4 — no vehicle stops, gaps open and close under TraCI control.
  * 100 m later, at J2, the platoon turns left onto e_j2_north.
  * v1, v3, v6 peel off straight onto e_j2_east (leaver routes).
  * v0,v2,j0,j1,v4,v5,v7 close ranks and continue north for 200 m.

Control: a *progress coordinate* p (signed metres, increasing along the
platoon's nominal path) lets us run a single CACC law across the right
turn, the merge segment, and the left turn.

  South approach (north, y in [-200, 0]):
        p = +south_length - (y - y_south_start)        (p grows as we go north)
  J1 corner (0,0):                                    p = +south_length
  East segment (x in [0, 100]):
        p = +south_length + x
  J2 corner (100,0):                                  p = +south_length + 100
  North leg (y in [0, 200]):
        p = +south_length + 100 + y

  Joiners (west, x in [-200, 0]) use a parallel "joiner progress" p_j:
        p_j = +west_length - (0 - x)                   (x grows eastwards, p_j grows)

  Both coordinate systems are *aligned at J1*: when the platoon is at J1 its
  p == south_length, and the joiner trailing edge at the same physical point
  has p_j == west_length.  We map joiner slots onto platoon slots in p by an
  offset = (south_length - west_length) at insertion time.  The CACC loop
  for joiners then targets a position in *platoon-p* derived from a target
  p_j offset.

Output:
  fcd_output.xml                 — SUMO FCD for visual validation
  platoon_intersection_merge_split.mob.csv  — ns-3 mobility trace
"""

from __future__ import annotations

import csv
import math
import subprocess
import sys
import xml.etree.ElementTree as ET
from enum import Enum, auto
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent.resolve()
SUMO_BIN    = Path("/Users/hadih/Documents/groupcast-adaptation-ns3/sumo/bin")
SUMO_EXE    = SUMO_BIN / "sumo"
NETCONVERT  = SUMO_BIN / "netconvert"
SUMO_TOOLS  = Path("/Users/hadih/Documents/groupcast-adaptation-ns3/sumo/tools")

# ── Geometry ───────────────────────────────────────────────────────────────
# NOTE: netconvert with --geometry.remove=true re-scales the network.
# These lengths are the *actual* lengths from net.net.xml, not the
# nominal values in nodes.nod.xml.  Keep them in sync with build_net.sh.
SOUTH_LENGTH  = 186.80   # e_south_in
EAST_LENGTH   = 76.80    # e_j1_j2
NORTH_LENGTH  = 790.00   # e_j2_north
WEST_LENGTH   = 190.00   # e_west_in
J1 = (0.0,   0.0)
J2 = (100.0, 0.0)

# ── Platoon design ─────────────────────────────────────────────────────────
N_PLATOON   = 8
VEH_LENGTH  = 4.5
PLATOON_IVD = 15.0        # m — safe time-headway-based gap
SPACING     = VEH_LENGTH + PLATOON_IVD   # 19.5 m front-to-front
V_CRUISE    = 10.0        # m/s  (36 km/h)
V_CRUISE_BOOST = 15.0    # m/s  allowed when gap > SPACING to close ranks
V_TURN      = 5.0         # m/s  through the junction corners

# Slotted joiners try to cruise at this speed so the back of the platoon
# doesn't get stuck at MIN_SPEED while the joiner is still mid-merge.
# It is strictly less than V_CRUISE so the joiner will slow slightly
# if it gets too close to its leader, but high enough to give the back
# of the platoon forward progress.
V_JOIN_CRUISE = 6.0       # m/s

# During a joiner merge, the back of the platoon (the vehicles
# following the joiner in platoon_order) will slow down to let the
# joiner catch up.  V_MERGE_BACK is the *minimum* speed they drop to
# (so they keep making forward progress and the merge finishes in a
# reasonable time).
V_MERGE_BACK = 4.0       # m/s

V_JOINER    = 10.0        # m/s  joiner cruise
T_SIM       = 150.0
DT          = 0.1
KP          = 3.0
KD          = 1.5
MAX_ACCEL   = 2.5
MAX_DECEL   = -5.0
MIN_SPEED   = 2.0         # never let a vehicle stop (also = leaver hold speed)
LEADER_START_P = 156.5    # v0 (front of platoon) starts at lane_pos 156.5 on e_south_in


# ── Progress coordinate mappings ───────────────────────────────────────────

def platoon_p(x: float, y: float, edge: str = "", lane_pos: float = -1.0) -> float:
    """Map (x, y) OR (edge, lane_pos) onto the platoon's nominal-path p.

    Prefers (edge, lane_pos) when edge is provided, because SUMO's net
    transform does not preserve the world coordinates we set in
    nodes.nod.xml.  The edge-id mapping below uses edge lengths from the
    generated net, which are stable regardless of geometry.remove.
    """
    if edge:
        return _p_from_edge(edge, lane_pos, _PLATOON_EDGE_P)
    return platoon_p_v2(x, y)


def joiner_p(x: float, y: float, edge: str = "", lane_pos: float = -1.0) -> float:
    """Map a joiner to its progress along the west approach."""
    if edge == "e_west_in":
        return WEST_LENGTH - lane_pos   # lane_pos: 0 at south end (x=-200), length at x=0
    return WEST_LENGTH + x


# Edge → cumulative nominal-p at the *start* of the edge.  p is the
# cumulative path-length along the nominal route, so the J1 right turn
# and J2 left turn do not introduce any offset.
_PSTART_E_SOUTH_IN = 0.0
_PSTART_E_J1_J2    = SOUTH_LENGTH
_PSTART_E_J2_NORTH = SOUTH_LENGTH + EAST_LENGTH
_PSTART_E_J2_EAST  = SOUTH_LENGTH + EAST_LENGTH   # leaver — same start p as north leg

# Map edge id → (start_p, length) — these must match the actual
# lengths in net.net.xml (see "Geometry" above).
_PLATOON_EDGE_P = {
    "e_south_in": (_PSTART_E_SOUTH_IN, 186.80),
    "e_west_in":  (0.0,                190.00),   # joiner approach (not in platoon-p)
    "e_j1_j2":    (_PSTART_E_J1_J2,     76.80),
    "e_j2_north": (_PSTART_E_J2_NORTH, 190.00),
    "e_j2_east":  (_PSTART_E_J2_EAST,  186.80),
}


def _p_from_edge(edge: str, lane_pos: float, table: dict) -> float:
    if edge in table:
        ps, length = table[edge]
        return ps + min(max(lane_pos, 0.0), length)
    # Junction-internal or unknown edge → return last known by snapping.
    # We don't expect to control vehicles on internal edges.
    return float("nan")


def xy_to_p(x: float, y: float) -> float:
    """Map world (x, y) → p, handling intersection internal corners.

    Network geometry (from net.net.xml after geometry.remove):
      e_south_in : x≈201.6, y in [13.20, 200.0]    (lane_pos 0 at y=13.2, length 186.8)
      e_west_in  : y≈198.4, x in [10.00, 200.0]    (lane_pos 0 at x=10, length 190.0)
      e_j1_j2    : y≈198.4, x in [213.20, 290.0]   (length 76.8)
      e_j2_north : x≈301.6, y in [210.0, 1000.0]   (length 790.0)
      e_j2_east  : y≈198.4, x in [313.20, 500.0]   (length 186.8)

    Cumulative p:
      south leg: 0..186.8
      east leg : 186.8..263.6
      north leg: 263.6..1053.6
      leaver east (J2 straight): 263.6..450.4
    """
    # south leg (but NOT the J1 internal corner region; check
    # J1 internal first to avoid mis-mapping joiners that are
    # in the J1 internal corner as "south leg")
    if 200.0 < x < 203.5 and 0.0 <= y <= 187.0:
        return y
    # J1 internal corner (right turn from south to east)
    if 200.0 < x < 214.0 and 187.0 < y < 200.0:
        return 186.80 + (x - 201.6) * 0.92
    # east leg (J1 → J2)
    if 212.0 < x < 290.0 and abs(y - 198.4) < 5.0:
        return 186.80 + (x - 200.0)
    # J2 internal corner (east straight continuation OR left turn)
    if 289.0 < x < 315.0 and 195.0 < y < 211.0:
        return 186.80 + 76.80 + (x - 290.0)
    # north leg
    if 300.0 < x < 304.0 and 210.0 <= y <= 1000.0:
        return 263.60 + (y - 210.0)
    # leaver east (past J2)
    if 313.0 < x < 510.0 and abs(y - 198.4) < 5.0:
        return 263.60 + (x - 300.0)
    # west approach (joiner)
    if 0.0 <= x < 200.0 and abs(y - 198.4) < 5.0:
        return 200.0 - (200.0 - x)   # increases as x→200
    return float("nan")


def platoon_p_v2(x: float, y: float) -> float:
    """Map (x,y) onto the platoon's nominal path, p increases with travel."""
    # 1) South leg: y in [-200, 0], x≈0  →  p = (0 - y)             ∈ [0, 200]
    # 2) East leg : x in [0, 100],  y≈0  →  p = 200 + x              ∈ [200, 300]
    # 3) North leg: y in [0, 200],  x≈100 →  p = 300 + y             ∈ [300, 500]
    if y <= 0 and x <= 0 + 1e-6:
        # south leg (small x jitter near the approach axis)
        return (0.0 - y)
    if 0.0 - 1e-6 <= y <= 0.0 + 1e-6 and x >= 0.0:
        return 200.0 + x
    if x >= 100.0 - 1e-6 and y >= 0.0:
        return 300.0 + y
    # Snap ambiguous inside-corner points to the nearest leg
    # (vehicles travel along the corner arc, but our control law only needs
    #  a consistent p; snapping to the next leg is fine for our purposes).
    if abs(x) < 1.0 and abs(y) < 1.0:
        return 200.0
    if abs(x - 100.0) < 1.0 and abs(y) < 1.0:
        return 300.0
    # Unknown region: leaver straight past J2
    return 300.0 + math.hypot(x - 100.0, y)


def leader_p_target(t: float) -> float:
    """Leader's target progress p at time t (open-loop speed profile).

    p is the cumulative path length, so the south leg is 0..200, the
    east segment is 200..300, and the north leg is 300..500.  We cruise
    at V_CRUISE throughout; SUMO's lateral control naturally slows the
    vehicle slightly in the corners.
    """
    p0 = LEADER_START_P
    if t < 0:
        return p0
    pmax = SOUTH_LENGTH + EAST_LENGTH + NORTH_LENGTH
    return min(p0 + V_CRUISE * t, pmax)


def leader_speed(t: float) -> float:
    return V_CRUISE


# ── TraCI helpers ──────────────────────────────────────────────────────────

def xy_of(vid: str, traci) -> tuple[float, float]:
    try:
        return traci.vehicle.getPosition(vid)
    except Exception:
        return (math.nan, math.nan)


def spd_of(vid: str, traci) -> float:
    try:
        return traci.vehicle.getSpeed(vid)
    except Exception:
        return 0.0


def set_modes(vid: str, traci) -> None:
    try:
        traci.vehicle.setSpeedMode(vid, 0)
        traci.vehicle.setLaneChangeMode(vid, 0)
    except Exception:
        pass


def set_spd(vid: str, v: float, traci) -> None:
    try:
        traci.vehicle.setSpeed(vid, max(0.0, v))
    except Exception:
        pass


# ── CACC law ───────────────────────────────────────────────────────────────

def cacc_step(self_vid: str, self_p: float, self_v: float,
              pred_p: float, pred_v: float, succ_p: float, succ_v: float,
              traci, leaver: bool = False) -> float:
    """Compute CACC speed for a single vehicle.

    `succ_p` is the position of the immediate successor in
    platoon_order, or -inf if there is no successor.  This is used
    so the CACC chain doesn't run away: when the chain has a gap
    that needs closing, only the LAST vehicle in the chain boosts
    above V_CRUISE; the earlier ones stay at V_CRUISE so the last
    vehicle can actually catch up.

    `succ_v` is the speed of the successor (or 0 if no successor).
    """
    gap = pred_p - self_p - VEH_LENGTH
    succ_gap = (succ_p - self_p - VEH_LENGTH) if succ_p > 0 else -float("inf")
    # If we're at or below the desired IVD, we should NEVER chase
    # the pred — that just closes the gap further.  Disable KD in
    # that regime so we don't accelerate into the pred's bumper.
    if gap < PLATOON_IVD:
        acc = KP * (gap - PLATOON_IVD)        # only KP, brake-only
    elif gap > SPACING:
        acc = KP * (gap - PLATOON_IVD) + KD * (pred_v - self_v)
    else:
        acc = 0.0                              # hold speed
    acc = max(MAX_DECEL, min(MAX_ACCEL, acc))
    new_v = max(MIN_SPEED, self_v + acc * DT)
    # V_CRUISE_BOOST when the gap to pred is large AND the successor
    # is at SPACING or further behind (succ_gap <= -SPACING).  If
    # the successor is too close or ahead of us, boosting would
    # risk rear-ending / overtaking them — so stay at V_CRUISE
    # and let the car-following brake if needed.
    # V_CRUISE_BOOST when the gap to pred is large AND the successor
    # is at SPACING or further behind (succ_gap <= -SPACING).  If
    # the successor is too close or ahead of us, boosting would
    # risk rear-ending / overtaking them — so stay at V_CRUISE
    # and let the car-following brake if needed.
    if gap > SPACING and succ_gap <= -SPACING and not leaver:
        v_cap = V_CRUISE_BOOST
    else:
        v_cap = V_CRUISE
    # If we're already at proper distance from our pred AND our
    # successor is too close (or ahead of us), cap our speed at
    # theirs so we don't rear-end them.  We DON'T cap when gap to
    # pred is large — in that case we have legitimate room to
    # boost and the succ will catch up later.
    if gap < SPACING and succ_p > 0 and succ_gap > -SPACING:
        v_cap = min(v_cap, succ_v)
    new_v = min(new_v, v_cap)
    return new_v


# ── State machine ──────────────────────────────────────────────────────────

class State(Enum):
    APPROACH    = auto()    # south leg, before J1
    MERGE       = auto()    # right turn + east segment, joiners inserting
    SPLIT       = auto()    # J2 left turn, leavers peeling off
    CRUISE_N    = auto()    # after J2, north leg
    DONE        = auto()


# ── Build network ──────────────────────────────────────────────────────────

def build_network() -> None:
    net = SCRIPT_DIR / "net.net.xml"
    if net.exists():
        return
    subprocess.run([str(NETCONVERT),
                    "--node-files",       str(SCRIPT_DIR / "nodes.nod.xml"),
                    "--edge-files",       str(SCRIPT_DIR / "edges.edg.xml"),
                    "--connection-files", str(SCRIPT_DIR / "connections.con.xml"),
                    "--output-file",      str(net),
                    "--no-turnarounds",   "--geometry.remove=true",
                    "--junctions.corner-detail=5"], check=True)


# ── Run simulation ─────────────────────────────────────────────────────────

def run_simulation() -> None:
    sys.path.insert(0, str(SUMO_TOOLS))
    import traci

    traci.start([str(SUMO_EXE), "-c", str(SCRIPT_DIR / "scenario.sumocfg"),
                 "--no-step-log"])
    step = 0
    t = 0.0

    platoon_vids = [f"v{i}" for i in range(N_PLATOON)]
    leaver_ids = {"v1", "v3", "v6"}
    joiner_vids = ["j0", "j1"]

    # For plotting / mob.csv ordering, fixed map.
    node_id_map: dict[str, int] = {}
    nid = 0
    for vid in platoon_vids:
        node_id_map[vid] = nid; nid += 1
    for vid in joiner_vids:
        node_id_map[vid] = nid; nid += 1
    # Track in-platoon ordering (after merge) separately
    platoon_order: list[str] = list(platoon_vids)        # 8 entries
    joiner_order: list[str] = []                          # 0..2 entries

    state = State.APPROACH
    insert_pending = False   # True while joiners are mid-merge
    split_done_vids: set[str] = set()

    def get_state(vid: str) -> tuple[str, float, float, float]:
        """Return (edge, lane_pos, x, y) for vid, or ('',nan,nan,nan) on err."""
        try:
            edge = traci.vehicle.getRoadID(vid)
            lp   = traci.vehicle.getLanePosition(vid)
            x, y = traci.vehicle.getPosition(vid)
            return edge, lp, x, y
        except Exception:
            return "", float("nan"), float("nan"), float("nan")

    # State initialization: deploy joiners on e_west_in at t=0 so they
    # cruise towards J1 in parallel with the platoon approaching from
    # the south.  CACC will keep them in their target_p slot.
    joiner_target_p: dict[str, float] = {}
    joiner_target_p_j0: float = 0.0
    joiner_target_p_j1: float = 0.0

    # ── Deploy joiners on the west approach at t=0 ─────────────────
    # Platoon departs with v0 at lane_pos 156.5 (front, 30.3m before J1)
    # trailing back to v7 at lane_pos 20.  v3 is the 4th vehicle, at
    # lane_pos 98.0.  v3 reaches J1 at t = (SOUTH_LENGTH - 98) / V_CRUISE
    # = ~8.88s, so p_v3 at J1 is SOUTH_LENGTH.  We want:
    #   j0 target_p at J1 = SOUTH_LENGTH - SPACING
    #   j1 target_p at J1 = SOUTH_LENGTH - 2*SPACING
    # Deploying at t=0 with V_JOINER=V_CRUISE.
    joiner_target_p_j0 = SOUTH_LENGTH - SPACING
    joiner_target_p_j1 = joiner_target_p_j0 - SPACING
    t_v3_to_J1 = (SOUTH_LENGTH - 98.0) / V_CRUISE
    for k, jid in enumerate(joiner_vids):
        target_p_jk = (joiner_target_p_j0 if k == 0 else joiner_target_p_j1)
        time_to_J1 = t_v3_to_J1 + (k + 1) * SPACING / V_CRUISE
        deploy_lp = max(2.0, WEST_LENGTH - time_to_J1 * V_JOINER)
        joiner_target_p[jid] = target_p_jk
    # Note: actual vehicle.add happens at the end of the FIRST step via
    # a state init step.

    def get_p(vid: str, edge: str, lp: float) -> float:
        if vid in joiner_target_p and edge == "e_west_in":
            # On the west approach, p grows from (target - edge_len) to target
            # as lane_pos grows from 0 to edge_length.
            return joiner_target_p[vid] - (200.0 - lp)
        return platoon_p(0.0, 0.0, edge, lp)

    # Prime the simulation so vehicles are inserted into TraCI's view.
    traci.simulationStep()
    step = 1

    # Per-vehicle "brake hold" — if a vehicle is in this dict with a
    # future time, the CACC loop will force it to MIN_SPEED until that
    # time passes.  Currently unused (the geometric platoon-order fix
    # below makes it unnecessary), but kept as an escape hatch.
    brake_hold: dict[str, float] = {}

    # Deploy joiners on e_west_in at t≈0.
    for k, jid in enumerate(joiner_vids):
        target_p_jk = (joiner_target_p_j0 if k == 0 else joiner_target_p_j1)
        time_to_J1 = t_v3_to_J1 + (k + 1) * SPACING / V_CRUISE
        deploy_lp = max(2.0, WEST_LENGTH - time_to_J1 * V_JOINER)
        joiner_target_p[jid] = target_p_jk
        try:
            traci.vehicle.add(jid, "r_joiner", typeID="joiner",
                               depart="now", departLane="0",
                               departPos=str(deploy_lp),
                               departSpeed=str(V_JOINER))
            traci.vehicle.setSpeedMode(jid, 0)
            traci.vehicle.setLaneChangeMode(jid, 0)
            joiner_order.append(jid)
            print(f"[t={t:.1f}] deploy {jid} lp={deploy_lp:.1f} target_p={target_p_jk:.1f}")
        except Exception as e:
            print(f"[warn] add {jid}: {e}")
    state = State.MERGE
    insert_pending = True

    while traci.simulation.getMinExpectedNumber() > 0 and t < T_SIM:
        t = round(step * DT, 2)

        # ── Modes for every live vehicle ──
        live = set(traci.vehicle.getIDList())
        for vid in live:
            set_modes(vid, traci)

        # ── Leader: drive open-loop along p_target(t) regardless of which
        #    edge it's currently on.  This includes junction internal
        #    edges where we can't read lane position.  ──
        leader = "v0"
        vL_cmd = leader_speed(t)
        # Track the target p exactly at V_CRUISE — no 5% boost on
        # the leader, otherwise the gap from the leader to the
        # rest of the chain grows and the chain can't compress.
        try:
            edge_L, lp_L, _, _ = get_state(leader)
            if edge_L and not edge_L.startswith(":"):
                pL_cur = get_p(leader, edge_L, lp_L)
                pL_tgt = leader_p_target(t)
                vL_cmd = max(MIN_SPEED,
                             min(V_CRUISE,
                                 vL_cmd + 0.6 * (pL_tgt - pL_cur)))
        except Exception:
            pass
        set_spd(leader, vL_cmd, traci)

        # ── Build live (p, v) per vehicle.  We use (x, y) directly so
        #    that vehicles on junction internal edges still get a valid
        #    p.  This is important for the merge slot check, which
        #    needs to see v4, v5, … even when they're crossing J1/J2. ──
        cur: dict[str, tuple[float, float, str, float]] = {}
        live_controlled: list[tuple[str, float, float]] = []   # (vid, last_p, last_v)
        for vid in live:
            try:
                x, y = traci.vehicle.getPosition(vid)
            except Exception:
                continue
            e = ""
            try:
                e = traci.vehicle.getRoadID(vid)
            except Exception:
                pass
            p = xy_to_p(x, y)
            if math.isnan(p):
                continue
            v = spd_of(vid, traci)
            cur[vid] = (p, v, e, 0.0)
            live_controlled.append((vid, p, v))

        # ── Re-issue setSpeed for any vehicle that needs it: vehicles
        #    currently on junction internal edges (so SUMO doesn't fall
        #    back to road speed limit) and released leavers continuing
        #    east on e_j2_east. ──
        last_cmd: dict[str, float] = getattr(run_simulation, "_last_cmd", {})
        for vid in live:
            try:
                e = traci.vehicle.getRoadID(vid)
            except Exception:
                continue
            needs_hold = (e.startswith(":") or
                          (vid in split_done_vids and not e.startswith(":")))
            if needs_hold and vid in last_cmd:
                set_spd(vid, last_cmd[vid], traci)

        # ── Platoon CACC: each non-leader follows its immediate predecessor
        #    in the *platoon_order* list (not the nearest in p, which
        #    could be a joiner or a leaver that we're passing). ──
        # Two cases:
        #   (a) pred is AHEAD in p (normal CACC): maintain SPACING.
        #   (b) pred is BEHIND in p (merge transient, the joiner is
        #       closing the gap from behind to slot in): slow down
        #       proportional to (behind_gap + IVD) so the joiner can
        #       catch up and slot in.  This is what makes the back of
        #       the platoon "reduce their speed to maintain a safe
        #       IVD" while the joiner slots in. ──
        # When the self is a freshly-slotted joiner, we override the
        # CACC to V_CRUISE: the joiner needs to make forward progress
        # so the back of the platoon doesn't lock at MIN_SPEED
        # waiting for it. ──
        order_index = {vid: i for i, vid in enumerate(platoon_order)}
        slotted_joiners = {"j0", "j1"} & set(platoon_order)
        for vid in platoon_order:
            if vid == leader or vid not in cur:
                continue
            # Brake-hold: if this vehicle is in a brake_hold window
            # (e.g. v3 right after j0 slotted in front of it), force
            # MIN_SPEED and skip the CACC calculation entirely.
            if vid in brake_hold and brake_hold[vid] > t:
                v_cmd = MIN_SPEED
                last_cmd[vid] = v_cmd
                set_spd(vid, v_cmd, traci)
                continue
            self_p, self_v, _, _ = cur[vid]
            # Find predecessor in platoon_order
            my_idx = order_index[vid]
            pred_vid = None
            for j in range(my_idx - 1, -1, -1):
                cand = platoon_order[j]
                if cand in cur:
                    pred_vid = cand
                    break
            if pred_vid is None:
                continue
            pred_p, pred_v, _, _ = cur[pred_vid]
            # Find successor in platoon_order (for "boost only at the back")
            succ_vid = None
            for j in range(my_idx + 1, len(platoon_order)):
                cand = platoon_order[j]
                if cand in cur:
                    succ_vid = cand
                    break
            succ_p = cur[succ_vid][0] if succ_vid else -1.0
            succ_v = cur[succ_vid][1] if succ_vid else 0.0
            # Slotted joiners: only force V_JOIN_CRUISE if we're comfortably
            # behind our pred (gap >= SPACING).  If we're too close
            # to (or ahead of) our pred, we MUST let the brake logic
            # fire — otherwise we oscillate between V_JOIN_CRUISE
            # and MIN_SPEED and can collide with the pred.
            if vid in slotted_joiners and self_v < V_JOIN_CRUISE and pred_p + SPACING <= self_p:
                v_cmd = V_JOIN_CRUISE
            elif pred_p > self_p + 0.05:
                # (a) Pred ahead: normal CACC, but for the back of
                # the platoon (vehicles following a slotted joiner
                # or a slowed vehicle) we relax the IVD floor so
                # they don't keep cascading down to MIN_SPEED.
                if vid in slotted_joiners or pred_vid in slotted_joiners:
                    # Follow with a more lenient CACC: don't go
                    # below V_MERGE_BACK even if the gap is small
                    # (the joiner is still settling into place).
                    raw = cacc_step(vid, self_p, self_v, pred_p, pred_v, succ_p, succ_v,
                                    traci, leaver=vid in leaver_ids)
                    v_cmd = max(V_MERGE_BACK, raw)
                else:
                    v_cmd = cacc_step(vid, self_p, self_v, pred_p, pred_v, succ_p, succ_v,
                                      traci, leaver=vid in leaver_ids)
            else:
                # (b) Pred behind (or coincident): merge case — slow
                # down so the pred can catch up.  Brake proportional
                # to the behind-gap + IVD so the back of the platoon
                # "reduces speed to maintain a safe IVD from the
                # joiner".  As the pred catches up, the brake
                # releases and we naturally transition back to
                # case (a) once the pred is in front of us. ──
                behind_gap = self_p - pred_p - VEH_LENGTH
                if behind_gap > 0.0:
                    # Pred is behind: mild brake proportional to the
                    # gap, capped so we never go below
                    # V_MERGE_BACK (= 4 m/s) — this is the lowest
                    # speed the back of the platoon will drop to
                    # while the joiner slots in.
                    brake = 0.05 * KP * (behind_gap + PLATOON_IVD)
                    v_cmd = max(V_MERGE_BACK, V_CRUISE - brake)
                else:
                    # Pred is right at or past our rear bumper:
                    # close-quarters merge.  SUMO's car-following
                    # can only brake so fast (MAX_DECEL), so we
                    # force our desired speed to MIN_SPEED so the
                    # controller's command matches the physics.
                    v_cmd = MIN_SPEED
            last_cmd[vid] = v_cmd
            set_spd(vid, v_cmd, traci)

        # ── Joiner CACC (only while not yet slotted in platoon_order) ──
        if joiner_order:
            v3_state = cur.get("v3")
            for k, jid in enumerate(joiner_order):
                if jid not in cur or v3_state is None:
                    continue
                if jid in platoon_order:
                    continue
                j_p, j_v, j_e, j_lp = cur[jid]
                v3_p = v3_state[0]
                if j_e == "e_west_in":
                    last_cmd[jid] = V_JOINER
                    set_spd(jid, V_JOINER, traci)
                    continue
                target_p = v3_p - (k + 1) * SPACING
                v_cmd = cacc_step(jid, j_p, j_v, target_p, V_CRUISE, -1.0, 0.0, traci)
                last_cmd[jid] = v_cmd
                set_spd(jid, v_cmd, traci)

        # Save last_cmd for the next iteration (function attribute)
        run_simulation._last_cmd = last_cmd
        # Also remember the leader's last command
        run_simulation._last_cmd[leader] = vL_cmd

        # ── State transitions ──
        # (Joiners are deployed at startup on e_west_in; no further
        #  deploy needed here.)
        if (state == State.APPROACH
                and cur.get("v0", (-1, 0, "", 0))[0] > SOUTH_LENGTH - 30):
            state = State.MERGE
            print(f"[t={t:.1f}] → MERGE phase (joiners on e_west_in)")

        # ── Slot the joiners into the platoon early: when the
        #    joiner is still on the WEST APPROACH and approaching J1
        #    (within ~SPACING of the intersection).  This gives the
        #    back of the platoon (v4, v5, v6, v7) enough time to
        #    slow down so the joiner can catch up and slot in
        #    seamlessly with a SPACING gap.  We slot j0 first, then
        #    j1 once j0 has made enough progress. ──
        if state == State.MERGE and insert_pending:
            j0_state = cur.get("j0")
            j1_state = cur.get("j1")
            v3_state2 = cur.get("v3")
            v4_state2 = cur.get("v4")
            if v3_state2 and v4_state2:
                v3_p, v4_p = v3_state2[0], v4_state2[0]
                # j0 slot: when j0 is on the west approach or just
                # past J1, and v4 is approaching J1 (so v4 will be
                # the one to brake).  Condition: j0 is "close to
                # entering" the platoon — within SPACING of v3's
                # position, OR on the west approach within 5m of J1.
                if j0_state and "j0" not in platoon_order:
                    j0_p = j0_state[0]
                    j0_e = j0_state[2]
                    # Slot only after v3 has crossed J1 too.  Otherwise
                    # j0 (already past J1 on e_j1_j2) ends up in front
                    # of v3 (still on south leg) and v3 at V_CRUISE
                    # catches j0 from behind.  v3_p > SOUTH_LENGTH
                    # ensures v3 is on e_j1_j2.
                    j0_close = (j0_e == "e_west_in" and j0_p > SOUTH_LENGTH - 5 and
                                v3_p > SOUTH_LENGTH) or \
                                (j0_e != "e_west_in" and j0_p > SOUTH_LENGTH and
                                 v3_p > SOUTH_LENGTH and
                                 abs(v3_p - j0_p) < 2 * SPACING)
                    if j0_close:
                        # j0's p can be ahead of v3 (j0 entered
                        # e_j1_j2 first) or behind (v3 entered
                        # first).  Insert j0 at the correct
                        # position in platoon order so the CACC
                        # chain reflects p-space proximity.
                        if j0_p > v3_p:
                            # j0 geometrically ahead of v3 on
                            # e_j1_j2 — insert j0 before v3.
                            idx = platoon_order.index("v3")
                            platoon_order.insert(idx, "j0")
                        else:
                            # j0 behind v3 (rare) — insert after.
                            idx = platoon_order.index("v3")
                            platoon_order.insert(idx + 1, "j0")
                        # IMMEDIATELY brake the just-slotted joiner so
                        # it doesn't overshoot v3 (which can happen if
                        # the slot fires when j0 is already past v3).
                        set_spd("j0", MIN_SPEED, traci)
                        run_simulation._last_cmd["j0"] = MIN_SPEED
                        print(f"[t={t:.1f}] j0 SLOTTED  v3_p={v3_p:.1f} j0_p={j0_p:.1f} v4_p={v4_p:.1f}  order={platoon_order}")
                # j1 slot: after j0 has progressed past J1.
                if "j0" in platoon_order and j1_state and "j1" not in platoon_order:
                    j0_p = cur["j0"][0]
                    j1_p = j1_state[0]
                    j1_e = j1_state[2]
                    j1_close = (j1_e == "e_west_in" and j1_p > SOUTH_LENGTH - 5 and
                                v3_p > SOUTH_LENGTH) or \
                                (j1_e != "e_west_in" and j1_p > SOUTH_LENGTH and
                                 v3_p > SOUTH_LENGTH and
                                 abs(j0_p - j1_p) < 2 * SPACING)
                    if j1_close:
                        j0_idx = platoon_order.index("j0")
                        platoon_order.insert(j0_idx + 1, "j1")
                        # IMMEDIATELY brake the just-slotted joiner
                        set_spd("j1", MIN_SPEED, traci)
                        run_simulation._last_cmd["j1"] = MIN_SPEED
                        insert_pending = False
                        print(f"[t={t:.1f}] j1 SLOTTED  v3_p={v3_p:.1f} j1_p={j1_p:.1f} j0_p={j0_p:.1f} v4_p={v4_p:.1f}  order={platoon_order}")

        # Once v0 is well past J1 AND joiners are slotted (or joiner
        # insert window has expired), move to SPLIT phase.
        if state == State.MERGE:
            v0_p = cur.get("v0", (-1, 0, "", 0))[0]
            if v0_p > SOUTH_LENGTH + 50 and not insert_pending:
                state = State.SPLIT
                print(f"[t={t:.1f}] SPLIT phase (J2 left turn)")

        # Leavers: as each crosses J2 (p > SOUTH_LENGTH+EAST_LENGTH) we
        # 'release' them from platoon_order so they continue straight.
        if state in (State.SPLIT, State.CRUISE_N):
            for vid in list(leaver_ids):
                if vid in split_done_vids:
                    continue
                p_vid = cur.get(vid, (-1, 0, "", 0))[0]
                if p_vid > SOUTH_LENGTH + EAST_LENGTH + 1.0:
                    if vid in platoon_order:
                        platoon_order.remove(vid)
                    # Set the released leaver's last_cmd to V_CRUISE so
                    # the re-issue loop doesn't hold it at a low CACC value.
                    run_simulation._last_cmd[vid] = V_CRUISE
                    set_spd(vid, V_CRUISE, traci)
                    split_done_vids.add(vid)
                    print(f"[t={t:.1f}] LEAVER {vid} released at J2")

            if cur.get("v0", (-1, 0, "", 0))[0] > SOUTH_LENGTH + EAST_LENGTH + 30:
                state = State.CRUISE_N

        if state == State.CRUISE_N and t > T_SIM - 5:
            state = State.DONE

        traci.simulationStep()
        step += 1

    traci.close()
    print(f"[sumo] done. steps={step}, state={state.name}, platoon={platoon_order}")
    print(f"[sumo] node_id_map: {node_id_map}")


# ── FCD → mob.csv ──────────────────────────────────────────────────────────

def fcd_to_mob_csv() -> None:
    fcd = SCRIPT_DIR / "fcd_output.xml"
    out = SCRIPT_DIR / "platoon_intersection_merge_split.mob.csv"
    if not fcd.exists():
        print(f"[fcd→csv] no {fcd}, skipping")
        return
    print(f"[fcd→csv] parsing {fcd} …")
    tree = ET.parse(fcd)
    root = tree.getroot()

    # Fixed node-id map: v0..v7 → 0..7, j0/j1 → 8/9
    node_id = {f"v{i}": i for i in range(N_PLATOON)}
    node_id.update({"j0": 8, "j1": 9})

    rows = 0
    with open(out, "w", newline="") as f:
        f.write("# time,node_id,x,y,speed\n")
        w = csv.writer(f)
        for ts in root.iter("timestep"):
            t = float(ts.attrib["time"])
            for veh in ts.iter("vehicle"):
                vid = veh.attrib["id"]
                if vid not in node_id:
                    continue
                x = float(veh.attrib["x"])
                y = float(veh.attrib["y"])
                s = float(veh.attrib["speed"])
                w.writerow([f"{t:.3f}", node_id[vid],
                            f"{x:.3f}", f"{y:.3f}", f"{s:.3f}"])
                rows += 1
    print(f"[fcd→csv] wrote {rows} rows → {out}")


if __name__ == "__main__":
    build_network()
    run_simulation()
    fcd_to_mob_csv()
    print("All done.")
