"""
topology.py — Derived view of the simulation's node graph.

Built from DataCache (nodeInfo + relayTiming + pktTxRx). Holds positions,
leader/follower/relay classification, and the per-TX intended receiver set.

The "intended set" convention (per 3GPP TR 37.885 §5.2.2 — "company needs to
clarify"):

  - No relay nodes: TX's intended set = all other UEs in topology.
  - With relay nodes: per-hop partitioning by leader node ID.
      * leader (min nodeId, or detected from pktTxRx.tx traffic): intended
        set = followers with nodeId < relay_node (front-half)
      * relay_node: intended set = followers with nodeId > relay_node (back-half)

Multi-relay: partition is by position relative to whichever TX is closest in
front of each receiver. For now we partition by each TX's own position; the
default works for the standard 1-relay configuration and degrades gracefully
when more relays are added.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Set

import math
import pandas as pd

from .data import DataCache
from .range import Ranges


@dataclass
class NodeInfo:
    node_id: int
    imsi: int
    ip: str
    x: float
    y: float


@dataclass
class Topology:
    """Per-DB view of the node graph."""
    nodes: Dict[int, NodeInfo] = field(default_factory=dict)
    all_nodes: Set[int] = field(default_factory=set)
    leader_node: int = 0
    relay_nodes: Set[int] = field(default_factory=set)
    followers: Set[int] = field(default_factory=set)
    intended_set: Dict[int, Set[int]] = field(default_factory=dict)
    tx_ip_to_node: Dict[str, int] = field(default_factory=dict)
    ranges: Ranges = field(default_factory=Ranges.empty)

    # ------------------------------------------------------------------ #
    # Derived accessors
    # ------------------------------------------------------------------ #
    def tx_nodes(self) -> Set[int]:
        """All nodes that emitted traffic in this DB (have TX events)."""
        return set(self.tx_ip_to_node.values())

    def distance(self, a: int, b: int) -> float:
        """Distance in meters between two nodes."""
        if a == b:
            return 0.0
        na, nb = self.nodes[a], self.nodes[b]
        return math.hypot(na.x - nb.x, na.y - nb.y)

    def intended_for(self, tx_node: int) -> Set[int]:
        """Return intended receiver set for the given TX."""
        return self.intended_set.get(tx_node, set())

    def is_relay(self, node_id: int) -> bool:
        return node_id in self.relay_nodes

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def build(cls, cache: DataCache, ranges: Ranges) -> "Topology":
        nodes = cls._build_node_info(cache)
        if not nodes:
            return cls(ranges=ranges)
        all_nodes = set(nodes.keys())

        # TX ips and their node IDs from pktTxRx (TX side only).
        tx_ip_to_node: Dict[str, int] = {}
        pkt = cache.pkt_txrx()
        if pkt is not None and len(pkt):
            tx_only = pkt[pkt["txRx"] == "tx"]
            for r in tx_only.itertuples(index=False):
                if r.srcIp and r.nodeId is not None:
                    tx_ip_to_node[str(r.srcIp)] = int(r.nodeId)
        # Fallback: appPktSeq may have TX events even if pktTxRx is empty
        # (older or partial sims).
        if not tx_ip_to_node:
            app = cache.app_pkt_seq()
            if app is not None and len(app):
                app_tx = app[app["txRx"] == "tx"]
                # appPktSeq doesn't carry srcIp, so use nodeId alone.
                for nid in app_tx["nodeId"].dropna().unique():
                    tx_ip_to_node.setdefault(f"app:{int(nid)}", int(nid))

        # Relay nodes from relayTiming.relayNodeId.
        relay_nodes: Set[int] = set()
        rt = cache.relay_timing()
        if rt is not None and "relayNodeId" in rt.columns and len(rt):
            relay_nodes = {int(n) for n in rt["relayNodeId"].dropna().unique()}

        # Leader detection: smallest nodeId that emitted TX traffic.
        tx_node_set = set(tx_ip_to_node.values())
        leader = min(tx_node_set) if tx_node_set else (min(all_nodes) if all_nodes else 0)
        followers = all_nodes - {leader}

        # Augment tx_ip_to_node with relay IPs (from nodeInfo). The relay's
        # forwarded packets are emitted from the relay's IP even though no
        # "tx" row exists in pktTxRx for it (it uses RelayApp, not OnOff).
        for nid in relay_nodes:
            ni = nodes.get(nid)
            if ni is not None and ni.ip:
                tx_ip_to_node[ni.ip] = nid
        tx_node_set = set(tx_ip_to_node.values())

        # Per-TX intended set.
        intended: Dict[int, Set[int]] = {}
        for tx_node in tx_node_set:
            if not relay_nodes:
                # Broadcast: all other UEs.
                intended[tx_node] = set(followers)
            else:
                # Per-hop partition: TX's intended set = followers on the OTHER
                # side of the closest relay (or all if no relay between).
                intended[tx_node] = cls._partition_for_tx(
                    tx_node, leader, relay_nodes, followers
                )

        return cls(
            nodes=nodes,
            all_nodes=all_nodes,
            leader_node=leader,
            relay_nodes=relay_nodes,
            followers=followers,
            intended_set=intended,
            tx_ip_to_node=tx_ip_to_node,
            ranges=ranges,
        )

    @staticmethod
    def _build_node_info(cache: DataCache) -> Dict[int, NodeInfo]:
        ni = cache.node_info()
        if ni is None or len(ni) == 0:
            return {}
        out: Dict[int, NodeInfo] = {}
        for r in ni.itertuples(index=False):
            try:
                out[int(r.nodeId)] = NodeInfo(
                    node_id=int(r.nodeId),
                    imsi=int(r.imsi) if r.imsi is not None else 0,
                    ip=str(r.ip) if r.ip is not None else "",
                    x=float(r.x) if r.x is not None else 0.0,
                    y=float(r.y) if r.y is not None else 0.0,
                )
            except (AttributeError, ValueError):
                continue
        return out

    @staticmethod
    def _partition_for_tx(tx_node: int, leader: int,
                          relay_nodes: Set[int],
                          followers: Set[int]) -> Set[int]:
        """Partition followers into front-half / back-half relative to TX.

        Convention: TX's intended set is the followers on the OPPOSITE side of
        the closest relay from TX. For the leader (no relay in front of it),
        intended set = front-half. For a relay node, intended set = back-half.
        """
        # Find the relay node "closest in front of" tx_node (smaller nodeId).
        # If tx_node itself is a relay, intended set is followers with larger nodeId.
        if tx_node in relay_nodes:
            return {n for n in followers if n > tx_node}
        # Leader or any non-relay TX: intended set is front-half (smaller nodeId).
        # In the standard 1-relay case this is {1..relay-1}.
        if not relay_nodes:
            return set(followers)
        # Take the smallest relay nodeId > tx_node as the partition boundary.
        relay_boundary = min((r for r in relay_nodes if r > tx_node), default=None)
        if relay_boundary is None:
            # No relay in front of tx_node → all followers are "back" relative to TX.
            return set(followers)
        return {n for n in followers if n < relay_boundary}


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from pathlib import Path
    from .range import Ranges

    db = Path("/Users/hadih/Documents/groupcast-adaptation-ns3/results_c4/"
              "fixed4_s100_seed1/fixed4_s100_seed1.db")
    if not db.exists():
        print(f"  (skipped: {db} not found)")
        raise SystemExit(0)
    with DataCache.open(db, seed=1, run=1, t_start=1.0, t_end=7.0) as cache:
        topo = Topology.build(cache, Ranges.from_cli("320"))
    print(f"  nodes: {sorted(topo.nodes.keys())}")
    print(f"  leader: {topo.leader_node}  relay_nodes: {topo.relay_nodes}")
    print(f"  tx_ip_to_node: {topo.tx_ip_to_node}")
    for tx, intended in topo.intended_set.items():
        print(f"  intended_for({tx}) = {sorted(intended)}")
    print("topology.py smoke test passed.")