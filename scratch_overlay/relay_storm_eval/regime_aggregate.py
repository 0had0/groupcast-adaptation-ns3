#!/usr/bin/env python3
"""
regime_aggregate.py — REGIME sweep post-processor.

Forked from agg_fixed_sweep.py for the controlled regime experiment
(`run_regime_study.sh`). Re-uses `compute_kpis` (now with the fixed relay-IP
lookup) so the relay-PRR bug fix carries over automatically.

Per run, additionally computes a `regime` label from the run's (relay_node,
mcd, ivd):
    out       : mcd <  relay_node * ivd
    worst     : relay_node*ivd <= mcd < (relay_node+1)*ivd
    not_worst : mcd >= (relay_node+1)*ivd

Outputs:
  regime_per_run.csv  — one row per (run); enables Wilcoxon within regime
  regime_aggregate.csv — one row per (speed, mcd, relay_node, regime) cell,
                         with the no-relay baseline prr_worst joined in.

Usage:
  python3 regime_aggregate.py [results_dir]    (default ./results_regime_study)
"""
import sys
import csv
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

# Import the bug-fixed KPI pipeline from the main aggregator
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agg_fixed_sweep import compute_kpis, parse_dir, open_db  # noqa: E402

BASELINE = Path(sys.argv[1] if len(sys.argv) > 1
                else "./results_regime_study").resolve()


def classify_regime(relay_node: int, mcd: float, ivd: float) -> str:
    if relay_node <= 0 or mcd <  relay_node * ivd:
        return "out"
    if mcd < (relay_node + 1) * ivd:
        return "worst"
    return "not_worst"


def run():
    dirs = sorted(d for d in BASELINE.iterdir() if d.is_dir() and parse_dir(d.name))
    print(f"Found {len(dirs)} run directories under {BASELINE}")

    per_run_rows = []     # full per-run detail (for stat tests)
    grouped = defaultdict(list)  # key=(speed, ivd, mcd, relay_node, regime) -> list of per-run dicts
    n_dirs = len(dirs)
    for i, d in enumerate(dirs, 1):
        info = parse_dir(d.name)
        db_files = sorted(d.glob("*.db"))
        if not db_files:
            print(f"  WARNING: no .db in {d.name}")
            continue

        try:
            conn = open_db(str(db_files[0]))
            kpi  = compute_kpis(conn, info)
            conn.close()
        except Exception as ex:
            print(f"  ERROR {d.name}: {ex}")
            continue

        regime = (classify_regime(info["relay_node"], info["mcd"], info["ivd"])
                  if not info["is_baseline"] else "baseline")
        row = {
            "runId":       d.name,
            "speed":       info["speed"],
            "ivd":         info["ivd"],
            "mcd":         info["mcd"],
            "relay_node":  info["relay_node"],
            "is_baseline": info["is_baseline"],
            "seed":        info["seed"],
            "regime":      regime,
            "relay_dist_m": info["relay_node"] * info["ivd"],
            "mcd_over_relay_dist": (info["mcd"] / (info["relay_node"] * info["ivd"])
                                    if info["relay_node"] > 0 and info["ivd"] > 0
                                    else float("nan")),
            "storm_ratio":  kpi["storm_ratio"],
            "hop1_retx":    kpi["hop1_retx"],
            "prr_mean":     kpi["prr_mean"],
            "prr_worst":    kpi["prr_worst"],
            "aoi_mean_ms":  kpi["aoi_mean_ms"],
            "harq_nack":    kpi["harq_nack"],
            "ch_occ":       kpi["ch_occ"],
            "psr":          kpi["psr"],
            "relay_decoded":   kpi["relay_decoded"],
            "relay_forwarded": kpi["relay_forwarded"],
            "leader_tx_total": kpi["leader_tx_total"],
        }
        per_run_rows.append(row)
        key = (row["speed"], row["ivd"], row["mcd"], row["relay_node"], row["regime"])
        grouped[key].append(row)
        if i % 30 == 0 or i == n_dirs:
            print(f"  [{i}/{n_dirs}] processed")

    # ── Per-run CSV ────────────────────────────────────────────────────────
    per_run_path = BASELINE / "regime_per_run.csv"
    if per_run_rows:
        with open(per_run_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(per_run_rows[0].keys()))
            w.writeheader()
            w.writerows(per_run_rows)
        print(f"\nWrote {len(per_run_rows)} rows → {per_run_path}")

    # ── Aggregate per (speed, ivd, mcd, relay_node, regime) ───────────────
    base_lookup = {
        (r["speed"], r["ivd"], r["mcd"]): r["prr_worst"]
        for r in per_run_rows if r["is_baseline"]
    }
    base_storm_lookup = {
        (r["speed"], r["ivd"], r["mcd"]): r["storm_ratio"]
        for r in per_run_rows if r["is_baseline"]
    }

    def _agg(vals):
        s = sorted(v for v in vals if v is not None)
        n = len(s)
        if n == 0:
            return (0.0, 0.0, 0.0, 0.0, 0.0)
        return (
            sum(s) / n, s[0], s[-1],
            s[max(0, int(n * 0.05) - 1)],
            s[min(n - 1, int(n * 0.95))],
        )

    agg_rows = []
    for key in sorted(grouped):
        sd = grouped[key]
        speed, ivd, mcd, relay_node, regime = key
        prr_worst_a = _agg([d["prr_worst"]   for d in sd])
        prr_mean_a  = _agg([d["prr_mean"]    for d in sd])
        storm_a     = _agg([d["storm_ratio"] for d in sd])
        aoi_a       = _agg([d["aoi_mean_ms"] for d in sd])
        nack_a      = _agg([d["harq_nack"]   for d in sd])
        occ_a       = _agg([d["ch_occ"]      for d in sd])
        psr_a       = _agg([d["psr"]         for d in sd if d["psr"] is not None])
        baseline_prr_worst = base_lookup.get((speed, ivd, mcd), None)
        baseline_storm     = base_storm_lookup.get((speed, ivd, mcd), None)
        agg_rows.append({
            "speed": speed, "ivd": ivd, "mcd": mcd, "relay_node": relay_node,
            "regime": regime,
            "relay_dist_m": relay_node * ivd,
            "mcd_over_relay_dist": (mcd / (relay_node * ivd)
                                    if relay_node > 0 and ivd > 0 else float("nan")),
            "n_seeds": len(sd),
            "prr_worst_mean": prr_worst_a[0],
            "prr_worst_std":  (stdev([d["prr_worst"] for d in sd])
                               if len(sd) > 1 else 0.0),
            "prr_mean_mean":  prr_mean_a[0],
            "prr_worst_min":  prr_worst_a[1],
            "prr_worst_max":  prr_worst_a[2],
            "storm_ratio_mean": storm_a[0],
            "storm_ratio_std": (stdev([d["storm_ratio"] for d in sd])
                                if len(sd) > 1 else 0.0),
            "aoi_mean_ms":  aoi_a[0],
            "harq_nack":    nack_a[0],
            "ch_occ":       occ_a[0],
            "psr_mean":     (psr_a[0] if any(d["psr"] is not None for d in sd) else None),
            "hop1_retx_mean": sum(d["hop1_retx"] for d in sd) / len(sd),
            "baseline_prr_worst_at_same_mcd": baseline_prr_worst,
            "baseline_storm_ratio_at_same_mcd": baseline_storm,
            "delta_prr_worst_vs_baseline": (
                prr_worst_a[0] - baseline_prr_worst
                if baseline_prr_worst is not None else None
            ),
        })

    agg_path = BASELINE / "regime_aggregate.csv"
    if agg_rows:
        with open(agg_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(agg_rows[0].keys()))
            w.writeheader()
            w.writerows(agg_rows)
        print(f"Wrote {len(agg_rows)} rows → {agg_path}")

    # ── Regime count summary ───────────────────────────────────────────────
    print("\n=== Regime counts (per relay_node × ivd) ===")
    by_op = defaultdict(lambda: defaultdict(int))
    for r in agg_rows:
        by_op[(r["relay_node"], r["ivd"])][r["regime"]] += 1
    for (rn, ivd), reg in sorted(by_op.items()):
        print(f"  relay={rn}  ivd={ivd}  "
              f"out={reg['out']:>2}  worst={reg.get('worst', 0):>2}  "
              f"not_worst={reg.get('not_worst', 0):>2}")


def stdev(vals):
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    return (sum((v - m) ** 2 for v in vals) / (n - 1)) ** 0.5


if __name__ == "__main__":
    run()
