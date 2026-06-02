#!/usr/bin/env python3
"""plot_trajectory_nlosv.py

5-panel figure comparing NLOSv link-condition decisions on the
intersection_nlosv.mob.csv platoon trace (9 vehicles, L-shaped route).

Each panel shows the same trajectory; dots colored by per-vehicle NLOSv
link fraction (0 = all LOS → green, 1 = all NLOSv → red) sampled every 2 s.

Panels:
  1. Trajectory reference (vehicle IDs, corner annotation)
  2. Geometric — position-exact Liang-Barsky AABB test (ground truth)
  3. V2V Urban   — probabilistic density-based, MEDIUM tier
  4. V2V Highway — probabilistic density-based, MEDIUM tier
  5. Mix         — Highway on straight segments, Urban at corner
"""

import os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
import numpy as np
import pandas as pd

HERE  = os.path.dirname(os.path.abspath(__file__))
REPO  = os.path.join(HERE, "..", "..")
TRACE = os.path.join(REPO, "experiment_ra_apc", "traces", "intersection_nlosv.mob.csv")
OUT   = os.path.join(HERE, "results_nlosv_model_eval", "plots",
                     "trajectory_nlosv_comparison.png")

BLOCKER_HL  = 2.5    # half vehicle length [m]
BLOCKER_HW  = 1.25   # half vehicle width  [m]
SAMPLE_EVERY = 2.0   # s  (visualisation cadence)
N_NODES = 9

CMAP = "RdYlGn_r"
NORM = Normalize(vmin=0, vmax=1)

# intersection corridor: vertical segment x ≈ 401.6
INT_X_LO, INT_X_HI = 370, 440
INT_Y_LO, INT_Y_HI = -20, 320

# ── closed-form formulas (mirrors nlosv_model_eval.cc::ClosedFormProbs) ──────

def _clamp(v):
    return min(1.0, max(0.0, float(v)))

def _pnlosv(scenario: str, density: str, d: float) -> float:
    if d < 1.0:
        d = 1.0
    if scenario == "urban":
        if density == "LOW":
            pL = _clamp(0.8548 * np.exp(-0.0064 * d))
            pNr = _clamp(1/(0.0396*d) * np.exp(-((np.log(d)-5.2718)**2)/3.4827))
        elif density == "MEDIUM":
            pL = _clamp(0.8372 * np.exp(-0.0114 * d))
            pNr = _clamp(1/(0.0312*d) * np.exp(-((np.log(d)-5.0063)**2)/2.4544))
        else:
            pL = _clamp(0.8962 * np.exp(-0.017  * d))
            pNr = _clamp(1/(0.0242*d) * np.exp(-((np.log(d)-5.0115)**2)/2.2092))
        pNlosModel = 1.0 - pL - pNr          # unclamped (matches model bucket logic)
    else:  # highway
        if density == "LOW":
            aL,bL,cL = 1.5e-6,-0.0015,1.0;  aN,bN,cN = -2.9e-7,0.00059,0.0017
        elif density == "MEDIUM":
            aL,bL,cL = 2.7e-6,-0.0025,1.0;  aN,bN,cN = -3.7e-7,0.00061,0.015
        else:
            aL,bL,cL = 3.2e-6,-0.003,1.0;   aN,bN,cN = -4.1e-7,0.00067,0.0
        pL = _clamp(aL*d*d + bL*d + cL)
        pNlosModel = _clamp(aN*d*d + bN*d + cN)
    pNlosEff  = max(0.0, min(pNlosModel, 1.0 - pL))
    return max(0.0, 1.0 - pL - pNlosEff)   # effective P(NLOSv) bucket

# ── Liang-Barsky AABB intersection ───────────────────────────────────────────

def _hits_aabb(tx, rx, cx, cy) -> bool:
    dx, dy = rx[0]-tx[0], rx[1]-tx[1]
    x0, y0 = tx
    t0, t1 = 0.0, 1.0
    for p, q in [(-dx, x0-(cx-BLOCKER_HL)), (dx, (cx+BLOCKER_HL)-x0),
                 (-dy, y0-(cy-BLOCKER_HW)), (dy, (cy+BLOCKER_HW)-y0)]:
        if p == 0.0:
            if q < 0: return False
        elif p < 0: t0 = max(t0, q/p)
        else:       t1 = min(t1, q/p)
    return t0 <= t1

def _geo_nlosv(ti, rj, blockers) -> bool:
    return any(_hits_aabb(ti, rj, bx, by) for bx, by in blockers)

def _in_corner(mx, my) -> bool:
    return (INT_X_LO <= mx <= INT_X_HI) and (INT_Y_LO <= my <= INT_Y_HI)

# ── per-vehicle NLOSv fraction at each sample time ───────────────────────────

def compute_fractions(df: pd.DataFrame, sample_times):
    """Returns dict model → list of (t, node_id, x, y, frac)."""
    models = ["geo", "urban", "highway", "mix"]
    rows = {m: [] for m in models}

    for t in sample_times:
        sub = df[df["time"] == t].set_index("node_id")
        if len(sub) < N_NODES:
            continue
        ids = sorted(sub.index.tolist())
        pos = {i: (float(sub.loc[i,"x"]), float(sub.loc[i,"y"])) for i in ids}
        n = len(ids) - 1   # links per vehicle (to all others)

        for i in ids:
            geo_c = urb_s = hwy_s = mix_s = 0.0
            ti = np.array(pos[i])
            for j in ids:
                if j == i: continue
                rj = np.array(pos[j])
                d  = float(np.linalg.norm(ti - rj))
                # geometric
                blk = [pos[k] for k in ids if k != i and k != j]
                geo_c += float(_geo_nlosv(ti, rj, blk))
                # probabilistic
                urb_s += _pnlosv("urban",   "MEDIUM", d)
                hwy_s += _pnlosv("highway", "MEDIUM", d)
                # mix: use midpoint to pick model
                mx, my = 0.5*(ti[0]+rj[0]), 0.5*(ti[1]+rj[1])
                mix_s += (_pnlosv("urban","MEDIUM",d) if _in_corner(mx,my)
                          else _pnlosv("highway","MEDIUM",d))
            xi, yi = pos[i]
            rows["geo"].append((t, i, xi, yi, geo_c/n))
            rows["urban"].append((t, i, xi, yi, urb_s/n))
            rows["highway"].append((t, i, xi, yi, hwy_s/n))
            rows["mix"].append((t, i, xi, yi, mix_s/n))

    return {m: pd.DataFrame(v, columns=["time","node_id","x","y","frac"])
            for m, v in rows.items()}

# ── plot helpers ──────────────────────────────────────────────────────────────

TRAJ_COLOR = "#b0b0b0"
CORNER_FC  = "#d7191c"

def _draw_traj(ax, df_trace):
    for nid in sorted(df_trace["node_id"].unique()):
        nd = df_trace[df_trace["node_id"] == nid].sort_values("time")
        ax.plot(nd["x"], nd["y"], color=TRAJ_COLOR, lw=0.9, zorder=1)
    # corner zone rectangle
    rect = mpatches.FancyArrowPatch  # just a visual aid
    ax.add_patch(mpatches.Rectangle(
        (INT_X_LO, INT_Y_LO), INT_X_HI-INT_X_LO, INT_Y_HI-INT_Y_LO,
        fc=CORNER_FC, alpha=0.07, ec=CORNER_FC, lw=0.6, zorder=0,
        label="intersection corridor"))

def _scatter_fracs(ax, fdf):
    cmap = plt.get_cmap(CMAP)
    sc = ax.scatter(fdf["x"], fdf["y"], c=fdf["frac"],
                    cmap=CMAP, norm=NORM, s=22, linewidths=0,
                    alpha=0.82, zorder=3)
    return sc

def _set_lims(ax, df_trace):
    ax.set_xlim(df_trace["x"].min()-30, df_trace["x"].max()+50)
    ax.set_ylim(df_trace["y"].min()-25, df_trace["y"].max()+60)

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    if not os.path.isfile(TRACE):
        sys.exit(f"Trace not found: {TRACE}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    df = pd.read_csv(TRACE, comment="#")
    df["time"] = df["time"].round(3)

    all_times = np.sort(df["time"].unique())
    dt = round(float(all_times[1] - all_times[0]), 4)
    stride = max(1, int(round(SAMPLE_EVERY / dt)))
    sample_times = [float(t) for t in all_times[::stride]
                    if (df["time"] == t).sum() >= N_NODES]
    print(f"  {len(sample_times)} sample times  (every {SAMPLE_EVERY} s)")

    print("  Computing NLOSv fractions ...")
    fracs = compute_fractions(df, sample_times)

    # ── figure: 5 panels ──
    fig, axes = plt.subplots(1, 5, figsize=(26, 7),
                             gridspec_kw={"wspace": 0.06})

    panel_cfgs = [
        ("ref",     "Trajectory\n(reference)"),
        ("geo",     "① Geometric\n(position-exact ground truth)"),
        ("urban",   "② V2V Urban\n(probabilistic · MEDIUM)"),
        ("highway", "③ V2V Highway\n(probabilistic · MEDIUM)"),
        ("mix",     "④ Mix\n(Highway straight / Urban corner)"),
    ]

    for ax, (key, title) in zip(axes, panel_cfgs):
        _draw_traj(ax, df)
        _set_lims(ax, df)

        if key == "ref":
            # node labels at t=0
            t0 = sample_times[0]
            sub0 = df[df["time"] == t0].set_index("node_id")
            cmap_t = plt.get_cmap("cool")
            for nid in sorted(sub0.index):
                xi, yi = sub0.loc[nid,"x"], sub0.loc[nid,"y"]
                ax.plot(xi, yi, "o", ms=6, color="#333333", zorder=5)
                ax.text(xi, yi+12, f"N{nid}", ha="center", va="bottom",
                        fontsize=6.5, color="#111111", fontweight="bold")
            # direction arrow on node 0 trajectory
            nd0 = df[df["node_id"]==0].sort_values("time")
            mid_idx = len(nd0) // 3
            ax.annotate("", xy=(nd0["x"].iloc[mid_idx+5], nd0["y"].iloc[mid_idx+5]),
                        xytext=(nd0["x"].iloc[mid_idx], nd0["y"].iloc[mid_idx]),
                        arrowprops=dict(arrowstyle="->", color="#555555", lw=1.3))
            # labels
            ax.text(INT_X_LO + 5, INT_Y_HI/2, "corner\n(Urban)", fontsize=6.5,
                    color="#a00000", va="center", ha="left",
                    bbox=dict(fc="white", ec="none", alpha=0.6))
            ax.text(df["x"].min()+20, df["y"].min()-15,
                    "horizontal → V2V_Highway segment", fontsize=6.0, color="#555")
            ax.text(INT_X_HI+10, INT_Y_HI*0.5,
                    "← vertical\nV2V_Urban\nsegment", fontsize=6.0, color="#555",
                    va="center")
        else:
            fdf = fracs[key]
            sc = _scatter_fracs(ax, fdf)

            # annotate per-phase mean NLOSv fraction in a small table
            # phase 1: horizontal (y < 10)
            # phase 2: corner (in intersection corridor)
            # phase 3: post-corner horizontal (y ≈ 298)
            for fdf_mask, label, xpos, ypos in [
                (fdf["y"] < 10,  "Horiz in\n", df["x"].min()+30,  df["y"].min()-18),
                (fdf.apply(lambda r: _in_corner(r["x"],r["y"]), axis=1),
                 "Corner\n", INT_X_LO+5, INT_Y_HI+8),
                (fdf["y"] > 280, "Horiz out\n", df["x"].max()-200, df["y"].max()+8),
            ]:
                sub = fdf[fdf_mask]
                if len(sub) == 0: continue
                mean_f = sub["frac"].mean()
                ax.text(xpos, ypos,
                        f"{label}NLOSv={mean_f:.2f}",
                        fontsize=5.8, color="#222",
                        bbox=dict(boxstyle="round,pad=0.2", fc="lightyellow",
                                  ec="0.6", lw=0.5, alpha=0.85))

        ax.set_title(title, fontsize=8.5, fontweight="bold", pad=6)
        ax.tick_params(labelsize=7)
        ax.set_xlabel("x [m]", fontsize=7)
        if ax is axes[0]:
            ax.set_ylabel("y [m]", fontsize=7)
        else:
            ax.tick_params(labelleft=False)
        ax.grid(alpha=0.2, lw=0.5)

    # shared colourbar
    sm = ScalarMappable(norm=NORM, cmap=CMAP)
    sm.set_array([])
    cb = fig.colorbar(sm, ax=axes, orientation="vertical",
                      fraction=0.012, pad=0.01, shrink=0.75)
    cb.set_label("NLOSv fraction per vehicle\n(fraction of 8 links that are NLOSv)",
                 fontsize=8)
    cb.ax.tick_params(labelsize=7)
    cb.set_ticks([0, 0.25, 0.5, 0.75, 1.0])
    cb.set_ticklabels(["0.0\n(all LOS)", "0.25", "0.5", "0.75", "1.0\n(all NLOSv)"])

    fig.suptitle(
        "Platoon NLOSv link fraction along L-shaped intersection route\n"
        r"intersection_nlosv.mob.csv  ·  9 vehicles (N0 = leader/TX, N1–N8 = followers)  ·  sampled every 2 s",
        fontsize=10, y=1.02
    )

    # legend for intersection zone
    patch = mpatches.Patch(fc=CORNER_FC, alpha=0.15, ec=CORNER_FC,
                           label="intersection corridor (corner turn)")
    axes[0].legend(handles=[patch], loc="upper right", fontsize=6.5,
                   framealpha=0.85)

    fig.savefig(OUT, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved → {OUT}")


if __name__ == "__main__":
    main()
