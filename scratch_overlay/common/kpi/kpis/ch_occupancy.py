"""
ch_occupancy.py — Channel occupancy per TX node.

For each TX node:
    occupancy = total subchannel-slots used / total subchannel-slots available

Output:
  <tag>_ch_occupancy.csv — one row per tx_node
"""
from __future__ import annotations

from typing import Dict, List

from kpi.registry import (
    ScalarResult, KpiBase, KpiOutputColumn, register,
)


OUTPUT_COLUMNS = (
    KpiOutputColumn("tx_node_id", "", "Transmitter node ID"),
    KpiOutputColumn("n_grants", "", "Number of PSSCH grants in window"),
    KpiOutputColumn("subch_slots_used", "", "Total subchannel-slots consumed"),
    KpiOutputColumn("subch_slots_total", "", "Total subchannel-slots available"),
    KpiOutputColumn("ch_occupancy", "", "occupancy = used / total"),
    KpiOutputColumn("subch_slots_per_pkt", "", "Subchannel-slots per emitted packet"),
)


@register(
    id="ch_occupancy",
    title="Channel occupancy per TX node",
    spec_ref="3GPP NR Sidelink resource pool",
    output_kind="scalar",
    output_columns=OUTPUT_COLUMNS,
)
class ChOccupancy(KpiBase):
    def compute(self, ctx):
        topo = ctx.topology
        cache = ctx.cache
        out = ctx.outcomes
        rows: List[Dict] = []

        grants = cache.pssch_tx()
        if grants is None or len(grants) == 0:
            return [ScalarResult(rows, filename="ch_occupancy.csv")]

        # Map imsi -> nodeId via nodeInfo (rnti == imsi in our sims).
        imsi_to_node = cache.imsi_to_node()
        # Aggregate subchannel-slots used per nodeId.
        used_per: Dict[int, int] = {}
        sbChSize_first: Dict[int, int] = {}
        for g in grants.itertuples(index=False):
            try:
                imsi = int(g.imsi); rb_len = int(g.rbLen); sbch = int(g.sbChSize)
            except (TypeError, ValueError, AttributeError):
                continue
            nid = imsi_to_node.get(imsi)
            if nid is None:
                continue
            if sbch > 0:
                used_per[nid] = used_per.get(nid, 0) + (rb_len + sbch - 1) // sbch
                sbChSize_first[nid] = sbch

        # Total subchannel-slots in pool: window_s * 100 subframes/s * n_subchannels.
        n_subch = max(sbChSize_first.values()) if sbChSize_first else 12
        window_s = ctx.t_end - ctx.t_start
        subframes_total = int(window_s * 100)
        total_subch_slots = subframes_total * n_subch

        for nid in sorted(used_per.keys()):
            used = used_per[nid]
            tx_seqs = out.tx_seqs(nid)
            n_tx = len(tx_seqs)
            rows.append({
                "tx_node_id": nid,
                "n_grants": used,
                "subch_slots_used": used,
                "subch_slots_total": total_subch_slots,
                "ch_occupancy": used / total_subch_slots if total_subch_slots > 0 else None,
                "subch_slots_per_pkt": (used / n_tx) if n_tx > 0 else None,
            })

        return [ScalarResult(rows, filename="ch_occupancy.csv")]