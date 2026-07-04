#!/usr/bin/env python3
"""
gen_mcr_grid.py — emit the per-(N, IVD) MCR grid for the
groupcast_mcr_baseline_eval campaign.

For each (N, IVD) operating point we want MCR values that admit EXACTLY
{0, 1, 2, ..., N-1, all} follower UEs. Follower i sits at ~ i*IVD from
the leader, so:

  admit_k      when  k*IVD - 0.5 <= MCR < k*IVD + 0.5
  admit_0      when  MCR < 0.5*IVD
  admit_all    when  MCR > (N-1)*IVD

We emit the grid endpoints (the value halfway between two admit boundaries):

  k*IVD - 0.5    for k=1..N-1   (admits exactly k followers)
  (N-1)*IVD + 0.5                (admits all followers)

Plus a "below-first" MCR = 0.5*IVD - 0.5 = (IVD - 1)/2  for admit_0.
That value is small (< 0.5*IVD), so it suppresses ALL feedback — useful
as a "no PSFCH" sanity point.

Usage:
  python3 gen_mcr_grid.py [--out PATH] [--quick]
  --quick  : emit only the {admit_0, admit_1, admit_all} endpoints to
             keep the smoke test small.
  --full   : the default. Emit the full grid.

Output JSON schema:
  {
    "schema_version": 1,
    "eps_m": 0.5,                  # boundary offset (so MCR_k = k*IVD - eps_m)
    "grid": [
      {
        "N": 8,
        "ivd": 25,
        "mcr_values": [12.0, 24.5, 49.5, 74.5, 99.5, 124.5, 149.5, 174.5],
        "mcr_labels":  ["admit_0", "admit_1", "admit_2", "admit_3", "admit_4",
                        "admit_5", "admit_6", "admit_all"]
      },
      ...
    ]
  }

The runner reads this JSON and enumerates one cell per (N, harqOption,
traffic, speed-IVD, MCR) tuple.
"""
import argparse
import json
import sys
from pathlib import Path

# 1-s safe-headway coupled speed/IVD operating points, verbatim from
# relay_storm_eval/run_relay_storm.sh.
SPEED_IVD_PAIRS = [
    (80, 25), (90, 27), (100, 29),
    (110, 32), (120, 34), (130, 37), (140, 39),
]

# Swept platoon sizes for the full study.
N_VALUES = [8, 10, 16, 20]
N_QUICK = [8]  # first-pass smoke subset (smallest MCR grid)

EPS = 0.5  # half-meter offset so MCR_k sits cleanly between k*IVD and (k+1)*IVD


def grid_for(N: int, ivd: float, eps: float = EPS):
    """Return (mcr_values, mcr_labels) for one (N, ivd) operating point."""
    values = []
    labels = []
    # admit_0: small enough to suppress ALL feedback
    values.append(max(0.0, 0.5 * ivd - eps))
    labels.append("admit_0")
    # admit_k for k=1..N-1
    for k in range(1, N):
        values.append(k * ivd - eps)
        labels.append(f"admit_{k}")
    # admit_all
    values.append((N - 1) * ivd + eps)
    labels.append("admit_all")
    return values, labels


def build_grid(quick: bool = False):
    grid = []
    n_values = N_QUICK if quick else N_VALUES
    for N in n_values:
        for spd, ivd in SPEED_IVD_PAIRS:
            values, labels = grid_for(N, ivd)
            if quick:
                # Reduced first-pass: only {admit_0, admit_1, admit_N-1, admit_all}.
                keep_idx = [
                    labels.index("admit_0"),
                    labels.index("admit_1"),
                    labels.index(f"admit_{N - 1}"),
                    labels.index("admit_all"),
                ]
                values = [values[i] for i in keep_idx]
                labels = [labels[i] for i in keep_idx]
            grid.append({
                "N": N,
                "speed": spd,
                "ivd": ivd,
                "mcr_values": values,
                "mcr_labels": labels,
            })
    return grid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="mcr_grid.json",
                    help="Output JSON path (default: mcr_grid.json)")
    ap.add_argument("--quick", action="store_true",
                    help="Emit reduced first-pass subset of MCR values per cell.")
    args = ap.parse_args()

    grid = build_grid(quick=args.quick)

    out = {
        "schema_version": 1,
        "eps_m": EPS,
        "speed_ivd_pairs": [{"speed": s, "ivd": d} for s, d in SPEED_IVD_PAIRS],
        "n_values": N_VALUES,
        "quick": bool(args.quick),
        "grid": grid,
    }

    out_path = Path(args.out)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {len(grid)} (N, speed, ivd) rows -> {out_path}")
    print(f"  Total cells (excl. harq/traffic/seed): "
          f"{sum(len(g['mcr_values']) for g in grid)}")
    print(f"  QUICK={bool(args.quick)}")


if __name__ == "__main__":
    main()