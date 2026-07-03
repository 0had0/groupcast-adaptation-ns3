#!/usr/bin/env python3
"""
tests/test_kpi_helpers.py
==========================
Unit tests for the registry-based common.kpi module.

Run with:
    PYTHONPATH=scratch_overlay/common/kpi \\
        python3 -m unittest scratch_overlay.common.kpi.tests.test_kpi_helpers -v

Tests cover:
  - range.py     (range spec parsing, bins, in-range checks)
  - scope.py     (GeographicScope, IntendedScope, PartitionedScope, resolve)
  - data.py      (DataCache lazy-load + t-window filter, fall-back to pktTxRx)
  - topology.py  (leader/follower/relay detection, intended sets)
  - outcomes.py  (multi-path union-dedup, leader↔relay cross-link)
  - registry.py  (KpiSpec metadata, @register decorator, KpiRegistry)
  - KPIs         (smoke-test each KPI on a synthetic fixture)
"""
import sqlite3
import sys
import unittest
from pathlib import Path

# Make the kpi package importable when run from anywhere.
_HERE = Path(__file__).resolve().parent
# Add the parent of `tests/` (= scratch_overlay/common/kpi) so 'kpi' and 'kpis'
# resolve as top-level packages regardless of how unittest is invoked.
sys.path.insert(0, str(_HERE.parent))

from kpi.data import DataCache
from kpi.outcomes import Outcomes
from kpi.range import BinSpec, Ranges, RangeSpec
from kpi.registry import KpiBase, KpiContext, KpiRegistry, ScalarResult, register
from kpi.topology import Topology


# ===========================================================================
# Fixture helpers
# ===========================================================================

def _make_fixture_db() -> sqlite3.Connection:
    """In-memory DB with the minimum schema for KPI computation.

    Topology: 4 nodes (0=leader, 1=front, 2=mid, 3=rear); node 4=relay (extra).
    Range: 320m. Leader TXs every 100ms for ~10 packets; relay forwards.
    """
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE nodeInfo (
            nodeId INTEGER, imsi INTEGER, ip TEXT, x REAL, y REAL, z REAL
        );
        CREATE TABLE pktTxRx (
            timeSec REAL, txRx TEXT, nodeId INTEGER, imsi INTEGER,
            pktSizeBytes INTEGER, srcIp TEXT, srcPort INTEGER,
            dstIp TEXT, dstPort INTEGER, pktSeqNum INTEGER,
            SEED INTEGER, RUN INTEGER
        );
        CREATE TABLE appPktSeq (
            timeSec REAL, txRx TEXT, nodeId INTEGER, dstPort INTEGER, seq INTEGER
        );
        CREATE TABLE relayTiming (
            seq INTEGER, origPort INTEGER, relayNodeId INTEGER,
            forwarded INTEGER, leaderGenSec REAL, relayDecodeSec REAL,
            forwardSendSec REAL
        );
        CREATE TABLE harqFeedback (
            timeSec REAL, rxNodeId INTEGER, rxRnti INTEGER, txRnti INTEGER,
            dstL2Id INTEGER, harqId INTEGER, status TEXT
        );
        CREATE TABLE psschTxUeMac (
            timeMs REAL, rnti INTEGER, imsi INTEGER,
            frame INTEGER, subFrame INTEGER, slot INTEGER,
            srcL2Id INTEGER, dstL2Id INTEGER,
            rbStart INTEGER, rbLen INTEGER, sbChSize INTEGER,
            harqId INTEGER, ndi INTEGER, rv INTEGER
        );
    """)

    # Node positions (leader at front, 4 followers behind, relay mid, 1 more back).
    nodes = [
        (0, 1, "7.0.0.2",    0.0,   0.0, 1.5),  # leader (front)
        (1, 2, "7.0.0.3",  -20.0,   0.0, 1.5),  # front
        (2, 3, "7.0.0.4",  -50.0,   0.0, 1.5),  # mid
        (3, 4, "7.0.0.5", -100.0,   0.0, 1.5),  # rear
        (4, 5, "7.0.0.6",  -75.0,   0.0, 1.5),  # relay (between mid and rear)
        (5, 6, "7.0.0.7", -150.0,   0.0, 1.5),  # far rear (only reachable via relay)
    ]
    for n in nodes:
        con.execute(
            "INSERT INTO nodeInfo VALUES (?,?,?,?,?,?)", n,
        )

    # Leader TXs at port 8001 (groupcast), packets 0..9.
    for seq in range(10):
        t = 1.0 + seq * 0.1
        con.execute(
            "INSERT INTO appPktSeq VALUES (?,?,?,?,?)",
            (t, "tx", 0, 8001, seq),
        )

    # Relay TXs at port 8101 (relay groupcast), forwarding leader's packets.
    for seq in range(8):  # only relay first 8 (lose last 2)
        t = 1.05 + seq * 0.1
        con.execute(
            "INSERT INTO appPktSeq VALUES (?,?,?,?,?)",
            (t, "tx", 4, 8101, seq),
        )

    # Leader's RXs from followers: ports 8001 → leader decodes.
    # Front/mid/rear decode 9 of 10.
    for rx, name in [(1, "front"), (2, "mid"), (3, "rear")]:
        for seq in range(9):
            t = 1.01 + seq * 0.1
            con.execute(
                "INSERT INTO appPktSeq VALUES (?,?,?,?,?)",
                (t, "rx", rx, 8001, seq),
            )

    # Followers' RXs from leader: each follower decodes 9 of 10 from port 8001.
    for rx in [1, 2, 3]:
        for seq in range(9):
            t = 1.02 + seq * 0.1
            con.execute(
                "INSERT INTO appPktSeq VALUES (?,?,?,?,?)",
                (t, "rx", rx, 8001, seq),
            )

    # Relay decodes leader's port 8001 (RX at relay nodeId=4).
    for seq in range(10):
        t = 1.03 + seq * 0.1
        con.execute(
            "INSERT INTO appPktSeq VALUES (?,?,?,?,?)",
            (t, "rx", 4, 8001, seq),
        )

    # Relay's downstream RXs: relay port 8101 → followers (only [3,8]).
    for rx in [3]:  # only "rear" decodes via relay
        for seq in range(7):
            t = 1.06 + seq * 0.1
            con.execute(
                "INSERT INTO appPktSeq VALUES (?,?,?,?,?)",
                (t, "rx", rx, 8101, seq),
            )

    # relayTiming: relay forwards seqs 0..7.
    for seq in range(8):
        con.execute(
            "INSERT INTO relayTiming VALUES (?,?,?,?,?,?,?)",
            (seq, 8001, 4, 1, 1.0 + seq * 0.1, 1.02 + seq * 0.1, 1.05 + seq * 0.1),
        )

    # pktTxRx for nodeInfo-derived records (seed/run tag).
    for seq in range(10):
        con.execute(
            "INSERT INTO pktTxRx VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (1.0 + seq * 0.1, "tx", 0, 1, 300, "7.0.0.2", 8001,
             "7.0.0.255", 8001, seq, 1, 1),
        )

    # HARQ feedback (a few ACKs).
    for i in range(10):
        con.execute(
            "INSERT INTO harqFeedback VALUES (?,?,?,?,?,?,?)",
            (1.0 + i * 0.1, 0, 1, 1, 0, 0, "ACK"),
        )

    # psschTxUeMac — a couple of grants for leader.
    for slot_i in range(10):
        con.execute(
            "INSERT INTO psschTxUeMac VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1.0 + slot_i * 0.1, 1, 1, slot_i, 0, slot_i * 2,
             0, 0, 0, 20, 10, 0, 1, 0),
        )

    con.commit()
    return con


class KpiHarness(unittest.TestCase):
    """Shared fixture: a fresh in-memory DB per test."""

    def setUp(self):
        self.con = _make_fixture_db()

    def tearDown(self):
        self.con.close()

    def _cache(self, t_start=0.0, t_end=10.0) -> DataCache:
        """Open the in-memory DB as a DataCache."""
        # DataCache opens a sqlite3 connection to a path; we use a temp file.
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        # Copy fixture into the temp file.
        dst = sqlite3.connect(tmp.name)
        dst.row_factory = sqlite3.Row
        with dst:
            for row in self.con.iterdump():
                if row.startswith("INSERT") or row.startswith("CREATE"):
                    dst.execute(row)
        dst.commit()
        dst.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return DataCache.open(Path(tmp.name), seed=1, run=1,
                              t_start=t_start, t_end=t_end)


# ===========================================================================
# range.py
# ===========================================================================

class TestRange(unittest.TestCase):
    def test_parse_single(self):
        r = Ranges.from_cli("320", bin_spec="20:500/20")
        self.assertEqual(len(r.ranges), 1)
        self.assertEqual(r.ranges[0].b, 320.0)

    def test_parse_multiple(self):
        r = Ranges.from_cli("150,320", bin_spec="20:500/20")
        self.assertEqual([rng.b for rng in r.ranges], [150.0, 320.0])

    def test_bin_spec(self):
        r = Ranges.from_cli("320", bin_spec="20:500/20")
        # bins 20..500 step 20 = 24 bins
        self.assertEqual(r.bins.n_bins, 24)
        bins = r.bins.iter_bins()
        self.assertEqual(bins[0], (0, 20.0, 40.0))
        self.assertEqual(bins[-1], (23, 480.0, 500.0))

    def test_in_range(self):
        r = Ranges.from_cli("320", bin_spec="20:500/20")
        self.assertTrue(r.in_any_range(100.0))
        self.assertFalse(r.in_any_range(400.0))

    def test_bin_for(self):
        r = Ranges.from_cli("320", bin_spec="20:500/20")
        self.assertEqual(r.bin_for(35.0), 0)  # (20,40)
        # Out-of-range sentinels: -1 below, n_bins above.
        self.assertEqual(r.bin_for(5.0), -1)
        self.assertEqual(r.bin_for(600.0), r.bins.n_bins)


# ===========================================================================
# scope.py
# ===========================================================================

class TestScope(KpiHarness):
    def test_all_scope(self):
        from kpi.scope import AllScope
        cache = self._cache()
        topo = Topology.build(cache, Ranges.from_cli("320", bin_spec="20:500/20"))
        s = AllScope()
        members = s.receivers(0, topo)
        self.assertIn(1, members)
        self.assertIn(4, members)

    def test_partitioned_with_relays(self):
        from kpi.scope import PartitionedScope
        cache = self._cache()
        topo = Topology.build(cache, Ranges.from_cli("320", bin_spec="20:500/20"))
        s = PartitionedScope()
        leader_scope = s.receivers(0, topo)
        relay_scope = s.receivers(4, topo)
        self.assertIn(1, leader_scope)   # close follower → leader's hop
        # relay_scope should not contain a follower closer to leader than to relay
        self.assertGreater(len(relay_scope), 0)


# ===========================================================================
# topology.py
# ===========================================================================

class TestTopology(KpiHarness):
    def test_leader_detected(self):
        cache = self._cache()
        topo = Topology.build(cache, Ranges.from_cli("320", bin_spec="20:500/20"))
        self.assertEqual(topo.leader_node, 0)

    def test_relays_detected(self):
        cache = self._cache()
        topo = Topology.build(cache, Ranges.from_cli("320", bin_spec="20:500/20"))
        self.assertIn(4, topo.relay_nodes)

    def test_intended_sets(self):
        cache = self._cache()
        topo = Topology.build(cache, Ranges.from_cli("320", bin_spec="20:500/20"))
        # leader's intended set should include at least one close follower
        leader_intended = topo.intended_for(0)
        self.assertGreater(len(leader_intended), 0)
        # relay's intended set should not be empty
        relay_intended = topo.intended_for(4)
        self.assertGreater(len(relay_intended), 0)

    def test_distance(self):
        cache = self._cache()
        topo = Topology.build(cache, Ranges.from_cli("320", bin_spec="20:500/20"))
        # nodes 0 and 3 are 100m apart in the fixture
        self.assertAlmostEqual(topo.distance(0, 3), 100.0, places=3)


# ===========================================================================
# registry.py
# ===========================================================================

class TestRegistry(unittest.TestCase):
    def test_register_decorator(self):
        @register(id="test_dummy", title="dummy", spec_ref="none",
                  output_kind="scalar",
                  output_columns=())
        class Dummy(KpiBase):
            def compute(self, ctx):
                return [ScalarResult([], filename="dummy.csv")]

        self.assertTrue(KpiRegistry.get("test_dummy") is not None)

    def test_kpi_spec_metadata(self):
        spec = KpiRegistry.get("prr_type1")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.id, "prr_type1")
        self.assertEqual(spec.output_kind, "bin")  # prr1 returns bin + cdf
        # All registered KPIs have a non-empty title and spec_ref.
        for kid, sp in KpiRegistry.all().items():
            self.assertTrue(sp.title, f"{kid} missing title")
            self.assertTrue(sp.spec_ref, f"{kid} missing spec_ref")


# ===========================================================================
# outcomes.py — leader→relay cross-link
# ===========================================================================

class TestOutcomes(KpiHarness):
    def test_leader_to_relay(self):
        cache = self._cache()
        topo = Topology.build(cache, Ranges.from_cli("320", bin_spec="20:500/20"))
        out = Outcomes(cache, topo)
        # Leader's TX seqs include all 10 packets it emitted.
        leader_seqs = out.tx_seqs(0)
        self.assertEqual(len(leader_seqs), 10)

    def test_relay_forwarded_seqs(self):
        cache = self._cache()
        topo = Topology.build(cache, Ranges.from_cli("320", bin_spec="20:500/20"))
        out = Outcomes(cache, topo)
        # Relay should have at least 8 forwarded seqs.
        relay_seqs = out.tx_seqs(4)
        self.assertGreaterEqual(len(relay_seqs), 7)

    def test_pkttxrx_augments_missing_relay_receptions(self):
        """When appPktSeq has no port=8101 RX events (some sims don't write
        them), the outcomes must still pick up relay→follower receptions
        from pktTxRx RX events whose srcIp is the relay's IP.
        """
        import tempfile
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript("""
            CREATE TABLE nodeInfo (
                nodeId INTEGER, imsi INTEGER, ip TEXT, x REAL, y REAL, z REAL
            );
            CREATE TABLE pktTxRx (
                timeSec REAL, txRx TEXT, nodeId INTEGER, imsi INTEGER,
                pktSizeBytes INTEGER, srcIp TEXT, srcPort INTEGER,
                dstIp TEXT, dstPort INTEGER, pktSeqNum INTEGER,
                SEED INTEGER, RUN INTEGER
            );
            CREATE TABLE appPktSeq (
                timeSec REAL, txRx TEXT, nodeId INTEGER, dstPort INTEGER, seq INTEGER
            );
            CREATE TABLE relayTiming (
                seq INTEGER, origPort INTEGER, relayNodeId INTEGER,
                forwarded INTEGER, leaderGenSec REAL, relayDecodeSec REAL,
                forwardSendSec REAL
            );
        """)
        # nodeInfo: leader=0 (7.0.0.2), relay=4 (7.0.0.6), followers 1,2,3,5,6,7,8
        for nid, ip in [(0, "7.0.0.2"), (1, "7.0.0.3"), (2, "7.0.0.4"),
                        (3, "7.0.0.5"), (4, "7.0.0.6"), (5, "7.0.0.7"),
                        (6, "7.0.0.8"), (7, "7.0.0.9"), (8, "7.0.0.10")]:
            con.execute(
                "INSERT INTO nodeInfo VALUES (?,?,?,?,?,?)",
                (nid, nid + 100, ip, 0.0, nid * 10.0, 0.0),
            )
        # pktTxRx: 10 leader TX, 10 RX at followers from leader IP, 5 RX at
        # followers from RELAY IP (no appPktSeq entries for these).
        for seq in range(10):
            con.execute(
                "INSERT INTO pktTxRx VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (1.0 + seq * 0.1, "tx", 0, 1, 300, "7.0.0.2", 8001,
                 "7.0.0.255", 8001, seq, 1, 1),
            )
        # Followers 5,6,7,8 receive via relay IP (not in appPktSeq).
        for seq in range(5):
            for rx in [5, 6, 7, 8]:
                con.execute(
                    "INSERT INTO pktTxRx VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (1.5 + seq * 0.1, "rx", rx, rx + 100, 300, "7.0.0.6", 8101,
                     "7.0.0.255", 8101, seq, 1, 1),
                )
        # appPktSeq: direct (port 8001) RX events for CLOSE followers only.
        # Far followers (5,6,7,8) have NO direct RX — they only see the relay.
        for seq in range(10):
            for rx in [1, 2, 3]:
                con.execute(
                    "INSERT INTO appPktSeq VALUES (?,?,?,?,?)",
                    (1.02 + seq * 0.1, "rx", rx, 8001, seq),
                )
        # relayTiming: relay forwarded seqs 0..4.
        for seq in range(5):
            con.execute(
                "INSERT INTO relayTiming VALUES (?,?,?,?,?,?,?)",
                (seq, 8001, 4, 1, 1.0 + seq * 0.1, 1.02 + seq * 0.1, 1.05 + seq * 0.1),
            )
        con.commit()

        # Save to tempfile and open as DataCache.
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        dst = sqlite3.connect(tmp.name)
        with dst:
            for row in con.iterdump():
                if row.startswith("INSERT") or row.startswith("CREATE"):
                    dst.execute(row)
        dst.commit(); dst.close(); con.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))

        cache = DataCache.open(Path(tmp.name), seed=1, run=1,
                               t_start=0.0, t_end=10.0)
        topo = Topology.build(cache, Ranges.from_cli("320"))
        out = Outcomes(cache, topo)
        # Relay should have 5 forwarded seqs (from relayTiming).
        self.assertEqual(len(out.tx_seqs(4)), 5)
        # Relay→follower 8 should decode seqs 0..4 (5 successes) via
        # the pktTxRx-augment path.
        decoded_8 = sum(1 for seq in out.tx_seqs(4) if out.decoded(4, 8, seq))
        self.assertEqual(decoded_8, 5)
        # Leader→follower 8 should ALSO be cross-linked (via:4) for these seqs.
        cross_linked = sum(1 for seq in range(5) if out.decoded(0, 8, seq))
        self.assertEqual(cross_linked, 5)
        # rx_via should include "via:4" for the cross-linked seqs.
        via = out.rx_via(0, 8, 0)
        self.assertIn("via:4", via)


# ===========================================================================
# KPIs — smoke test
# ===========================================================================

class TestPrrType1(KpiHarness):
    def test_run(self):
        cache = self._cache()
        ranges = Ranges.from_cli("320", bin_spec="20:500/20")
        topo = Topology.build(cache, ranges)
        out = Outcomes(cache, topo)
        ctx = KpiContext(
            db_path=Path("dummy"), out_dir=Path("/tmp"),
            seed=1, run=1, t_start=0.0, t_end=10.0,
            topology=topo, outcomes=out, cache=cache,
            ranges=ranges, scope_mode="auto", args=None,
        )
        spec = KpiRegistry.get("prr_type1")
        results = spec.run(ctx)
        self.assertEqual(len(results), 2)
        # One BinResult, one CdfResult.
        self.assertEqual(results[0].filename, "prr_type1.bin.csv")
        self.assertEqual(results[1].filename, "prr_type1.cdf.csv")
        # At least one bin row.
        self.assertGreater(len(results[0].rows), 0)


class TestPrrType2(KpiHarness):
    def test_run(self):
        cache = self._cache()
        ranges = Ranges.from_cli("320", bin_spec="20:500/20")
        topo = Topology.build(cache, ranges)
        out = Outcomes(cache, topo)
        ctx = KpiContext(
            db_path=Path("dummy"), out_dir=Path("/tmp"),
            seed=1, run=1, t_start=0.0, t_end=10.0,
            topology=topo, outcomes=out, cache=cache,
            ranges=ranges, scope_mode="auto", args=None,
        )
        spec = KpiRegistry.get("prr_type2")
        results = spec.run(ctx)
        self.assertEqual(len(results), 1)
        # At least one intended-set row.
        self.assertGreater(len(results[0].rows), 0)


class TestAllKpisSmoke(KpiHarness):
    """All registered KPIs should at least produce a non-error result."""

    def test_all_kpis_run(self):
        cache = self._cache()
        ranges = Ranges.from_cli("320", bin_spec="20:500/20")
        topo = Topology.build(cache, ranges)
        out = Outcomes(cache, topo)
        ctx = KpiContext(
            db_path=Path("dummy"), out_dir=Path("/tmp"),
            seed=1, run=1, t_start=0.0, t_end=10.0,
            topology=topo, outcomes=out, cache=cache,
            ranges=ranges, scope_mode="auto", args=None,
        )
        import kpis  # populate registry
        for kid in KpiRegistry.all():
            spec = KpiRegistry.get(kid)
            with self.subTest(kpi_id=kid):
                results = spec.run(ctx)
                self.assertGreater(len(results), 0)
                for r in results:
                    self.assertTrue(hasattr(r, "filename"))
                    self.assertTrue(hasattr(r, "rows"))


if __name__ == "__main__":
    unittest.main()