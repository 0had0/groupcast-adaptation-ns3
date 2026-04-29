#!/usr/bin/env python3
"""
Read-only analysis of the in-progress sweep in /tmp.

For every completed run (identified by a `_run.csv` marker file with a
matching gzipped `.db.gz`), extracts headline KPIs and writes a master
summary CSV at <outDir>/_summary.csv. Skips raw `.db` files (those are
in-flight from the running sweep) and never modifies any input file.

Then prints:
  - coverage matrix across (mu, w, mcs, scenario, condition, seed)
  - per-flow PRR / BLER / collision quartiles
  - red flags: empty DBs, zero-PRR runs, all-collision runs

Usage:
  python3 analyze_sweep.py --tmp /Users/hadih/Documents/groupcast-adaptation-ns3/tmp
"""

import argparse
import csv
import gzip
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from collections import defaultdict

RUN_RE = re.compile(
    r"^(?P<scen>s\d)_(?P<cond>[a-zA-Z0-9]+)_mu(?P<mu>\d+)_w(?P<w>\d+)_mcs(?P<mcs>\d+)_seed(?P<seed>\d+)$"
)


def parse_run_id(stem: str):
    m = RUN_RE.match(stem)
    if not m:
        return None
    d = m.groupdict()
    return {
        "scen": d["scen"],
        "cond": d["cond"],
        "mu": int(d["mu"]),
        "w": int(d["w"]),
        "mcs": int(d["mcs"]),
        "seed": int(d["seed"]),
    }


def open_gz_db(db_gz: Path):
    """Decompress to a temp file and open as sqlite. Returns (conn, tmp_path)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    with gzip.open(db_gz, "rb") as fin:
        shutil.copyfileobj(fin, tmp, length=1 << 20)
    tmp.close()
    return sqlite3.connect(tmp.name), tmp.name


def extract_kpis(db_path_gz: Path, n_ues: int):
    """Extract headline KPIs from one compressed DB."""
    con, tmp_name = open_gz_db(db_path_gz)
    try:
        kpis = {}

        # Collision rate
        try:
            r = con.execute(
                "SELECT SUM(numOverlapping), SUM(totalTx) FROM simulPsschTx WHERE totalTx>0"
            ).fetchone()
            kpis["coll_rate"] = (r[0] / r[1]) if r and r[1] else None
            kpis["total_tx"] = r[1] if r else 0
        except sqlite3.OperationalError:
            kpis["coll_rate"] = None
            kpis["total_tx"] = 0

        # BLER
        try:
            r = con.execute(
                "SELECT SUM(psschCorrupt), COUNT(*) FROM psschRxUePhy"
            ).fetchone()
            kpis["pssch_bler"] = (r[0] / r[1]) if r and r[1] else None
            kpis["pssch_rx_count"] = r[1] if r else 0
        except sqlite3.OperationalError:
            kpis["pssch_bler"] = None
            kpis["pssch_rx_count"] = 0

        # Per-flow PRR (skip warmup at 3s)
        for port, label in [(8001, "cam"), (8002, "vid"), (8003, "bg")]:
            try:
                tx = con.execute(
                    "SELECT COUNT(*) FROM pktTxRx WHERE txRx='tx' AND dstPort=? AND timeSec>3",
                    (port,),
                ).fetchone()[0]
                rx = con.execute(
                    "SELECT COUNT(*) FROM pktTxRx WHERE txRx='rx' AND dstPort=? AND timeSec>3",
                    (port,),
                ).fetchone()[0]
                expected = tx * (n_ues - 1) if n_ues > 1 else tx
                kpis[f"prr_{label}"] = (rx / expected) if expected > 0 else None
                kpis[f"tx_{label}"] = tx
            except sqlite3.OperationalError:
                kpis[f"prr_{label}"] = None
                kpis[f"tx_{label}"] = 0

        # Group PRR = min over flows
        prrs = [kpis.get(f"prr_{l}") for l in ("cam", "vid", "bg")]
        prrs = [p for p in prrs if p is not None]
        kpis["prr_group"] = min(prrs) if prrs else None

        # HARQ feedback: ACK/NACK counts from slHarqFeedback, timeouts from slHarqTimeout
        try:
            ack = nack = 0
            for status, cnt in con.execute(
                "SELECT status, COUNT(*) FROM slHarqFeedback GROUP BY status"
            ).fetchall():
                s = str(status).strip().upper()
                if s == "ACK":
                    ack += cnt
                elif s == "NACK":
                    nack += cnt
            kpis["harq_ack"] = ack
            kpis["harq_nack"] = nack
            denom = ack + nack
            kpis["harq_ack_rate"] = (ack / denom) if denom > 0 else None
            kpis["harq_nack_rate"] = (nack / denom) if denom > 0 else None
        except sqlite3.OperationalError:
            kpis["harq_ack"] = 0
            kpis["harq_nack"] = 0
            kpis["harq_ack_rate"] = None
            kpis["harq_nack_rate"] = None

        try:
            r = con.execute("SELECT COUNT(*) FROM slHarqTimeout").fetchone()
            kpis["harq_timeout"] = r[0] if r else 0
        except sqlite3.OperationalError:
            kpis["harq_timeout"] = 0
        tx_total = kpis.get("total_tx") or 0
        kpis["harq_to_per_tx"] = (kpis["harq_timeout"] / tx_total) if tx_total > 0 else None

        # SINR mean (linear → dB)
        try:
            r = con.execute("SELECT AVG(avrgSinr) FROM psschRxUePhy").fetchone()
            import math
            kpis["sinr_mean_db"] = (
                10 * math.log10(r[0]) if r and r[0] and r[0] > 0 else None
            )
        except (sqlite3.OperationalError, ValueError):
            kpis["sinr_mean_db"] = None

        return kpis
    finally:
        con.close()
        os.unlink(tmp_name)


def num_ues_from_cond(scen: str, cond: str) -> int:
    """Replicate run_sweep.py's numUEs logic (defaults to 8)."""
    try:
        return int(cond[3:])  # 'nrx08' -> 8
    except (ValueError, IndexError):
        return 8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--tmp",
        default=str(Path(__file__).resolve().parents[3] / "tmp"),
        help="Sweep output directory",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="Output CSV path (default: <tmp>/_summary.csv)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N runs (debug)",
    )
    args = ap.parse_args()

    tmp = Path(args.tmp)
    out_csv = Path(args.out) if args.out else tmp / "_summary.csv"

    # Find all completed runs: those with a _run.csv AND a .db.gz
    run_csvs = sorted(tmp.glob("*_run.csv"))
    print(f"Found {len(run_csvs)} completed _run.csv markers")

    eligible = []
    skipped_inflight = 0
    skipped_missing = 0
    for rc in run_csvs:
        stem = rc.name[: -len("_run.csv")]
        db_gz = tmp / f"{stem}.db.gz"
        db_raw = tmp / f"{stem}.db"
        if db_raw.exists():
            skipped_inflight += 1  # Sweep is writing this; don't touch
            continue
        if not db_gz.exists():
            skipped_missing += 1
            continue
        eligible.append((stem, db_gz))

    print(
        f"  {len(eligible)} eligible (.db.gz present), "
        f"{skipped_inflight} skipped (raw .db in flight), "
        f"{skipped_missing} skipped (no DB at all)"
    )

    if args.limit:
        eligible = eligible[: args.limit]

    # Process each
    fieldnames = [
        "stem", "scen", "cond", "mu", "w", "mcs", "seed", "n_ues",
        "coll_rate", "pssch_bler", "sinr_mean_db",
        "prr_group", "prr_cam", "prr_vid", "prr_bg",
        "tx_cam", "tx_vid", "tx_bg",
        "total_tx", "pssch_rx_count",
        "harq_ack", "harq_nack", "harq_timeout",
        "harq_ack_rate", "harq_nack_rate", "harq_to_per_tx",
    ]
    rows = []
    failures = 0
    for i, (stem, db_gz) in enumerate(eligible, 1):
        meta = parse_run_id(stem)
        if not meta:
            failures += 1
            continue
        n = num_ues_from_cond(meta["scen"], meta["cond"])
        try:
            kpis = extract_kpis(db_gz, n)
        except Exception as e:
            failures += 1
            print(f"  ERROR on {stem}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        row = {"stem": stem, "n_ues": n, **meta, **kpis}
        rows.append({k: row.get(k) for k in fieldnames})
        if i % 200 == 0:
            print(f"  processed {i}/{len(eligible)}")

    # Write summary CSV
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\nWrote {out_csv} with {len(rows)} rows ({failures} failed extraction)")

    # ----- Diagnostics -----
    if not rows:
        print("No rows to analyse.")
        return

    print("\n" + "=" * 72)
    print("COVERAGE")
    print("=" * 72)
    by_scen_cond = defaultdict(set)
    by_phy = defaultdict(set)
    for r in rows:
        by_scen_cond[(r["scen"], r["cond"])].add(r["seed"])
        by_phy[(r["mu"], r["w"], r["mcs"])].add(r["seed"])
    print(f"  Distinct (scenario, condition): {len(by_scen_cond)}")
    print(f"  Distinct (mu, w, mcs):          {len(by_phy)}")
    seed_dist = defaultdict(int)
    for s in by_phy.values():
        seed_dist[len(s)] += 1
    print(f"  PHY combos by seed coverage:    {dict(sorted(seed_dist.items()))}")

    # PHY combos completely missing one or more seeds
    incomplete = sum(1 for s in by_phy.values() if len(s) < 5)
    print(f"  PHY combos with <5 seeds done:  {incomplete}/{len(by_phy)}")

    # ----- KPI quartiles -----
    print("\n" + "=" * 72)
    print("KPI DISTRIBUTION (across all eligible runs)")
    print("=" * 72)

    def quartiles(vals):
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return None
        n = len(vals)
        return {
            "min": vals[0],
            "p25": vals[n // 4],
            "med": vals[n // 2],
            "p75": vals[(3 * n) // 4],
            "max": vals[-1],
            "n": n,
        }

    for key in ["prr_group", "prr_cam", "prr_vid", "prr_bg",
                "coll_rate", "pssch_bler", "sinr_mean_db",
                "harq_ack_rate", "harq_nack_rate", "harq_to_per_tx"]:
        q = quartiles([r.get(key) for r in rows])
        if q is None:
            print(f"  {key:>14}: no data")
        else:
            print(
                f"  {key:>14}: min={q['min']:.3f} p25={q['p25']:.3f} "
                f"med={q['med']:.3f} p75={q['p75']:.3f} max={q['max']:.3f} (n={q['n']})"
            )

    # ----- Red flags -----
    print("\n" + "=" * 72)
    print("RED FLAGS")
    print("=" * 72)
    empty_db = sum(1 for r in rows if r.get("total_tx") == 0)
    zero_prr = sum(1 for r in rows if r.get("prr_group") is not None and r["prr_group"] == 0)
    full_coll = sum(1 for r in rows if r.get("coll_rate") is not None and r["coll_rate"] >= 0.99)
    no_pssch_rx = sum(1 for r in rows if r.get("pssch_rx_count") == 0)
    print(f"  runs with no PSSCH TX events recorded: {empty_db}")
    print(f"  runs with zero group PRR:              {zero_prr}")
    print(f"  runs with ≥99% collision rate:         {full_coll}")
    print(f"  runs with no PSSCH RX events:          {no_pssch_rx}")

    # ----- Best PHY by group size -----
    print("\n" + "=" * 72)
    print("BEST (mu, w, mcs) PER GROUP SIZE (max median groupPRR over seeds)")
    print("=" * 72)

    # Average across seeds for each (n, mu, w, mcs)
    agg = defaultdict(list)
    for r in rows:
        if r["scen"] != "s1":  # only group-size scenario
            continue
        if r["prr_group"] is None:
            continue
        agg[(r["n_ues"], r["mu"], r["w"], r["mcs"])].append(r["prr_group"])
    avg = {k: sum(v) / len(v) for k, v in agg.items() if len(v) >= 1}
    by_n = defaultdict(list)
    for (n, mu, w, mcs), p in avg.items():
        by_n[n].append((p, mu, w, mcs, len(agg[(n, mu, w, mcs)])))
    for n in sorted(by_n):
        top = sorted(by_n[n], reverse=True)[:3]
        print(f"  N={n:>2}: " + " | ".join(
            f"µ={mu} w={w:>3} mcs={mcs:>2} → PRR={p:.3f} ({k} seeds)"
            for p, mu, w, mcs, k in top
        ))


if __name__ == "__main__":
    main()
