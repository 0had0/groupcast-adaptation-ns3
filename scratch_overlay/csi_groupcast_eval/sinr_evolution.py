#!/usr/bin/env python3
"""
sinr_evolution.py
=================
Extracts the SINR distribution from a csi_groupcast_eval SQLite database
and shows how it evolves over the SUMO simulation time.

Usage:
    python3 sinr_evolution.py <db_path> [--window_ms 500] [--pair TX_IP RX_IP]
                              [--csv out.csv] [--no-text]

The psschRxUePhy table stores avrgSinr in linear (not dB) scale.
Each row is one received PSSCH TB at a given simulation time.

Outputs:
  - Text table of per-window SINR percentiles (dB) to stdout
  - Optional CSV of the same data (--csv)
  - Optional per-TX→RX pair breakdown (--pair or --all-pairs)
"""

import sqlite3
import math
import argparse
import csv
import sys
import os


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def to_db(linear):
    if linear <= 0:
        return float("nan")
    return 10.0 * math.log10(linear)


def percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    idx = int(len(sorted_vals) * p / 100.0)
    idx = max(0, min(len(sorted_vals) - 1, idx))
    return sorted_vals[idx]


def stats(values_db):
    """Return dict of statistics for a list of dB values."""
    v = sorted(v for v in values_db if not math.isnan(v))
    if not v:
        return None
    n = len(v)
    mean = sum(v) / n
    var  = sum((x - mean) ** 2 for x in v) / n
    return {
        "n":    n,
        "min":  v[0],
        "p10":  percentile(v, 10),
        "p25":  percentile(v, 25),
        "p50":  percentile(v, 50),
        "p75":  percentile(v, 75),
        "p90":  percentile(v, 90),
        "max":  v[-1],
        "mean": mean,
        "std":  math.sqrt(var),
    }


def sparkline(value, lo, hi, width=20):
    """Simple ASCII progress bar for a value in [lo, hi]."""
    if hi == lo:
        return "─" * width
    frac = max(0.0, min(1.0, (value - lo) / (hi - lo)))
    filled = int(round(frac * width))
    return "█" * filled + "░" * (width - filled)


# ---------------------------------------------------------------------------
# data loading
# ---------------------------------------------------------------------------

def load_sinr_rows(db_path, filter_tx_rnti=None, filter_rx_rnti=None):
    """
    Return list of (time_ms, sinr_dB) tuples from psschRxUePhy.
    Optionally filter to a specific (txRnti, rnti) pair.
    """
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()

    # check columns available
    cur.execute("PRAGMA table_info(psschRxUePhy);")
    cols = {row[1] for row in cur.fetchall()}

    where_clauses = ["avrgSinr > 0"]
    params        = []

    if filter_tx_rnti is not None:
        where_clauses.append("txRnti = ?")
        params.append(filter_tx_rnti)
    if filter_rx_rnti is not None:
        where_clauses.append("rnti = ?")
        params.append(filter_rx_rnti)

    where = " AND ".join(where_clauses)
    cur.execute(f"SELECT timeMs, avrgSinr FROM psschRxUePhy WHERE {where} ORDER BY timeMs;",
                params)
    rows = [(r[0], to_db(r[1])) for r in cur.fetchall()]
    conn.close()
    return rows


def load_rnti_map(db_path):
    """Return dict rnti→IP from avrgPrr if available."""
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    try:
        cur.execute("SELECT rnti, Ip FROM avrgPrr;")
        m = {r[0]: r[1] for r in cur.fetchall()}
    except Exception:
        m = {}
    conn.close()
    return m


def get_all_pairs(db_path):
    """Return list of (txRnti, rxRnti) pairs present in psschRxUePhy."""
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    cur.execute("SELECT DISTINCT txRnti, rnti FROM psschRxUePhy WHERE avrgSinr > 0 ORDER BY txRnti, rnti;")
    pairs = cur.fetchall()
    conn.close()
    return pairs


# ---------------------------------------------------------------------------
# windowed analysis
# ---------------------------------------------------------------------------

def windowed_stats(rows, window_ms):
    """
    Group (time_ms, sinr_dB) rows into windows of width window_ms.
    Return list of (window_centre_ms, stats_dict).
    """
    if not rows:
        return []

    t_min = rows[0][0]
    t_max = rows[-1][0]

    results = []
    t_lo = t_min
    while t_lo <= t_max:
        t_hi   = t_lo + window_ms
        t_mid  = t_lo + window_ms / 2.0
        bucket = [sinr for (t, sinr) in rows if t_lo <= t < t_hi]
        s = stats(bucket)
        if s:
            results.append((t_mid, s))
        t_lo = t_hi

    return results


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------

HEADER_COLS = ["t_ms", "n", "min", "p10", "p25", "p50", "p75", "p90", "max", "mean", "std"]


def print_table(windows, label="ALL PAIRS"):
    if not windows:
        print("  (no data)")
        return

    all_p50 = [s["p50"] for _, s in windows]
    lo_p50  = min(all_p50)
    hi_p50  = max(all_p50)

    print(f"\n{'─'*100}")
    print(f"  {label}")
    print(f"{'─'*100}")
    print(f"  {'t_ms':>8}  {'n':>5}  {'min':>7}  {'p10':>7}  {'p25':>7}  {'p50':>7}  "
          f"{'p75':>7}  {'p90':>7}  {'max':>7}  {'mean':>7}  {'std':>6}  median trend")
    print(f"  {'─'*8}  {'─'*5}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}  "
          f"{'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*6}  {'─'*20}")

    for t_ms, s in windows:
        bar = sparkline(s["p50"], lo_p50, hi_p50, width=20)
        print(f"  {t_ms:>8.0f}  {s['n']:>5d}  "
              f"{s['min']:>7.1f}  {s['p10']:>7.1f}  {s['p25']:>7.1f}  {s['p50']:>7.1f}  "
              f"{s['p75']:>7.1f}  {s['p90']:>7.1f}  {s['max']:>7.1f}  "
              f"{s['mean']:>7.1f}  {s['std']:>6.1f}  {bar}")


def write_csv(windows, path, label="all"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label"] + HEADER_COLS)
        for t_ms, s in windows:
            w.writerow([label, f"{t_ms:.1f}", s["n"],
                        f"{s['min']:.3f}", f"{s['p10']:.3f}", f"{s['p25']:.3f}",
                        f"{s['p50']:.3f}", f"{s['p75']:.3f}", f"{s['p90']:.3f}",
                        f"{s['max']:.3f}", f"{s['mean']:.3f}", f"{s['std']:.3f}"])


def print_drift_summary(windows, label=""):
    """Print a one-line summary of how much the median SINR drifts."""
    if len(windows) < 2:
        return
    p50s  = [s["p50"] for _, s in windows]
    drift = max(p50s) - min(p50s)
    trend = p50s[-1] - p50s[0]
    sign  = "▲" if trend > 0.5 else ("▼" if trend < -0.5 else "≈")
    print(f"  {label:40s}  median drift: {drift:5.1f} dB   "
          f"start→end: {p50s[0]:.1f} → {p50s[-1]:.1f} dB  {sign}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db",          help="Path to .db file produced by csi_groupcast_eval")
    ap.add_argument("--window_ms", type=float, default=500.0,
                    help="Time window width in ms for per-window statistics (default 500)")
    ap.add_argument("--csv",       default="",
                    help="Path for CSV output (default: no CSV)")
    ap.add_argument("--all-pairs", action="store_true",
                    help="Also output per-(txRnti,rxRnti) pair drift summary")
    ap.add_argument("--pair",      nargs=2, type=int, metavar=("TX_RNTI", "RX_RNTI"),
                    help="Focus on a single (txRnti, rxRnti) pair")
    ap.add_argument("--no-text",   action="store_true",
                    help="Suppress text table (useful when only --csv is needed)")
    args = ap.parse_args()

    if not os.path.isfile(args.db):
        sys.exit(f"ERROR: database not found: {args.db}")

    print(f"Database : {args.db}")
    print(f"Window   : {args.window_ms:.0f} ms")

    # ---- aggregate (all pairs) ----
    rows = load_sinr_rows(args.db,
                          filter_tx_rnti=args.pair[0] if args.pair else None,
                          filter_rx_rnti=args.pair[1] if args.pair else None)

    if not rows:
        sys.exit("ERROR: no SINR rows found in psschRxUePhy (avrgSinr > 0)")

    t_span_s = (rows[-1][0] - rows[0][0]) / 1000.0
    print(f"Rows     : {len(rows):,}  over {t_span_s:.2f} s  "
          f"({rows[0][0]:.0f} – {rows[-1][0]:.0f} ms)")

    windows = windowed_stats(rows, args.window_ms)

    label = f"TX_RNTI={args.pair[0]} → RX_RNTI={args.pair[1]}" if args.pair else "ALL PAIRS AGGREGATE"

    if not args.no_text:
        print_table(windows, label=label)

    if args.csv:
        write_csv(windows, args.csv, label=label.replace(",", ";"))
        print(f"\nCSV written → {args.csv}")

    # ---- overall drift summary ----
    print(f"\n{'─'*100}")
    print("  DRIFT SUMMARY  (how much does the median SINR shift over the simulation?)")
    print(f"{'─'*100}")
    print_drift_summary(windows, label=label)

    # ---- per-pair breakdown ----
    if args.all_pairs:
        rnti_map = load_rnti_map(args.db)
        pairs    = get_all_pairs(args.db)
        print(f"\n  Per-(TX,RX) pair drift  ({len(pairs)} pairs):")
        print(f"  {'TX_RNTI':>8}  {'RX_RNTI':>8}  {'TX_IP':>12}  {'RX_IP':>12}  ", end="")
        print(f"{'drift_dB':>9}  {'start_p50':>10}  {'end_p50':>8}  trend")
        print(f"  {'─'*8}  {'─'*8}  {'─'*12}  {'─'*12}  {'─'*9}  {'─'*10}  {'─'*8}  {'─'*5}")

        pair_drifts = []
        for (tx_r, rx_r) in pairs:
            pr = load_sinr_rows(args.db, filter_tx_rnti=tx_r, filter_rx_rnti=rx_r)
            pw = windowed_stats(pr, args.window_ms)
            if len(pw) < 2:
                continue
            p50s  = [s["p50"] for _, s in pw]
            drift = max(p50s) - min(p50s)
            trend = p50s[-1] - p50s[0]
            sign  = "▲" if trend > 0.5 else ("▼" if trend < -0.5 else "≈")
            pair_drifts.append((drift, tx_r, rx_r, p50s[0], p50s[-1], sign))

        pair_drifts.sort(reverse=True)
        for drift, tx_r, rx_r, p0, p1, sign in pair_drifts[:20]:  # top-20 drifters
            tx_ip = rnti_map.get(tx_r, "?")
            rx_ip = rnti_map.get(rx_r, "?")
            print(f"  {tx_r:>8}  {rx_r:>8}  {tx_ip:>12}  {rx_ip:>12}  "
                  f"{drift:>9.1f}  {p0:>10.1f}  {p1:>8.1f}  {sign}")


if __name__ == "__main__":
    main()
