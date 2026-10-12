#!/usr/bin/env python3
"""check_sps.py -- Option-1 wrapper checks on the leader's transmissions under SPS.

Reads <tag>_ltx.csv[.gz] (one row per leader PSSCH transmission) of one or more runs and
reports, per run:
  retx_after_ack      retransmissions of a TB after an implicit ACK on it    (must be 0)
  retx_without_nack   retransmissions whose previous transmission had no NACK (must be 0)
  ack_tbs             TBs with an implicit-ACK decision
  ack_tbs_kept        ... followed by the next new transmission on the same HARQ id one
                      reservation period later (the ACK cancelled only that period's
                      retransmissions, not the grant)
  ack_tbs_resel       ... followed by a new transmission elsewhere (reselection: counter
                      expiry or SDU misfit, see the run CSV), or by none (end of run)
  periods_kept        share of new transmissions exactly one period after the previous
                      one on the same HARQ id

Usage: check_sps.py --rri-ms 100 run_dir_or_ltx_file [...]   (mu = 1: 2 slots per ms)
Exit status 1 if a must-be-0 check fails.
"""
import argparse
import csv
import gzip
import pathlib
import sys
from collections import defaultdict


def rows(path):
    op = gzip.open if str(path).endswith(".gz") else open
    with op(path, "rt") as fh:
        yield from csv.DictReader(fh)


def check(path, rri_slots):
    txs = list(rows(path))
    tb = []          # TB index of each transmission
    cur = {}         # harq -> TB index
    tbs = []         # per TB: harq, first slot, list of tx indices
    for i, r in enumerate(txs):
        h = int(r["harq_id"])
        if int(r["tx_in_tb"]) == 1 or h not in cur:
            cur[h] = len(tbs)
            tbs.append({"harq": h, "slot": int(r["slot_abs"]), "rb": r["rb_start"], "tx": []})
        tbs[cur[h]]["tx"].append(i)
        tb.append(cur[h])
    retx_after_ack = retx_no_nack = 0
    for t in tbs:
        for a, b in zip(t["tx"], t["tx"][1:]):
            if int(txs[a]["decision"]) == 1:
                retx_after_ack += 1
            if int(txs[a]["n_nack"]) == 0:
                retx_no_nack += 1
    nxt = {}         # TB -> next TB on the same HARQ id
    last = {}
    for k, t in enumerate(tbs):
        if t["harq"] in last:
            nxt[last[t["harq"]]] = k
        last[t["harq"]] = k
    ack = kept = resel = 0
    for k, t in enumerate(tbs):
        if not any(int(txs[i]["decision"]) == 1 for i in t["tx"]):
            continue
        ack += 1
        n = nxt.get(k)
        if n is not None and tbs[n]["slot"] - t["slot"] == rri_slots:
            kept += 1
        else:
            resel += 1
    per = sum(1 for k, n in nxt.items() if tbs[n]["slot"] - tbs[k]["slot"] == rri_slots)
    return {"tx": len(txs), "tbs": len(tbs), "retx_after_ack": retx_after_ack,
            "retx_without_nack": retx_no_nack, "ack_tbs": ack, "ack_tbs_kept": kept,
            "ack_tbs_resel": resel,
            "periods_kept": round(per / max(1, len(tbs) - len(last)), 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rri-ms", type=float, required=True)
    ap.add_argument("--mu", type=int, default=1)
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args()
    rri_slots = int(round(a.rri_ms * (1 << a.mu)))
    files = []
    for p in map(pathlib.Path, a.paths):
        files += sorted(p.rglob("*_ltx.csv*")) if p.is_dir() else [p]
    bad = 0
    for f in files:
        res = check(f, rri_slots)
        bad += res["retx_after_ack"] + res["retx_without_nack"]
        print(f.name, " ".join(f"{k}={v}" for k, v in res.items()))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
