"""
throughput.py — Per-link throughput in kbps.

Per-link definition:
    throughput = sum(pktSize * 8) over all received packets / measurement_window

Output:
  <tag>_throughput.csv — one row per (tx_node, rx_node)
"""
from __future__ import annotations

from typing import Dict, List

from kpi.registry import (
    ScalarResult, KpiBase, KpiOutputColumn, register,
)


OUTPUT_COLUMNS = (
    KpiOutputColumn("tx_node_id", "", "Transmitter node ID"),
    KpiOutputColumn("rx_node_id", "", "Receiver node ID"),
    KpiOutputColumn("n_rx", "", "Number of packets received"),
    KpiOutputColumn("bytes_rx", "B", "Total bytes received"),
    KpiOutputColumn("thput_kbps", "kbps", "Throughput in kbps"),
)


@register(
    id="throughput",
    title="Per-link throughput",
    spec_ref="RFC 2544 / standard",
    output_kind="scalar",
    output_columns=OUTPUT_COLUMNS,
)
class Throughput(KpiBase):
    def compute(self, ctx):
        topo = ctx.topology
        cache = ctx.cache
        rows: List[Dict] = []

        pkt = cache.pkt_txrx()
        if pkt is None or len(pkt) == 0:
            return [ScalarResult(rows, filename="throughput.csv")]

        rx_only = pkt[pkt["txRx"] == "rx"]
        if len(rx_only) == 0:
            return [ScalarResult(rows, filename="throughput.csv")]

        window_s = ctx.t_end - ctx.t_start
        # Build a fast lookup: (rx_node, srcIp) → list of pktSizeBytes
        size_per_link: Dict[tuple, List[int]] = {}
        ip_to_node: Dict[str, int] = dict(topo.tx_ip_to_node)
        for nid in topo.relay_nodes:
            ni = topo.nodes.get(nid)
            if ni is not None and ni.ip:
                ip_to_node[ni.ip] = nid

        for r in rx_only.itertuples(index=False):
            try:
                rx_nid = int(r.nodeId); sz = int(r.pktSizeBytes)
            except (TypeError, ValueError):
                continue
            src_ip = str(r.srcIp) if r.srcIp else ""
            src_nid = ip_to_node.get(src_ip)
            if src_nid is None:
                continue
            size_per_link.setdefault((src_nid, rx_nid), []).append(sz)

        for tx_node in topo.tx_nodes():
            for rx_node in topo.all_nodes - {tx_node}:
                sizes = size_per_link.get((tx_node, rx_node), [])
                if not sizes:
                    continue
                bytes_rx = sum(sizes)
                n_rx = len(sizes)
                thput_kbps = (bytes_rx * 8.0 / window_s / 1000.0) if window_s > 0 else 0.0
                rows.append({
                    "tx_node_id": tx_node,
                    "rx_node_id": rx_node,
                    "n_rx": n_rx,
                    "bytes_rx": bytes_rx,
                    "thput_kbps": thput_kbps,
                })

        return [ScalarResult(rows, filename="throughput.csv")]