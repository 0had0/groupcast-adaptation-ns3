#!/usr/bin/env python3
"""
build_composite_trace.py
========================
Synthesise a highway → urban-intersection mobility trace for the
RA-APC-Groupcast experiment.

Stage 1 (highway, 0 ≤ t < 30 s):
  9 vehicles in a tight 30-m spaced platoon travel along +x at 25 m/s.
  All inter-vehicle links are near-LOS — feasible MCS is high and uniform.

Stage 2 (intersection turn, 30 ≤ t < 60 s):
  The lead vehicle follows a 90° arc around the corner pivot
  (x_c, y_c) = (1500, 0) with radius R = 25 m, completing the turn in 30 s.
  Followers ride along the same arc with a 30-m spacing delay.

  Two static "blocker" trucks are inserted near the corner (handled in the
  NS-3 binary via the existing geometric-NLOSv blocker model — we just
  output their positions here as node IDs 9 and 10).  These produce NLOSv
  on the tail of the platoon as it crosses the intersection arms.

Output CSV (consumed by the binary's --mobilityTrace):
  time,node_id,x,y,speed
"""

import argparse
import csv
import math
from pathlib import Path


# ── scenario constants ────────────────────────────────────────────────────────
DT             = 0.1     # CSV time step (s)
T_HW_END       = 30.0    # end of highway segment
T_TOTAL        = 60.0    # end of trace
SPEED          = 25.0    # m/s
SPACING        = 30.0    # m between consecutive vehicles (centre-to-centre)
N_VEHICLES     = 9       # node IDs 0..8 — node 0 is the leader/TX
ARC_RADIUS     = 25.0
PIVOT_X        = 1500.0  # corner pivot at end of highway segment
PIVOT_Y        = -ARC_RADIUS  # so the leader enters the arc tangent at y=0
LANE_Y         = 0.0


def leader_position(t):
    """Position of the lead vehicle (node 0) at time t."""
    if t < T_HW_END:
        # Straight highway segment, starts so leader reaches PIVOT_X at T_HW_END.
        x = PIVOT_X - SPEED * (T_HW_END - t)
        return (x, LANE_Y, SPEED)
    # Arc turn: angular speed ω = v / R, sweep 90° in 30 s for v=25 m/s ⇒
    # actually with R=25 and ω·R = v = 25, we sweep ω = 1 rad/s → 90° in
    # π/2 ≈ 1.57 s. To get a 30-s intersection segment, slow down on the arc
    # and add a post-arc straight run along +y.
    arc_omega = 0.05          # rad/s — gentle turn (~30 s for 90°)
    arc_v     = arc_omega * ARC_RADIUS  # ~1.25 m/s — slow crawl at intersection
    arc_duration = (math.pi / 2) / arc_omega  # ≈ 31.4 s
    t_arc = t - T_HW_END
    if t_arc <= arc_duration:
        # Quarter-circle arc from (PIVOT_X, 0) sweeping up to (PIVOT_X+R, R).
        theta = arc_omega * t_arc  # 0 → π/2
        x = PIVOT_X + ARC_RADIUS * math.sin(theta)
        y = LANE_Y + ARC_RADIUS - ARC_RADIUS * math.cos(theta)
        return (x, y, arc_v)
    # Should not be reached for t ≤ 60 s with arc_omega=0.05; safety fallback.
    extra = t_arc - arc_duration
    x = PIVOT_X + ARC_RADIUS
    y = ARC_RADIUS + arc_v * extra
    return (x, y, arc_v)


def follower_position(t, idx):
    """Vehicle idx ∈ [1, N_VEHICLES-1] follows leader along same path,
    SPACING * idx metres behind in arc length.

    Implementation: shift leader's trajectory by a constant time lag
    (lag = SPACING * idx / SPEED), and CONTINUE the highway segment
    backwards in time for followers — i.e. at t_eff < 0 the follower
    is still on the highway, just further back. This avoids clipping
    multiple followers to the same start position.
    """
    lag = SPACING * idx / SPEED  # constant time lag (highway speed)
    t_eff = t - lag
    if t_eff < T_HW_END:
        # Highway extrapolation (valid for t_eff < 0 too).
        x = PIVOT_X - SPEED * (T_HW_END - t_eff)
        return (x, LANE_Y, SPEED)
    # Arc — delegate to the leader function.
    return leader_position(t_eff)


def emit_trace(out_path, blockers=True):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    n_steps = int(round(T_TOTAL / DT)) + 1
    for k in range(n_steps):
        t = k * DT
        for nid in range(N_VEHICLES):
            x, y, v = (leader_position(t) if nid == 0
                       else follower_position(t, nid))
            rows.append((t, nid, x, y, v))
        if blockers:
            # Two static blocker vehicles at the corner. These never move;
            # the NS-3 geometric NLOSv model treats UE positions as blockers
            # (--blockerL / --blockerW set the bounding box).
            rows.append((t, N_VEHICLES,     PIVOT_X + 5.0,  10.0, 0.0))
            rows.append((t, N_VEHICLES + 1, PIVOT_X + 5.0, -10.0, 0.0))

    header_lines = [
        "# Composite highway → intersection mobility trace",
        "# Stage 1 (0–30 s): platoon on +x highway, 30 m spacing, 25 m/s",
        "# Stage 2 (30–60 s): 90° arc turn around (1500, 0), R=25 m, slow crawl",
        "# Node IDs: 0 = leader/TX, 1..8 = followers/RX",
        f"# Blockers: {'enabled (node IDs 9,10 at corner)' if blockers else 'disabled'}",
        "time,node_id,x,y,speed",
    ]
    with open(out_path, "w") as f:
        for line in header_lines[:-1]:
            f.write(line + "\n")
        f.write(header_lines[-1] + "\n")
        w = csv.writer(f)
        for t, nid, x, y, v in rows:
            w.writerow([f"{t:.3f}", nid, f"{x:.4f}", f"{y:.4f}", f"{v:.4f}"])
    print(f"Wrote {len(rows)} rows to {out_path}")


def quick_plot(csv_path, png_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from collections import defaultdict

    pts = defaultdict(list)
    with open(csv_path) as f:
        for line in f:
            if line.startswith("#") or line.startswith("time,"):
                continue
            t, nid, x, y, v = line.strip().split(",")
            pts[int(nid)].append((float(t), float(x), float(y)))

    fig, axs = plt.subplots(1, 2, figsize=(14, 5))
    cmap = plt.cm.get_cmap("tab10", max(pts.keys()) + 1)
    for nid in sorted(pts.keys()):
        xs = [p[1] for p in pts[nid]]
        ys = [p[2] for p in pts[nid]]
        ts = [p[0] for p in pts[nid]]
        marker = "x" if nid >= N_VEHICLES else "o"
        axs[0].plot(xs, ys, color=cmap(nid), lw=0.8, label=f"node {nid}",
                    marker=marker, ms=2, markevery=50)
        axs[1].plot(ts, [math.hypot(x - PIVOT_X, y) for _, x, y in pts[nid]],
                    color=cmap(nid), lw=1, label=f"node {nid}")

    axs[0].axvline(PIVOT_X, color="grey", ls=":", lw=0.5)
    axs[0].set_xlabel("x (m)"); axs[0].set_ylabel("y (m)")
    axs[0].set_title("Vehicle trajectories")
    axs[0].set_aspect("equal", "datalim")
    axs[0].legend(fontsize=7, ncol=2, loc="upper left")
    axs[0].grid(True, alpha=0.3)

    axs[1].axvline(T_HW_END, color="orange", ls="--", lw=0.8, label="intersection entry")
    axs[1].set_xlabel("time (s)"); axs[1].set_ylabel("distance to pivot (m)")
    axs[1].set_title("Distance to intersection over time")
    axs[1].legend(fontsize=7)
    axs[1].grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(png_path, dpi=140, bbox_inches="tight")
    print(f"Wrote sanity plot to {png_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", default=str(Path(__file__).parent
                                            / "traces" / "highway_intersection.mob.csv"))
    ap.add_argument("--no-blockers", action="store_true",
                    help="Omit the two static corner blocker UEs")
    ap.add_argument("--plot", action="store_true",
                    help="Write trace_sanity.png alongside the CSV")
    args = ap.parse_args()

    out = Path(args.output)
    emit_trace(out, blockers=not args.no_blockers)
    if args.plot:
        quick_plot(out, out.with_name("trace_sanity.png"))


if __name__ == "__main__":
    main()
