#!/usr/bin/env python3
"""
kpi_postprocess.py — registry-based NR-SL V2X KPI extractor.

Usage:
  python3 kpi_postprocess.py --db run.db --out-dir /tmp/kpi \\
      --warmup 1.0 --range 320 --bin-spec 20:500/20 \\
      --kpis prr_type1,prr_type2,aoi --tag sim1

Defaults: --warmup 1.0, --sim-end auto, --range 320, --bin-spec 20:500/20,
          --seed 1, --run 1, --scope auto, --kpis all.

Adding a new KPI = adding a file in kpis/ + importing it in kpis/__init__.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Importing the subpackages populates the registry.
import kpis                          # noqa: F401  — registers all KPIs
from kpi.data import DataCache
from kpi.outcomes import Outcomes
from kpi.range import Ranges
from kpi.registry import KpiContext, KpiRegistry
from kpi.topology import Topology


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Registry-based NR-SL V2X KPI post-processor.",
    )
    p.add_argument("--db", required=True, type=Path,
                   help="Path to simulation .db file")
    p.add_argument("--out-dir", required=True, type=Path,
                   help="Output directory for KPI CSVs")
    p.add_argument("--warmup", type=float, default=1.0,
                   help="Warm-up duration (s) skipped from start [default: 1.0]")
    p.add_argument("--sim-end", type=float, default=None,
                   help="Simulation end (s). If omitted, inferred from data.")
    p.add_argument("--range", type=float, default=320.0,
                   help="Geographical range gate (m) [default: 320]")
    p.add_argument("--bin-spec", type=str, default="20:500/20",
                   help="Bin spec for PRR Type 1: <lo>:<hi>/<width> [default: 20:500/20]")
    p.add_argument("--scope", type=str, default="auto",
                   choices=["auto", "all", "geographic", "intended", "partitioned"],
                   help="Which receivers are in scope [default: auto]")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--run", type=int, default=1)
    p.add_argument("--kpis", type=str, default="all",
                   help="Comma-separated KPI ids, or 'all' [default: all]")
    p.add_argument("--tag", type=str, default=None,
                   help="Output filename prefix [default: db stem]")
    p.add_argument("--format", type=str, default="csv",
                   choices=["csv"],
                   help="Output format [default: csv]")
    return p.parse_args(argv)


def _infer_sim_end(cache: DataCache) -> float:
    pkt = cache.pkt_txrx()
    if pkt is not None and len(pkt) > 0:
        return float(pkt["timeSec"].max())
    return 0.0


def main(argv=None):
    args = _parse_args(argv)
    if not args.db.exists():
        print(f"[ERROR] DB not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    tag = args.tag or args.db.stem

    # Resolve KPI list.
    if args.kpis.lower() == "all":
        kpi_ids = sorted(KpiRegistry.all())
    else:
        kpi_ids = [s.strip() for s in args.kpis.split(",") if s.strip()]
        for kid in kpi_ids:
            if KpiRegistry.get(kid) is None:
                print(f"[ERROR] unknown KPI id: {kid}", file=sys.stderr)
                sys.exit(2)

    # Open data cache once for all KPIs. We need sim_end (data-dependent) before
    # we can construct the cache, so do a cheap probe first.
    with DataCache.open(args.db, seed=args.seed, run=args.run,
                        t_start=0.0, t_end=1e12) as probe:
        t_end = (args.sim_end if args.sim_end is not None
                 else _infer_sim_end(probe))
    t_start = args.warmup
    if t_end <= t_start:
        print(f"[ERROR] bad window [{t_start}, {t_end}]", file=sys.stderr)
        sys.exit(3)

    with DataCache.open(args.db, seed=args.seed, run=args.run,
                        t_start=t_start, t_end=t_end) as cache:
        ranges = Ranges.from_cli(str(args.range), bin_spec=args.bin_spec)
        topo = Topology.build(cache, ranges)
        outcomes = Outcomes(cache, topo)

        ctx = KpiContext(
            db_path=args.db,
            out_dir=args.out_dir,
            seed=args.seed, run=args.run,
            t_start=t_start, t_end=t_end,
            topology=topo, outcomes=outcomes, cache=cache,
            ranges=ranges, scope_mode=args.scope, args=args,
        )

        total_files = 0
        for kid in kpi_ids:
            spec = KpiRegistry.get(kid)
            results = spec.run(ctx)
            for r in results:
                # Filenames: <tag>_<base_filename>
                out_path = args.out_dir / f"{tag}_{r.filename}"
                if hasattr(r, "rows") and r.rows:
                    import csv
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(out_path, "w", newline="") as f:
                        cols = list(r.rows[0].keys())
                        w = csv.DictWriter(f, fieldnames=cols)
                        w.writeheader()
                        for row in r.rows:
                            w.writerow(row)
                total_files += 1
            print(f"  [{kid:<14}] {len(results)} file(s)")

    print(f"[kpi_postprocess] Wrote {total_files} file(s) to {args.out_dir}")


if __name__ == "__main__":
    main()