"""
coll_rate.py — Leader↔relay RB-overlap collision rate (the H4 metric).

Definition:
  For each leader TX, check whether any relay TX (different srcL2Id) in the
  same (frame, subFrame, slot) has overlapping RBs. Report the fraction of
  leader TBs that collide. This is the "H4 feared cost" of the distinct-L2ID
  relay design — historically measured at 0.00 in all tested configurations.

Output:
  <tag>_coll.csv — one scalar per sim.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from kpi.registry import (
    ScalarResult, KpiBase, KpiOutputColumn, register,
)


OUTPUT_COLUMNS = (
    KpiOutputColumn("coll_rate", "", "leader TBs colliding with relay TBs / leader TBs"),
    KpiOutputColumn("n_colliding", "", "Number of leader TBs with overlapping relay TBs"),
    KpiOutputColumn("n_leader_tbs", "", "Total leader TBs in window"),
    KpiOutputColumn("n_relay_tbs", "", "Total relay TBs in window"),
)


@register(
    id="coll",
    title="Leader↔relay RB-overlap collision rate",
    spec_ref="agg_fixed_sweep.coll_rate (legacy H4 metric)",
    output_kind="scalar",
    output_columns=OUTPUT_COLUMNS,
)
class CollRate(KpiBase):
    def compute(self, ctx):
        cache = ctx.cache
        rows: List[Dict] = []

        tx = cache.pssch_tx()
        if tx is None or len(tx) == 0:
            return [ScalarResult([{
                "coll_rate": None, "n_colliding": 0, "n_leader_tbs": 0,
                "n_relay_tbs": 0,
            }], filename="coll.csv")]

        # Need frame/subFrame/slot/rbStart/rbLen
        required = ("frame", "subFrame", "slot", "rbStart", "rbLen", "srcL2Id")
        if not all(c in tx.columns for c in required):
            return [ScalarResult([{
                "coll_rate": None, "n_colliding": 0, "n_leader_tbs": 0,
                "n_relay_tbs": 0,
            }], filename="coll.csv")]

        leader_l2 = int(tx["srcL2Id"].min())
        leader_tx = tx[tx["srcL2Id"] == leader_l2]
        relay_tx = tx[tx["srcL2Id"] != leader_l2]
        n_leader = len(leader_tx)
        n_relay = len(relay_tx)

        if n_leader == 0 or n_relay == 0:
            # No relay in this sim — coll_rate is undefined (0 by convention).
            return [ScalarResult([{
                "coll_rate": 0.0 if n_relay == 0 else None,
                "n_colliding": 0,
                "n_leader_tbs": n_leader,
                "n_relay_tbs": n_relay,
            }], filename="coll.csv")]

        # Index relay TBs by (frame, subFrame, slot) → list of (rbStart, rbStart+rbLen).
        relay_slots: Dict[Tuple[int, int, int], List[Tuple[int, int]]] = {}
        for r in relay_tx.itertuples(index=False):
            key = (int(r.frame), int(r.subFrame), int(r.slot))
            relay_slots.setdefault(key, []).append(
                (int(r.rbStart), int(r.rbStart) + int(r.rbLen))
            )

        # For each leader TB, check if any relay TB in the same slot overlaps.
        n_colliding = 0
        for r in leader_tx.itertuples(index=False):
            key = (int(r.frame), int(r.subFrame), int(r.slot))
            a0, a1 = int(r.rbStart), int(r.rbStart) + int(r.rbLen)
            for b0, b1 in relay_slots.get(key, ()):
                if a0 < b1 and b0 < a1:
                    n_colliding += 1
                    break

        rows.append({
            "coll_rate": n_colliding / n_leader if n_leader > 0 else None,
            "n_colliding": n_colliding,
            "n_leader_tbs": n_leader,
            "n_relay_tbs": n_relay,
        })
        return [ScalarResult(rows, filename="coll.csv")]