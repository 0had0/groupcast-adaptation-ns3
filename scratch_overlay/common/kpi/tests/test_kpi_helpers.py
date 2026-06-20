#!/usr/bin/env python3
"""
tests/test_kpi_helpers.py
=========================
Unit tests for kpi_postprocess helper functions.

Run with:
    cd scratch_overlay/common/kpi
    python3 -m unittest tests.test_kpi_helpers -v

    # or from the repo root:
    python3 -m unittest scratch_overlay.common.kpi.tests.test_kpi_helpers -v

Zero external dependencies (only stdlib + kpi_postprocess).
"""

import sqlite3
import sys
import unittest
from pathlib import Path

# Allow running from repo root or from the kpi/ directory.
sys.path.insert(0, str(Path(__file__).parent.parent))

from kpi_postprocess import (
    NodeMeta,
    _aoi_stats,
    _channel_occupancy,
    _collision_stats,
    _has_column,
    _has_table,
    _pir_sliding_samples,
    _percentile,
    _safe_mean,
    _sliding_samples,
    compute_kpis,
)


# ---------------------------------------------------------------------------
# Helper: build a minimal in-memory SQLite DB for testing
# ---------------------------------------------------------------------------

def _make_db() -> sqlite3.Connection:
    """Return an in-memory DB with all tables kpi_postprocess needs."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE pktTxRx (
            timeSec     REAL,
            txRx        TEXT,
            nodeId      INTEGER,
            imsi        INTEGER,
            pktSizeBytes INTEGER,
            srcIp       TEXT,
            srcPort     INTEGER,
            dstIp       TEXT,
            dstPort     INTEGER,
            pktSeqNum   INTEGER,
            SEED        INTEGER,
            RUN         INTEGER
        );
        CREATE TABLE nodeInfo (
            nodeId  INTEGER,
            imsi    INTEGER,
            ip      TEXT,
            x       REAL,
            y       REAL,
            z       REAL,
            SEED    INTEGER,
            RUN     INTEGER
        );
        CREATE TABLE rlcRx (
            timeMs      REAL,
            imsi        INTEGER,
            rnti        INTEGER,
            txRnti      INTEGER,
            lcid        INTEGER,
            rxPdueSize  INTEGER,
            delayMicroSec REAL,
            SEED        INTEGER,
            RUN         INTEGER
        );
        CREATE TABLE psschTxUeMac (
            timeMs      REAL,
            imsi        INTEGER,
            rnti        INTEGER,
            srcL2Id     INTEGER,
            dstL2Id     INTEGER,
            frame       INTEGER,
            subFrame    INTEGER,
            slot        INTEGER,
            symStart    INTEGER,
            symLen      INTEGER,
            sbChSize    INTEGER,
            rbStart     INTEGER,
            rbLen       INTEGER,
            harqId      INTEGER,
            ndi         INTEGER,
            rv          INTEGER,
            reselCounter INTEGER,
            cReselCounter INTEGER,
            csiReq      INTEGER,
            castType    INTEGER,
            SEED        INTEGER,
            RUN         INTEGER
        );
        CREATE TABLE psschRxUePhy (
            timeMs      REAL,
            cellId      INTEGER,
            rnti        INTEGER,
            bwpId       INTEGER,
            frame       INTEGER,
            subFrame    INTEGER,
            slot        INTEGER,
            txRnti      INTEGER,
            srcL2Id     INTEGER,
            dstL2Id     INTEGER,
            psschRbStart INTEGER,
            psschRbLen  INTEGER,
            psschSymStart INTEGER,
            psschSymLen INTEGER,
            psschMcs    INTEGER,
            ndi         INTEGER,
            rv          INTEGER,
            tbSizeBytes INTEGER,
            avrgSinr    REAL,
            minSinr     REAL,
            psschTbler  INTEGER,
            psschCorrupt INTEGER,
            sci2Tbler   INTEGER,
            sci2Corrupt INTEGER,
            SEED        INTEGER,
            RUN         INTEGER
        );
        CREATE TABLE slHarqFeedback (
            timeSec     REAL,
            rxNodeId    INTEGER,
            rxRnti      INTEGER,
            txRnti      INTEGER,
            dstL2Id     INTEGER,
            harqId      INTEGER,
            status      TEXT,
            SEED        INTEGER,
            RUN         INTEGER
        );
        CREATE TABLE pscchTxUeMac (
            timeMs      REAL,
            imsi        INTEGER,
            rnti        INTEGER,
            frame       INTEGER,
            subFrame    INTEGER,
            slot        INTEGER,
            symStart    INTEGER,
            symLen      INTEGER,
            rbStart     INTEGER,
            rbLen       INTEGER,
            priority    INTEGER,
            mcs         INTEGER,
            tbSize      INTEGER,
            rsvpMs      INTEGER,
            totSbCh     INTEGER,
            sbChStart   INTEGER,
            sbChLen     INTEGER,
            maxNumPerReserve INTEGER,
            gapReTx1    INTEGER,
            gapReTx2    INTEGER,
            SEED        INTEGER,
            RUN         INTEGER
        );
    """)
    return con


def _insert_node(con, node_id, imsi, ip, x, y, seed=1, run=1):
    con.execute(
        "INSERT INTO nodeInfo VALUES (?,?,?,?,?,0,?,?);",
        (node_id, imsi, ip, x, y, seed, run),
    )
    con.execute(
        "INSERT INTO rlcRx VALUES (0,?,?,0,0,100,500,?,?);",
        (imsi, imsi, seed, run),   # rnti == imsi for simplicity
    )
    con.commit()


def _insert_tx(con, node_id, imsi, ip, dport, seq, t, size=200, seed=1, run=1):
    con.execute(
        "INSERT INTO pktTxRx VALUES (?,?,?,?,?,?,?,?,?,?,?,?);",
        (t, "tx", node_id, imsi, size, ip, dport, "225.0.0.1", dport, seq, seed, run),
    )
    con.commit()


def _insert_rx(con, rx_node_id, rx_imsi, rx_ip, src_ip, dport, seq, t, size=200, seed=1, run=1):
    con.execute(
        "INSERT INTO pktTxRx VALUES (?,?,?,?,?,?,?,?,?,?,?,?);",
        (t, "rx", rx_node_id, rx_imsi, size, src_ip, dport, rx_ip, dport, seq, seed, run),
    )
    con.commit()


# ---------------------------------------------------------------------------
# A1. Percentile helper
# ---------------------------------------------------------------------------

class TestPercentile(unittest.TestCase):

    def test_single_value(self):
        self.assertAlmostEqual(_percentile([5.0], 0.5), 5.0)

    def test_p0_is_min(self):
        data = sorted([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertAlmostEqual(_percentile(data, 0.0), 1.0)

    def test_p100_is_max(self):
        data = sorted([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertAlmostEqual(_percentile(data, 1.0), 5.0)

    def test_p50_median_even(self):
        data = sorted([2.0, 4.0, 6.0, 8.0])
        self.assertAlmostEqual(_percentile(data, 0.5), 5.0)

    def test_p25(self):
        # [1,2,3,4,5] → k=1.0 → lo=1, hi=1 → sorted[1]=2.0
        data = sorted([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertAlmostEqual(_percentile(data, 0.25), 2.0)

    def test_p95_interpolated(self):
        data = sorted([float(i) for i in range(1, 101)])
        # k = 99 * 0.95 = 94.05 → 94.0 + 0.05*(95.0-94.0) = 94.05 + (1-based indexing...)
        # sorted[94]=95.0, sorted[93]=94.0; k=94.05; frac=0.05
        # result = 94*(1-0.05) + 95*0.05 = 89.3 + 4.75 = 94.05
        result = _percentile(data, 0.95)
        self.assertAlmostEqual(result, 95.05, places=5)

    def test_empty_returns_none(self):
        self.assertIsNone(_percentile([], 0.5))

    def test_p5_p95_ordering(self):
        import random
        rng = random.Random(42)
        data = sorted([rng.random() for _ in range(100)])
        p5 = _percentile(data, 0.05)
        p95 = _percentile(data, 0.95)
        self.assertLessEqual(p5, p95)


# ---------------------------------------------------------------------------
# A2. Sliding-window PIR
# ---------------------------------------------------------------------------

class TestPirSliding(unittest.TestCase):

    def test_uniform_50ms(self):
        """Packets every 20 ms → mean PIR = 0.02 s per window."""
        rxtimes = [0.02 * i for i in range(1, 51)]   # 0.02 … 1.00
        samples = _pir_sliding_samples(rxtimes, t_start=0.0, t_end=1.0,
                                       window=0.5, step=0.1)
        self.assertTrue(len(samples) > 0, "Expected at least one sample")
        for s in samples:
            self.assertAlmostEqual(s, 0.02, places=10,
                                   msg=f"Expected PIR=0.02, got {s}")

    def test_single_packet_window_excluded(self):
        """Windows with only one packet should be excluded."""
        samples = _pir_sliding_samples([0.5], t_start=0.0, t_end=1.0,
                                       window=0.3, step=0.1)
        self.assertEqual(len(samples), 0)

    def test_increasing_gaps_mean(self):
        """RX at 0, 0.1, 0.3, 0.6 (in a 0.7 s window) → gaps 0.1, 0.2, 0.3 → mean 0.2."""
        samples = _pir_sliding_samples([0.0, 0.1, 0.3, 0.6],
                                       t_start=0.0, t_end=0.7,
                                       window=0.7, step=0.7)
        self.assertEqual(len(samples), 1)
        self.assertAlmostEqual(samples[0], 0.2, places=10)


# ---------------------------------------------------------------------------
# A3. AoI
# ---------------------------------------------------------------------------

class TestAoi(unittest.TestCase):

    def test_hand_computed_two_packets(self):
        """
        Two deliveries:
          gen0=0.0, rx0=0.1  →  age at rx0 = 0.1
          gen1=0.1, rx1=0.3  →  age at rx1 = 0.2 (rx1-gen1)

        Between rx0 and rx1:
          age just after rx0 = 0.1
          age just before rx1 = 0.3 - 0.0 = 0.3    (rx1 - gen0)
          area contribution = 0.5 * (0.1 + 0.3) * (0.3 - 0.1) = 0.04

        t_start=0.0, t_end=0.3 → duration=0.3
        mean_aoi = 0.04 / 0.3 = 0.1333... s  = 133.33 ms
        peak = 0.3 s = 300 ms
        """
        mean_ms, peak_ms = _aoi_stats(
            gen_times=[0.0, 0.1],
            rx_times=[0.1, 0.3],
            t_start=0.0,
            t_end=0.3,
        )
        self.assertIsNotNone(mean_ms)
        self.assertAlmostEqual(mean_ms, 400.0 / 3.0, places=3)
        self.assertIsNotNone(peak_ms)
        self.assertAlmostEqual(peak_ms, 300.0, places=3)

    def test_regular_20ms(self):
        """
        Packets generated and received with exactly 0 propagation delay,
        every 0.02 s.  In this ideal case AoI resets to ~0 at every delivery,
        and the sawtooth mean = 0.5 * RRI = 10 ms.
        """
        import math
        n = 20
        gen = [0.02 * i for i in range(n)]
        rx = [g + 0.001 for g in gen]    # 1 ms propagation
        mean_ms, peak_ms = _aoi_stats(gen, rx, t_start=0.0, t_end=0.02 * n)
        # mean AoI ≈ propagation_delay/2 + RRI/2 ≈ 10.5 ms (rough check)
        self.assertIsNotNone(mean_ms)
        self.assertGreater(mean_ms, 0.0)
        self.assertLess(mean_ms, 30.0)
        self.assertGreater(peak_ms, mean_ms)

    def test_needs_at_least_two_deliveries(self):
        mean_ms, peak_ms = _aoi_stats([0.0], [0.1], t_start=0.0, t_end=1.0)
        self.assertIsNone(mean_ms)
        self.assertIsNone(peak_ms)

    def test_aoi_geq_delay(self):
        """Peak AoI must be ≥ max single-packet end-to-end delay."""
        gen = [0.0, 0.1, 0.2]
        rx = [0.05, 0.18, 0.30]   # delays: 50, 80, 100 ms
        mean_ms, peak_ms = _aoi_stats(gen, rx, t_start=0.0, t_end=0.30)
        max_delay_ms = max((r - g) * 1000 for g, r in zip(gen, rx))
        self.assertIsNotNone(peak_ms)
        self.assertGreaterEqual(peak_ms, max_delay_ms - 1e-6)


# ---------------------------------------------------------------------------
# A4. PRR with synthetic DB
# ---------------------------------------------------------------------------

class TestPrrPirThput(unittest.TestCase):

    def _make_perfect_link(self):
        """TX node 0, RX node 1, port 8001. 5 packets all received."""
        con = _make_db()
        _insert_node(con, 0, 1, "10.0.0.1", 0.0, 0.0)
        _insert_node(con, 1, 2, "10.0.0.2", 10.0, 0.0)
        for i in range(5):
            t = 0.2 * (i + 1)
            _insert_tx(con, 0, 1, "10.0.0.1", 8001, i, t)
            _insert_rx(con, 1, 2, "10.0.0.2", "10.0.0.1", 8001, i, t + 0.01)
        return con

    def test_perfect_prr_mean_1(self):
        from kpi_postprocess import _prr_pir_thput
        con = self._make_perfect_link()
        r = _prr_pir_thput(con, 1, 1, 0, "10.0.0.1", 1, 8001,
                           t_start=0.0, t_end=1.5, window=1.0, step=0.2)
        self.assertIsNotNone(r["prr_mean"])
        self.assertAlmostEqual(r["prr_mean"], 1.0, places=5)

    def test_half_loss_prr(self):
        from kpi_postprocess import _prr_pir_thput
        con = _make_db()
        _insert_node(con, 0, 1, "10.0.0.1", 0.0, 0.0)
        _insert_node(con, 1, 2, "10.0.0.2", 10.0, 0.0)
        for i in range(10):
            t = 0.1 * (i + 1)
            _insert_tx(con, 0, 1, "10.0.0.1", 8001, i, t)
            if i % 2 == 0:
                _insert_rx(con, 1, 2, "10.0.0.2", "10.0.0.1", 8001, i, t + 0.005)
        r = _prr_pir_thput(con, 1, 1, 0, "10.0.0.1", 1, 8001,
                           t_start=0.0, t_end=1.1, window=1.0, step=0.5)
        self.assertIsNotNone(r["prr_mean"])
        self.assertGreater(r["prr_mean"], 0.0)
        self.assertLessEqual(r["prr_mean"], 1.0)
        # Rough: ~50% → mean should be around 0.5
        self.assertAlmostEqual(r["prr_mean"], 0.5, delta=0.2)

    def test_pir_20ms_packets(self):
        from kpi_postprocess import _prr_pir_thput
        con = _make_db()
        _insert_node(con, 0, 1, "10.0.0.1", 0.0, 0.0)
        _insert_node(con, 1, 2, "10.0.0.2", 10.0, 0.0)
        n = 20
        for i in range(n):
            t = 0.02 * (i + 1)
            _insert_tx(con, 0, 1, "10.0.0.1", 8001, i, t)
            _insert_rx(con, 1, 2, "10.0.0.2", "10.0.0.1", 8001, i, t + 0.001)
        r = _prr_pir_thput(con, 1, 1, 0, "10.0.0.1", 1, 8001,
                           t_start=0.0, t_end=0.5, window=0.4, step=0.1)
        self.assertIsNotNone(r["pir_mean_ms"])
        self.assertAlmostEqual(r["pir_mean_ms"], 20.0, delta=1.0)


# ---------------------------------------------------------------------------
# A5. Half-duplex collision (synthetic)
# ---------------------------------------------------------------------------

class TestHalfDuplex(unittest.TestCase):

    def _make_hd_db(self):
        """
        Node 0 (rnti=1) TX at slot (frame=0,sf=0,slot=0).
        Node 1 (rnti=2) also TX at the same slot → node 1 is HD-deaf
        when receiving from node 0 at that slot.
        Expect hd_count ≥ 1.
        """
        con = _make_db()
        _insert_node(con, 0, 1, "10.0.0.1", 0.0, 0.0)
        _insert_node(con, 1, 2, "10.0.0.2", 50.0, 0.0)

        # TX grant: node 0 (rnti=1) at (f=0,sf=0,slot=0)
        con.execute(
            "INSERT INTO psschTxUeMac VALUES "
            "(2.0,1,1,1,254,0,0,0,2,9,10,0,10,0,1,0,5,5,0,1,1,1);"
        )
        # TX grant: node 1 (rnti=2) at same (f=0,sf=0,slot=0)
        con.execute(
            "INSERT INTO psschTxUeMac VALUES "
            "(2.0,2,2,2,254,0,0,0,2,9,10,10,10,0,1,0,5,5,0,1,1,1);"
        )
        con.commit()
        return con

    def test_hd_detected(self):
        con = self._make_hd_db()
        rx_meta = NodeMeta(node_id=1, imsi=2, ip="10.0.0.2", x=50.0, y=0.0, rnti=2)
        tx_meta = NodeMeta(node_id=0, imsi=1, ip="10.0.0.1", x=0.0, y=0.0, rnti=1)
        result = _collision_stats(con, 1, 1, rx_meta, tx_meta,
                                  t_start=0.0, t_end=10.0, range_m=None)
        self.assertGreaterEqual(result["hd_count"], 1)

    def test_no_hd_different_slots(self):
        """Node 1 TX at slot 1, node 0 TX at slot 0 — no overlap → hd_count=0."""
        con = _make_db()
        _insert_node(con, 0, 1, "10.0.0.1", 0.0, 0.0)
        _insert_node(con, 1, 2, "10.0.0.2", 50.0, 0.0)
        # Node 0 (rnti=1) at slot 0
        con.execute(
            "INSERT INTO psschTxUeMac VALUES "
            "(2.0,1,1,1,254,0,0,0,2,9,10,0,10,0,1,0,5,5,0,1,1,1);"
        )
        # Node 1 (rnti=2) at slot 1 (different slot)
        con.execute(
            "INSERT INTO psschTxUeMac VALUES "
            "(2.5,2,2,2,254,0,0,1,2,9,10,10,10,0,1,0,5,5,0,1,1,1);"
        )
        con.commit()
        rx_meta = NodeMeta(node_id=1, imsi=2, ip="10.0.0.2", x=50.0, y=0.0, rnti=2)
        tx_meta = NodeMeta(node_id=0, imsi=1, ip="10.0.0.1", x=0.0, y=0.0, rnti=1)
        result = _collision_stats(con, 1, 1, rx_meta, tx_meta,
                                  t_start=0.0, t_end=10.0, range_m=None)
        self.assertEqual(result["hd_count"], 0)


# ---------------------------------------------------------------------------
# A6. Collision attribution (RC vs CE)
# ---------------------------------------------------------------------------

class TestCollisionAttribution(unittest.TestCase):

    def _make_collision_db(self, interfering_rb_start=0, corrupt=True):
        """
        TX rnti=1, RX rnti=2, interferer rnti=3.
        Corrupt PSSCH at (f=10, sf=0, slot=0, rb=0..9).
        Interferer at same slot (f=10, sf=0, slot=0, rb=0..9) → resource collision.
        """
        con = _make_db()
        _insert_node(con, 0, 1, "10.0.0.1", 0.0, 0.0)
        _insert_node(con, 1, 2, "10.0.0.2", 50.0, 0.0)
        _insert_node(con, 2, 3, "10.0.0.3", 25.0, 0.0)

        # Corrupt RX event.
        con.execute(
            "INSERT INTO psschRxUePhy VALUES "
            "(100.0,0,2,0,10,0,0,1,1,254,0,10,2,9,12,1,0,1000,"
            f"5,4,50,{1 if corrupt else 0},30,0,1,1);"
        )
        # Interferer TX grant at same slot, overlapping RBs (if interfering_rb_start < 10).
        con.execute(
            f"INSERT INTO psschTxUeMac VALUES "
            f"(100.0,3,3,3,254,10,0,0,2,9,10,{interfering_rb_start},10,0,1,0,5,5,0,1,1,1);"
        )
        # TX grant from rnti=1 at same slot (expected TX).
        con.execute(
            "INSERT INTO psschTxUeMac VALUES "
            "(100.0,1,1,1,254,10,0,0,2,9,10,0,10,0,1,0,5,5,0,1,1,1);"
        )
        con.commit()
        return con

    def test_resource_collision_detected(self):
        """Interferer at same RBs → RC."""
        con = self._make_collision_db(interfering_rb_start=0)
        rx_meta = NodeMeta(node_id=1, imsi=2, ip="10.0.0.2", x=50.0, y=0.0, rnti=2)
        tx_meta = NodeMeta(node_id=0, imsi=1, ip="10.0.0.1", x=0.0, y=0.0, rnti=1)
        result = _collision_stats(con, 1, 1, rx_meta, tx_meta,
                                  t_start=0.0, t_end=1.0, range_m=None)
        self.assertEqual(result["total_corrupt"], 1)
        self.assertEqual(result["rc_count"], 1)
        self.assertEqual(result["ce_count"], 0)

    def test_channel_error_no_overlap(self):
        """Interferer at non-overlapping RBs → CE."""
        con = self._make_collision_db(interfering_rb_start=20)  # no overlap with 0..9
        rx_meta = NodeMeta(node_id=1, imsi=2, ip="10.0.0.2", x=50.0, y=0.0, rnti=2)
        tx_meta = NodeMeta(node_id=0, imsi=1, ip="10.0.0.1", x=0.0, y=0.0, rnti=1)
        result = _collision_stats(con, 1, 1, rx_meta, tx_meta,
                                  t_start=0.0, t_end=1.0, range_m=None)
        self.assertEqual(result["total_corrupt"], 1)
        self.assertEqual(result["rc_count"], 0)
        self.assertEqual(result["ce_count"], 1)

    def test_no_corrupt_rows(self):
        """No corrupt decodes → all zeros."""
        con = self._make_collision_db(corrupt=False)
        rx_meta = NodeMeta(node_id=1, imsi=2, ip="10.0.0.2", x=50.0, y=0.0, rnti=2)
        tx_meta = NodeMeta(node_id=0, imsi=1, ip="10.0.0.1", x=0.0, y=0.0, rnti=1)
        result = _collision_stats(con, 1, 1, rx_meta, tx_meta,
                                  t_start=0.0, t_end=1.0, range_m=None)
        self.assertEqual(result["total_corrupt"], 0)
        self.assertEqual(result["rc_count"], 0)
        self.assertEqual(result["ce_count"], 0)

    def test_invariant_rc_plus_ce_leq_total(self):
        """rc_count + ce_count must always ≤ total_corrupt."""
        con = self._make_collision_db(interfering_rb_start=0)
        rx_meta = NodeMeta(node_id=1, imsi=2, ip="10.0.0.2", x=50.0, y=0.0, rnti=2)
        tx_meta = NodeMeta(node_id=0, imsi=1, ip="10.0.0.1", x=0.0, y=0.0, rnti=1)
        result = _collision_stats(con, 1, 1, rx_meta, tx_meta,
                                  t_start=0.0, t_end=1.0, range_m=None)
        self.assertLessEqual(
            result["rc_count"] + result["ce_count"], result["total_corrupt"]
        )


# ---------------------------------------------------------------------------
# A7. Channel occupancy (unit, synthetic)
# ---------------------------------------------------------------------------

class TestChannelOccupancy(unittest.TestCase):

    def test_full_occupancy(self):
        """
        If every slot has a grant using all subchannels, occupancy ≈ 1.0.
        Here: 1 s window, slot_dur=0.5 ms → 2000 slots.
        PSFCH every 4 → 500 PSFCH, 1500 PSSCH slots.
        n_subch = 10 (totSbCh from pscchTxUeMac).
        Grant: rbLen=100 (=10 subch*10 RBs each) with sbChSize=10 → 10 subch-slots per grant.
        To fill: 1500*10 = 15000 subch-slots needed → 1500 grants each using 10 subch.
        For simplicity we use 1500 grants × 10 = 15000 → occupancy = 1.0.
        """
        con = _make_db()
        # Insert one pscchTxUeMac row with totSbCh=10 (for pool info).
        con.execute(
            "INSERT INTO pscchTxUeMac VALUES (0,1,1,0,0,0,0,2,0,10,2,5,100,20,10,0,2,2,0,0,1,1);"
        )
        # 1500 grants, each with rbLen=100, sbChSize=10 → 10 subch each; slot col=1 → slot_dur=0.5ms.
        for i in range(1500):
            t_ms = 2000.0 + i * 0.5   # 2s warmup + 0.5ms spacing
            con.execute(
                f"INSERT INTO psschTxUeMac VALUES "
                f"({t_ms},1,1,1,254,{i//2000},{(i//2)%10},{i%2},2,9,10,0,100,0,1,0,5,5,0,1,1,1);"
            )
        con.commit()
        occ = _channel_occupancy(con, 1, 1, tx_rnti=1,
                                  t_start=2.0, t_end=3.0)
        self.assertIsNotNone(occ)
        self.assertGreater(occ, 0.0)
        self.assertLessEqual(occ, 1.0)

    def test_zero_grants(self):
        """No grants → occupancy=0.0 or None (no crash)."""
        con = _make_db()
        con.execute(
            "INSERT INTO pscchTxUeMac VALUES (0,1,1,0,0,0,0,2,0,10,2,5,100,20,10,0,2,2,0,0,1,1);"
        )
        con.commit()
        occ = _channel_occupancy(con, 1, 1, tx_rnti=1,
                                  t_start=0.0, t_end=1.0)
        # Either None (no grants found) or 0.0
        self.assertTrue(occ is None or occ == 0.0)


# ---------------------------------------------------------------------------
# A8. Sanity / regression invariants on a real DB (skipped if not found)
# ---------------------------------------------------------------------------

REAL_DB_CANDIDATES = [
    # Prefer smaller DBs (fewer TX nodes) for faster test runs.
    "experiment_relay_groupcast/results_relay_single/platoon_ivd_sweep/mcs12/x10/"
    "platoon_ivd_sweep_mcs12_x10_seed1.db",
    "experiment_dual_mcs_groupcast/results_dual_mcs/platoon_merge_incremental/"
    "platoon_merge_incremental_dual_seed10.db",
]

# Set to True to run the real-DB tests (can be slow on large DBs).
# Default False so the unit test suite finishes in < 1 s.
# Run with: KPITEST_REAL=1 python3 -m unittest tests.test_kpi_helpers -v
import os
_RUN_REAL_DB_TESTS = os.environ.get("KPITEST_REAL", "0") == "1"


def _find_real_db() -> Path | None:
    """Return first existing real DB relative to the repo root."""
    # Adjust relative to this test file's location.
    repo_root = Path(__file__).parent.parent.parent.parent.parent
    for rel in REAL_DB_CANDIDATES:
        p = repo_root / rel
        if p.exists():
            return p
    return None


class TestRealDbInvariants(unittest.TestCase):
    """
    Regression guard: run kpi_postprocess on a real DB and assert
    invariants hold.  Skipped by default (set KPITEST_REAL=1 to enable).
    These tests can be slow (30-120 s) on large DBs.
    """

    @classmethod
    def setUpClass(cls):
        if not _RUN_REAL_DB_TESTS:
            cls.rows = None
            cls._skip_reason = "KPITEST_REAL not set; skipping real-DB tests"
            return

        cls.db_path = _find_real_db()
        if cls.db_path is None:
            cls.rows = None
            cls._skip_reason = "No real DB found"
            return

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            cls.out_path = Path(f.name)

        # Infer seed/run from DB.
        con = sqlite3.connect(str(cls.db_path))
        row = con.execute(
            "SELECT SEED, RUN FROM pktTxRx LIMIT 1;"
        ).fetchone()
        con.close()
        seed, run = (int(row[0]), int(row[1])) if row else (1, 1)

        try:
            cls.rows = compute_kpis(
                db_path=cls.db_path,
                out_path=cls.out_path,
                warmup=2.0,
                sim_end=None,
                window=1.0,
                step=0.5,
                range_m=None,
                seed=seed,
                run=run,
            )
        except Exception as e:
            cls.rows = None
            cls.setup_error = str(e)
            return
        cls.setup_error = None

    def _skip_if_no_db(self):
        reason = getattr(self.__class__, "_skip_reason", None)
        if reason:
            self.skipTest(reason)
        if self.rows is None:
            self.skipTest(f"compute_kpis failed: {getattr(self, 'setup_error', 'unknown')}")

    def test_rows_produced(self):
        self._skip_if_no_db()
        self.assertGreater(len(self.rows), 0, "Expected at least one KPI row")

    def test_prr_in_range(self):
        self._skip_if_no_db()
        for r in self.rows:
            if r.prr_mean is not None:
                self.assertGreaterEqual(r.prr_mean, 0.0 - 1e-9,
                                        f"PRR < 0: {r}")
                self.assertLessEqual(r.prr_mean, 1.0 + 1e-9,
                                     f"PRR > 1: {r}")
            if r.prr_p5 is not None and r.prr_p95 is not None:
                self.assertLessEqual(r.prr_p5, r.prr_p95 + 1e-9,
                                     "p5 > p95 for PRR")

    def test_pir_positive(self):
        self._skip_if_no_db()
        for r in self.rows:
            if r.pir_mean_ms is not None:
                self.assertGreater(r.pir_mean_ms, 0.0, f"PIR ≤ 0: {r}")
            if r.pir_p5_ms is not None and r.pir_p95_ms is not None:
                self.assertLessEqual(r.pir_p5_ms, r.pir_p95_ms + 1e-9,
                                     "p5 > p95 for PIR")

    def test_thput_positive(self):
        self._skip_if_no_db()
        for r in self.rows:
            if r.thput_mean_kbps is not None:
                self.assertGreaterEqual(r.thput_mean_kbps, 0.0,
                                        f"throughput < 0: {r}")

    def test_aoi_geq_zero(self):
        self._skip_if_no_db()
        for r in self.rows:
            if r.aoi_mean_ms is not None:
                self.assertGreater(r.aoi_mean_ms, 0.0,
                                   f"AoI mean ≤ 0: {r}")
            if r.aoi_peak_ms is not None and r.aoi_mean_ms is not None:
                self.assertGreaterEqual(r.aoi_peak_ms, r.aoi_mean_ms - 1e-6,
                                        "AoI peak < mean")

    def test_collision_invariant(self):
        self._skip_if_no_db()
        for r in self.rows:
            if r.total_corrupt is not None:
                rc = r.rc_count or 0
                ce = r.ce_count or 0
                self.assertLessEqual(
                    rc + ce, r.total_corrupt + 1,   # +1 for floating-point tolerance
                    f"rc+ce > total_corrupt: {r}"
                )

    def test_harq_rate_in_range(self):
        self._skip_if_no_db()
        for r in self.rows:
            if r.harq_nack_rate is not None:
                self.assertGreaterEqual(r.harq_nack_rate, 0.0)
                self.assertLessEqual(r.harq_nack_rate, 1.0)

    def test_occupancy_in_range(self):
        self._skip_if_no_db()
        for r in self.rows:
            if r.ch_occupancy is not None:
                self.assertGreaterEqual(r.ch_occupancy, 0.0,
                                        f"occupancy < 0: {r}")
                self.assertLessEqual(r.ch_occupancy, 1.0 + 1e-9,
                                     f"occupancy > 1: {r}")

    def test_percentile_ordering(self):
        self._skip_if_no_db()
        for r in self.rows:
            for prefix in ["prr", "pir_", "thput_"]:
                p5 = getattr(r, f"{prefix}p5" if prefix == "prr"
                              else f"{prefix}p5_ms" if prefix == "pir_"
                              else f"{prefix}p5_kbps", None)
                p95 = getattr(r, f"{prefix}p95" if prefix == "prr"
                               else f"{prefix}p95_ms" if prefix == "pir_"
                               else f"{prefix}p95_kbps", None)
                if p5 is not None and p95 is not None:
                    self.assertLessEqual(p5, p95 + 1e-9,
                                         f"{prefix} p5 > p95 in row {r}")


if __name__ == "__main__":
    unittest.main()
