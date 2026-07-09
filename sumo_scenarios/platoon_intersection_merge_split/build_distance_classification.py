#!/usr/bin/env python3
"""Generate NxN distance + channel-condition (LOS / NLOS / NLOSv) CSV.

For every time-step in the FCD trace, for every ordered pair (i, j) of
nodes we record:

  * `distance_m`   — Euclidean distance between the two vehicles
  * `link`         — "LOS", "NLOS" or "NLOSv"
  * `reason`       — short tag explaining the classification

The classification follows the geometric NLOSv module in
`scratch_overlay/common/channel/three-gpp-geometric-nlosv.{h,cc}`
which decorates an underlying 3GPP channel-condition model with a
deterministic NLOSv flag whenever the straight-line TX→RX path is
blocked by another vehicle's AABB (Liang–Barsky segment-vs-AABB).

The "NLOS" classification is the one we add here: outside the route
edges there are building footprints at the four corners of each
intersection (J1 at (201.6, 200), J2 at (300, 200)).  If the TX→RX
segment passes through any of those footprints the link is NLOS.  In
practice this is exactly the "leader has passed J1 / J2 but the
trailer has not yet reached the intersection" case the user asked
about — the line cuts through the building on the inside corner of
the intersection.

We additionally add a coarse road-segment check: if the two vehicles
are on clearly different road segments whose straight-line connection
crosses a building, force NLOS.  The road-segment check is redundant
with the building AABB test but makes the reason labels more readable.

Output:
  * `distances_classified.csv` — long-format (one row per ordered pair
    per time-step) with the columns above plus `tx`, `rx`, `time`,
    `tx_x`, `tx_y`, `rx_x`, `rx_y`, `tx_v`, `rx_v`.
"""
import math
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
CSV_IN  = HERE / "platoon_intersection_merge_split.mob.csv"
CSV_OUT = HERE / "distances_classified.csv"

# ── Network geometry (mirrors nodes.nod.xml) ────────────────────────────
J1 = (201.6, 200.0)   # south-east of the L
J2 = (300.0, 200.0)   # north-west of the reverse-L

# Building footprints at the 4 inside corners of each intersection.
# These are the rectangular patches a real city-block would occupy on
# the inside corner of the junction.  When the TX→RX segment passes
# through any of these the link is NLOS.  Size 15 m × 15 m is a
# reasonable city-block corner.
BUILDING_HALF = 7.5
BUILDINGS = []
for cx, cy in (J1, J2):
    # cx, cy is the centre of the intersection.  The four building
    # centres are offset by (BUILDING_HALF + ~2 m road half-width) so
    # they sit just outside the intersection footprint.
    OFFSET = BUILDING_HALF + 2.0
    for dx, dy in ((-OFFSET, -OFFSET), (OFFSET, -OFFSET),
                   (-OFFSET,  OFFSET), (OFFSET,  OFFSET)):
        BUILDINGS.append((cx + dx, cy + dy))

# AABB of every building (Liang–Barsky needs half-extents).
def get_building_aabbs():
    return [(cx, cy, BUILDING_HALF, BUILDING_HALF) for cx, cy in BUILDINGS]

# ── Vehicle AABB (used for NLOSv blocker test) ──────────────────────────
VEH_HALF_LEN = 2.25   # 4.5 m / 2
VEH_HALF_WID = 1.0    # 2.0 m / 2

# ── Liang–Barsky segment-vs-AABB (copied verbatim from the C++ module) ──
def segment_intersects_aabb(x1, y1, x2, y2, cx, cy, hl, hw):
    dx = x2 - x1
    dy = y2 - y1
    state = [0.0, 1.0]   # [t_enter, t_exit]

    def clip(p, q):
        if abs(p) < 1e-12:
            return q >= 0.0
        r = q / p
        if p < 0.0:
            state[0] = max(state[0], r)
        else:
            state[1] = min(state[1], r)
        return state[0] <= state[1]

    return (clip(-dx, x1 - (cx - hl)) and
            clip( dx, (cx + hl) - x1) and
            clip(-dy, y1 - (cy - hw)) and
            clip( dy, (cy + hw) - y1) and
            state[0] <= state[1])

# ── Per-vehicle "which road segment is this?" classification ────────────
# Used purely to produce a human-readable `reason` label.
def segment_of(x, y):
    if abs(x - 201.6) < 1.5 and 0.0 <= y <= 200.0:
        return "south_leg"
    if abs(x - 301.6) < 1.5 and 200.0 <= y <= 1000.0:
        return "north_leg"
    if abs(y - 198.4) < 1.5 and 0.0 <= x <= 200.0:
        return "west_approach"
    if abs(y - 198.4) < 1.5 and 200.0 <= x <= 300.0:
        return "east_leg"
    if abs(y - 198.4) < 1.5 and 300.0 <= x <= 500.0:
        return "leaver_east"
    if 200.0 <= x <= 214.0 and 187.0 <= y <= 200.0:
        return "J1_internal"
    if 289.0 <= x <= 315.0 and 195.0 <= y <= 211.0:
        return "J2_internal"
    return "transition"

# ── Core link classifier ────────────────────────────────────────────────
def classify_link(tx_x, tx_y, rx_x, rx_y, blockers, building_aabbs):
    """Return (label, reason) for the TX→RX link."""
    # Self-link is meaningless — distance 0, mark LOS
    if tx_x == rx_x and tx_y == rx_y:
        return "LOS", "self"

    # 1. NLOSv: any *other* vehicle's AABB intersects the segment.
    #    The blocker AABBs are pre-computed once per frame in `blockers`
    #    and exclude the TX/RX pair.
    for (bx, by, bl, bw) in blockers:
        if segment_intersects_aabb(tx_x, tx_y, rx_x, rx_y,
                                   bx, by, bl, bw):
            return "NLOSv", f"blocker@({bx:.1f},{by:.1f})"

    # 2. NLOS: any building AABB intersects the segment.
    for (bx, by, bl, bw) in building_aabbs:
        if segment_intersects_aabb(tx_x, tx_y, rx_x, rx_y,
                                   bx, by, bl, bw):
            seg_tx = segment_of(tx_x, tx_y)
            seg_rx = segment_of(rx_x, rx_y)
            # The common "leader passed J1 / J2 but trailer hasn't"
            # case shows up as a NLOS hit on the J1 or J2 building.
            if "J1" in (seg_tx, seg_rx) or (seg_tx in ("east_leg", "north_leg")
                                            and seg_rx == "south_leg"):
                return "NLOS", f"building@J1 leader-past-J1 trailer-not"
            if "J2" in (seg_tx, seg_rx) or (seg_tx in ("north_leg", "leaver_east")
                                            and seg_rx in ("south_leg", "east_leg")):
                return "NLOS", f"building@J2 leader-past-J2 trailer-not"
            return "NLOS", f"building@({bx:.1f},{by:.1f})"

    return "LOS", "clear"


# ── Main: walk every frame, every ordered pair ──────────────────────────
def main():
    df = pd.read_csv(CSV_IN, sep=",")
    df.columns = [c.strip().lstrip("#").strip() for c in df.columns]
    df["node_id"] = df["node_id"].astype(int)

    building_aabbs = get_building_aabbs()
    rows = []

    for t, g in df.groupby("time"):
        g = g.set_index("node_id")
        ids = sorted(g.index.tolist())
        # Pre-compute every vehicle's AABB (used as potential blockers
        # for every pair in this frame).
        veh_aabbs = {nid: (g.loc[nid, "x"], g.loc[nid, "y"],
                           VEH_HALF_LEN, VEH_HALF_WID)
                     for nid in ids}
        for i in ids:
            ix, iy, iv = g.loc[i, "x"], g.loc[i, "y"], g.loc[i, "speed"]
            for j in ids:
                jx, jy, jv = g.loc[j, "x"], g.loc[j, "y"], g.loc[j, "speed"]
                d = math.hypot(jx - ix, jy - iy)
                # Blockers = all vehicles except i and j themselves.
                blockers = [veh_aabbs[k] for k in ids if k != i and k != j]
                label, reason = classify_link(ix, iy, jx, jy,
                                              blockers, building_aabbs)
                rows.append({
                    "time":       round(float(t), 1),
                    "tx":         int(i),
                    "rx":         int(j),
                    "tx_x":       round(float(ix), 2),
                    "tx_y":       round(float(iy), 2),
                    "tx_v":       round(float(iv), 2),
                    "rx_x":       round(float(jx), 2),
                    "rx_y":       round(float(jy), 2),
                    "rx_v":       round(float(jv), 2),
                    "distance_m": round(d, 2),
                    "link":       label,
                    "reason":     reason,
                })

    out = pd.DataFrame(rows)
    out.to_csv(CSV_OUT, index=False)
    print(f"Wrote {CSV_OUT}  ({len(out):,} rows, {df.time.nunique()} frames × {len(ids)}² pairs)")
    # Per-class counts
    counts = out["link"].value_counts()
    print("Per-class counts:")
    for k, v in counts.items():
        print(f"  {k:6s}: {v:>9,}")
    # Top reasons
    print("\nTop reasons per class:")
    for cls in ("NLOSv", "NLOS", "LOS"):
        sub = out[out.link == cls]
        if len(sub) == 0:
            continue
        print(f"  {cls}:")
        for reason, n in sub.reason.value_counts().head(6).items():
            print(f"    {n:>7,}  {reason}")


if __name__ == "__main__":
    main()
