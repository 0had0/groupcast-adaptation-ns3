#!/usr/bin/env python3
"""
analyze_l2id_compare.py — re-analyze relay_l2id_compare simulation data
using the new registry-based kpi/ pipeline.

Replaces the inline ``compute_kpis`` function in the legacy analysis
notebook. Reads per-KPI CSVs (one file per KPI, joined by ``combine_run``)
and produces the same l2id_compare aggregate that the notebook produced,
plus storm_indicator and verdict tables.

Inputs: <runs_root>/<runId>/<runId>_<kpi>.csv (per-KPI files + summary)
Output: l2id_compare_aggregate.csv, l2id_compare_runs.csv, storm_indicator.csv, verdict.csv

Usage:
  python3 analyze_l2id_compare.py <runs_root> [output_dir] [--seed-runs]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np


# Allow ``from join_per_run import combine_run``.
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))
from join_per_run import combine_run  # noqa: E402


NOMINAL_BOUNDARY = 4  # for 9-UE platoon: front = nodes 1..4, tail = 5..8


# --------------------------------------------------------------------------- #
# Per-cell aggregation
# --------------------------------------------------------------------------- #
def _safe_mean(vals: pd.Series) -> Optional[float]:
    v = pd.to_numeric(vals, errors="coerce").dropna()
    if len(v) == 0:
        return None
    return float(v.mean())


def _safe_min(vals: pd.Series) -> Optional[float]:
    v = pd.to_numeric(vals, errors="coerce").dropna()
    if len(v) == 0:
        return None
    return float(v.min())


def _safe_first(vals: pd.Series) -> Optional[float]:
    """Take the first non-null value (for scalar KPIs that were broadcasted)."""
    v = pd.to_numeric(vals, errors="coerce").dropna()
    if len(v) == 0:
        return None
    return float(v.iloc[0])


def _safe_max(vals: pd.Series) -> Optional[float]:
    v = pd.to_numeric(vals, errors="coerce").dropna()
    if len(v) == 0:
        return None
    return float(v.max())


def _relay_mode_of(meta: pd.Series) -> str:
    rm = str(meta.get("relayMode", "none")).lower()
    return rm if rm in ("none", "fixed", "broadcast", "samel2") else "unknown"


def _classify_arm(arm: str) -> str:
    """Map the clean run-id (without _seedN suffix) to a display arm name.

    Examples (c4 corpus): 'none', 'none_adapt', 'fixed4', 'fixed6',
        'broadcast4', 'broadcast6', 'sameL24', 'sameL26'.
    Examples (gen corpus): 'adapt_N9_v2v-highway_s140', 'fixed4_N9_v2v-urban_s80'.

    For the c4-style corpus we strip _sNN (speed) suffix so arms group
    across speeds. For the gen-style corpus, the arm field already encodes
    (arm + N + scenario + speed), so we strip those to get the bare arm
    name.
    """
    a = arm.lower()
    # Strip the leading arm identifier; everything after is config.
    for prefix in (
        "samel211", "samel27", "samel26", "samel24",
        "broadcast11", "broadcast7", "broadcast6", "broadcast4",
        "fixed11", "fixed7", "fixed6", "fixed4",
        "none_adapt", "adapt",
        "sameL211", "sameL27", "sameL26", "sameL24",
        "broadcast11", "broadcast7", "broadcast6", "broadcast4",
        "fixed11", "fixed7", "fixed6", "fixed4",
        "none_adapt", "adapt",
        "none",
    ):
        if a.startswith(prefix):
            rest = a[len(prefix):]
            # If the rest begins with "_sNN" (speed), the c4 corpus — group
            # across speeds.
            # If the rest begins with "_N", the gen corpus — keep the rest
            # attached so the arm is per-config.
            # Heuristic: c4 has 5-letter arms (none, fixed4) so the arm
            # token ends right before "_s<digits>". For gen, the next
            # token is "_N<digit>" or "_N16_<scenario>_s<speed>".
            return prefix
    # Fallback: leave it alone.
    return a


def aggregate_cell(df_cell: pd.DataFrame) -> Dict:
    """Compute the per-cell aggregate from one (arm, config) cell's rows.

    ``df_cell`` is the rows-per-(seed, tx, rx) from combine_run; one row per
    (tx_node_id, rx_node_id, seed). KPIs are broadcast across (tx, rx)
    per row (so per-row aggregation is per-(seed, link); seed aggregation
    averages over both seeds and links).
    """
    out: Dict = {}

    # Per-link PRR/AoI/Throughput.
    prr_mean_per_row = pd.to_numeric(df_cell["prr_type2_prr_mean"], errors="coerce")
    aoi_per_row = pd.to_numeric(df_cell["aoi_aoi_mean_ms"], errors="coerce")
    thput_per_row = pd.to_numeric(df_cell["throughput_thput_kbps"], errors="coerce")
    harq_nack_rate_per_row = pd.to_numeric(df_cell["harq_harq_nack_rate"], errors="coerce")
    ch_occ_per_row = pd.to_numeric(df_cell["ch_occupancy_ch_occupancy"], errors="coerce")

    out["prr_mean"] = _safe_mean(prr_mean_per_row)
    out["prr_worst"] = _safe_min(prr_mean_per_row)
    out["aoi_mean_ms"] = _safe_mean(aoi_per_row)
    out["aoi_worst_ms"] = _safe_max(pd.to_numeric(df_cell.get("aoi_aoi_max_ms", pd.Series(dtype=float)), errors="coerce"))
    out["thput_mean_kbps"] = _safe_mean(thput_per_row)
    out["harq_nack"] = _safe_mean(harq_nack_rate_per_row)
    out["ch_occ"] = _safe_mean(ch_occ_per_row)

    # Front/tail split by rx_node_id vs nominal boundary.
    rx = pd.to_numeric(df_cell.get("rx_node_id"), errors="coerce")
    front_mask = rx <= NOMINAL_BOUNDARY
    tail_mask = rx > NOMINAL_BOUNDARY
    if front_mask.any():
        out["prr_front"] = _safe_mean(prr_mean_per_row[front_mask])
    else:
        out["prr_front"] = None
    if tail_mask.any():
        out["prr_tail"] = _safe_mean(prr_mean_per_row[tail_mask])
    else:
        out["prr_tail"] = None

    # Scalar KPIs (storm, psr, coll) are broadcasted to all rows — take first.
    out["storm_ratio"] = _safe_first(df_cell["storm_storm_ratio"])
    out["hop1_retx"] = _safe_first(df_cell["storm_hop1_retx"])
    out["psr"] = _safe_first(df_cell["psr_psr"])
    out["coll_rate"] = _safe_first(df_cell["coll_coll_rate"])

    return out


# --------------------------------------------------------------------------- #
# Walk runs
# --------------------------------------------------------------------------- #
def discover_runs(runs_root: Path) -> List[Path]:
    runs = []
    for sub in sorted(runs_root.iterdir()):
        if not sub.is_dir():
            continue
        if any(sub.glob("*_summary.csv")) and any(sub.glob("*_*.csv")):
            runs.append(sub)
    return runs


def load_all_runs(runs_root: Path) -> pd.DataFrame:
    """Load every per-run directory, combine KPIs, concat into one wide DF."""
    runs = discover_runs(runs_root)
    print(f"[analyze] Discovered {len(runs)} run dirs under {runs_root}")
    rows = []
    for i, run in enumerate(runs):
        try:
            df = combine_run(run)
        except Exception as e:
            print(f"  skip {run.name}: {e}")
            continue
        # Tag arm from runId (cleaned of _seedN suffix already).
        df["arm"] = _classify_arm(str(df["runId"].iloc[0]))
        rows.append(df)
        if (i + 1) % 500 == 0:
            print(f"  loaded {i+1}/{len(runs)} runs")
    if not rows:
        raise SystemExit("No usable runs found")
    big = pd.concat(rows, ignore_index=True, sort=False)
    print(f"[analyze] Combined rows: {len(big)}, cols: {len(big.columns)}")
    return big


# --------------------------------------------------------------------------- #
# Storm indicator (relay vs baseline)
# --------------------------------------------------------------------------- #
def compute_storm_indicator(df: pd.DataFrame) -> pd.DataFrame:
    """Compute storm_ratio_hop1 per (arm, speed, ivd, mcd, scenario).

    For each non-baseline arm, this is hop1_retx / baseline's hop1_retx at
    the same (speed, ivd, scenario, numUEs). The baseline (none arm) is
    only run at mcd=80 in the gen corpus — its hop1_retx is reused for
    mcd=300 sameL2 measurements (matches the legacy behaviour).
    """
    keys_with_mcd = ["speedKmh", "ivd", "mcdMeters", "scenario", "numUEs"]
    keys_no_mcd = ["speedKmh", "ivd", "scenario", "numUEs"]
    # Per-run storm metrics (one value per seed).
    storm_per_run = (
        df.groupby(["arm"] + keys_with_mcd + ["seed"], as_index=False)
          .agg(storm_ratio=("storm_storm_ratio", "first"),
               hop1_retx=("storm_hop1_retx", "first"))
    )
    # Baseline average (across seeds) per (speed, ivd, scenario, numUEs),
    # ignoring mcd (legacy behaviour: sameL2@300 paired with none@80).
    base = (
        storm_per_run[storm_per_run["arm"] == "none"]
        .groupby(keys_no_mcd, as_index=False)
        .agg(b_storm_ratio=("storm_ratio", "mean"),
             b_hop1_retx=("hop1_retx", "mean"))
    )
    # Join baseline values into the per-run table (ignoring mcd).
    joined = storm_per_run.merge(base, on=keys_no_mcd, how="left")
    joined["storm_ratio_hop1"] = joined["hop1_retx"] / joined["b_hop1_retx"].replace(0, pd.NA)
    # Aggregate per (arm, speed, ivd, mcd, scenario).
    agg = (
        joined.groupby(["arm"] + keys_with_mcd, as_index=False)
              .agg(storm_ratio_mean=("storm_ratio", "mean"),
                   storm_ratio_std=("storm_ratio", "std"),
                   hop1_retx_mean=("hop1_retx", "mean"),
                   b_storm_ratio=("b_storm_ratio", "first"),
                   b_hop1_retx=("b_hop1_retx", "first"),
                   storm_ratio_hop1_mean=("storm_ratio_hop1", "mean"),
                   storm_ratio_hop1_std=("storm_ratio_hop1", "std"))
    )
    agg["storm_ratio_rel"] = agg["storm_ratio_mean"] / agg["b_storm_ratio"].replace(0, pd.NA)
    return agg


# --------------------------------------------------------------------------- #
# Per-cell aggregate
# --------------------------------------------------------------------------- #
def compute_cell_aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per (arm, speedKmh, ivd, mcdMeters, scenario, numUEs) cell."""
    keys = ["arm", "speedKmh", "ivd", "mcdMeters", "scenario", "numUEs",
            "relayMode", "relayNode"]
    cells = []
    for cell_key, df_cell in df.groupby(keys, dropna=False):
        agg = aggregate_cell(df_cell)
        # Build a row dict.
        d = dict(zip(keys, cell_key))
        d.update(agg)
        # Mean prr_front / prr_tail also benefit from per-seed min — for
        # now the simple mean is enough.
        cells.append(d)
    out = pd.DataFrame(cells)
    return out


def attach_baseline_deltas(agg: pd.DataFrame) -> pd.DataFrame:
    """For each (speed, ivd, mcd, scenario), pull the none baseline's values
    and attach them as ``b_*`` columns, plus the deltas ``d_*``."""
    keys = ["speedKmh", "ivd", "mcdMeters", "scenario", "numUEs"]
    base = (
        agg[agg["arm"] == "none"]
        .groupby(keys, as_index=False)
        .agg(b_hop1_retx=("hop1_retx", "mean"),
             b_ch_occ=("ch_occ", "mean"),
             b_prr_worst=("prr_worst", "mean"),
             b_prr_tail=("prr_tail", "mean"),
             b_prr_front=("prr_front", "mean"),
             b_aoi_mean_ms=("aoi_mean_ms", "mean"))
    )
    out = agg.merge(base, on=keys, how="left")
    # Deltas (arm - baseline).
    for col in ("prr_worst", "prr_tail", "prr_front", "aoi_mean_ms"):
        out[f"d_{col}"] = out[col] - out[f"b_{col}"]
    # Occupancy ratio.
    out["occ_ratio"] = out["ch_occ"] / out["b_ch_occ"].replace(0, pd.NA)
    return out


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #
def compute_verdict(agg: pd.DataFrame, storm: pd.DataFrame) -> pd.DataFrame:
    """Compute the verdict table (per arm)."""
    # Per-arm storm_ratio_rel at hiMCD, computed from the storm indicator
    # (which has the proper baseline normalization).
    hi_mcd_storm = storm[storm["mcdMeters"] >= 200]
    storm_rel_hi = (
        hi_mcd_storm.groupby("arm", as_index=False)
                    .agg(storm_ratio_hiMCD=("storm_ratio_rel", "mean"))
    )

    rows = []
    for arm, df_arm in agg.groupby("arm"):
        d = {"arm": arm}
        # Storm ratio at hiMCD (relay/baseline from the storm indicator).
        s = storm_rel_hi[storm_rel_hi["arm"] == arm]
        d["storm_ratio_hiMCD"] = (
            float(s["storm_ratio_hiMCD"].iloc[0])
            if len(s) > 0 and pd.notna(s["storm_ratio_hiMCD"].iloc[0])
            else None
        )
        # Aggregate per-arm deltas (across all configs).
        if len(df_arm) > 0:
            d["d_tail_PRR"] = float(df_arm["d_prr_tail"].astype(float).mean())
            d["d_front_PRR"] = float(df_arm["d_prr_front"].astype(float).mean())
            d["occ_ratio"] = float(df_arm["occ_ratio"].astype(float).mean())
            d["coll_rate"] = float(df_arm["coll_rate"].astype(float).mean())
            d["d_AoI_ms"] = float(df_arm["d_aoi_mean_ms"].astype(float).mean())
        else:
            for k in ("d_tail_PRR", "d_front_PRR", "occ_ratio",
                      "coll_rate", "d_AoI_ms"):
                d[k] = None
        # Hypotheses.
        if arm in ("fixed4", "fixed6", "fixed11", "fixed7"):
            sr = d["storm_ratio_hiMCD"]
            d["H1_no_storm"] = (sr is not None and sr <= 1.20)
            d["H2_tail_kept"] = (d["d_tail_PRR"] is not None and d["d_tail_PRR"] >= -0.005)
            d["H3_cheaper_than_sameL2"] = (d["occ_ratio"] is not None and d["occ_ratio"] < 1.5)
        elif arm in ("broadcast4", "broadcast6", "broadcast11", "broadcast7"):
            d["H1_no_storm"] = None
            d["H2_tail_kept"] = (d["d_tail_PRR"] is not None and d["d_tail_PRR"] >= 0.0)
            d["H3_cheaper_than_sameL2"] = None
        elif arm in ("sameL24", "sameL26", "sameL27", "sameL211"):
            d["H1_no_storm"] = None
            d["H2_tail_kept"] = None
            d["H3_cheaper_than_sameL2"] = None
        else:
            d["H1_no_storm"] = None
            d["H2_tail_kept"] = None
            d["H3_cheaper_than_sameL2"] = None
        rows.append(d)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs_root", type=Path)
    ap.add_argument("output_dir", nargs="?", type=Path, default=None)
    ap.add_argument("--name", default="l2id_compare",
                    help="Output filename prefix (default l2id_compare)")
    args = ap.parse_args()

    runs_root = args.runs_root.resolve()
    out_dir = (args.output_dir or runs_root).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    name = args.name

    print(f"[analyze] runs_root = {runs_root}")
    print(f"[analyze] output_dir = {out_dir}")

    # 1. Load all per-run KPIs into one big DataFrame.
    df = load_all_runs(runs_root)
    df.to_csv(out_dir / f"{name}_runs.csv", index=False)
    print(f"[analyze] Wrote {name}_runs.csv ({len(df)} rows)")

    # 2. Cell aggregate.
    agg = compute_cell_aggregate(df)
    agg = attach_baseline_deltas(agg)
    agg.to_csv(out_dir / f"{name}_aggregate.csv", index=False)
    print(f"[analyze] Wrote {name}_aggregate.csv ({len(agg)} rows)")

    # 3. Storm indicator.
    storm = compute_storm_indicator(df)
    storm.to_csv(out_dir / f"{name}_storm_indicator.csv", index=False)
    print(f"[analyze] Wrote {name}_storm_indicator.csv ({len(storm)} rows)")

    # 4. Verdict.
    verdict = compute_verdict(agg, storm)
    verdict.to_csv(out_dir / f"{name}_verdict.csv", index=False)
    print(f"[analyze] Wrote {name}_verdict.csv ({len(verdict)} rows)")

    # 5. Verdict by regime (for the gen corpus that has scenario/numUEs).
    by_regime_rows = []
    for (arm, scenario, numUEs), df_grp in agg.groupby(
        ["arm", "scenario", "numUEs"]
    ):
        d = {"arm": arm, "scenario": scenario, "numUEs": numUEs}
        d["d_tail_PRR"] = float(df_grp["d_prr_tail"].astype(float).mean())
        d["d_front_PRR"] = float(df_grp["d_prr_front"].astype(float).mean())
        d["occ_ratio"] = float(df_grp["occ_ratio"].astype(float).mean())
        d["coll_rate"] = float(df_grp["coll_rate"].astype(float).mean())
        d["d_AoI_ms"] = float(df_grp["d_aoi_mean_ms"].astype(float).mean())
        by_regime_rows.append(d)
    if by_regime_rows:
        by_regime = pd.DataFrame(by_regime_rows)
        by_regime.to_csv(out_dir / f"{name}_verdict_by_regime.csv", index=False)
        print(f"[analyze] Wrote {name}_verdict_by_regime.csv ({len(by_regime)} rows)")


if __name__ == "__main__":
    main()