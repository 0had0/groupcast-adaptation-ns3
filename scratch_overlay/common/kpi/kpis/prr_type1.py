"""
prr_type1.py — 3GPP TR 37.885 §5.2.1 PRR Type 1 (broadcast, geographic).

Per-packet definition:
    PRR_i = X / Y
where Y = #UEs in distance range (a, b) from Tx, X = #decoded among Y.

Aggregated as:
  - per-bin (20m wide by default) average PRR = (X1+...+Xn)/(Y1+...+Yn)
  - per-packet CDF over a baseline range (320m highway, etc.)

Outputs:
  <tag>_prr_type1.bin.csv  — one row per (tx_node, dist_bin)
  <tag>_prr_type1.cdf.csv  — one row per (tx_node, pkt_seq, range_idx)
"""
from __future__ import annotations

from typing import Dict, List

from kpi.registry import (
    BinResult, CdfResult, KpiBase, KpiOutputColumn, register,
)


OUTPUT_COLUMNS_BIN = (
    KpiOutputColumn("tx_node_id", "", "Transmitter node ID"),
    KpiOutputColumn("baseline_lo_m", "m", "Baseline range lower bound (0)"),
    KpiOutputColumn("baseline_hi_m", "m", "Baseline range upper bound"),
    KpiOutputColumn("dist_lo_m", "m", "Bin lower edge"),
    KpiOutputColumn("dist_hi_m", "m", "Bin upper edge"),
    KpiOutputColumn("n_packets", "", "Packets emitted by this TX in window"),
    KpiOutputColumn("X_sum", "", "Total successful decoders (sum across packets)"),
    KpiOutputColumn("Y_sum", "", "Total intended receivers (sum across packets)"),
    KpiOutputColumn("prr_avg", "", "(X1+...+Xn)/(Y1+...+Yn)"),
)


OUTPUT_COLUMNS_CDF = (
    KpiOutputColumn("tx_node_id", "", "Transmitter node ID"),
    KpiOutputColumn("range_idx", "", "Index of the range spec (0..N-1)"),
    KpiOutputColumn("baseline_lo_m", "m", "Baseline range lower bound"),
    KpiOutputColumn("baseline_hi_m", "m", "Baseline range upper bound"),
    KpiOutputColumn("pkt_seq", "", "Packet sequence number"),
    KpiOutputColumn("prr_i", "", "Per-packet PRR (X/Y)"),
)


@register(
    id="prr_type1",
    title="PRR Type 1 (broadcast, geographic)",
    spec_ref="3GPP TR 37.885 V15.3.0 §5.2.1",
    output_kind="bin",  # primary; also emits cdf
    output_columns=OUTPUT_COLUMNS_BIN,
)
class PrrType1(KpiBase):
    def compute(self, ctx):
        topo = ctx.topology
        out = ctx.outcomes
        ranges = ctx.ranges

        bin_rows: List[Dict] = []
        cdf_rows: List[Dict] = []

        # Pre-compute the per-rx distance from each TX node (constant for our
        # moving-platoon scenario; for variable mobility we'd interpolate).
        # tx_node -> {rx_node: distance}
        dist_cache: Dict[int, Dict[int, float]] = {}

        for tx_node in topo.tx_nodes():
            tx_seqs = out.tx_seqs(tx_node)
            if not tx_seqs:
                continue

            # Build the (rx_node, distance) map for this TX once.
            if tx_node not in dist_cache:
                dist_cache[tx_node] = {
                    rx: topo.distance(tx_node, rx)
                    for rx in topo.all_nodes - {tx_node}
                }
            distances = dist_cache[tx_node]

            for r_idx, r in enumerate(ranges.ranges):
                # Per-bin accumulators: bin_idx -> [X_sum, Y_sum, n_packets]
                bin_acc: Dict[int, List[float]] = {}

                for seq in tx_seqs:
                    # Y = #rx within range (a, b] from TX.
                    in_range = [rx for rx, d in distances.items()
                                if r.contains(d)]
                    if not in_range:
                        continue
                    Y = len(in_range)
                    # X = #rx that decoded this seq.
                    X = sum(1 for rx in in_range
                            if out.decoded(tx_node, rx, seq))

                    # Emit CDF row.
                    cdf_rows.append({
                        "tx_node_id": tx_node,
                        "range_idx": r_idx,
                        "baseline_lo_m": r.a,
                        "baseline_hi_m": r.b,
                        "pkt_seq": seq,
                        "prr_i": X / Y,
                    })

                    # Accumulate per distance bin.
                    for rx in in_range:
                        d = distances[rx]
                        if ranges.bins is None:
                            continue
                        bin_idx = ranges.bins.bin_for(d)
                        if bin_idx < 0 or bin_idx >= ranges.bins.n_bins:
                            continue
                        if bin_idx not in bin_acc:
                            bin_acc[bin_idx] = [0.0, 0.0, 0.0]
                        bin_acc[bin_idx][1] += Y / len(in_range)  # per-rx Y contribution
                        # Note: X is per-packet, summed across rx in bin.
                        # We need to also sum X per (rx, seq); easier to do
                        # the per-(rx, seq) X check inside the rx loop.
                        # For accuracy, track X per rx:
                        if out.decoded(tx_node, rx, seq):
                            bin_acc[bin_idx][0] += 1.0
                    # Update Y_sum contribution for all rx in range:
                    for bin_idx in bin_acc:
                        # Y per rx is 1 (each rx is a Y candidate).
                        bin_acc[bin_idx][2] += Y  # total intended per packet

                # Emit bin rows for this range.
                if ranges.bins is not None:
                    for bin_idx, acc in bin_acc.items():
                        lo, hi = ranges.bins.bin_edges(bin_idx)
                        X_sum, _, n_packets_total = acc[0], acc[1], acc[2]
                        # Y_sum is the per-packet Y contribution summed across packets
                        # in this bin. We sum Y per packet × #packets in range where
                        # at least one rx falls into the bin.
                        # Easier: recompute Y_sum directly from per-(rx, packet).
                        Y_sum = 0.0
                        for seq in tx_seqs:
                            in_bin = [rx for rx, d in distances.items()
                                      if bin_idx == ranges.bins.bin_for(d)
                                      and r.contains(d)]
                            if in_bin:
                                Y_sum += len(in_bin)
                        prr_avg = X_sum / Y_sum if Y_sum > 0 else None
                        bin_rows.append({
                            "tx_node_id": tx_node,
                            "baseline_lo_m": r.a,
                            "baseline_hi_m": r.b,
                            "dist_lo_m": lo,
                            "dist_hi_m": hi,
                            "n_packets": len(tx_seqs),
                            "X_sum": int(X_sum),
                            "Y_sum": int(Y_sum),
                            "prr_avg": prr_avg,
                        })

        return [
            BinResult(bin_rows, filename="prr_type1.bin.csv"),
            CdfResult(cdf_rows, filename="prr_type1.cdf.csv"),
        ]