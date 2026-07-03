#!/usr/bin/env python3
"""
sweep_aggregate.py — seed-aggregate the per-sim KPI outputs of kpi_postprocess.py.

Walks a directory of per-sim output subdirs (each written by kpi_postprocess.py)
and produces:

  sweep_aggregate.csv  : seed-aggregated per-(mode, speed, ivd, mcd, relay_node)
                         statistics (mean/min/max/p5/p95) for each KPI column.
  storm_indicator.csv  : for each (speed, ivd, mcd) pair, the ratio of
                         hop1 retransmissions in relay runs vs baseline.
                         A ratio >1 ⇒ the relay inflates the leader's
                         retransmission load (the "storm").

Layout (post-refactor):

    <runs_root>/
      <runId>/
        <runId>.db
        <runId>_summary.csv             # per-cell config (one row)
        <runId>_prr_type2.csv           # per-link PRR  (tx_node_id, rx_node_id, ...)
        <runId>_aoi.csv                 # per-link AoI
        <runId>_throughput.csv          # per-link throughput
        <runId>_harq.csv                # per-rx ACK/NACK
        <runId>_ch_occupancy.csv        # per-tx occupancy
        ...

Per-run metrics computed (one row per run):

  prr_mean        : mean(prr_type2_prr_mean) over all (tx, rx) link rows
                    (matches old "average effective PRR per node")
  prr_worst       : min(prr_type2_prr_mean) over all (tx, rx) link rows
                    (matches old "worst-case PRR")
  prr_leader_agg  : sum_over_followers(max(leader_rx,relay_rx)) / sum(leader_tx)
                    (3GPP-correct aggregate leader PRR with multi-hop union)
  aoi_mean_ms     : mean(aoi_aoi_mean_ms) over all (tx, rx) link rows
  harq_nack_rate  : mean(harq_harq_nack_rate) over all rx
  ch_occupancy    : mean(ch_occupancy_ch_occupancy) over all tx
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Match a "seedN" suffix in a run dir name.
SEED_RE = re.compile(r"_seed(\d+)$")

# Group prefix in dir name. We use the runId as the group key (with the
# "_seedN" suffix stripped).
def strip_seed_tag(run_id: str) -> str:
    m = SEED_RE.search(run_id)
    return run_id[: m.start()] if m else run_id


def parse_seed(run_id: str) -> Optional[int]:
    m = SEED_RE.search(run_id)
    return int(m.group(1)) if m else None


def _safe_read(p: Path) -> Optional[pd.DataFrame]:
    try:
        return pd.read_csv(p)
    except Exception:
        return None


def _agg(values: list[float]) -> tuple[float, float, float, float, float]:
    """Return (mean, min, max, p5, p95) for a list of floats."""
    if not values:
        return (0.0, 0.0, 0.0, 0.0, 0.0)
    arr = np.asarray(values, dtype=float)
    return (
        float(arr.mean()),
        float(arr.min()),
        float(arr.max()),
        float(np.percentile(arr, 5)),
        float(np.percentile(arr, 95)),
    )


def per_run_metrics(sim_dir: Path) -> Optional[dict]:
    """Compute per-run metrics from <sim_dir>'s KPI CSVs.

    Returns a dict that can be written as one row of the per-run table.
    """
    # Find the summary file (and thus the runId).
    summary_files = sorted(sim_dir.glob("*_summary.csv"))
    if not summary_files:
        return None
    summary = pd.read_csv(summary_files[0])
    if len(summary) != 1:
        return None
    srow = summary.iloc[0].to_dict()
    run_id = str(srow.get("runId", sim_dir.name))
    group = strip_seed_tag(run_id)
    seed = parse_seed(run_id) or int(srow.get("seed", 0))

    out = {
        "runId":     run_id,
        "group":     group,
        "seed":      seed,
        "speed":     srow.get("speedKmh"),
        "ivd":       srow.get("ivd"),
        "mcd":       srow.get("mcdMeters"),
        "relay_node":srow.get("relayNode", 0),
        "is_baseline": not bool(srow.get("relayEnabled", False)),
    }

    # PRR Type 2: per-link (tx, rx)
    prr2 = _safe_read(sim_dir / f"{run_id}_prr_type2.csv")
    if prr2 is not None and len(prr2) > 0:
        out["n_links"] = len(prr2)
        # Old "prr_mean" = mean over (tx, rx) of prr_mean (per-link effective PRR).
        # This is equivalent to: for each (tx, rx), effective_rx / n_tx.
        out["prr_mean"]  = float(prr2["prr_mean"].mean())
        out["prr_worst"] = float(prr2["prr_mean"].min())
        # 3GPP-correct leader aggregate: for the leader (tx=0), group by rx.
        leader = prr2[prr2["tx_node_id"] == 0].copy()
        if len(leader):
            # max(n_rx_success via direct OR relay) per follower, divided by total n_tx.
            # But this CSV only has one (tx, rx) per row, so we can't do max(leader,relay)
            # from prr_type2 directly. Instead, we approximate with the simple mean
            # over (tx, rx) of prr_mean.
            # For the "union" we need to merge the multi-path: defer to a later step.
            out["prr_leader_agg_approx"] = float(leader["prr_mean"].mean())
    else:
        out["n_links"] = 0
        out["prr_mean"] = 0.0
        out["prr_worst"] = 0.0

    # AoI
    aoi = _safe_read(sim_dir / f"{run_id}_aoi.csv")
    if aoi is not None and len(aoi) > 0 and "aoi_mean_ms" in aoi.columns:
        out["aoi_mean_ms"] = float(aoi["aoi_mean_ms"].mean())
    else:
        out["aoi_mean_ms"] = 0.0

    # HARQ
    harq = _safe_read(sim_dir / f"{run_id}_harq.csv")
    if harq is not None and len(harq) > 0 and "harq_nack_rate" in harq.columns:
        out["harq_nack_rate"] = float(harq["harq_nack_rate"].mean())
    else:
        out["harq_nack_rate"] = 0.0

    # Channel occupancy
    ch = _safe_read(sim_dir / f"{run_id}_ch_occupancy.csv")
    if ch is not None and len(ch) > 0:
        col = "ch_occupancy" if "ch_occupancy" in ch.columns else ch.columns[0]
        out["ch_occupancy"] = float(ch[col].mean())
    else:
        out["ch_occupancy"] = 0.0

    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs-root", required=True, type=Path,
                    help="Directory containing per-sim subdirs with kpi_postprocess output")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output sweep_aggregate.csv path (default: <runs-root>/sweep_aggregate.csv)")
    ap.add_argument("--storm-out", type=Path, default=None,
                    help="Output storm_indicator.csv path (default: <runs-root>/storm_indicator.csv)")
    ap.add_argument("--pattern", type=str, default="*",
                    help="Glob filter for sim dir names")
    args = ap.parse_args()

    sim_dirs = sorted(
        d for d in args.runs_root.iterdir()
        if d.is_dir() and fnmatch.fnmatch(d.name, args.pattern)
    )
    print(f"[sweep] {len(sim_dirs)} sim dirs under {args.runs_root} (pattern={args.pattern!r})")

    rows: list[dict] = []
    n_skip = 0
    for d in sim_dirs:
        r = per_run_metrics(d)
        if r is None:
            n_skip += 1
            continue
        rows.append(r)
    print(f"[sweep] {len(rows)} runs with KPIs, {n_skip} skipped (no summary)")

    if not rows:
        sys.exit(1)
    df = pd.DataFrame(rows)

    # Group by (mode, speed, ivd, mcd, relay_node) and aggregate across seeds.
    df["mode"] = df["group"].str.extract(r"^(baseline|relay\d+|storm|sweep)")
    df["mode"] = df["mode"].fillna("unknown")

    out_cols = [
        "group", "mode", "speed", "ivd", "mcd", "relay_node", "is_baseline", "n_rows",
        "prr_mean_agg_mean", "prr_mean_agg_min", "prr_mean_agg_max",
        "prr_mean_agg_p5",  "prr_mean_agg_p95",
        "prr_worst_agg_mean", "prr_worst_agg_min", "prr_worst_agg_max",
        "prr_worst_agg_p5",  "prr_worst_agg_p95",
        "aoi_mean_ms_agg_mean", "aoi_mean_ms_agg_p5", "aoi_mean_ms_agg_p95",
        "harq_nack_rate_agg_mean",
        "ch_occupancy_agg_mean",
    ]
    rows_agg = []
    for key, sub in df.groupby("group", sort=True):
        first = sub.iloc[0]
        prr_mean_a = _agg(sub["prr_mean"].tolist())
        prr_wr_a   = _agg(sub["prr_worst"].tolist())
        aoi_a      = _agg(sub["aoi_mean_ms"].tolist())
        nack_a     = _agg(sub["harq_nack_rate"].tolist())
        occ_a      = _agg(sub["ch_occupancy"].tolist())
        rows_agg.append({
            "group":     key,
            "mode":      first["mode"],
            "speed":     first["speed"],
            "ivd":       first["ivd"],
            "mcd":       first["mcd"],
            "relay_node":int(first["relay_node"]) if pd.notna(first["relay_node"]) else 0,
            "is_baseline":bool(first["is_baseline"]),
            "n_rows":    len(sub),
            "prr_mean_agg_mean":  prr_mean_a[0],
            "prr_mean_agg_min":   prr_mean_a[1],
            "prr_mean_agg_max":   prr_mean_a[2],
            "prr_mean_agg_p5":    prr_mean_a[3],
            "prr_mean_agg_p95":   prr_mean_a[4],
            "prr_worst_agg_mean": prr_wr_a[0],
            "prr_worst_agg_min":  prr_wr_a[1],
            "prr_worst_agg_max":  prr_wr_a[2],
            "prr_worst_agg_p5":   prr_wr_a[3],
            "prr_worst_agg_p95":  prr_wr_a[4],
            "aoi_mean_ms_agg_mean": aoi_a[0],
            "aoi_mean_ms_agg_p5":   aoi_a[3],
            "aoi_mean_ms_agg_p95":  aoi_a[4],
            "harq_nack_rate_agg_mean": nack_a[0],
            "ch_occupancy_agg_mean":   occ_a[0],
        })

    out_path = args.out or (args.runs_root / "sweep_aggregate.csv")
    pd.DataFrame(rows_agg, columns=out_cols).to_csv(out_path, index=False)
    print(f"[sweep] Wrote {len(rows_agg)} rows → {out_path}")

    # ── storm_indicator.csv: relay-vs-baseline per-(speed, ivd, mcd) ───────
    # We don't have storm_ratio/hop1_retx in the new pipeline yet
    # (psschTxUeMac access). For now, write a stub that records the
    # baseline/relay PRR-worst ratio as a proxy "degradation indicator".
    base = {(r["speed"], r["ivd"], r["mcd"]): r for r in rows_agg if r["is_baseline"]}
    storm_rows = []
    for r in rows_agg:
        if r["is_baseline"]:
            continue
        b = base.get((r["speed"], r["ivd"], r["mcd"]), None)
        if b is None:
            continue
        # "Worst-case degradation" = baseline worst PRR / relay worst PRR.
        # >1 means relay makes the worst case better; <1 means worse.
        ratio_prr = (b["prr_worst_agg_mean"] / r["prr_worst_agg_mean"]
                     if r["prr_worst_agg_mean"] > 0 else 0.0)
        # AoI increase: relay_aoi / baseline_aoi.
        ratio_aoi = (r["aoi_mean_ms_agg_mean"] / b["aoi_mean_ms_agg_mean"]
                     if b["aoi_mean_ms_agg_mean"] > 0 else 0.0)
        storm_rows.append({
            "group": r["group"],
            "mode":  r["mode"],
            "speed": r["speed"],
            "ivd":   r["ivd"],
            "mcd":   r["mcd"],
            "relay_node": r["relay_node"],
            "baseline_prr_worst": b["prr_worst_agg_mean"],
            "relay_prr_worst":    r["prr_worst_agg_mean"],
            "relay_baseline_prr_ratio": ratio_prr,
            "baseline_aoi_ms":   b["aoi_mean_ms_agg_mean"],
            "relay_aoi_ms":      r["aoi_mean_ms_agg_mean"],
            "relay_baseline_aoi_ratio": ratio_aoi,
        })
    si_path = args.storm_out or (args.runs_root / "storm_indicator.csv")
    if storm_rows:
        cols = list(storm_rows[0].keys())
        pd.DataFrame(storm_rows, columns=cols).to_csv(si_path, index=False)
        print(f"[sweep] Wrote {len(storm_rows)} rows → {si_path}")
    else:
        print(f"[sweep] No storm_indicator rows (no baseline matches found)")


if __name__ == "__main__":
    main()
