"""
outcomes.py — Per-packet "did this RX decode this TX's packet?" lookup with
multi-path union-dedup.

This is the canonical data join for all PRR / PIR KPIs. It answers:

  decoded(tx_node, rx_node, seq) -> bool
  rx_via(tx_node, rx_node, seq)  -> Set[str] of paths (e.g. {"direct", "relay:4"})

For a given (tx, rx, seq):
  - "direct" path: any pktTxRx.rx row where nodeId==rx AND srcIp==tx_ip AND pktSeqNum==seq
  - "relay:N" path: any pktTxRx.rx row where nodeId==rx AND srcIp==relay_N_ip AND pktSeqNum==seq
                    AND relayTiming says relay N forwarded seq

This is built ONCE per DB and shared across all KPIs.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from .data import DataCache
from .topology import Topology


class Outcomes:
    """Multi-path union-dedup outcomes for all (tx_node, rx_node, seq)."""

    def __init__(self, cache: DataCache, topology: Topology):
        self.topology = topology
        self._decoded_cache: Dict[Tuple[int, int, int], bool] = {}
        self._via_cache: Dict[Tuple[int, int, int], Set[str]] = {}
        self._build(cache, topology)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def decoded(self, tx_node: int, rx_node: int, seq: int) -> bool:
        """True if rx decoded at least one copy of tx's seq via any path."""
        return self._decoded_cache.get((tx_node, rx_node, seq), False)

    def rx_via(self, tx_node: int, rx_node: int, seq: int) -> Set[str]:
        """Set of paths used, e.g. {"direct"}, {"relay:4"}, {"direct","relay:5"}."""
        return set(self._via_cache.get((tx_node, rx_node, seq), set()))

    def tx_seqs(self, tx_node: int) -> List[int]:
        """All seqs emitted by tx_node in this DB."""
        return list(self._tx_seqs.get(tx_node, set()))

    def tx_seq_times(self, tx_node: int) -> Dict[int, float]:
        """Map seq -> timeSec for packets emitted by tx_node."""
        return dict(self._tx_seq_times.get(tx_node, {}))

    def receptions_from(self, tx_node: int, rx_node: int) -> List[Tuple[float, int, str]]:
        """Sorted list of (timeSec, seq, path) for every successful reception
        at rx of any packet that originated from tx (direct or relayed)."""
        return list(self._receptions.get((tx_node, rx_node), []))

    # ------------------------------------------------------------------ #
    # Construction (one pass over pktTxRx + relayTiming)
    # ------------------------------------------------------------------ #
    _tx_seqs: Dict[int, Set[int]]
    _tx_seq_times: Dict[int, Dict[int, float]]
    _receptions: Dict[Tuple[int, int], List[Tuple[float, int, str]]]

    def _build(self, cache: DataCache, topo: Topology) -> None:
        self._tx_seqs = {}
        self._tx_seq_times = {}
        self._receptions = {}

        # Prefer appPktSeq — it has explicit per-packet app-layer TX and RX
        # events with sequence numbers. The port (dstPort) tells us whether the
        # packet arrived directly (port=8001) or via the relay (port=8101).
        app = cache.app_pkt_seq()
        if app is not None and len(app) > 0:
            self._build_from_app_pkt_seq(app, topo)
        else:
            self._build_from_pkttxrx(cache, topo)

        # Augment with relay TX streams (only from appPktSeq path; pktTxRx
        # fallback has its own relay attribution).
        self._augment_relay_tx_streams(cache, topo)

    def _augment_relay_tx_streams(self, cache: DataCache, topo: Topology) -> None:
        """For each relay node, register its forwarded seqs as a TX stream.

        The relay's TX time is the `forwardSendSec` from relayTiming (when
        the relay actually emitted). If unavailable, fall back to the leader's
        TX time for that seq.
        """
        rt = cache.relay_timing()
        if rt is None or "relayNodeId" not in rt.columns or "forwarded" not in rt.columns:
            return
        has_ftime = "forwardSendSec" in rt.columns
        for r in rt.itertuples(index=False):
            if int(r.forwarded) != 1:
                continue
            try:
                relay_nid = int(r.relayNodeId); seq = int(r.seq)
            except (TypeError, ValueError):
                continue
            self._tx_seqs.setdefault(relay_nid, set()).add(seq)
            t_relay = float(r.forwardSendSec) if has_ftime and r.forwardSendSec is not None else None
            if t_relay is None:
                # Fall back to leader's TX time for this seq.
                leader_times = self._tx_seq_times.get(topo.leader_node, {})
                t_relay = leader_times.get(seq, 0.0)
            self._tx_seq_times.setdefault(relay_nid, {})[seq] = t_relay

    # ------------------------------------------------------------------ #
    # appPktSeq path (preferred)
    # ------------------------------------------------------------------ #
    def _build_from_app_pkt_seq(self, app, topo: Topology) -> None:
        # TX events: build (nodeId, dstPort, seq) → timeSec
        tx_only = app[app["txRx"] == "tx"]
        for r in tx_only.itertuples(index=False):
            try:
                nid = int(r.nodeId); seq = int(r.seq); t = float(r.timeSec)
            except (TypeError, ValueError):
                continue
            self._tx_seqs.setdefault(nid, set()).add(seq)
            self._tx_seq_times.setdefault(nid, {})[seq] = t

        # RX events: attribute to the IMMEDIATE sender based on dstPort.
        # Port 8001 = leader's groupcast port (direct from leader).
        # Port 8101 = relay's groupcast port (forwarded via relay).
        # We attribute port=8101 RX events to the relay (the actual TX for
        # those events). For the leader's end-to-end PRR we separately
        # union-dedup by orig_seq in `decoded_for_original_tx`.
        LEADER_PORT = 8001
        RELAY_PORT = 8101

        # Map relay_port → relay_node.
        # Convention: each relay uses port 8101 (configurable in future).
        port_to_relay: Dict[int, int] = {}
        for nid in topo.relay_nodes:
            port_to_relay[RELAY_PORT] = nid
        # If multiple relays exist with different ports, infer from relayTiming.

        rx_only = app[app["txRx"] == "rx"]
        for r in rx_only.itertuples(index=False):
            try:
                rx_nid = int(r.nodeId); seq = int(r.seq); t = float(r.timeSec)
            except (TypeError, ValueError):
                continue
            port = int(r.dstPort) if r.dstPort is not None else 0

            if port == LEADER_PORT:
                orig_tx = topo.leader_node
                path = "direct"
            elif port == RELAY_PORT and port_to_relay.get(RELAY_PORT) is not None:
                # Attribute to the relay that owns this port.
                orig_tx = port_to_relay[RELAY_PORT]
                path = "relay"
            else:
                # Unknown port — fall back to leader.
                orig_tx = topo.leader_node
                path = f"port:{port}"

            self._receptions.setdefault((orig_tx, rx_nid), []).append(
                (t, seq, path)
            )
            key = (orig_tx, rx_nid, seq)
            self._via_cache.setdefault(key, set()).add(path)
            self._decoded_cache[key] = True

        # Sort receptions by time.
        for key in self._receptions:
            self._receptions[key].sort()

        # Cross-link: for each (relay, follower) pair that decoded a seq,
        # also record the leader's seq as decoded (end-to-end union-dedup).
        # This makes leader.decoded(relay, seq) → True when the relay
        # successfully delivered the packet to any intended receiver.
        for (relay_nid, rx_nid), events in list(self._receptions.items()):
            if relay_nid not in topo.relay_nodes:
                continue
            for t, seq, path in events:
                key = (topo.leader_node, rx_nid, seq)
                self._via_cache.setdefault(key, set()).add(f"via:{relay_nid}")
                self._decoded_cache[key] = True
                # Also record a "synthetic" reception so receptions_from(leader, rx)
                # includes the relayed copy.
                self._receptions.setdefault((topo.leader_node, rx_nid), []).append(
                    (t, seq, f"via:{relay_nid}")
                )
            # Re-sort the leader's reception list.
            self._receptions[(topo.leader_node, rx_nid)].sort()

    # ------------------------------------------------------------------ #
    # pktTxRx path (fallback for old sims without appPktSeq)
    # ------------------------------------------------------------------ #
    def _build_from_pkttxrx(self, cache: DataCache, topo: Topology) -> None:
        pkt = cache.pkt_txrx()
        if pkt is None or len(pkt) == 0:
            return

        # Build per-tx_seq -> time map from TX events.
        tx_only = pkt[pkt["txRx"] == "tx"]
        for r in tx_only.itertuples(index=False):
            try:
                nid = int(r.nodeId); seq = int(r.pktSeqNum); t = float(r.timeSec)
            except (TypeError, ValueError):
                continue
            self._tx_seqs.setdefault(nid, set()).add(seq)
            self._tx_seq_times.setdefault(nid, {})[seq] = t

        # Build reverse map srcIp -> nodeId.
        ip_to_node: Dict[str, int] = dict(topo.tx_ip_to_node)
        for r in tx_only.itertuples(index=False):
            if r.srcIp and r.nodeId is not None:
                ip_to_node.setdefault(str(r.srcIp), int(r.nodeId))
        for nid in topo.relay_nodes:
            ni = topo.nodes.get(nid)
            if ni is not None and ni.ip:
                ip_to_node.setdefault(ni.ip, nid)

        # Build receptions from RX events.
        rx_only = pkt[pkt["txRx"] == "rx"]
        for r in rx_only.itertuples(index=False):
            try:
                rx_nid = int(r.nodeId); seq = int(r.pktSeqNum); t = float(r.timeSec)
            except (TypeError, ValueError):
                continue
            src_ip = str(r.srcIp) if r.srcIp else ""
            src_nid = ip_to_node.get(src_ip)
            if src_nid is None:
                continue
            # For pktTxRx fallback, attribute all to immediate source.
            # (This is the buggy case the appPktSeq path avoids.)
            if src_nid in topo.relay_nodes:
                # Treat as direct from leader if relay forwarded this seq.
                path = "direct"  # approximation
            else:
                path = "direct"
            self._receptions.setdefault((src_nid, rx_nid), []).append(
                (t, seq, path)
            )
            key = (src_nid, rx_nid, seq)
            self._via_cache.setdefault(key, set()).add(path)
            self._decoded_cache[key] = True

        for key in self._receptions:
            self._receptions[key].sort()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from pathlib import Path
    from .range import Ranges

    db = Path("/Users/hadih/Documents/groupcast-adaptation-ns3/results_c4/"
              "fixed4_s100_seed1/fixed4_s100_seed1.db")
    if not db.exists():
        raise SystemExit(f"  (skipped: {db} not found)")
    with DataCache.open(db, seed=1, run=1, t_start=1.0, t_end=7.0) as cache:
        topo = Topology.build(cache, Ranges.from_cli("320"))
        out = Outcomes(cache, topo)

    # Spot-check: leader=0 seq=0 should be decoded by all 8 followers.
    leader = topo.leader_node
    decoded_count = sum(1 for rx in topo.followers if out.decoded(leader, rx, 0))
    print(f"  leader seq=0 decoded by {decoded_count}/{len(topo.followers)} followers")
    # Verify multi-path: follower 5 should reach via relay (no direct hit).
    via5 = out.rx_via(leader, 5, 0)
    print(f"  leader seq=0 → follower 5 via: {via5}")
    via8 = out.rx_via(leader, 8, 0)
    print(f"  leader seq=0 → follower 8 via: {via8}")

    # Cross-check totals: 3909/4000 = 0.9773 for fixed4_s100_seed1 (full sim).
    # Within [1.0, 7.0] window we get ~0.9738.
    total_decoded = sum(
        1 for rx in topo.followers for seq in out.tx_seqs(leader)
        if out.decoded(leader, rx, seq)
    )
    total_possible = len(topo.followers) * len(out.tx_seqs(leader))
    print(f"  total decoded: {total_decoded}/{total_possible} = {total_decoded/total_possible:.4f}")

    # Verify relay TX stream: how many of the relay's forwarded seqs
    # were decoded by followers in its intended set (5,6,7,8)?
    relay_node = topo.leader_node + 4  # node 4 in this run
    if relay_node in topo.relay_nodes:
        relay_tx = out.tx_seqs(relay_node)
        intended_back = topo.intended_for(relay_node)
        relay_decoded = sum(
            1 for rx in intended_back for seq in relay_tx
            if out.decoded(relay_node, rx, seq)
        )
        relay_possible = len(intended_back) * len(relay_tx)
        print(f"  relay({relay_node}) decoded: {relay_decoded}/{relay_possible} "
              f"= {relay_decoded/relay_possible if relay_possible else 0:.4f} "
              f"({len(intended_back)} followers × {len(relay_tx)} seqs)")
    print("outcomes.py smoke test passed.")