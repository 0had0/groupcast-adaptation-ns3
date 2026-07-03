"""
storm_ratio.py — Hop-1 retransmission pressure on the shared channel.

Storm ratio = psschTx(ndi=0) / psschTx(ndi=1), aggregated over all TX
nodes in the DB (the legacy ``agg_fixed_sweep`` definition). A value of
~0.10 means 10% of all scheduled TBs are retransmissions — the
"retransmission storm" that motivates the distinct-L2ID relay.

Output:
  <tag>_storm.csv — one scalar per sim.
"""
from __future__ import annotations

from typing import Dict, List

from kpi.registry import (
    ScalarResult, KpiBase, KpiOutputColumn, register,
)


OUTPUT_COLUMNS = (
    KpiOutputColumn("storm_ratio", "", "n_retx / n_new on hop 1"),
    KpiOutputColumn("n_retx", "", "psschTx rows where ndi=0"),
    KpiOutputColumn("n_new", "", "psschTx rows where ndi=1"),
    KpiOutputColumn("hop1_retx", "", "leader-only retx count (ndi=0, srcL2Id=leader)"),
    KpiOutputColumn("leader_l2_id", "", "Lowest srcL2Id in psschTx (the leader)"),
)


@register(
    id="storm",
    title="Hop-1 retransmission storm ratio",
    spec_ref="agg_fixed_sweep.storm_ratio (legacy)",
    output_kind="scalar",
    output_columns=OUTPUT_COLUMNS,
)
class Storm(KpiBase):
    def compute(self, ctx):
        cache = ctx.cache
        rows: List[Dict] = []

        tx = cache.pssch_tx()
        if tx is None or len(tx) == 0:
            return [ScalarResult([{
                "storm_ratio": None, "n_retx": 0, "n_new": 0,
                "hop1_retx": 0, "leader_l2_id": None,
            }], filename="storm.csv")]

        ndi = tx["ndi"].astype(int)
        n_retx = int((ndi == 0).sum())
        n_new = int((ndi == 1).sum())
        storm_ratio = n_retx / n_new if n_new > 0 else None

        # Leader is the lowest srcL2Id (the head of the platoon).
        leader_l2 = int(tx["srcL2Id"].min())
        leader_tx = tx[tx["srcL2Id"] == leader_l2]
        hop1_retx = int((leader_tx["ndi"].astype(int) == 0).sum())

        rows.append({
            "storm_ratio": storm_ratio,
            "n_retx": n_retx,
            "n_new": n_new,
            "hop1_retx": hop1_retx,
            "leader_l2_id": leader_l2,
        })
        return [ScalarResult(rows, filename="storm.csv")]