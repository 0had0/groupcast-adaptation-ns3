#!/usr/bin/env python3
"""
join_per_run.py
===============
Combine per-seed registry-based KPI files with the per-cell summary CSV.

Layout convention (post-refactor):

    <runs_root>/
      <runId>/
        <runId>.db
        <runId>_summary.csv             # per-cell config (one row)
        <runId>_prr_type2.csv           # per-link PRR
        <runId>_aoi.csv                 # per-link AoI
        <runId>_throughput.csv          # per-link throughput
        <runId>_harq.csv                # per-rx ACK/NACK
        <runId>_ch_occupancy.csv        # per-tx occupancy
        <runId>_prr_type1.bin.csv       # per-tx bin (aggregate, broadcast)
        <runId>_prr_type1.cdf.csv       # per-pkt (broadcast)
        <runId>_pir_type1.cdf.csv       # per-link PIR samples
        <runId>_pir_type2.cdf.csv       # per-link PIR samples (intended)

The output CSV is a "wide" table — one row per (tx_node, rx_node) per run,
with summary columns broadcast as constants. This is the format consumed by
``aggregate_baseline.aggregate_cell``.
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


# --------------------------------------------------------------------------- #
# Tag handling
# --------------------------------------------------------------------------- #

SEED_TAG_RE = re.compile(r"_seed(?P<seed>\d+)(?:_mcd\d+)?$")


def split_seed_tag(run_id: str) -> tuple[str, Optional[int]]:
    """Split *run_id* into ``(clean_tag, seed_or_None)``."""
    m = SEED_TAG_RE.search(run_id)
    if not m:
        return run_id, None
    seed = int(m.group("seed"))
    clean = run_id[: m.start()]
    return clean, seed


def strip_seed_tag(run_id: str) -> str:
    return split_seed_tag(run_id)[0]


def _infer_tag(run_dir: Path) -> str:
    summary_candidates = sorted(run_dir.glob("*_summary.csv"))
    if summary_candidates:
        try:
            s = pd.read_csv(summary_candidates[0], nrows=1)
            if "runId" in s.columns and isinstance(s["runId"].iloc[0], str):
                return s["runId"].iloc[0]
        except Exception:
            pass
    return run_dir.name


# --------------------------------------------------------------------------- #
# Per-KPI link keys
# --------------------------------------------------------------------------- #

# KPIs that are emitted on a per-(tx_node_id, rx_node_id) row.
LINK_KEYS_KPIS = {
    "prr_type2":      ["prr_mean", "prr_worst", "prr_best", "n_rx_success"],
    "aoi":            ["aoi_mean_ms", "aoi_p5_ms", "aoi_p95_ms", "aoi_max_ms", "aoi_min_ms"],
    "throughput":     ["thput_kbps", "bytes_rx", "n_rx"],
}

# KPIs that are emitted per-tx_node only.
TX_KEYS_KPIS = {
    "ch_occupancy":   ["ch_occupancy", "subch_slots_used", "subch_slots_total", "subch_slots_per_pkt"],
}

# KPIs emitted per-rx_node only.
RX_KEYS_KPIS = {
    "harq":           ["harq_ack", "harq_nack", "harq_nack_rate"],
}

# KPIs that are scalar per sim (one row total). They are broadcast across
# all (tx_node_id, rx_node_id) rows.
SCALAR_KPIS = {
    "storm":          ["storm_ratio", "n_retx", "n_new", "hop1_retx", "leader_l2_id"],
    "psr":            ["psr", "forwarded_count", "leader_gen_count"],
    "coll":           ["coll_rate", "n_colliding", "n_leader_tbs", "n_relay_tbs"],
}


# --------------------------------------------------------------------------- #
# Per-run combine
# --------------------------------------------------------------------------- #

SUMMARY_IDENTITY = [
    "runId", "seed", "numUEs", "speedKmh", "ivd", "mcrMeters",
    "harqOption", "traffic", "mu", "numSubchannels", "bwPRBs",
    "mcs", "psfchPeriod", "simTime",
]


def _find_kpi_files(run_dir: Path) -> dict:
    """Map of kpi_id -> list of CSV files. Empty if none."""
    found: dict = {}
    for p in sorted(run_dir.glob("*_*.csv")):
        name = p.name
        if name.endswith("_summary.csv"):
            continue
        # Match pattern: <tag>_<kpi_id>.csv or <tag>_<kpi_id>.<kind>.csv
        for kpi_id in LINK_KEYS_KPIS | TX_KEYS_KPIS | RX_KEYS_KPIS | SCALAR_KPIS:
            # e.g. testrun_prr_type2.csv → kpi_id=prr_type2
            # e.g. testrun_prr_type1.bin.csv → kpi_id=prr_type1
            # e.g. testrun_pir_type1.cdf.csv → kpi_id=pir_type1
            # We match against base name = name without "<tag>_" prefix.
            if name.endswith(f"_{kpi_id}.csv") or f"_{kpi_id}." in name:
                found.setdefault(kpi_id, []).append(p)
                break
    return found


def _safe_read(path: Path) -> Optional[pd.DataFrame]:
    try:
        return pd.read_csv(path)
    except Exception:
        return None


def combine_run(
    run_dir: Path | str,
    *,
    src_dir_column: str = "srcDir",
    seed_column: str = "seed",
    run_id_column: str = "runId",
) -> pd.DataFrame:
    """Combine one run directory's per-KPI CSVs + ``*_summary.csv``.

    Returns a wide DataFrame: one row per (tx_node_id, rx_node_id), with
    summary columns broadcast as constants, runId (seed-stripped), seed
    (extracted from runId suffix), and srcDir added.
    """
    run_dir = Path(run_dir).resolve()
    tag = _infer_tag(run_dir)
    summary_path = run_dir / f"{tag}_summary.csv"

    if not summary_path.exists():
        raise FileNotFoundError(f"Missing summary CSV: {summary_path}")

    summary = pd.read_csv(summary_path)
    if len(summary) != 1:
        raise ValueError(
            f"Expected 1 row in {summary_path}, got {len(summary)}"
        )

    summary_row = summary.iloc[0].to_dict()
    full_run_id = str(summary_row.get("runId", tag))
    clean_run_id, arm_seed = split_seed_tag(full_run_id)

    broadcast = {
        col: summary_row[col]
        for col in summary.columns
        if col not in ("runId", "seed")
    }

    # Find KPI files.
    kpi_files = _find_kpi_files(run_dir)

    # Start with the link-keyed KPI as the base table. If none, start empty.
    base_df: Optional[pd.DataFrame] = None
    for kpi_id, cols in LINK_KEYS_KPIS.items():
        for path in kpi_files.get(kpi_id, []):
            df = _safe_read(path)
            if df is None or len(df) == 0:
                continue
            # Rename columns to qualified names: e.g. prr_mean → prr_type2_mean.
            ren = {c: f"{kpi_id}_{c}" for c in cols if c in df.columns}
            df = df.rename(columns=ren)
            # Keep only the join key + renamed columns.
            keep = ["tx_node_id", "rx_node_id"] + list(ren.values())
            df = df[[c for c in keep if c in df.columns]]
            if base_df is None:
                base_df = df
            else:
                base_df = pd.merge(base_df, df, on=["tx_node_id", "rx_node_id"],
                                   how="outer")

    if base_df is None:
        # No link-keyed KPI files found — synthesize a single placeholder row.
        base_df = pd.DataFrame({
            "tx_node_id": [pd.NA],
            "rx_node_id": [pd.NA],
        })

    # Merge in tx-keyed KPIs (ch_occupancy) by tx_node_id.
    for kpi_id, cols in TX_KEYS_KPIS.items():
        for path in kpi_files.get(kpi_id, []):
            df = _safe_read(path)
            if df is None or len(df) == 0:
                continue
            ren = {c: f"{kpi_id}_{c}" for c in cols if c in df.columns}
            df = df.rename(columns=ren)
            keep = ["tx_node_id"] + list(ren.values())
            df = df[[c for c in keep if c in df.columns]]
            base_df = pd.merge(base_df, df, on="tx_node_id", how="outer")

    # Merge in rx-keyed KPIs (harq) by rx_node_id.
    for kpi_id, cols in RX_KEYS_KPIS.items():
        for path in kpi_files.get(kpi_id, []):
            df = _safe_read(path)
            if df is None or len(df) == 0:
                continue
            ren = {c: f"{kpi_id}_{c}" for c in cols if c in df.columns}
            df = df.rename(columns=ren)
            # harq uses rx_node_id; rename it for the join.
            if "rx_node_id" not in df.columns:
                # harq uses rxNodeId in the original kpi file; the kpi renames it
                # to rx_node_id internally. So we're fine.
                continue
            keep = ["rx_node_id"] + list(ren.values())
            df = df[[c for c in keep if c in df.columns]]
            base_df = pd.merge(base_df, df, on="rx_node_id", how="outer")

    # Merge in scalar KPIs (storm, psr, coll). Each is a single-row CSV;
    # we take row 0 (or aggregate per relay if psr has multiple rows) and
    # broadcast across all (tx, rx) rows.
    for kpi_id, cols in SCALAR_KPIS.items():
        for path in kpi_files.get(kpi_id, []):
            df = _safe_read(path)
            if df is None or len(df) == 0:
                continue
            ren = {c: f"{kpi_id}_{c}" for c in cols if c in df.columns}
            df = df.rename(columns=ren)
            # If multiple rows (e.g. psr has one row per relay), take the
            # first row only for broadcasting. Multi-relay scenarios can be
            # handled later if needed.
            df = df.iloc[0:1] if len(df) > 0 else df
            for col in ren.values():
                if col in df.columns:
                    base_df[col] = df[col].iloc[0]

    # Broadcast summary columns.
    out = base_df.copy()
    for col, val in broadcast.items():
        out[col] = val

    # Seed column.
    if arm_seed is not None:
        out[seed_column] = arm_seed
    elif seed_column not in out.columns:
        out[seed_column] = pd.NA

    out[run_id_column] = clean_run_id
    out[src_dir_column] = str(run_dir)

    # Column ordering.
    front = SUMMARY_IDENTITY + [src_dir_column]
    ordered = [c for c in front if c in out.columns]
    rest = [c for c in out.columns if c not in ordered]
    return out[ordered + rest]


# --------------------------------------------------------------------------- #
# Walk + concat
# --------------------------------------------------------------------------- #

def _iter_run_dirs(
    runs_root: Path,
    pattern: str,
    require_kpi: bool = True,
) -> Iterable[Path]:
    if not runs_root.is_dir():
        return
    for run_dir in sorted(runs_root.iterdir()):
        if not run_dir.is_dir():
            continue
        if pattern and not fnmatch.fnmatch(run_dir.name, pattern):
            continue
        # Treat a directory as a "run" if it has any non-summary *_*.csv files.
        has_kpi = any(
            p.name.endswith("_summary.csv") is False and p.suffix == ".csv"
            for p in run_dir.glob("*_*.csv")
        )
        if require_kpi and not has_kpi:
            continue
        yield run_dir


def join_all(
    runs_root: Path | str,
    out_csv: Path | str,
    *,
    pattern: str = "*_seed*",
    verbose: bool = True,
) -> int:
    runs_root = Path(runs_root)
    out_csv = Path(out_csv)
    dfs = []
    n_runs = 0
    for run_dir in _iter_run_dirs(runs_root, pattern):
        try:
            df = combine_run(run_dir)
        except FileNotFoundError as e:
            if verbose:
                print(f"  skip {run_dir.name}: {e}")
            continue
        dfs.append(df)
        n_runs += 1
    if not dfs:
        if verbose:
            print(f"[join_per_run] No runs found under {runs_root}")
        return 0
    full = pd.concat(dfs, ignore_index=True, sort=False)
    full.to_csv(out_csv, index=False)
    if verbose:
        print(f"[join_per_run] Wrote {len(full)} rows from {n_runs} runs → {out_csv}")
    return n_runs


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    p = argparse.ArgumentParser(
        description="Join per-run registry-based KPI files into one wide CSV.",
    )
    p.add_argument("--runs-root", required=True, type=Path,
                   help="Root directory containing per-run sub-directories.")
    p.add_argument("--out", required=True, type=Path,
                   help="Output CSV path.")
    p.add_argument("--pattern", default="*_seed*",
                   help="Glob pattern for run-dir names [default: *_seed*]")
    args = p.parse_args(argv)
    return join_all(args.runs_root, args.out, pattern=args.pattern)


if __name__ == "__main__":
    sys.exit(main() or 0)