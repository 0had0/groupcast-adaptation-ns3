#!/usr/bin/env python3
"""
nlosv_trajectory_animation.py
==============================
Generates per-frame PNG images for ffmpeg video assembly.

Each frame shows a 9-vehicle platoon driving an L-shaped intersection route
(horizontal approach → corner turn → vertical climb → second turn → horizontal
departure).  Four NLOSv-condition models are rendered as parallel "swim-lanes"
alongside each vehicle, colour-coded by that vehicle's NLOSv link percentage.

Models
------
  1. Geometric   — position-exact Liang-Barsky AABB ground truth
  2. V2V Urban   — 3GPP density-based probabilistic model, MEDIUM tier
  3. V2V Highway — 3GPP density-based probabilistic model, MEDIUM tier
  4. Mix         — Urban at the intersection corner, Highway on straight runs

Lane geometry
-------------
Each model lane is offset **perpendicularly** to the local direction of travel
(computed via finite-difference tangent vectors) so lanes stay parallel and
non-crossing even through the 90° turn.

Usage
-----
    python3 nlosv_trajectory_animation.py [OPTIONS]
    ffmpeg -r 20 -i frames_nlosv/frame_%04d.png \\
           -c:v libx264 -pix_fmt yuv420p nlosv.mp4

Options
-------
  --trace    PATH   path to intersection_nlosv.mob.csv  (auto-detected if omitted)
  --out-dir  DIR    output directory for PNGs            (default: ./frames_nlosv)
  --stride   N      use every N-th timestep  (default 5 → 0.5 s / frame)
  --dpi      N      PNG resolution                        (default 120)
"""

# ── Standard library ──────────────────────────────────────────────────────────
import argparse
import os
import sys
import time
from pathlib import Path

# ── Third-party ───────────────────────────────────────────────────────────────
import matplotlib
matplotlib.use("Agg")                       # headless rendering
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
import numpy as np
import pandas as pd
import seaborn as sns

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION  — swap these values to retune without touching logic
# ═══════════════════════════════════════════════════════════════════════════════

VEHICLE_HL   = 2.5    # vehicle half-length [m]  (for blocker AABB)
VEHICLE_HW   = 1.25   # vehicle half-width  [m]

LANE_SPACING = 20.0   # perpendicular gap between adjacent model lanes [m]
FIRST_LANE   = 1      # which normal-unit to start at (≥1 to clear the vehicle body)

# Model names — all share ONE colormap so intensities are directly comparable.
# Lanes are told apart only by text labels at their leading end, not by color.
MODELS = ["Geometric", "V2V Urban", "V2V Highway", "Mix"]
CMAP   = "RdYlGn_r"   # red = 100 % NLOSv, green = 0 % — intuitive & perceptually uniform

LEADER_FC   = "#1565c0"   # blue  — leading vehicle
FOLLOWER_FC = "#b71c1c"   # red   — follower vehicles

# Intersection corridor bounding box (vertical segment of the L-route)
INT_X_LO, INT_X_HI = 370, 440
INT_Y_LO, INT_Y_HI = -20, 320

# Seaborn / figure aesthetics
FIGURE_SIZE   = (22, 9)   # wide landscape for the 5:1 trajectory aspect ratio
DOT_SIZE      = 130       # scatter-point marker size
TRAJ_ALPHA    = 0.35      # background trajectory line alpha
GRID_ALPHA    = 0.25
LABEL_TANGENT_PUSH = 2.0  # tangent-units added per lane-index step to stagger labels

# ═══════════════════════════════════════════════════════════════════════════════
# PROBABILITY FORMULAS  ── mirrors ClosedFormProbs() in nlosv_model_eval.cc
# ═══════════════════════════════════════════════════════════════════════════════

def _c(v: float) -> float:
    """Clamp v to [0, 1]."""
    return float(np.clip(v, 0.0, 1.0))


def pnlosv_cf(scenario: str, density: str, d: float) -> float:
    """
    Effective P(NLOSv) bucket probability for a single link of length d [m].

    Reproduces the draw logic of ThreeGppChannelConditionModel exactly:
    the NLOS bucket probability is computed first, then NLOSv fills the
    remainder to 1 − P(LOS) − P(NLOS).

    Parameters
    ----------
    scenario : "urban" | "highway"
    density  : "LOW" | "MEDIUM" | "HIGH"
    d        : 2-D TX–RX distance [m]  (clamped to ≥ 1 m internally)
    """
    if d < 1.0:
        d = 1.0

    if scenario == "urban":
        if density == "LOW":
            p_los     = _c(0.8548 * np.exp(-0.0064 * d))
            p_nlosv_r = _c((1 / (0.0396 * d)) *
                           np.exp(-((np.log(d) - 5.2718) ** 2) / 3.4827))
        elif density == "MEDIUM":
            p_los     = _c(0.8372 * np.exp(-0.0114 * d))
            p_nlosv_r = _c((1 / (0.0312 * d)) *
                           np.exp(-((np.log(d) - 5.0063) ** 2) / 2.4544))
        else:  # HIGH
            p_los     = _c(0.8962 * np.exp(-0.017  * d))
            p_nlosv_r = _c((1 / (0.0242 * d)) *
                           np.exp(-((np.log(d) - 5.0115) ** 2) / 2.2092))
        # model returns 1 − pLOS − pNLOSv_raw as the NLOS probability (unclamped)
        p_nlos_model = 1.0 - p_los - p_nlosv_r

    else:  # "highway"
        if density == "LOW":
            aL, bL, cL = 1.5e-6, -0.0015, 1.0
            aN, bN, cN = -2.9e-7, 0.00059, 0.0017
        elif density == "MEDIUM":
            aL, bL, cL = 2.7e-6, -0.0025, 1.0
            aN, bN, cN = -3.7e-7, 0.00061, 0.015
        else:  # HIGH
            aL, bL, cL = 3.2e-6, -0.003,  1.0
            aN, bN, cN = -4.1e-7, 0.00067, 0.0
        p_los        = _c(aL * d * d + bL * d + cL)
        p_nlos_model = _c(aN * d * d + bN * d + cN)

    # Effective bucket widths (bucket logic from ComputeChannelCondition)
    p_nlos_eff  = max(0.0, min(p_nlos_model, 1.0 - p_los))
    p_nlosv_eff = max(0.0, 1.0 - p_los - p_nlos_eff)
    return p_nlosv_eff


# ═══════════════════════════════════════════════════════════════════════════════
# GEOMETRIC NLOSv  ── Liang–Barsky segment ↔ axis-aligned bounding-box test
# ═══════════════════════════════════════════════════════════════════════════════

def _seg_hits_aabb(tx: np.ndarray, rx: np.ndarray,
                   cx: float, cy: float) -> bool:
    """True if the segment tx → rx intersects the AABB centred at (cx, cy)."""
    dx, dy = rx[0] - tx[0], rx[1] - tx[1]
    x0, y0 = tx
    t0, t1 = 0.0, 1.0
    for p, q in [
        (-dx, x0 - (cx - VEHICLE_HL)), (dx, (cx + VEHICLE_HL) - x0),
        (-dy, y0 - (cy - VEHICLE_HW)), (dy, (cy + VEHICLE_HW) - y0),
    ]:
        if p == 0.0:
            if q < 0:
                return False        # segment parallel and outside slab
        elif p < 0:
            t0 = max(t0, q / p)
        else:
            t1 = min(t1, q / p)
    return t0 <= t1


def geo_nlosv(ti: np.ndarray, rj: np.ndarray,
              blocker_positions: list) -> bool:
    """True if any blocker vehicle's AABB intersects the ti → rj segment."""
    return any(_seg_hits_aabb(ti, rj, bx, by) for bx, by in blocker_positions)


def _in_corner(mx: float, my: float) -> bool:
    """True if a link midpoint falls in the intersection corridor."""
    return (INT_X_LO <= mx <= INT_X_HI) and (INT_Y_LO <= my <= INT_Y_HI)


# ═══════════════════════════════════════════════════════════════════════════════
# TRACE LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_trace(path: str) -> pd.DataFrame:
    """Read intersection_nlosv.mob.csv; return sorted DataFrame."""
    df = pd.read_csv(path, comment="#")
    df["time"] = df["time"].round(3)
    return df.sort_values(["time", "node_id"]).reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════════════
# NORMAL-VECTOR COMPUTATION  (perpendicular to local direction of travel)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_normals(df: pd.DataFrame) -> pd.DataFrame:
    """
    For every (time, node_id) entry compute the unit normal vector that points
    **to the left** of the vehicle's instantaneous direction of travel.

    Algorithm
    ---------
    1. For each node, sort positions by time.
    2. Compute the tangent with central finite differences
       (forward difference at t=0, backward at t=T).
    3. Rotate tangent 90° counter-clockwise → left-perpendicular normal.

    This guarantees that lane offsets track the vehicle through turns without
    crossing each other, because the normal is always locally perpendicular.
    """
    rows = []
    for nid in sorted(df["node_id"].unique()):
        nd  = df[df["node_id"] == nid].sort_values("time")
        x   = nd["x"].to_numpy()
        y   = nd["y"].to_numpy()
        n   = len(x)

        # ── finite-difference tangent ──────────────────────────────────────
        dx = np.empty(n)
        dy = np.empty(n)
        dx[1:-1] = x[2:] - x[:-2]          # central differences
        dy[1:-1] = y[2:] - y[:-2]
        dx[0]    = x[1]  - x[0]            # forward  at left  boundary
        dy[0]    = y[1]  - y[0]
        dx[-1]   = x[-1] - x[-2]           # backward at right boundary
        dy[-1]   = y[-1] - y[-2]

        mag = np.hypot(dx, dy)
        mag[mag < 1e-9] = 1e-9             # guard against division by zero

        # ── 90° CCW rotation of unit tangent → left-normal ────────────────
        # tangent = (dx/mag, dy/mag)  →  left-normal = (-dy/mag, dx/mag)
        nx = -dy / mag
        ny =  dx / mag

        for k, t in enumerate(nd["time"].to_numpy()):
            rows.append({"time": t, "node_id": int(nid),
                         "nx": float(nx[k]), "ny": float(ny[k])})
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# NLOSv FRACTION PRE-COMPUTATION  (0–100 % per vehicle, model, frame)
# ═══════════════════════════════════════════════════════════════════════════════

def precompute(df: pd.DataFrame, frame_times: list) -> dict:
    """
    Compute the NLOSv link-percentage for every (frame_time, node, model).

    For a vehicle i at time t, the percentage is:
        100 × (# links to other vehicles that are NLOSv) / (# other vehicles)

    Returns
    -------
    pct_data : dict  {model_name → pd.DataFrame[time, node_id, pct]}
    """
    n_nodes = df["node_id"].nunique()
    records = {m: [] for m in MODELS}

    for t in frame_times:
        sub = df[df["time"] == t].set_index("node_id")
        ids = sorted(sub.index.tolist())
        if len(ids) < 2:
            continue
        pos       = {i: (float(sub.loc[i, "x"]), float(sub.loc[i, "y"])) for i in ids}
        n_others  = len(ids) - 1

        for i in ids:
            ti = np.array(pos[i])
            geo_c = urb_s = hwy_s = mix_s = 0.0

            for j in ids:
                if j == i:
                    continue
                rj  = np.array(pos[j])
                d   = float(np.linalg.norm(ti - rj))
                blk = [pos[k] for k in ids if k != i and k != j]

                # Geometric: hard NLOSv via Liang–Barsky
                geo_c += float(geo_nlosv(ti, rj, blk))

                # Probabilistic: closed-form P(NLOSv)
                urb_s += pnlosv_cf("urban",   "MEDIUM", d)
                hwy_s += pnlosv_cf("highway", "MEDIUM", d)

                # Mix: pick model by link midpoint location
                mx = 0.5 * (ti[0] + rj[0])
                my = 0.5 * (ti[1] + rj[1])
                mix_s += (pnlosv_cf("urban", "MEDIUM", d) if _in_corner(mx, my)
                          else pnlosv_cf("highway", "MEDIUM", d))

            for model, val in zip(MODELS, [geo_c, urb_s, hwy_s, mix_s]):
                records[model].append({
                    "time":    t,
                    "node_id": i,
                    "pct":     100.0 * val / n_others,   # → 0–100 %
                })

    return {m: pd.DataFrame(records[m]) for m in MODELS}


# ═══════════════════════════════════════════════════════════════════════════════
# VEHICLE RECTANGLE DRAWING
# ═══════════════════════════════════════════════════════════════════════════════

def draw_vehicle_rect(ax: plt.Axes,
                      x: float, y: float,
                      tangent_x: float, tangent_y: float,
                      is_leader: bool) -> None:
    """
    Draw a filled rectangle representing a vehicle centred at (x, y),
    oriented along (tangent_x, tangent_y).

    The rectangle is built from its four corners in the vehicle's local frame
    and then rotated by the heading angle — this avoids any matplotlib
    transform gymnastics.
    """
    angle = np.arctan2(tangent_y, tangent_x)
    c, s  = np.cos(angle), np.sin(angle)
    R     = np.array([[c, -s], [s, c]])         # 2-D rotation matrix

    # Corners in local frame: ±half-length × ±half-width
    corners_local = np.array([
        [-VEHICLE_HL, -VEHICLE_HW],
        [ VEHICLE_HL, -VEHICLE_HW],
        [ VEHICLE_HL,  VEHICLE_HW],
        [-VEHICLE_HL,  VEHICLE_HW],
    ])
    corners_world = (R @ corners_local.T).T + np.array([x, y])

    fc    = LEADER_FC if is_leader else FOLLOWER_FC
    patch = mpatches.Polygon(corners_world, closed=True,
                             facecolor=fc, edgecolor="white",
                             linewidth=0.9, zorder=6, alpha=0.93)
    ax.add_patch(patch)


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLE-FRAME RENDERER
# ═══════════════════════════════════════════════════════════════════════════════

_NORM = Normalize(vmin=0, vmax=100)     # shared intensity scale for all models


def render_frame(ax: plt.Axes,
                 df_trace: pd.DataFrame,
                 pct_data: dict,
                 normals:   pd.DataFrame,
                 x_full: dict, y_full: dict,
                 t: float, frame_idx: int) -> None:
    """
    Clear *ax* and draw one complete animation frame at simulation time *t*.

    Steps
    -----
    1. Full trajectory background (gray/black path lines).
    2. Parallel model lanes: for each model × vehicle, compute the offset
       position using the perpendicular normal and scatter-plot the dot,
       coloured by that vehicle's NLOSv % under the model.
    3. Rotated vehicle rectangles at current positions.
    4. Formatting: axis labels, title, grid, legend.
    """
    ax.cla()

    ids     = sorted(x_full.keys())
    sub     = df_trace[df_trace["time"] == t].set_index("node_id")
    n_sub   = normals[normals["time"] == t].set_index("node_id")

    # ── 1. Trajectory background ──────────────────────────────────────────────
    for nid in ids:
        lw  = 2.0 if nid == 0 else 0.8
        col = "#1a1a1a" if nid == 0 else "#9e9e9e"
        ax.plot(x_full[nid], y_full[nid],
                color=col, linewidth=lw, alpha=TRAJ_ALPHA,
                solid_capstyle="round", zorder=1)

    # Highlight the already-travelled portion of the leader path (up to now)
    lead_df = df_trace[(df_trace["node_id"] == 0) &
                       (df_trace["time"] <= t)].sort_values("time")
    if len(lead_df) > 1:
        ax.plot(lead_df["x"], lead_df["y"],
                color="#1a1a1a", linewidth=2.2, alpha=0.65, zorder=2)

    # Intersection corridor shading
    ax.add_patch(mpatches.Rectangle(
        (INT_X_LO, INT_Y_LO),
        INT_X_HI - INT_X_LO,
        INT_Y_HI - INT_Y_LO,
        facecolor="#ef5350", alpha=0.07, edgecolor="#ef5350",
        linewidth=0.6, zorder=0))
    ax.text(INT_X_LO + 3, INT_Y_HI + 6,
            "intersection\ncorridor", fontsize=7, color="#c62828",
            va="bottom", zorder=7)

    # ── 2. Parallel model lanes ───────────────────────────────────────────────
    # All four lanes share the SAME colormap (CMAP) and the SAME norm (0–100 %).
    # The only visual distinction between lanes is the text label placed at the
    # leading end of each lane (just ahead of the leader vehicle in that lane).
    # This lets the viewer compare intensities directly across models.

    # Pre-fetch leader (N0) normal so we can compute label positions later
    leader_nx = float(n_sub.loc[0, "nx"]) if 0 in n_sub.index else 0.0
    leader_ny = float(n_sub.loc[0, "ny"]) if 0 in n_sub.index else 1.0
    leader_tx =  leader_ny   # tangent = 90° CW of left-normal
    leader_ty = -leader_nx

    for lane_k, model in enumerate(MODELS, start=FIRST_LANE):
        pct_at_t = pct_data[model][pct_data[model]["time"] == t].set_index("node_id")

        xs, ys, cs = [], [], []

        for nid in ids:
            if nid not in sub.index or nid not in n_sub.index:
                continue
            if nid not in pct_at_t.index:
                continue

            xi  = float(sub.loc[nid, "x"])
            yi  = float(sub.loc[nid, "y"])
            nx  = float(n_sub.loc[nid, "nx"])
            ny  = float(n_sub.loc[nid, "ny"])
            pct = float(pct_at_t.loc[nid, "pct"])

            # Perpendicular offset: lane_k × LANE_SPACING along the left-normal
            xs.append(xi + lane_k * LANE_SPACING * nx)
            ys.append(yi + lane_k * LANE_SPACING * ny)
            cs.append(pct)

        if xs:
            ax.scatter(xs, ys,
                       c=cs, cmap=CMAP, norm=_NORM,
                       s=DOT_SIZE, zorder=4,
                       linewidths=0.5, edgecolors="white", alpha=0.92)

        # ── Lane-index badge next to the leader dot ───────────────────────────
        # A small bold index (①②③④) is placed just beyond the leader's dot
        # in the outward normal direction.  The full model name is in the fixed
        # legend panel drawn below (build_lane_legend), which is never clipped.
        badge_idx = lane_k - FIRST_LANE + 1          # 1 … 4
        BADGES = ["①", "②", "③", "④"]
        if 0 in sub.index and 0 in n_sub.index:
            lx0 = float(sub.loc[0, "x"])
            ly0 = float(sub.loc[0, "y"])
            # One extra half-spacing beyond this lane's dot
            bx = lx0 + (lane_k + 0.4) * LANE_SPACING * leader_nx
            by = ly0 + (lane_k + 0.4) * LANE_SPACING * leader_ny
            ax.text(bx, by, BADGES[badge_idx - 1],
                    fontsize=7, fontweight="bold", color="#333333",
                    ha="center", va="center", zorder=8)

    # ── 3. Vehicle rectangles ─────────────────────────────────────────────────
    # Drawn AFTER lanes so they sit on top and remain clearly visible.
    for nid in ids:
        if nid not in sub.index or nid not in n_sub.index:
            continue
        xi  = float(sub.loc[nid, "x"])
        yi  = float(sub.loc[nid, "y"])
        nx  = float(n_sub.loc[nid, "nx"])
        ny  = float(n_sub.loc[nid, "ny"])
        # tangent = 90° CW rotation of the left-normal: (ny, −nx)
        draw_vehicle_rect(ax, xi, yi, ny, -nx, is_leader=(nid == 0))

        # Node ID label — placed on the OPPOSITE side from the model lanes
        # (right-normal direction = negative of left-normal) so it never overlaps data dots
        ax.text(xi - LANE_SPACING * 0.4 * nx,
                yi - LANE_SPACING * 0.4 * ny,
                f"N{nid}", fontsize=6, color="#111111",
                fontweight="bold", zorder=9, ha="center", va="center",
                bbox=dict(boxstyle="round,pad=0.15",
                          facecolor="white", edgecolor="none", alpha=0.7))

    # ── 4. Formatting ─────────────────────────────────────────────────────────
    ax.set_aspect("equal")
    ax.set_xlabel("x  [m]", fontsize=9)
    ax.set_ylabel("y  [m]", fontsize=9)
    ax.set_title(
        f"Platoon NLOSv condition  —  t = {t:.1f} s   (frame {frame_idx:04d})",
        fontsize=11, fontweight="bold", pad=8)
    ax.grid(True, alpha=GRID_ALPHA, linewidth=0.5)
    ax.tick_params(labelsize=7.5)

    # Fixed axis limits — wide enough for 4 lanes × 20 m = 80 m off-road on all sides
    ax.set_xlim(80, 1800)
    ax.set_ylim(-110, 420)

    # ── Fixed lane-guide panel (top-right, axes coordinates) ─────────────────
    # Always in view regardless of platoon position.  Maps badge ① – ④ to models.
    BADGES = ["①", "②", "③", "④"]
    guide_lines = (
        ["Leader (N0)", LEADER_FC,   "square"],
        ["Followers (N1–N8)", FOLLOWER_FC, "square"],
        [None, None, None],                        # spacer
        ["— Lane guide —", "none", "none"],
    ) + tuple(
        (f"  {b}  {model}  (lane {i+1})", "#333333", "circle")
        for i, (b, model) in enumerate(zip(BADGES, MODELS))
    ) + (
        [None, None, None],
        ["█ Intersection corridor", "#ef5350", "fill"],
    )

    legend_patches = [
        mpatches.Patch(facecolor=LEADER_FC,   edgecolor="white", label="Leader  (N0)"),
        mpatches.Patch(facecolor=FOLLOWER_FC, edgecolor="white", label="Followers  (N1–N8)"),
        mpatches.Patch(facecolor="#ef5350",   alpha=0.25,        label="Intersection corridor"),
    ]
    # Append one line per lane with its badge
    for badge, model in zip(BADGES, MODELS):
        legend_patches.append(
            mpatches.Patch(facecolor="white", edgecolor="#888",
                           label=f"{badge} {model}"))

    ax.legend(handles=legend_patches, loc="upper right",
              fontsize=7.5, framealpha=0.93, edgecolor="0.7",
              title="Lane guide (① = closest to road)",
              title_fontsize=7, ncol=1)

    # Elapsed simulation time annotation (bottom-left)
    ax.text(0.01, 0.02, f"sim time: {t:.1f} s",
            transform=ax.transAxes, fontsize=8.5, color="#333333",
            va="bottom", ha="left",
            bbox=dict(boxstyle="round,pad=0.3", fc="white",
                      ec="0.7", alpha=0.85))


# ═══════════════════════════════════════════════════════════════════════════════
# COLOURBAR STRIP  (shared 0–100 % scale, one bar per model)
# ═══════════════════════════════════════════════════════════════════════════════

def build_colourbar_axes(fig: plt.Figure, gs_row) -> None:
    """
    Single horizontal colorbar shared by all four model lanes.

    Because all lanes use the same colormap and norm, one bar is sufficient
    to read off any lane's NLOSv % from its dot colour.  Model identity is
    conveyed by the end-of-lane text labels, not by colour.
    """
    ax_cb = fig.add_subplot(gs_row)
    sm    = ScalarMappable(norm=_NORM, cmap=CMAP)
    sm.set_array([])
    cb = fig.colorbar(sm, cax=ax_cb, orientation="horizontal")
    cb.set_label(
        "NLOSv link %  —  shared scale for all model lanes  "
        "(0 % = all links LOS  ·  100 % = all links NLOSv)",
        fontsize=8.5, labelpad=4)
    cb.ax.tick_params(labelsize=8)
    cb.set_ticks([0, 25, 50, 75, 100])
    cb.set_ticklabels(["0 %\n(all LOS)", "25 %", "50 %", "75 %",
                       "100 %\n(all NLOSv)"])


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    # ── CLI ───────────────────────────────────────────────────────────────────
    ap = argparse.ArgumentParser(
        description="Generate per-frame PNGs for ffmpeg NLOSv animation.",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    here          = Path(__file__).parent
    default_trace = (here / ".." / ".." / "experiment_ra_apc" /
                     "traces" / "intersection_nlosv.mob.csv").resolve()
    default_out   = here / "results_nlosv_model_eval" / "frames_nlosv"

    ap.add_argument("--trace",   default=str(default_trace),
                    help="path to intersection_nlosv.mob.csv")
    ap.add_argument("--out-dir", default=str(default_out),
                    help="directory for output PNGs")
    ap.add_argument("--stride",  type=int, default=5,
                    help="render every N-th timestep (default 5 → 0.5 s/frame)")
    ap.add_argument("--dpi",     type=int, default=120,
                    help="PNG resolution (default 120)")
    args = ap.parse_args()

    if not os.path.isfile(args.trace):
        sys.exit(f"ERROR: trace not found: {args.trace}")
    os.makedirs(args.out_dir, exist_ok=True)

    # ── Global seaborn theme  (set once; survives ax.cla() calls) ─────────────
    sns.set_theme(style="whitegrid", font_scale=0.9,
                  rc={"axes.facecolor": "#f8f8f8",
                      "figure.facecolor": "white"})

    # ── Load trace ─────────────────────────────────────────────────────────────
    print("▸ Loading trace …")
    df        = load_trace(args.trace)
    all_times = np.sort(df["time"].unique())
    ids       = sorted(df["node_id"].unique())

    # Frame times: every stride-th simulation timestep
    frame_times = [float(t) for t in all_times[::args.stride]
                   if (df["time"] == t).sum() >= len(ids)]
    print(f"  {len(all_times)} total timesteps  →  {len(frame_times)} frames"
          f"  (stride={args.stride}, dt={args.stride*0.1:.1f} s)")

    # Pre-build full trajectory arrays for fast redraw
    x_full = {int(nid): df[df["node_id"] == nid]
              .sort_values("time")["x"].to_numpy() for nid in ids}
    y_full = {int(nid): df[df["node_id"] == nid]
              .sort_values("time")["y"].to_numpy() for nid in ids}

    # ── Compute normal vectors (full trace resolution) ─────────────────────────
    print("▸ Computing perpendicular normal vectors …")
    normals = compute_normals(df)

    # ── Pre-compute NLOSv fractions for all frame times ────────────────────────
    print(f"▸ Pre-computing NLOSv fractions for {len(frame_times)} frames …")
    t_start  = time.time()
    pct_data = precompute(df, frame_times)
    elapsed  = time.time() - t_start
    print(f"  done in {elapsed:.1f} s")

    # ── Figure layout: main axes + 4 colourbar strips ─────────────────────────
    fig = plt.figure(figsize=FIGURE_SIZE, constrained_layout=False)
    fig.subplots_adjust(left=0.04, right=0.98, top=0.93, bottom=0.10,
                        hspace=0.55)
    import matplotlib.gridspec as mgs
    gs      = mgs.GridSpec(2, 1, figure=fig,
                           height_ratios=[10, 0.45], hspace=0.7)
    ax_main = fig.add_subplot(gs[0])
    build_colourbar_axes(fig, gs[1])   # single shared colour bar (permanent)

    # ── Render frames ──────────────────────────────────────────────────────────
    print(f"▸ Rendering {len(frame_times)} frames to: {args.out_dir}/")
    t_render = time.time()

    for fi, t in enumerate(frame_times):
        render_frame(ax_main, df, pct_data, normals,
                     x_full, y_full, t, fi)

        out_path = os.path.join(args.out_dir, f"frame_{fi:04d}.png")
        fig.savefig(out_path, dpi=args.dpi, bbox_inches="tight")

        # Progress report every 20 frames
        if fi % 20 == 0 or fi == len(frame_times) - 1:
            fps_so_far = (fi + 1) / max(time.time() - t_render, 1e-3)
            eta        = (len(frame_times) - fi - 1) / max(fps_so_far, 1e-3)
            print(f"  frame {fi + 1:4d}/{len(frame_times)}"
                  f"  t={t:6.1f} s"
                  f"  {fps_so_far:.1f} fps"
                  f"  ETA {eta:.0f} s")

    plt.close(fig)

    total = time.time() - t_render
    print(f"\n✓ {len(frame_times)} frames saved in {total:.0f} s"
          f"  ({total/len(frame_times):.2f} s/frame)")
    print(f"\nAssemble video with ffmpeg:")
    print(f"  ffmpeg -r 20 -i '{args.out_dir}/frame_%04d.png'"
          " -c:v libx264 -pix_fmt yuv420p -crf 18 nlosv.mp4")


if __name__ == "__main__":
    main()
