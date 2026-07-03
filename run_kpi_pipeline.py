#!/usr/bin/env python3
"""
Run kpi_postprocess.py on every <sim_dir>/*.db under --runs-root,
in parallel, writing per-sim outputs to <out-dir>/<sim_name>/.

Usage:
  python3 run_kpi_pipeline.py \
      --runs-root results_relay_storm_eval \
      --out-dir  kpi_results \
      --workers  8
"""
import argparse
import fnmatch
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Locate kpi_postprocess.py relative to this file
HERE = Path(__file__).resolve().parent
KPI_DIR = HERE / "scratch_overlay" / "common" / "kpi"
KPI_SCRIPT = KPI_DIR / "kpi_postprocess.py"

# Match "seedN" at the end of the dir name (or anywhere as a fallback).
_SEED_RE = re.compile(r"_seed(\d+)\b")


def parse_seed(sim_name: str) -> int:
    """Extract seed (used as RUN id) from sim dir name like 'baseline_s100_ivd29_mcd100_seed21'."""
    m = _SEED_RE.search(sim_name)
    return int(m.group(1)) if m else 1


def process_one(sim_dir: Path, out_root: Path, range_m: float, bin_spec: str,
                warmup_s: float, kpis: str, scope: str) -> tuple[str, str]:
    """Run kpi_postprocess.py on a single sim directory.

    Returns (sim_name, status) where status is 'ok' | 'skip' | 'err:<msg>'.
    """
    sim_name = sim_dir.name
    db_files = sorted(sim_dir.glob("*.db"))
    if not db_files:
        return sim_name, "skip:no_db"
    db_path = db_files[0]
    sim_out = out_root / sim_name
    sim_out.mkdir(parents=True, exist_ok=True)
    # Skip if already done (kpi_postprocess wrote 7+ KPI files, not just summary).
    n_kpi = sum(1 for p in sim_out.glob(f"{sim_name}_*.csv")
                if not p.name.endswith("_summary.csv"))
    if n_kpi >= 7:
        return sim_name, "skip:already_done"
    # Copy the per-cell summary CSV (if it exists) so downstream tools
    # (join_per_run, sweep_aggregate) can read seed/run config.
    for summary in sim_dir.glob("*_summary.csv"):
        (sim_out / summary.name).write_bytes(summary.read_bytes())
    run_id = parse_seed(sim_name)
    cmd = [
        sys.executable, str(KPI_SCRIPT),
        "--db", str(db_path),
        "--out-dir", str(sim_out),
        "--tag", sim_name,
        "--range", str(range_m),
        "--bin-spec", bin_spec,
        "--warmup", str(warmup_s),
        "--scope", scope,
        "--kpis", kpis,
        "--seed", "1",          # this corpus uses SEED=1 for all sims
        "--run",  str(run_id),  # RUN id == seed number
    ]
    env = {**os.environ, "PYTHONPATH": str(KPI_DIR)}
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return sim_name, "ok"
        # Print stderr first 300 chars for debugging
        return sim_name, f"err:rc={r.returncode} stderr={r.stderr[-300:]}"
    except subprocess.TimeoutExpired:
        return sim_name, "err:timeout"
    except Exception as e:
        return sim_name, f"err:ex={e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", required=True, type=Path,
                    help="Directory containing per-sim subdirs")
    ap.add_argument("--out-dir", required=True, type=Path,
                    help="Directory to write <sim_name>/<tag>_<kpi>.csv")
    ap.add_argument("--workers", type=int, default=max(1, os.cpu_count() // 2))
    ap.add_argument("--range", type=float, default=320.0)
    ap.add_argument("--bin-spec", type=str, default="20:500/20")
    ap.add_argument("--warmup", type=float, default=1.0)
    ap.add_argument("--scope", type=str, default="auto")
    ap.add_argument("--kpis", type=str, default="all")
    ap.add_argument("--pattern", type=str, default="*",
                    help="Glob filter for sim subdir names (e.g. 'baseline_*')")
    args = ap.parse_args()

    if not KPI_SCRIPT.exists():
        print(f"FATAL: kpi_postprocess.py not found at {KPI_SCRIPT}", file=sys.stderr)
        sys.exit(1)

    sim_dirs = sorted(
        d for d in args.runs_root.iterdir()
        if d.is_dir()
        and fnmatch.fnmatch(d.name, args.pattern)
        and list(d.glob("*.db"))
    )
    print(f"[runner] Found {len(sim_dirs)} sim dirs under {args.runs_root} "
          f"(pattern={args.pattern!r}); using {args.workers} workers")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    n_ok = n_skip = n_err = 0
    errors: list[tuple[str, str]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = [
            ex.submit(process_one, d, args.out_dir, args.range, args.bin_spec,
                      args.warmup, args.kpis, args.scope)
            for d in sim_dirs
        ]
        for i, fut in enumerate(as_completed(futs), 1):
            sim_name, status = fut.result()
            if status == "ok":
                n_ok += 1
            elif status.startswith("skip"):
                n_skip += 1
            else:
                n_err += 1
                errors.append((sim_name, status))
            if i % 50 == 0 or i == len(futs):
                rate = i / (time.time() - t0)
                print(f"[runner] {i}/{len(futs)} "
                      f"ok={n_ok} skip={n_skip} err={n_err}  "
                      f"({rate:.1f} sims/s, ETA {(len(futs)-i)/rate:.0f}s)")

    elapsed = time.time() - t0
    print(f"\n[runner] Done in {elapsed:.1f}s: "
          f"ok={n_ok} skip={n_skip} err={n_err} "
          f"({len(sim_dirs)/elapsed:.1f} sims/s)")
    if errors:
        print(f"\n[runner] First 20 errors:")
        for s, st in errors[:20]:
            print(f"  {s}: {st}")


if __name__ == "__main__":
    main()
