#!/usr/bin/env python3
"""
csi_report_estimator.py
=======================
Estimates NR CQI reports from the SINR measurements stored in a
csi_groupcast_eval SQLite database (psschRxUePhy.avrgSinr).

The script maps measured SINR → CQI index (3GPP TS 38.214 Table 5.2.2.1-3,
CQI Table 1) per time window and per TX→RX link, producing a time series
of CSI reports that a receiver would have fed back.

Usage:
    python3 csi_report_estimator.py <db_path> [--window_ms 500]
                                    [--all-pairs] [--csv out.csv]
                                    [--no-text]

Notes:
  - avrgSinr in psschRxUePhy is stored in LINEAR scale → converted to dB.
  - For accurate channel CSI (free of collision interference) use a database
    produced with bwPRBs=10 (collision rate ≈ 7%).
  - CQI 0 means SINR is below the lowest entry (< -6.7 dB): no transmission
    recommended.
"""

import sqlite3
import math
import argparse
import csv
import sys
import os
from collections import defaultdict


# ---------------------------------------------------------------------------
# CQI table — 3GPP TS 38.214 Table 5.2.2.1-3 (CQI Table 1)
# (cqi_index, modulation_order, code_rate_x1024, min_sinr_dB)
# SINR thresholds are the Shannon-capacity-derived values widely used in
# open-source NR simulators (target BLER ≤ 10 %).
# ---------------------------------------------------------------------------
CQI_TABLE = [
    ( 1, 2,  78,  -6.7),  # QPSK
    ( 2, 2, 120,  -4.7),
    ( 3, 2, 193,  -2.3),
    ( 4, 2, 308,   0.2),
    ( 5, 2, 449,   2.4),
    ( 6, 2, 602,   4.3),
    ( 7, 4, 378,   5.9),  # 16-QAM
    ( 8, 4, 490,   8.1),
    ( 9, 4, 616,  10.3),
    (10, 6, 466,  11.7),  # 64-QAM
    (11, 6, 567,  14.1),
    (12, 6, 666,  16.3),
    (13, 6, 772,  18.7),
    (14, 6, 873,  21.0),
    (15, 6, 948,  22.7),
]

MOD_NAME = {2: "QPSK", 4: "16QAM", 6: "64QAM"}


def sinr_to_cqi(sinr_db):
    """Return CQI index (0 = out-of-range / too low) for a given SINR (dB)."""
    if math.isnan(sinr_db) or sinr_db < CQI_TABLE[0][3]:
        return 0
    best = 1
    for (cqi, mod, cr, thr) in CQI_TABLE:
        if sinr_db >= thr:
            best = cqi
    return best


def cqi_info(cqi):
    if cqi == 0:
        return "—", "—", 0.0
    c = CQI_TABLE[cqi - 1]
    modname = MOD_NAME[c[1]]
    cr = c[2] / 1024.0
    spectral_eff = c[1] * cr
    return modname, f"{c[2]}/1024", spectral_eff


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
    idx = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * p / 100.0)))
    return sorted_vals[idx]


# ---------------------------------------------------------------------------
# data loading
# ---------------------------------------------------------------------------

def load_sinr_rows(db_path, tx_rnti=None, rx_rnti=None):
    """Return list of (time_ms, sinr_dB) sorted by time."""
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    clauses = ["avrgSinr > 0"]
    params  = []
    if tx_rnti is not None:
        clauses.append("txRnti = ?"); params.append(tx_rnti)
    if rx_rnti is not None:
        clauses.append("rnti = ?");   params.append(rx_rnti)
    where = " AND ".join(clauses)
    cur.execute(
        f"SELECT timeMs, avrgSinr FROM psschRxUePhy WHERE {where} ORDER BY timeMs;",
        params,
    )
    rows = [(r[0], to_db(r[1])) for r in cur.fetchall()]
    conn.close()
    return rows


def load_all_pairs(db_path):
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    cur.execute(
        "SELECT DISTINCT txRnti, rnti FROM psschRxUePhy "
        "WHERE avrgSinr > 0 ORDER BY txRnti, rnti;"
    )
    pairs = cur.fetchall()
    conn.close()
    return pairs


# ---------------------------------------------------------------------------
# windowed CSI estimation
# ---------------------------------------------------------------------------

def windowed_cqi(rows, window_ms):
    """
    Return list of (t_mid_ms, n, cqi_p10, cqi_p50, cqi_p90,
                    sinr_p10_dB, sinr_p50_dB, sinr_p90_dB)
    one entry per time window.
    """
    if not rows:
        return []
    t_min, t_max = rows[0][0], rows[-1][0]
    results = []
    t_lo = t_min
    while t_lo <= t_max:
        t_hi  = t_lo + window_ms
        t_mid = t_lo + window_ms / 2.0
        bucket = sorted(
            sinr for (t, sinr) in rows if t_lo <= t < t_hi and not math.isnan(sinr)
        )
        if bucket:
            p10 = percentile(bucket, 10)
            p50 = percentile(bucket, 50)
            p90 = percentile(bucket, 90)
            results.append((
                t_mid, len(bucket),
                sinr_to_cqi(p10), sinr_to_cqi(p50), sinr_to_cqi(p90),
                p10, p50, p90,
            ))
        t_lo = t_hi
    return results


# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------

MOD_CHAR = {0: "—", 2: "Q", 4: "16", 6: "64"}  # compact mod label

def _cqi_bar(cqi, width=15):
    if cqi == 0:
        return "░" * width
    filled = int(round(cqi / 15.0 * width))
    return "█" * filled + "░" * (width - filled)


def print_csi_table(windows, label="ALL PAIRS"):
    if not windows:
        print("  (no data)")
        return
    print(f"\n{'─'*110}")
    print(f"  {label}")
    print(f"{'─'*110}")
    print(f"  {'t_ms':>8}  {'n':>5}  "
          f"{'SINR_p10':>9}  {'CQI_p10':>7}  "
          f"{'SINR_p50':>9}  {'CQI_p50':>7}  {'mod':>5}  {'CR':>9}  {'SE':>5}  "
          f"{'SINR_p90':>9}  {'CQI_p90':>7}  "
          f"  CQI_p50 trend")
    print(f"  {'─'*8}  {'─'*5}  "
          f"{'─'*9}  {'─'*7}  "
          f"{'─'*9}  {'─'*7}  {'─'*5}  {'─'*9}  {'─'*5}  "
          f"{'─'*9}  {'─'*7}  {'─'*15}")
    for t_ms, n, cq10, cq50, cq90, s10, s50, s90 in windows:
        mod, cr_str, se = cqi_info(cq50)
        bar = _cqi_bar(cq50)
        print(f"  {t_ms:>8.0f}  {n:>5d}  "
              f"{s10:>9.1f}  {cq10:>7d}  "
              f"{s50:>9.1f}  {cq50:>7d}  {mod:>5}  {cr_str:>9}  {se:>5.2f}  "
              f"{s90:>9.1f}  {cq90:>7d}  "
              f"  {bar}")


def print_csi_summary(windows, label=""):
    if not windows:
        return
    cqis = [cq50 for (_, _, _, cq50, *_) in windows]
    drift = max(cqis) - min(cqis)
    mods  = set()
    for c in cqis:
        if c > 0:
            mods.add(MOD_NAME.get(CQI_TABLE[c-1][1], "?"))
    mod_str = "/".join(sorted(mods))
    print(f"  {label:45s}  CQI_p50: {min(cqis):2d}→{max(cqis):2d}  "
          f"drift={drift:2d}  mod={mod_str}")


def write_csv(windows, path, label="all"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label", "t_ms", "n",
                    "cqi_p10", "cqi_p50", "cqi_p90",
                    "sinr_p10_dB", "sinr_p50_dB", "sinr_p90_dB"])
        for t_ms, n, cq10, cq50, cq90, s10, s50, s90 in windows:
            w.writerow([label, f"{t_ms:.1f}", n,
                        cq10, cq50, cq90,
                        f"{s10:.3f}", f"{s50:.3f}", f"{s90:.3f}"])


def print_cqi_histogram(windows):
    """Print CQI frequency histogram across all time windows."""
    from collections import Counter
    counts = Counter(cq50 for (_, _, _, cq50, *_) in windows)
    total = sum(counts.values())
    print(f"\n  CQI distribution across {total} time windows:")
    print(f"  {'CQI':>4}  {'mod':>5}  {'CR':>9}  {'SE':>5}  {'count':>6}  {'pct':>6}  bar")
    print(f"  {'─'*4}  {'─'*5}  {'─'*9}  {'─'*5}  {'─'*6}  {'─'*6}  {'─'*20}")
    for cqi in range(16):
        cnt = counts.get(cqi, 0)
        if cnt == 0:
            continue
        mod, cr_str, se = cqi_info(cqi)
        pct = 100.0 * cnt / total
        bar = "█" * int(round(pct / 5))
        print(f"  {cqi:>4}  {mod:>5}  {cr_str:>9}  {se:>5.2f}  "
              f"{cnt:>6d}  {pct:>5.1f}%  {bar}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("db",           help="Path to .db file from csi_groupcast_eval")
    ap.add_argument("--window_ms",  type=float, default=500.0,
                    help="Time window width in ms (default 500)")
    ap.add_argument("--csv",        default="",
                    help="Path for CSV output")
    ap.add_argument("--all-pairs",  action="store_true",
                    help="Per-(txRnti,rxRnti) CQI drift summary")
    ap.add_argument("--pair",       nargs=2, type=int,
                    metavar=("TX_RNTI", "RX_RNTI"),
                    help="Focus on a single TX→RX pair")
    ap.add_argument("--no-text",    action="store_true",
                    help="Suppress text table")
    args = ap.parse_args()

    if not os.path.isfile(args.db):
        sys.exit(f"ERROR: database not found: {args.db}")

    print(f"Database : {args.db}")
    print(f"Window   : {args.window_ms:.0f} ms")
    print()
    print("CQI reference table (3GPP TS 38.214, Table 5.2.2.1-3):")
    print(f"  {'CQI':>4}  {'Mod':>6}  {'CodeRate':>9}  {'SE (b/s/Hz)':>12}  {'Min SINR (dB)':>14}")
    print(f"  {'─'*4}  {'─'*6}  {'─'*9}  {'─'*12}  {'─'*14}")
    for (cqi, mod, cr, thr) in CQI_TABLE:
        se = mod * cr / 1024.0
        print(f"  {cqi:>4}  {MOD_NAME[mod]:>6}  {cr:>4}/1024  {se:>12.4f}  {thr:>14.1f}")

    # ---- aggregate ----
    rows = load_sinr_rows(args.db,
                          tx_rnti=args.pair[0] if args.pair else None,
                          rx_rnti=args.pair[1] if args.pair else None)
    if not rows:
        sys.exit("ERROR: no SINR rows found (avrgSinr > 0)")

    t_span_s = (rows[-1][0] - rows[0][0]) / 1000.0
    print(f"\nRows     : {len(rows):,}  over {t_span_s:.2f} s  "
          f"({rows[0][0]:.0f}–{rows[-1][0]:.0f} ms)")

    windows = windowed_cqi(rows, args.window_ms)
    label = (f"TX_RNTI={args.pair[0]} → RX_RNTI={args.pair[1]}"
             if args.pair else "ALL PAIRS AGGREGATE")

    if not args.no_text:
        print_csi_table(windows, label=label)
        print_cqi_histogram(windows)

    if args.csv:
        write_csv(windows, args.csv, label=label)
        print(f"\nCSV written → {args.csv}")

    # ---- drift summary ----
    print(f"\n{'─'*100}")
    print("  CSI DRIFT SUMMARY  (how much does the recommended CQI shift over the simulation?)")
    print(f"{'─'*100}")
    print_csi_summary(windows, label=label)

    # ---- per-pair ----
    if args.all_pairs:
        pairs = load_all_pairs(args.db)
        print(f"\n  Per-(TX,RX) pair CQI drift  ({len(pairs)} pairs):")
        print(f"  {'TX':>6}  {'RX':>6}  {'CQI_min':>7}  {'CQI_max':>7}  "
              f"{'drift':>5}  {'start':>5}  {'end':>5}  {'mod_range'}")
        print(f"  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*7}  "
              f"{'─'*5}  {'─'*5}  {'─'*5}  {'─'*20}")

        pair_data = []
        for (tx_r, rx_r) in pairs:
            pr = load_sinr_rows(args.db, tx_rnti=tx_r, rx_rnti=rx_r)
            pw = windowed_cqi(pr, args.window_ms)
            if len(pw) < 2:
                continue
            cqis = [cq50 for (_, _, _, cq50, *_) in pw]
            drift = max(cqis) - min(cqis)
            mods  = set()
            for c in cqis:
                if c > 0:
                    mods.add(MOD_NAME.get(CQI_TABLE[c-1][1], "?"))
            pair_data.append((drift, tx_r, rx_r, cqis[0], cqis[-1],
                              min(cqis), max(cqis), "/".join(sorted(mods))))

        pair_data.sort(reverse=True)
        for drift, tx_r, rx_r, c0, c1, cmin, cmax, mod_str in pair_data[:20]:
            print(f"  {tx_r:>6}  {rx_r:>6}  {cmin:>7d}  {cmax:>7d}  "
                  f"{drift:>5d}  {c0:>5d}  {c1:>5d}  {mod_str}")


if __name__ == "__main__":
    main()
