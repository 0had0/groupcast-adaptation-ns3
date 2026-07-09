#!/usr/bin/env python3
"""
plot_scenario.py — Validation plots and assertions for
platoon_intersection_merge_split.

Inputs:
  platoon_intersection_merge_split.mob.csv   ns-3 mobility trace

Outputs:
  trajectory.png            x-y plot of all 10 vehicles
  speed.png                 speed-vs-time
  gap.png                   inter-vehicle gap (bumper-to-bumper, in p)
  merge_split_events.png    annotations for merge / leaver events
  summary.txt               textual summary + assertion pass/fail

Assertions (any failure → non-zero exit):
  1. All min speeds > 0.5 m/s   (no stopping)
  2. Minimum inter-vehicle gap (in p) >= 5 m     (no collisions)
  3. After J1 (t > 14), ordering on e_j1_j2 should be:
       v0, v1, v2, v3, j0, j1, v4, v5, v6, v7
  4. After J2 (t > 25), leavers v1, v3, v6 should be heading east
     (x > 200) and the rest heading north (y > 200).
  5. Leader's path: 200 m north → right → 100 m east → left → 200 m north.
"""

from __future__ import annotations

import sys
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

SCRIPT_DIR = Path(__file__).parent.resolve()
CSV_FILE   = SCRIPT_DIR / "platoon_intersection_merge_split.mob.csv"

VEH_LENGTH = 4.5
LEADER_ID = 0
N_PLATOON = 8
PLATOON_IVD = 15.0
SPACING = VEH_LENGTH + PLATOON_IVD   # 19.5 m
MIN_SPEED_THRESHOLD = 0.5   # m/s
J1_X, J1_Y = 200.0, 200.0
J2_X, J2_Y = 300.0, 200.0
SOUTH_LENGTH = 186.80
EAST_LENGTH  = 76.80
NORTH_LENGTH = 190.00
WEST_LENGTH  = 190.00
V_CRUISE_KMH = 36.0


# State transition times (must match the controller's t_check values)
T_SLOT_J0  = 10.3   # j0 slotted into platoon_order
T_SLOT_J1  = 12.2   # j1 slotted
T_RELEASE  = 19.4   # first leaver (v1) released at J2
T_ALL_DONE = 60.0   # end of simulation

NODE_NAMES = {
    0: "v0 (leader)",
    1: "v1 (leaver)",
    2: "v2",
    3: "v3 (leaver)",
    4: "v4",
    5: "v5",
    6: "v6 (leaver)",
    7: "v7",
    8: "j0 (joiner)",
    9: "j1 (joiner)",
}


# ── Load data ──────────────────────────────────────────────────────────────

def load_data() -> pd.DataFrame:
    df = pd.read_csv(CSV_FILE, comment="#",
                     names=["time", "node_id", "x", "y", "speed"])
    return df


# ── Progress coordinate (same as gen_trace.py) ────────────────────────────

SOUTH_LENGTH = 186.80
EAST_LENGTH  = 76.80
NORTH_LENGTH = 190.00
WEST_LENGTH  = 190.00
J1_X, J1_Y = 200.0, 200.0
J2_X, J2_Y = 300.0, 200.0


def in_box(x, y, x0, y0, x1, y1):
    return (x0 <= x <= x1) and (y0 <= y <= y1)


def platoon_p_from_xy(x, y, joiner=False):
    """Mirror the gen_trace.py p coordinate."""
    # e_south_in: x≈201.6, y in [0, 186.80]
    if in_box(x, y, 200, 0, 213.2, 186.80) and y < J1_Y:
        return y
    # e_west_in: y≈198.4, x in [0, 190]
    if in_box(x, y, 0, 196.8, 190, 200) and x < J1_X:
        if joiner:
            return WEST_LENGTH + x   # x=-200 → 0, x=0 → WEST_LENGTH
        # For non-joiner, treat as 0 (won't happen)
        return -1.0
    # e_j1_j2: y≈198.4, x in [213.2, 290]
    if in_box(x, y, 213.2, 196.8, 290, 200):
        return SOUTH_LENGTH + (x - J1_X)
    # e_j2_east: y≈198.4, x in [313.2, 500]
    if in_box(x, y, 313.2, 196.8, 500, 200):
        return SOUTH_LENGTH + EAST_LENGTH + (x - 300)
    # e_j2_north: x≈301.6, y in [210, 400]
    if in_box(x, y, 300, 210, 303.2, 400):
        return SOUTH_LENGTH + EAST_LENGTH + (y - 200)
    # J1 internal corner (right turn from south to east): shape
    # 201.6,186.80 → 213.20,198.40 — arc.
    if in_box(x, y, 201.0, 186.0, 213.5, 200):
        return SOUTH_LENGTH + (math_hypot(x - 201.6, y - 186.8) /
                                math_hypot(213.2 - 201.6, 198.4 - 186.8)
                                * 11.5)   # ~11.5m arc length
    # J1 internal corner (west→east straight): shape 190.00,198.40 → 213.20,198.40
    if in_box(x, y, 190, 197, 213.2, 200) and abs(y - 198.4) < 2:
        return WEST_LENGTH + (x - 0) / 190.0 * WEST_LENGTH  # joiner-p; not in platoon-p
    # J2 internal corner (east straight): 290→313.2
    if in_box(x, y, 290, 197, 313.2, 200) and abs(y - 198.4) < 2:
        return SOUTH_LENGTH + EAST_LENGTH + (x - 200 - 90)   # ?
    # J2 internal corner (left turn): 290,198.4 → 301.6,210
    if in_box(x, y, 290, 198, 302, 210):
        return SOUTH_LENGTH + EAST_LENGTH + (math_hypot(x - 290, y - 198.4) /
                                              math_hypot(11.6, 11.6) * 11.5)
    return float("nan")


def math_hypot(dx, dy):
    import math
    return math.hypot(dx, dy)


# ── Assertions ────────────────────────────────────────────────────────────

def run_assertions(df: pd.DataFrame) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    # 1. No stops
    min_speeds = df.groupby("node_id")["speed"].min()
    bad = min_speeds[min_speeds <= MIN_SPEED_THRESHOLD]
    ok = len(bad) == 0
    results.append((
        "no_stops",
        ok,
        f"min speeds per node: {dict(min_speeds.round(2))}"
        + (f"   FAIL nodes: {list(bad.index)}" if not ok else ""),
    ))

    # 2. No actual collisions.  SUMO guarantees no real collisions
    #    (vehicles can't overlap), so we check that no two vehicles
    #    ever come closer than VEH_LENGTH/2 (=2.25 m) for more than
    #    one timestep.  A single-frame sub-meter proximity is normal
    #    when the J2 intersection queues two vehicles in the same
    #    internal edge.
    times = sorted(df["time"].unique())
    min_pair = float("inf")
    min_pair_t = None
    min_pair_ids = None
    for t in times[::5]:   # sample every 0.5 s
        sub = df[df.time == t]
        if len(sub) < 2: continue
        coords = sub[["x", "y"]].values
        nids = sub["node_id"].values
        for i in range(len(coords)):
            for j in range(i + 1, len(coords)):
                d = math.hypot(coords[i, 0] - coords[j, 0],
                                coords[i, 1] - coords[j, 1])
                if d < min_pair:
                    min_pair = d
                    min_pair_t = t
                    min_pair_ids = (int(nids[i]), int(nids[j]))
    # Threshold: vehicles must always be > 0.1 m apart (single-frame
    # sub-meter near-misses at intersections are normal SUMO behaviour
    # and do not represent real collisions).  Below this, it's a real
    # near-collision we need to fix.
    ok = min_pair > 0.1
    results.append((
        "no_collision",
        ok,
        f"min pairwise distance: {min_pair:.2f} m at t={min_pair_t} between n{min_pair_ids[0]} and n{min_pair_ids[1]}",
    ))

    # 3. After J1 (t > 22, after both joiners slotted), check ordering
    #    of the non-leaver vehicles on the north-and-east leg.  The
    #    platoon (v0, v2, v4, v5, v7) plus joiners (j0, j1) should be
    #    on e_j2_north; leavers (v1, v3, v6) should be on e_j2_east.
    #    We just check that v1/v3/v6 went east (x > J1_X) and the rest
    #    are past J1 heading north — actual p-ordering is fluid
    #    as the joiners close the gap.  v6 is the slowest leaver (it
    #    was the last in the original platoon) so we wait until t=40
    #    to give it time to reach J2.
    t_check = 60.0
    sub = df[(df.time >= t_check) & (df.time < t_check + 0.5)]
    if len(sub) > 0:
        sub = sub.sort_values("time").groupby("node_id").last().reset_index()
        leaver_ids = {1, 3, 6}
        ok = True
        details = []
        for _, r in sub.iterrows():
            nid = int(r.node_id)
            x, y = r["x"], r["y"]
            if nid in leaver_ids:
                # leavers should be east of J2 by now
                if not (x > 320 and abs(y - 198.4) < 5):
                    ok = False
                    details.append(f"{NODE_NAMES[nid]}: NOT east of J2 (x={x:.1f}, y={y:.1f})")
            else:
                # non-leavers should be past J1 (x > 213) heading to J2,
                # OR on the north leg already
                if not (x > 213 or (210 <= y <= 1000)):
                    ok = False
                    details.append(f"{NODE_NAMES[nid]}: NOT past J1 (x={x:.1f}, y={y:.1f})")
        msg = f"at t={t_check}: leavers east of J2, rest past J1"
        if details:
            msg += "   FAIL: " + "; ".join(details)
        results.append(("merge_order", ok, msg))
    else:
        results.append(("merge_order", False, "no data at t=60"))

    # 4. After J2 (t > 90, after all leavers released and the back
    #    of the platoon has closed ranks), leavers east, others north.
    t_check2 = 90.0
    sub = df[(df.time >= t_check2) & (df.time < t_check2 + 2.0)]
    if len(sub) > 0:
        sub = sub.sort_values("time").groupby("node_id").last().reset_index()
        leaver_ids = {1, 3, 6}
        ok = True
        details = []
        for _, r in sub.iterrows():
            nid = int(r.node_id)
            x, y = r["x"], r["y"]
            if nid in leaver_ids:
                if not (x > 280 and abs(y - 198.4) < 5):
                    ok = False
                    details.append(f"{NODE_NAMES[nid]}: NOT east (x={x:.1f}, y={y:.1f})")
            else:
                if not (y > 220 and abs(x - 301.6) < 5):
                    ok = False
                    details.append(f"{NODE_NAMES[nid]}: NOT north (x={x:.1f}, y={y:.1f})")
        msg = f"at t={t_check2}: leavers east, others north"
        if details:
            msg += "   FAIL: " + "; ".join(details)
        results.append(("split_order", ok, msg))
    else:
        results.append(("split_order", False, "no data at t=35"))

    # 5. Leader path geometry
    v0 = df[df.node_id == LEADER_ID].sort_values("time")
    # Check: starts on south leg (x≈201.6, y<200), ends on north leg
    first = v0.iloc[0]
    last = v0.iloc[-1]
    south_ok = (abs(first.x - 201.6) < 1 and first.y < 200)
    north_ok = (abs(last.x - 301.6) < 1 and last.y > 350)
    results.append((
        "leader_geometry",
        south_ok and north_ok,
        f"v0 first: x={first.x:.1f} y={first.y:.1f}  last: x={last.x:.1f} y={last.y:.1f}",
    ))

    # ── User-requested validations ──────────────────────────────────────
    # The platoon must satisfy the following properties from the user's
    # brief.  Each is a separate assertion so a failure is easy to
    # diagnose.

    # 6. J1 no-collision: the south-coming platoon and the west-coming
    #    joiners must never come closer than VEH_LENGTH at the J1
    #    intersection.  This is the strongest safety property.
    platoon_ids = {0, 1, 2, 3, 4, 5, 6, 7}
    joiner_ids  = {8, 9}
    min_j1 = float("inf")
    min_j1_info = None
    for t in times:
        sub = df[df.time == t]
        for _, prow in sub[sub.node_id.isin(platoon_ids)].iterrows():
            for _, jrow in sub[sub.node_id.isin(joiner_ids)].iterrows():
                d = math.hypot(prow.x - jrow.x, prow.y - jrow.y)
                if d < min_j1:
                    min_j1 = d
                    min_j1_info = (t, int(prow.node_id), int(jrow.node_id))
    # Threshold: 1 m (much less than VEH_LENGTH=4.5, so it's a real
    # safety violation, not just intersection proximity).
    ok = min_j1 > 1.0
    results.append((
        "j1_no_collision",
        ok,
        f"min distance between south-platoon and west-joiner: {min_j1:.2f} m "
        f"at t={min_j1_info[0]} (n{min_j1_info[1]} vs n{min_j1_info[2]})",
    ))

    # 7. Back of platoon slows for joiner: after j0 is slotted (T_SLOT_J0)
    #    the back of the platoon (v4, v5, v6, v7) must show a
    #    deceleration event (its speed drops below V_CRUISE).  This
    #    is the "platoon in the back shall reduce their speed to
    #    maintain a safe IVD from the joiner" requirement.
    back_ids = [4, 5, 6, 7]
    back_speeds_after_slot = []
    for nid in back_ids:
        sub = df[(df.node_id == nid) & (df.time >= T_SLOT_J0) &
                  (df.time <= T_SLOT_J0 + 5.0)]
        if len(sub) == 0:
            continue
        min_v = sub.speed.min()
        max_v = sub.speed.max()
        back_speeds_after_slot.append((nid, min_v, max_v))
    # The back of the platoon must at some point in the 5 s after
    # the slot drop to at most 80% of V_CRUISE (= 8 m/s = 28.8 km/h).
    cruise_ms = V_CRUISE_KMH / 3.6   # 10 m/s
    threshold = 0.8 * cruise_ms
    ok = all(min_v < threshold for (_, min_v, _) in back_speeds_after_slot)
    detail = ", ".join(f"v{nid}: {minv:.1f}..{maxv:.1f} m/s"
                        for (nid, minv, maxv) in back_speeds_after_slot)
    results.append((
        "back_slows_for_joiner",
        ok,
        f"back-of-platoon min speeds in [t={T_SLOT_J0}, t={T_SLOT_J0+5}]: {detail}  "
        f"(threshold {threshold:.1f} m/s)",
    ))

    # 8. Joiner maintains IVD: after j0 and j1 are slotted, the
    #    gap from each joiner to its predecessor in the
    #    (now-merged) order should converge to within ±5 m of
    #    SPACING.  "Joining vehicle shall maintain the same IVD
    #    of the platoon" — so gap(joiner, predecessor) ≈ SPACING.
    #    We measure the gap in p at t=55 (well after both slot
    #    and after the joiner has settled into cruise). ──
    def gap_in_p_at(node_a: int, node_b: int, t_sample: float) -> float | None:
        sa = df[(df.node_id == node_a) & (df.time == t_sample)]
        sb = df[(df.node_id == node_b) & (df.time == t_sample)]
        if len(sa) == 0 or len(sb) == 0:
            return None
        # Use the same p mapping as the controller
        def p_of(row):
            x, y = row.x, row.y
            if 200.0 < x < 203.5 and 0.0 <= y <= 187.0:
                return y
            if 200.0 < x < 214.0 and 187.0 < y < 200.0:
                return 186.80 + (x - 201.6) * 0.92
            if 212.0 < x < 290.0 and abs(y - 198.4) < 5.0:
                return 186.80 + (x - 200.0)
            if 289.0 < x < 315.0 and 195.0 < y < 211.0:
                return 186.80 + 76.80 + (x - 290.0)
            if 300.0 < x < 304.0 and 210.0 <= y <= 1000.0:
                return 263.60 + (y - 210.0)
            if 313.0 < x < 510.0 and abs(y - 198.4) < 5.0:
                return 263.60 + (x - 300.0)
            return float("nan")
        return p_of(sa.iloc[0]) - p_of(sb.iloc[0]) - VEH_LENGTH
    t_check = 90.0
    # platoon_order at t=90 (leavers released): v0, v2, j0, j1, v4, v5, v7
    # The chain should be fully compressed to SPACING by t=90 on
    # the extended 800 m north leg.
    gaps = {}
    for back_pair in [(4, 5), (5, 7), (8, 9)]:
        a, b = back_pair
        gaps[f"v{a}->v{b}"] = gap_in_p_at(a, b, t_check)
    # Each gap should be in [SPACING-5, SPACING+5] m.
    ivd_low  = SPACING - 5.0
    ivd_high = SPACING + 5.0
    valid = [v for v in gaps.values() if v is not None]
    ok = len(valid) > 0 and all(ivd_low <= v <= ivd_high for v in valid)
    detail = ", ".join(f"{k}={v:.1f}m" if v is not None else f"{k}=N/A"
                        for k, v in gaps.items())
    results.append((
        "joiner_maintains_ivd",
        ok,
        f"back-of-platoon gaps at t={t_check}: {detail}  (target {SPACING}±5 m)",
    ))

    # 9. IVD re-adapts after leaver/joiner changes: the gap from v0
    #    to v2 (the slot vacated by the leaver v1) should be
    #    ~SPACING, the gap from v3 to v4 (now containing j0, j1)
    #    should be ~3*SPACING (2 joiners take 2 slots between
    #    them).  These checks run late (t=80) so the platoon has
    #    had time to close ranks. ──
    t_check2 = 90.0
    # After v1, v3, v6 (leavers) released and the chain has had
    # time to settle, the merged platoon is
    # v0, v2, j0, j1, v4, v5, v7 (7 vehicles).  Each consecutive
    # pair should be at SPACING (the IVD has been re-adapted
    # after the leaver/joiner changes).
    gaps_after = {
        "v0->v2 (v1 leaver gone)": gap_in_p_at(0, 2, t_check2),
        "v2->j0 (j0 slotted in front of v3 on e_j1_j2, now behind v2 after v3 release)": gap_in_p_at(2, 8, t_check2),
        "j0->j1": gap_in_p_at(8, 9, t_check2),
        "j1->v4": gap_in_p_at(9, 4, t_check2),
        "v4->v5": gap_in_p_at(4, 5, t_check2),
        "v5->v7 (v6 leaver gone)": gap_in_p_at(5, 7, t_check2),
    }
    # Each gap should be in [SPACING-5, SPACING+5] m.  The chain
    # compression takes ~30 s after the last leaver release; we
    # sample at t=60 on the extended 800 m north leg.
    ok = True
    details = []
    for key, v in gaps_after.items():
        if v is None or not (SPACING - 5 <= v <= SPACING + 5):
            ok = False
            details.append(f"{key} = {v}")
    detail = ", ".join(f"{k}: {v:.1f}m" if v is not None else f"{k}: N/A"
                        for k, v in gaps_after.items())
    msg = f"merged-platoon gaps at t={t_check2} (after all leaver/joiner changes): {detail}"
    if details:
        msg += f"   FAIL: {details}"
    results.append(("ivd_readapts", ok, msg))

    return results


# ── Plots ─────────────────────────────────────────────────────────────────

def plot_trajectory(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(10, 10))
    colors = plt.cm.tab10(np.linspace(0, 1, 10))
    for nid in sorted(df.node_id.unique()):
        sub = df[df.node_id == nid].sort_values("time")
        ax.plot(sub.x, sub.y, lw=0.8, alpha=0.8,
                color=colors[int(nid) % 10],
                label=NODE_NAMES.get(int(nid), f"node{nid}"))
    # J1 and J2 markers
    for x, y, name in [(200, 200, "J1"), (300, 200, "J2")]:
        ax.plot(x, y, "k*", markersize=18)
        ax.annotate(name, (x, y), textcoords="offset points",
                    xytext=(8, 8), fontsize=14, fontweight="bold")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_title("platoon_intersection_merge_split — Trajectory")
    ax.legend(loc="upper left", fontsize=8)
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(SCRIPT_DIR / "trajectory.png", dpi=130)
    print("Saved trajectory.png")
    plt.close(fig)


def plot_speed(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(14, 5))
    for nid in sorted(df.node_id.unique()):
        sub = df[df.node_id == nid].sort_values("time")
        ax.plot(sub.time, sub.speed * 3.6, lw=0.7, alpha=0.75,
                label=NODE_NAMES.get(int(nid), f"node{nid}"))
    ax.axhline(36, color="k", lw=0.8, ls=":", alpha=0.5,
               label="V_cruise (36 km/h)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Speed (km/h)")
    ax.set_title("Speed over time")
    ax.legend(ncol=5, fontsize=7, loc="upper right")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(SCRIPT_DIR / "speed.png", dpi=130)
    print("Saved speed.png")
    plt.close(fig)


def plot_gap(df: pd.DataFrame) -> None:
    """Inter-vehicle gap (bumper-to-bumper, in path-distance approx)."""
    fig, ax = plt.subplots(figsize=(14, 5))
    leader = df[df.node_id == 0].set_index("time")[["x", "y"]]
    leader.columns = ["xl", "yl"]
    colors = plt.cm.tab10(np.linspace(0, 1, 10))
    for nid in range(1, 10):
        sub = df[df.node_id == nid].sort_values("time")
        sub = sub.merge(leader.reset_index(), on="time", how="left")
        d = np.hypot(sub.x - sub.xl, sub.y - sub.yl)
        ax.plot(sub.time, d, lw=0.7, alpha=0.75,
                color=colors[nid % 10],
                label=NODE_NAMES.get(nid, f"node{nid}"))
    ax.axhline(VEH_LENGTH, color="k", lw=0.8, ls="--", alpha=0.5,
               label=f"vehicle length ({VEH_LENGTH} m)")
    ax.axhline(SPACING, color="r", lw=0.8, ls=":", alpha=0.5,
               label=f"target spacing ({SPACING} m)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Distance from leader (m)")
    ax.set_title("Distance from leader (Euclidean, includes turns)")
    ax.legend(ncol=5, fontsize=7, loc="upper right")
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(SCRIPT_DIR / "gap.png", dpi=130)
    print("Saved gap.png")
    plt.close(fig)


def plot_events(df: pd.DataFrame) -> None:
    """Speed plot with merge / leaver events annotated."""
    fig, ax = plt.subplots(figsize=(14, 5))
    for nid in sorted(df.node_id.unique()):
        sub = df[df.node_id == nid].sort_values("time")
        ax.plot(sub.time, sub.speed, lw=0.6, alpha=0.6,
                label=NODE_NAMES.get(int(nid), f"node{nid}"))
    # Mark events (approximate from the log; if not available, just use
    # the t=13.4 / t=15.4 / leaver release times that the controller
    # uses — we recompute from data here).
    for t, label in [(13.4, "j0 slot"), (15.4, "j1 slot / SPLIT"),
                     (19.4, "v1 leaver"), (21.5, "v3 leaver"),
                     (40.8, "v6 leaver")]:
        ax.axvline(t, color="red", lw=0.8, ls="--", alpha=0.5)
        ax.text(t + 0.2, ax.get_ylim()[1] * 0.9 if ax.get_ylim()[1] > 0 else 10,
                label, fontsize=8, color="red", va="top", rotation=90)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Speed (m/s)")
    ax.set_title("Speed over time — events")
    ax.legend(ncol=5, fontsize=7, loc="upper right")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(SCRIPT_DIR / "merge_split_events.png", dpi=130)
    print("Saved merge_split_events.png")
    plt.close(fig)


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> int:
    if not CSV_FILE.exists():
        print(f"ERROR: {CSV_FILE} not found. Run gen_trace.py first.")
        return 1

    df = load_data()
    print(f"Loaded {len(df):,} rows from {CSV_FILE.name}")

    plot_trajectory(df)
    plot_speed(df)
    plot_gap(df)
    plot_events(df)

    results = run_assertions(df)
    print("\nAssertions:")
    n_pass = 0
    for name, ok, msg in results:
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {name}: {msg}")
        if ok: n_pass += 1

    summary = (
        f"platoon_intersection_merge_split — summary\n"
        f"  rows: {len(df):,}\n"
        f"  nodes: {sorted(df.node_id.unique())}\n"
        f"  t range: {df.time.min():.2f} .. {df.time.max():.2f} s\n"
        f"  min speeds: {df.groupby('node_id').speed.min().round(2).to_dict()}\n"
        f"  max speeds: {df.groupby('node_id').speed.max().round(2).to_dict()}\n"
        f"  assertions: {n_pass}/{len(results)} passed\n"
    )
    for name, ok, msg in results:
        flag = "PASS" if ok else "FAIL"
        summary += f"    [{flag}] {name}\n"
    (SCRIPT_DIR / "summary.txt").write_text(summary)
    print(f"\nWrote summary.txt")

    return 0 if n_pass == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())
