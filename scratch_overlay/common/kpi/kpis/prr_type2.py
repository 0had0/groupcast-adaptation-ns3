"""
prr_type2.py — 3GPP TR 37.885 §5.2.2 PRR Type 2 (groupcast, intended set).

Per-packet definition:
    PRR_i = S / Z
where Z = #UEs in Tx's intended set, S = #decoded among Z.

Per-link output:
  <tag>_prr_type2.csv — one row per (tx_node, rx_node) with PRR_i statistics.
"""
from __future__ import annotations

from typing import Dict, List

from kpi.registry import (
    ScalarResult, KpiBase, KpiOutputColumn, register,
)


OUTPUT_COLUMNS = (
    KpiOutputColumn("tx_node_id", "", "Transmitter node ID"),
    KpiOutputColumn("rx_node_id", "", "Receiver node ID"),
    KpiOutputColumn("intended_set_size", "", "|Z| for this TX"),
    KpiOutputColumn("n_tx", "", "Number of packets emitted by TX"),
    KpiOutputColumn("n_rx_success", "", "Number of decoded packets at RX"),
    KpiOutputColumn("prr_mean", "", "Average PRR_i over packets (= S_sum / (Z * n_tx))"),
    KpiOutputColumn("prr_worst", "", "min(PRR_i) over packets"),
    KpiOutputColumn("prr_best", "", "max(PRR_i) over packets"),
)


@register(
    id="prr_type2",
    title="PRR Type 2 (groupcast, intended set)",
    spec_ref="3GPP TR 37.885 V15.3.0 §5.2.2",
    output_kind="scalar",
    output_columns=OUTPUT_COLUMNS,
)
class PrrType2(KpiBase):
    def compute(self, ctx):
        topo = ctx.topology
        out = ctx.outcomes
        rows: List[Dict] = []

        for tx_node in topo.tx_nodes():
            intended = topo.intended_for(tx_node)
            if not intended:
                continue
            tx_seqs = out.tx_seqs(tx_node)
            if not tx_seqs:
                continue
            Z = len(intended)

            for rx_node in intended:
                per_pkt_prr: List[float] = []
                S_total = 0
                for seq in tx_seqs:
                    S = sum(1 for r in intended if out.decoded(tx_node, r, seq))
                    S_total += S
                    if rx_node in intended:  # this specific rx decoded?
                        per_pkt_prr.append(1.0 if out.decoded(tx_node, rx_node, seq) else 0.0)

                n_tx = len(tx_seqs)
                n_rx_success = sum(1 for seq in tx_seqs if out.decoded(tx_node, rx_node, seq))
                prr_mean = n_rx_success / n_tx if n_tx > 0 else None
                rows.append({
                    "tx_node_id": tx_node,
                    "rx_node_id": rx_node,
                    "intended_set_size": Z,
                    "n_tx": n_tx,
                    "n_rx_success": n_rx_success,
                    "prr_mean": prr_mean,
                    "prr_worst": min(per_pkt_prr) if per_pkt_prr else None,
                    "prr_best": max(per_pkt_prr) if per_pkt_prr else None,
                })

        return [ScalarResult(rows, filename="prr_type2.csv")]