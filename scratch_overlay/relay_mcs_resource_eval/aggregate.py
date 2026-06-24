#!/usr/bin/env python3
"""
aggregate.py — per-phase aggregator for relay_mcs_resource_eval.

Subcommands:
  phase1  : Build P_hit(i, m, pair) surface, derive m*_i(pair), mcsBaseline(pair).
            Reads every *_kpi.csv under the Phase 1 results dir and emits:
              phase1_surface.csv  -- one row per (pair, mcs, rx): mcs_index,
                                    speed, ivd, rx_node_id, prr_mean, n_rx, n_tx,
                                    n_seeds, resource columns.
              phase1_mstar.csv    -- per (pair, rx): m* = max MCS with
                                    P_hit >= 0.95 (else "best" tail PRR).
              phase1_baseline.csv -- per pair: mcsBaseline = m*_{rx=7}.
  phase2  : For each (pair, relayNode), build a one-row summary (resource,
            PSR, storm, balance, latency) and emit phase2_sweep.csv.
  phase2b : MCD threshold sweep summary -> phase2b_sweep.csv.
  phase3  : Head-to-head: baseline(m*_7) vs relay(r*, m*_{r*}, m*_{7-r*}, MCD)
            -> phase3_compare.csv with Wilcoxon p-values per (pair, kpi).

Usage:
  python3 aggregate.py phase1 <results_phase1_dir> [out_dir]
  python3 aggregate.py phase2 <results_phase2_dir> [out_dir]
  python3 aggregate.py phase2b <results_phase2b_dir> [out_dir]
  python3 aggregate.py phase3 <results_phase3_dir> [out_dir]
"""

import argparse
import csv
import math
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, stdev
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants (mirrored from the sim and PROBLEM.md)
# ---------------------------------------------------------------------------
PRR_TARGET = 0.99
P_HIT_CONF = 0.95   # confidence: m*_i = max MCS with P_hit >= 0.95
WINDOW     = 1.0    # s (sliding window size in kpi_postprocess)
EPS_BALANCE = 0.02  # |PRR_hop1 - PRR_hop2| tolerance for "balance"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_float(x, default=None):
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def _safe_mean(xs):
    xs = [x for x in xs if x is not None]
    return mean(xs) if xs else None


def _safe_stdev(xs):
    xs = [x for x in xs if x is not None]
    return stdev(xs) if len(xs) >= 2 else 0.0


def _parse_tag(tag: str) -> Dict:
    """
    Parse tags of the form:
      baseline_s<speed>_ivd<ivd>_mcs<mcs>_seed<seed>
      baseline_s<speed>_ivd<ivd>_mcs<mcs>_mcd<mcd>_seed<seed>
      relay<R>_s<speed>_ivd<ivd>_mcs<mcs>_seed<seed>
      relay<R>_s<speed>_ivd<ivd>_mcs<mcs>_mcd<mcd>_seed<seed>
      relay<R>_s<speed>_ivd<ivd>_mcs1_<mcs1>_mcs2_<mcs2>_mcd<mcd>_seed<seed>
    Returns dict with keys: speed, ivd, mcs, mcd, seed, relay, mode, valid.
    """
    out = {"valid": False, "relay": 0, "mcd": None, "mode": "baseline", "mcs1": None, "mcs2": None}
    m = re.match(
        r"(?P<tag>relay(?P<r>\d+)_|baseline_)?s(?P<speed>\d+)_ivd(?P<ivd>\d+)"
        r"(?:_mcs1_(?P<mcs1>\d+)_mcs2_(?P<mcs2>\d+))?"
        r"(?:_mcs(?P<mcs>\d+))?"
        r"(?:_mcd(?P<mcd>\d+))?"
        r"_seed(?P<seed>\d+)$",
        tag,
    )
    if not m:
        return out
    out["speed"] = int(m["speed"])
    out["ivd"]   = int(m["ivd"])
    if m["mcs1"] and m["mcs2"]:
        out["mcs1"] = int(m["mcs1"])
        out["mcs2"] = int(m["mcs2"])
        out["mcs"]  = int(m["mcs1"])  # backward-compat: mcs = mcs1 (leader)
    elif m["mcs"]:
        out["mcs"] = int(m["mcs"])
    out["mcd"]   = int(m["mcd"]) if m["mcd"] else None
    out["seed"]  = int(m["seed"])
    out["relay"] = int(m["r"]) if m["r"] else 0
    out["mode"]  = "relay" if m["r"] else "baseline"
    out["valid"] = True
    return out


def _read_kpi_csv(path: Path) -> List[Dict]:
    with open(path) as f:
        return list(csv.DictReader(f))


# Per-MCS subchannel-slots/pkt lookup, calibrated from Phase 1 leader-only kpi
# rows (which match the leader-side resource in relay mode because the same
# NrSlUeMacSchedulerFixedMcs allocation pipeline applies). This is the cost
# PER packet transmitted, not including retx; for a fair baseline-vs-relay
# comparison we multiply by (leader_n_tx + relay_n_tx) to get total subch-slots
# consumed. Using this avoids dependence on psschTxUeMac.RETX inclusion.
MCS_TO_SUBCH_PER_PKT: Dict[int, float] = {
    0: 11.8729, 2: 8.0, 4: 5.25084, 6: 4.0, 8: 3.57191, 10: 3.57191,
    12: 2.55518, 14: 2.55518, 16: 2.55518, 18: 2.55518,
    20: 1.17391, 22: 1.17391,
}


def _read_pssch_resource(db_path: Path) -> Dict:
    """
    Read per-RNTI subchannel-slot totals from psschTxUeMac directly.
    Returns {rnti: (sbChSize, sum_rb, n_grants, subch_slots_used)}.
    Note: the kpi_hooks DB stores the actual SEED/RUN values that were set
    at sim time; we read all rows regardless (each .db is single-tag anyway).
    """
    if not db_path.exists():
        return {}
    con = sqlite3.connect(str(db_path))
    out = {}
    try:
        for rnti, sbch, sum_rb, cnt in con.execute(
            "SELECT rnti, sbChSize, SUM(rbLen), COUNT(*) "
            "FROM psschTxUeMac GROUP BY rnti, sbChSize;"
        ):
            rnti = int(rnti); sbch = int(sbch)
            subch = int(sum_rb) // sbch if sbch else 0
            out[rnti] = (sbch, int(sum_rb), int(cnt), subch)
    finally:
        con.close()
    return out


def _read_relay_timing(db_path: Path) -> Dict:
    """Return summary stats from the relayTiming table: {n_total, n_decoded, n_fwd, latencies}."""
    if not db_path.exists():
        return {"n_total": 0, "n_decoded": 0, "n_fwd": 0, "hop1_ms": [], "hop2_ms": [], "e2e_ms": []}
    con = sqlite3.connect(str(db_path))
    if "relayTiming" not in [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table';").fetchall()]:
        con.close()
        return {"n_total": 0, "n_decoded": 0, "n_fwd": 0, "hop1_ms": [], "hop2_ms": [], "e2e_ms": []}
    rows = con.execute(
        "SELECT leaderGenSec, relayDecodeSec, forwardSendSec, forwarded "
        "FROM relayTiming ORDER BY leaderGenSec;"
    ).fetchall()
    con.close()
    if not rows:
        return {"n_total": 0, "n_decoded": 0, "n_fwd": 0, "hop1_ms": [], "hop2_ms": [], "e2e_ms": []}
    hop1, hop2, e2e = [], [], []
    n_dec = 0; n_fwd = 0
    for g, d, s, fwd in rows:
        if d is not None and g is not None:
            hop1.append((d - g) * 1000.0)
            n_dec += 1
        if s is not None and d is not None and fwd == 1:
            hop2.append((s - d) * 1000.0)
            n_fwd += 1
        if s is not None and g is not None and fwd == 1:
            e2e.append((s - g) * 1000.0)
    return {"n_total": len(rows), "n_decoded": n_dec, "n_fwd": n_fwd,
            "hop1_ms": hop1, "hop2_ms": hop2, "e2e_ms": e2e}


# ---------------------------------------------------------------------------
# Phase 1: MCS × distance (R-A) surface
# ---------------------------------------------------------------------------
def phase1(results_dir: Path, out_dir: Path) -> None:
    """
    Build P_hit(i, m, pair) surface, derive m*_i(pair), mcsBaseline(pair).
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # (speed, ivd, mcs) -> list of (rx_node_id, prr_mean)
    surf: Dict[Tuple[int,int,int], List[Tuple[int,float]]] = defaultdict(list)
    # (speed, ivd, mcs) -> n_seeds counted
    n_seeds: Dict[Tuple[int,int,int], int] = defaultdict(int)
    # (speed, ivd, mcs) -> resource mean across seeds
    res_leader: Dict[Tuple[int,int,int], List[int]] = defaultdict(list)

    n_skipped = 0
    for kpi_csv in sorted(results_dir.rglob("*_kpi.csv")):
        tag = kpi_csv.name[: -len("_kpi.csv")]
        info = _parse_tag(tag)
        if not info["valid"] or info["mode"] != "baseline" or info["mcs"] is None:
            n_skipped += 1
            continue
        speed, ivd, mcs, seed = info["speed"], info["ivd"], info["mcs"], info["seed"]
        rows = _read_kpi_csv(kpi_csv)
        for r in rows:
            rx = int(r["rx_node_id"])
            prr = _safe_float(r["prr_mean"])
            if prr is not None:
                surf[(speed, ivd, mcs)].append((rx, prr))
        n_seeds[(speed, ivd, mcs)] += 1
        # Resource: leader is the only TX in baseline mode (RNTI 1 typically)
        # Use subch_slots_used from the first per-RX row (same value for all rows).
        if rows:
            used = _safe_float(rows[0].get("subch_slots_used"))
            if used is not None:
                res_leader[(speed, ivd, mcs)].append(int(used))
    print(f"[phase1] {len(surf)} (pair, mcs) cells  ({n_skipped} kpi files skipped)")

    # ---- phase1_surface.csv: per (pair, mcs, rx) row ----
    surface_csv = out_dir / "phase1_surface.csv"
    with open(surface_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["speed", "ivd", "mcs", "rx_node_id", "prr_mean", "n_seeds"])
        for (speed, ivd, mcs), rx_prr_list in sorted(surf.items()):
            # aggregate over seeds: per (rx, mcs, pair) -> mean prr
            by_rx: Dict[int, List[float]] = defaultdict(list)
            for rx, prr in rx_prr_list:
                by_rx[rx].append(prr)
            for rx, vals in sorted(by_rx.items()):
                w.writerow([speed, ivd, mcs, rx, f"{mean(vals):.4f}", len(vals)])
    print(f"[phase1] wrote {surface_csv}")

    # ---- phase1_mstar.csv: per (pair, rx) -> m* and mcsBaseline (m* for rx=7) ----
    mstar_csv = out_dir / "phase1_mstar.csv"
    mstar_rows: List[Dict] = []
    # Re-organize: for each (pair, rx) find max MCS where P_hit >= P_HIT_CONF.
    pair_rx: Dict[Tuple[int,int], Dict[int, Dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
    for (speed, ivd, mcs), rx_prr_list in surf.items():
        by_rx: Dict[int, List[float]] = defaultdict(list)
        for rx, prr in rx_prr_list:
            by_rx[rx].append(prr)
        for rx, vals in by_rx.items():
            # P_hit = Pr[PRR >= target] across seeds (sample mean)
            n = len(vals)
            if n == 0: continue
            phit = sum(1 for v in vals if v >= PRR_TARGET) / n
            pair_rx[(speed, ivd)][rx][mcs] = phit
            # also store best tail PRR for fallback
    for (speed, ivd), rx_to_mcs in sorted(pair_rx.items()):
        for rx, mcs_to_phit in sorted(rx_to_mcs.items()):
            # find max mcs with P_hit >= conf
            qualifying = [m for m, p in mcs_to_phit.items() if p >= P_HIT_CONF]
            if qualifying:
                m_star = max(qualifying)
                mstar_kind = "target"
            else:
                # best-tail fallback (OP3): MCS with the highest mean PRR
                # for THIS (pair, rx), closest to the target from below.
                rx_prr_mean: Dict[int, float] = {}
                for m in mcs_to_phit:
                    prr_vals = [prr for r_rx, prr in surf[(speed, ivd, m)]
                                if r_rx == rx]
                    if prr_vals:
                        rx_prr_mean[m] = mean(prr_vals)
                if rx_prr_mean:
                    m_star = max(rx_prr_mean, key=rx_prr_mean.get)
                else:
                    m_star = -1
                mstar_kind = "best"
            w_row = {
                "speed": speed, "ivd": ivd, "rx_node_id": rx,
                "m_star": m_star, "kind": mstar_kind,
                "p_hit_at_mstar": mcs_to_phit.get(m_star),
                "n_mcs_qualifying": len(qualifying),
            }
            mstar_rows.append(w_row)
    with open(mstar_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["speed", "ivd", "rx_node_id", "m_star",
                                          "kind", "p_hit_at_mstar", "n_mcs_qualifying"])
        w.writeheader()
        for row in mstar_rows: w.writerow(row)
    print(f"[phase1] wrote {mstar_csv}")

    # ---- phase1_baseline.csv: per pair -> mcsBaseline = m*_{rx=7} ----
    base_csv = out_dir / "phase1_baseline.csv"
    base_rows: List[Dict] = []
    pair_to_mstar7: Dict[Tuple[int,int], Dict] = {}
    for row in mstar_rows:
        if row["rx_node_id"] == 7:
            pair_to_mstar7[(row["speed"], row["ivd"])] = row
    for (speed, ivd), row in sorted(pair_to_mstar7.items()):
        # Resource at m*_7 (mean across seeds for the rx=7 row at that mcs)
        key = (speed, ivd, row["m_star"])
        res_vals = res_leader.get(key, [])
        base_rows.append({
            "speed": speed, "ivd": ivd,
            "mcsBaseline": row["m_star"],
            "m_star_kind": row["kind"],
            "p_hit_at_mstar": row["p_hit_at_mstar"],
            "leader_subch_slots_at_baseline_mean": (mean(res_vals) if res_vals else None),
            "leader_subch_slots_at_baseline_std":  (_safe_stdev(res_vals)),
            "n_seeds": n_seeds.get(key, 0),
        })
    with open(base_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(base_rows[0].keys()) if base_rows else
                           ["speed","ivd","mcsBaseline","m_star_kind","p_hit_at_mstar",
                            "leader_subch_slots_at_baseline_mean","leader_subch_slots_at_baseline_std",
                            "n_seeds"])
        w.writeheader()
        for row in base_rows: w.writerow(row)
    print(f"[phase1] wrote {base_csv}")

    # ---- Gate: m*_1 > m*_7 spread sanity check ----
    if base_rows and mstar_rows:
        per_pair_spread = defaultdict(list)
        for row in mstar_rows:
            per_pair_spread[(row["speed"], row["ivd"])].append(row["m_star"])
        fails = []
        for (speed, ivd), ms in per_pair_spread.items():
            m1 = next((r["m_star"] for r in mstar_rows
                       if r["speed"]==speed and r["ivd"]==ivd and r["rx_node_id"]==1), None)
            m7 = next((r["m_star"] for r in mstar_rows
                       if r["speed"]==speed and r["ivd"]==ivd and r["rx_node_id"]==7), None)
            if m1 is not None and m7 is not None and m1 <= m7:
                fails.append((speed, ivd, m1, m7))
        if fails:
            print(f"[phase1][WARN] MCS-spread gate FAILED for {len(fails)} pairs "
                  f"(m*_1 <= m*_7): {fails[:5]}{'...' if len(fails)>5 else ''}")
        else:
            print(f"[phase1][OK] MCS-spread gate: m*_1 > m*_7 for all {len(per_pair_spread)} pairs.")
    print(f"[phase1] DONE.")


# ---------------------------------------------------------------------------
# Phase 2: relay selection (R-B) sweep
# ---------------------------------------------------------------------------
def phase2(results_dir: Path, out_dir: Path) -> None:
    """
    For each (pair, relayNode), aggregate across seeds:
      - total_subch_slots  (leader + relay, sum)
      - leader_subch_slots / relay_subch_slots
      - prr_worst (max worst of all RX nodes; computed at the application
        layer where the KPI sees the original leader flow, so tail-RX PRR
        is dominated by 2-hop end-to-end delivery).
      - psr  (relay decoded / leader TX) from relayTiming
      - storm_ratio (relay NACK rate at MCD == leader NACK rate at MCD)
      - balance = |PRR at relay - PRR at tail RX|
      - hop1/hop2/e2e latencies

    Reads every *_kpi.csv and the matching .db (for psschTxUeMac and
    relayTiming).  Tag format:
      relay<R>_s<speed>_ivd<ivd>_mcs1_<mcs1>_mcs2_<mcs2>_mcd<mcd>_seed<seed>
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict] = []
    n_skipped = 0
    for kpi_csv in sorted(results_dir.rglob("*_kpi.csv")):
        tag = kpi_csv.name[: -len("_kpi.csv")]
        info = _parse_tag(tag)
        if not info["valid"] or info["mode"] != "relay":
            n_skipped += 1
            continue
        speed, ivd, mcs1, mcs2, mcd, seed, r = (
            info["speed"], info["ivd"], info["mcs"], info.get("mcs"),
            info.get("mcd") or 0, info["seed"], info["relay"],
        )
        # mcs is a single field parsed; for relay tags we need both mcs1 and mcs2.
        # _parse_tag returns info["mcs"] for the first occurrence (mcs1). Parse mcs2
        # separately from the raw tag.
        m = re.search(r"_mcs1_(\d+)_mcs2_(\d+)_", tag)
        if not m:
            n_skipped += 1
            continue
        mcs1, mcs2 = int(m.group(1)), int(m.group(2))

        # KPI rows: all have tx=0 (leader flow). Compute per-RX PRR stats.
        kpi_rows = _read_kpi_csv(kpi_csv)
        prr_means = [_safe_float(r["prr_mean"]) for r in kpi_rows]
        prr_worsts = [_safe_float(r["prr_worst"]) for r in kpi_rows]
        prr_mean_overall = _safe_mean(prr_means)
        # worst-RX across RX nodes, then worst-window per RX = take min of prr_worst
        prr_worst_overall = min([p for p in prr_worsts if p is not None], default=None)

        # Per-RX hop-1 vs hop-2 PRR. Hop-1 receivers are 1..r (TX from node 0).
        # Hop-2 receivers are r+1..7 (TX from node r). The KPI is computed at the
        # application layer so we cannot directly distinguish hops — but the
        # physical layout gives us the partition. We use the same flow rows,
        # grouping by RX index. Hop-1 PRR ≈ mean of rx=1..r; hop-2 ≈ rx=r+1..7.
        prr_by_rx: Dict[int, float] = {}
        for kr in kpi_rows:
            rxi = int(kr["rx_node_id"])
            p = _safe_float(kr["prr_mean"])
            if p is not None:
                prr_by_rx[rxi] = p
        hop1_prrs = [prr_by_rx.get(rx) for rx in range(1, r+1)]
        hop2_prrs = [prr_by_rx.get(rx) for rx in range(r+1, 8)]
        hop1_mean = _safe_mean([p for p in hop1_prrs if p is not None])
        hop2_mean = _safe_mean([p for p in hop2_prrs if p is not None])
        balance = (abs(hop1_mean - hop2_mean)
                   if (hop1_mean is not None and hop2_mean is not None) else None)

        # Resource from kpi CSV (fair-comparison metric: same definition as
        # baseline mode — subch_slots_used in baseline = sum of (per-pkt-cost
        # * n_tx) for the leader only; for relay we add the relay's total).
        leader_subch_kpi = _safe_float(kpi_rows[0].get("subch_slots_used"))
        leader_n_tx      = int(kpi_rows[0].get("n_tx") or 0)
        hop1_retx        = int(kpi_rows[0].get("hop1_retx") or 0)
        hop2_retx        = int(kpi_rows[0].get("hop2_retx") or 0)
        relay_n_tx       = leader_n_tx + hop2_retx  # relayed + relay-added retx
        relay_per_pkt    = MCS_TO_SUBCH_PER_PKT.get(mcs2, 0.0)
        relay_subch_kpi  = relay_per_pkt * relay_n_tx if relay_per_pkt else None
        total_subch_kpi  = (leader_subch_kpi + relay_subch_kpi
                            if (leader_subch_kpi is not None and
                                relay_subch_kpi is not None) else None)

        # Also keep raw psschTxUeMac totals (include retx) for reference.
        db_path = kpi_csv.parent / f"{tag}.db"
        pssch = _read_pssch_resource(db_path)
        leader_subch_raw = sum(v[3] for k, v in pssch.items()
                               if k == 1 or k == min(pssch.keys()))
        relay_subch_raw = sum(v[3] for k, v in pssch.items()
                              if k != 1 and k != min(pssch.keys()))
        total_subch_raw = leader_subch_raw + relay_subch_raw

        # Relay timing → PSR, latencies.
        timing = _read_relay_timing(db_path)
        psr = (timing["n_decoded"] / max(timing["n_total"], 1)
               if timing["n_total"] > 0 else None)
        hop1_latency_ms = (_safe_mean(timing["hop1_ms"])
                           if timing["hop1_ms"] else None)
        hop2_latency_ms = (_safe_mean(timing["hop2_ms"])
                           if timing["hop2_ms"] else None)
        e2e_latency_ms = (_safe_mean(timing["e2e_ms"])
                          if timing["e2e_ms"] else None)

        # Storm ratio: relay retx / leader retx from kpi CSV (per-hop).
        leader_retx = hop1_retx
        relay_retx  = hop2_retx
        storm_ratio = (relay_retx / leader_retx
                       if (leader_retx and leader_retx > 0) else None)

        rows.append({
            "speed": speed, "ivd": ivd,
            "relay": r, "mcsHop1": mcs1, "mcsHop2": mcs2,
            "mcd": mcd, "seed": seed,
            "leader_n_tx": leader_n_tx,
            "relay_n_tx": relay_n_tx,
            "leader_subch_slots": leader_subch_kpi,
            "relay_subch_slots": relay_subch_kpi,
            "total_subch_slots": total_subch_kpi,
            "total_subch_raw": total_subch_raw,
            "prr_worst": prr_worst_overall,
            "prr_mean": prr_mean_overall,
            "hop1_prr_mean": hop1_mean,
            "hop2_prr_mean": hop2_mean,
            "balance": balance,
            "psr": psr,
            "hop1_latency_ms": hop1_latency_ms,
            "hop2_latency_ms": hop2_latency_ms,
            "e2e_latency_ms": e2e_latency_ms,
            "leader_retx": leader_retx,
            "relay_retx": relay_retx,
            "storm_ratio": storm_ratio,
        })

    out_csv = out_dir / "phase2_sweep.csv"
    if not rows:
        print("[phase2] No rows produced; nothing to write.", file=sys.stderr)
        return
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for row in rows: w.writerow(row)
    print(f"[phase2] wrote {out_csv}  ({len(rows)} rows, {n_skipped} skipped)")

    # ---- Selection frontier: per (speed, ivd), pick r* that minimises total
    #      resource subject to PSR >= 0.95, storm_ratio <= 1.5, balance <= 0.05.
    if not rows:
        return
    by_pair: Dict[Tuple[int,int], List[Dict]] = defaultdict(list)
    for row in rows: by_pair[(row["speed"], row["ivd"])].append(row)
    sel = []
    for (speed, ivd), rrows in sorted(by_pair.items()):
        per_r: Dict[int, List[Dict]] = defaultdict(list)
        for row in rrows: per_r[row["relay"]].append(row)
        r_stats = []
        for r, rs in per_r.items():
            r_stats.append({
                "relay": r,
                "n_seeds": len(rs),
                "total_subch_slots_mean": _safe_mean([x["total_subch_slots"] for x in rs]),
                "psr_mean": _safe_mean([x["psr"] for x in rs if x["psr"] is not None]),
                "storm_ratio_mean": _safe_mean([x["storm_ratio"] for x in rs if x["storm_ratio"] is not None]),
                "balance_mean": _safe_mean([x["balance"] for x in rs if x["balance"] is not None]),
                "prr_worst_mean": _safe_mean([x["prr_worst"] for x in rs if x["prr_worst"] is not None]),
                "e2e_latency_ms_mean": _safe_mean([x["e2e_latency_ms"] for x in rs if x["e2e_latency_ms"] is not None]),
            })
        r_stats.sort(key=lambda x: x["relay"])
        # pick r* = argmin total_subch among those meeting constraints
        feasible = [s for s in r_stats
                    if (s["psr_mean"] is None or s["psr_mean"] >= 0.95)
                    and (s["storm_ratio_mean"] is None or s["storm_ratio_mean"] <= 1.5)
                    and (s["balance_mean"] is None or s["balance_mean"] <= 0.05)]
        r_star = min(feasible, key=lambda s: s["total_subch_slots_mean"])["relay"] \
                 if feasible else r_stats[0]["relay"] if r_stats else None
        # also look up the (mcs1, mcs2) used for r*
        mcs1_at_r = mcs2_at_r = None
        for row in rrows:
            if row["relay"] == r_star:
                mcs1_at_r = row["mcsHop1"]
                mcs2_at_r = row["mcsHop2"]
                break
        sel.append({
            "speed": speed, "ivd": ivd,
            "r_star": r_star,
            "mcsHop1": mcs1_at_r,
            "mcsHop2": mcs2_at_r,
            "r_star_resource": next((s["total_subch_slots_mean"]
                                     for s in r_stats if s["relay"] == r_star), None),
            "psr_at_r_star": next((s["psr_mean"]
                                   for s in r_stats if s["relay"] == r_star), None),
            "storm_at_r_star": next((s["storm_ratio_mean"]
                                     for s in r_stats if s["relay"] == r_star), None),
            "balance_at_r_star": next((s["balance_mean"]
                                       for s in r_stats if s["relay"] == r_star), None),
            "prr_worst_at_r_star": next((s["prr_worst_mean"]
                                         for s in r_stats if s["relay"] == r_star), None),
        })
        print(f"[phase2]  pair=({speed},{ivd})  per-r resources={[s['total_subch_slots_mean'] for s in r_stats]}  -> r*={r_star}")
    sel_csv = out_dir / "phase2_selection.csv"
    with open(sel_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["speed","ivd","r_star","mcsHop1","mcsHop2",
                                          "r_star_resource","psr_at_r_star",
                                          "storm_at_r_star","balance_at_r_star",
                                          "prr_worst_at_r_star"])
        w.writeheader()
        for row in sel: w.writerow(row)
    print(f"[phase2] wrote {sel_csv}")
    print(f"[phase2] DONE.")


# ---------------------------------------------------------------------------
# Phase 2b: MCD threshold (R-D) sweep
# ---------------------------------------------------------------------------
def phase2b(results_dir: Path, out_dir: Path) -> None:
    """
    MCD threshold sweep. Tag format:
      relay<R>_s<speed>_ivd<ivd>_mcs1_<mcs1>_mcs2_<mcs2>_mcd<mcd>_seed<seed>
    For each (pair, mcd), aggregate across seeds the storm_ratio, balance,
    PSR, total resource, e2e latency.  Output: phase2b_sweep.csv (per-row)
    and phase2b_threshold.csv (per-pair, MCD that minimises resource subject
    to constraints).
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict] = []
    n_skipped = 0
    for kpi_csv in sorted(results_dir.rglob("*_kpi.csv")):
        tag = kpi_csv.name[: -len("_kpi.csv")]
        info = _parse_tag(tag)
        if not info["valid"] or info["mode"] != "relay":
            n_skipped += 1
            continue
        speed, ivd, mcd, seed, r = (
            info["speed"], info["ivd"], info["mcd"],
            info["seed"], info["relay"],
        )
        m = re.search(r"_mcs1_(\d+)_mcs2_(\d+)_", tag)
        if not m: continue
        mcs1, mcs2 = int(m.group(1)), int(m.group(2))

        kpi_rows = _read_kpi_csv(kpi_csv)
        prr_by_rx: Dict[int, float] = {}
        for kr in kpi_rows:
            rxi = int(kr["rx_node_id"])
            p = _safe_float(kr["prr_mean"])
            if p is not None: prr_by_rx[rxi] = p
        hop1_mean = _safe_mean([prr_by_rx.get(rx) for rx in range(1, r+1)
                                if prr_by_rx.get(rx) is not None])
        hop2_mean = _safe_mean([prr_by_rx.get(rx) for rx in range(r+1, 8)
                                if prr_by_rx.get(rx) is not None])
        balance = (abs(hop1_mean - hop2_mean)
                   if (hop1_mean is not None and hop2_mean is not None) else None)

        db_path = kpi_csv.parent / f"{tag}.db"
        pssch = _read_pssch_resource(db_path)
        total_subch = sum(v[3] for v in pssch.values())

        timing = _read_relay_timing(db_path)
        psr = (timing["n_decoded"] / max(timing["n_total"], 1)
               if timing["n_total"] > 0 else None)
        e2e_latency_ms = (_safe_mean(timing["e2e_ms"])
                          if timing["e2e_ms"] else None)

        # storm: leader reTX / leader total TX  (recomputed from psschTxUeMac)
        leader_retx = None; leader_tx = None
        if db_path.exists():
            con = sqlite3.connect(str(db_path))
            try:
                rnti_node = {}
                for row in con.execute(
                    "SELECT DISTINCT ps.rnti, n.nodeId FROM psschTxUeMac ps "
                    "JOIN nodeInfo n ON ps.imsi = n.imsi;"
                ):
                    rnti_node[int(row[0])] = int(row[1])
                for rn, nid in rnti_node.items():
                    if nid != 0: continue
                    leader_retx = con.execute(
                        "SELECT COUNT(*) FROM psschTxUeMac "
                        "WHERE ndi=0 AND rnti=?;",
                        (rn,),
                    ).fetchone()[0]
                    leader_tx = con.execute(
                        "SELECT COUNT(*) FROM psschTxUeMac "
                        "WHERE rnti=?;",
                        (rn,),
                    ).fetchone()[0]
            finally:
                con.close()
        storm_ratio = (leader_retx / leader_tx
                       if (leader_tx and leader_tx > 0 and leader_retx is not None) else None)

        rows.append({
            "speed": speed, "ivd": ivd,
            "relay": r, "mcsHop1": mcs1, "mcsHop2": mcs2,
            "mcd": mcd, "seed": seed,
            "total_subch_slots": total_subch,
            "psr": psr,
            "storm_ratio": storm_ratio,
            "balance": balance,
            "hop1_prr_mean": hop1_mean,
            "hop2_prr_mean": hop2_mean,
            "e2e_latency_ms": e2e_latency_ms,
        })

    out_csv = out_dir / "phase2b_sweep.csv"
    if not rows:
        print("[phase2b] No rows produced; nothing to write.", file=sys.stderr)
        return
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for row in rows: w.writerow(row)
    print(f"[phase2b] wrote {out_csv}  ({len(rows)} rows, {n_skipped} skipped)")

    # Per-pair: pick min-resource MCD meeting constraints.
    by_pair: Dict[Tuple[int,int], List[Dict]] = defaultdict(list)
    for row in rows: by_pair[(row["speed"], row["ivd"])].append(row)
    thresh = []
    for (speed, ivd), rrows in sorted(by_pair.items()):
        per_mcd: Dict[int, List[Dict]] = defaultdict(list)
        for row in rrows: per_mcd[row["mcd"]].append(row)
        mcd_stats = []
        for mcd, rs in per_mcd.items():
            mcd_stats.append({
                "mcd": mcd,
                "n_seeds": len(rs),
                "total_subch_slots_mean": _safe_mean([x["total_subch_slots"] for x in rs]),
                "psr_mean": _safe_mean([x["psr"] for x in rs if x["psr"] is not None]),
                "storm_ratio_mean": _safe_mean([x["storm_ratio"] for x in rs if x["storm_ratio"] is not None]),
                "balance_mean": _safe_mean([x["balance"] for x in rs if x["balance"] is not None]),
            })
        feasible = [s for s in mcd_stats
                    if (s["psr_mean"] is None or s["psr_mean"] >= 0.95)
                    and (s["storm_ratio_mean"] is None or s["storm_ratio_mean"] <= 0.3)
                    and (s["balance_mean"] is None or s["balance_mean"] <= 0.05)]
        mcd_star = min(feasible, key=lambda s: s["total_subch_slots_mean"])["mcd"] \
                   if feasible else mcd_stats[0]["mcd"] if mcd_stats else None
        thresh.append({
            "speed": speed, "ivd": ivd, "mcd_star": mcd_star,
            "mcd_star_resource": next((s["total_subch_slots_mean"]
                                       for s in mcd_stats if s["mcd"] == mcd_star), None),
        })
    sel_csv = out_dir / "phase2b_threshold.csv"
    with open(sel_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["speed","ivd","mcd_star","mcd_star_resource"])
        w.writeheader()
        for row in thresh: w.writerow(row)
    print(f"[phase2b] wrote {sel_csv}")
    print(f"[phase2b] DONE.")


# ---------------------------------------------------------------------------
# Phase 3: head-to-head baseline vs relay
# ---------------------------------------------------------------------------
def phase3(results_dir: Path, out_dir: Path) -> None:
    """
    Head-to-head comparison.  Each pair (pair, seed) appears twice: once for
    baseline, once for relay (with the chosen r* and mcs values).
    Output: phase3_compare.csv (one row per pair, paired by seed, with
    baseline_/relay_ columns) + Wilcoxon p-values per KPI per pair.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Read all kpi csv + db into rows: parse tag, build per-pair record.
    base_rows: Dict[Tuple[int,int,int], Dict] = {}
    relay_rows: Dict[Tuple[int,int,int], Dict] = {}
    n_skipped = 0

    for kpi_csv in sorted(results_dir.rglob("*_kpi.csv")):
        tag = kpi_csv.name[: -len("_kpi.csv")]
        info = _parse_tag(tag)
        if not info["valid"]:
            n_skipped += 1
            continue
        speed, ivd, mcs, mcd, seed = (
            info["speed"], info["ivd"], info["mcs"],
            info.get("mcd") or 0, info["seed"],
        )
        kpi_rows = _read_kpi_csv(kpi_csv)
        prr_worsts = [_safe_float(r["prr_worst"]) for r in kpi_rows]
        prr_means  = [_safe_float(r["prr_mean"])  for r in kpi_rows]
        prr_worst = min([p for p in prr_worsts if p is not None], default=None)
        prr_mean  = _safe_mean(prr_means)
        pirs = [_safe_float(r["pir_p95_ms"]) for r in kpi_rows]
        aois = [_safe_float(r["aoi_peak_ms"]) for r in kpi_rows]
        pir_p95 = max([p for p in pirs if p is not None], default=None)
        aoi_peak = max([p for p in aois if p is not None], default=None)

        db_path = kpi_csv.parent / f"{tag}.db"
        pssch = _read_pssch_resource(db_path)
        total_subch = sum(v[3] for v in pssch.values())
        timing = _read_relay_timing(db_path)
        psr = (timing["n_decoded"] / max(timing["n_total"], 1)
               if timing["n_total"] > 0 else None)

        rec = {
            "speed": speed, "ivd": ivd, "seed": seed,
            "mcs": mcs, "mcd": mcd, "relay": info["relay"], "mode": info["mode"],
            "prr_worst": prr_worst, "prr_mean": prr_mean,
            "pir_p95_ms": pir_p95, "aoi_peak_ms": aoi_peak,
            "total_subch_slots": total_subch, "psr": psr,
        }
        key = (speed, ivd, seed)
        if info["mode"] == "baseline":
            base_rows[key] = rec
        else:
            relay_rows[key] = rec

    # Paired by seed (only pairs where both baseline and relay exist).
    pairs = []
    for key in sorted(base_rows.keys()):
        if key not in relay_rows:
            continue
        b = base_rows[key]; r = relay_rows[key]
        pairs.append({
            "speed": b["speed"], "ivd": b["ivd"], "seed": b["seed"],
            "mcsBaseline": b["mcs"], "mcsHop1": r["mcs"], "mcsHop2": r["mcs"],
            "relayNode": r["relay"], "mcd": b["mcd"],
            "prr_worst_baseline": b["prr_worst"],
            "prr_worst_relay":    r["prr_worst"],
            "prr_mean_baseline":  b["prr_mean"],
            "prr_mean_relay":     r["prr_mean"],
            "pir_p95_ms_baseline": b["pir_p95_ms"],
            "pir_p95_ms_relay":    r["pir_p95_ms"],
            "aoi_peak_ms_baseline": b["aoi_peak_ms"],
            "aoi_peak_ms_relay":    r["aoi_peak_ms"],
            "total_subch_slots_baseline": b["total_subch_slots"],
            "total_subch_slots_relay":    r["total_subch_slots"],
            "psr_relay": r["psr"],
        })
    out_csv = out_dir / "phase3_compare.csv"
    if not pairs:
        print("[phase3] No paired rows; nothing to write.", file=sys.stderr)
        return
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(pairs[0].keys()))
        w.writeheader()
        for row in pairs: w.writerow(row)
    print(f"[phase3] wrote {out_csv}  ({len(pairs)} rows, {n_skipped} skipped)")

    # Per-pair Wilcoxon + verdict
    by_pair: Dict[Tuple[int,int], List[Dict]] = defaultdict(list)
    for row in pairs: by_pair[(row["speed"], row["ivd"])].append(row)
    try:
        from scipy.stats import wilcoxon
    except ImportError:
        print("[phase3] scipy not available; skipping Wilcoxon.", file=sys.stderr)
        return
    verdict = []
    for (speed, ivd), prows in sorted(by_pair.items()):
        if len(prows) < 5:
            continue
        # Wilcoxon on diffs (relay - baseline), nonzero only
        out = {"speed": speed, "ivd": ivd, "n_paired": len(prows)}
        for kpi in ["total_subch_slots", "prr_worst", "prr_mean",
                    "pir_p95_ms", "aoi_peak_ms"]:
            diffs = [r[f"{kpi}_relay"] - r[f"{kpi}_baseline"]
                     for r in prows
                     if r[f"{kpi}_relay"] is not None and r[f"{kpi}_baseline"] is not None]
            nz = [d for d in diffs if d != 0]
            if len(nz) >= 5:
                _, p = wilcoxon(nz)
                out[f"{kpi}_p"] = p
            else:
                out[f"{kpi}_p"] = None
            out[f"{kpi}_median_diff"] = median(diffs) if diffs else None
        # Mean resource ratio (relay / baseline) — the headline metric.
        br = [r["total_subch_slots_baseline"] for r in prows
              if r["total_subch_slots_baseline"] is not None
              and r["total_subch_slots_relay"] is not None]
        rr = [r["total_subch_slots_relay"] for r in prows
              if r["total_subch_slots_relay"] is not None
              and r["total_subch_slots_baseline"] is not None]
        ratios = [a/b for a, b in zip(rr, br) if b > 0]
        out["res_ratio_relay_over_base_mean"] = (
            sum(ratios)/len(ratios) if ratios else None)
        # Headline: did relay save any subch-slots on average?
        out["relay_saves_resource"] = (
            (out["res_ratio_relay_over_base_mean"] is not None)
            and (out["res_ratio_relay_over_base_mean"] < 1.0))
        verdict.append(out)
        print(f"[phase3]  pair=({speed},{ivd})  n={len(prows)}  "
              f"res_ratio={out['res_ratio_relay_over_base_mean']:.3f}  "
              f"res_p={out['total_subch_slots_p']}  prr_p={out['prr_worst_p']}")
    v_csv = out_dir / "phase3_verdict.csv"
    if verdict:
        with open(v_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(verdict[0].keys()))
            w.writeheader()
            for row in verdict: w.writerow(row)
        print(f"[phase3] wrote {v_csv}")
    print(f"[phase3] DONE.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("phase", choices=["phase1", "phase2", "phase2b", "phase3"])
    p.add_argument("results_dir", type=Path)
    p.add_argument("out_dir", nargs="?", type=Path, default=None)
    args = p.parse_args()

    if args.out_dir is None:
        args.out_dir = args.results_dir

    if args.phase == "phase1":
        phase1(args.results_dir, args.out_dir)
    elif args.phase == "phase2":
        phase2(args.results_dir, args.out_dir)
    elif args.phase == "phase2b":
        phase2b(args.results_dir, args.out_dir)
    elif args.phase == "phase3":
        phase3(args.results_dir, args.out_dir)
    else:
        print(f"[aggregate] Phase {args.phase} not implemented.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
