"""
data.py — Lazy bulk-load cache for KPI computation.

Reads the simulation SQLite DB once, caches each table as a pandas DataFrame.
KPIs access tables via methods (pkt_txrx(), node_info(), ...) which load on
first call and return the cached frame on subsequent calls.

Every accessor is optional-aware: tables that may not exist (e.g. appPktSeq,
relayTiming, psscchTxUeMac) return None when absent instead of raising.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd


class DataCache:
    """Lazy bulk-load cache over a simulation SQLite DB.

    Usage:
        cache = DataCache.open(Path("run.db"), seed=1, run=1,
                               t_start=1.0, t_end=6.0)
        df = cache.pkt_txrx()        # auto-loaded on first call
        df = cache.pkt_txrx()        # cached
        cache.close()
    """

    def __init__(self, con: sqlite3.Connection, seed: int, run: int,
                 t_start: float, t_end: float):
        self.con = con
        self.seed = seed
        self.run = run
        self.t_start = float(t_start)
        self.t_end = float(t_end)
        self._cache: dict = {}

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def open(cls, db_path: Path, seed: int, run: int,
             t_start: float, t_end: float) -> "DataCache":
        con = sqlite3.connect(str(db_path))
        return cls(con, seed=seed, run=run, t_start=t_start, t_end=t_end)

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _has_table(self, table: str) -> bool:
        row = self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?;",
            (table,),
        ).fetchone()
        return row is not None

    def _table_has_columns(self, table: str, cols: tuple) -> bool:
        """Return True iff the table exists and has ALL of the given columns."""
        try:
            existing = {row[1] for row in self.con.execute(
                f"PRAGMA table_info({table});"
            ).fetchall()}
        except sqlite3.OperationalError:
            return False
        return all(c in existing for c in cols)

    def _load(self, key: str, table: str, columns: str,
              time_col: Optional[str] = None,
              time_scale: float = 1.0) -> Optional[pd.DataFrame]:
        if key in self._cache:
            return self._cache[key]
        if not self._has_table(table):
            self._cache[key] = None
            return None
        existing = {row[1] for row in self.con.execute(
            f"PRAGMA table_info({table});"
        ).fetchall()}
        # Drop columns that don't exist in this DB.
        requested = [c.strip() for c in columns.split(",")]
        actual_cols = [c for c in requested if c in existing]
        if not actual_cols:
            self._cache[key] = None
            return None
        # Build WHERE clause based on what's actually in the schema.
        where_parts = []
        params: list = []
        if self._table_has_columns(table, ("SEED", "RUN")):
            where_parts.append("SEED=? AND RUN=?")
            params.extend([self.seed, self.run])
        if time_col is not None and self._table_has_columns(table, (time_col,)):
            lo = self.t_start * time_scale
            hi = self.t_end * time_scale
            where_parts.append(f"{time_col}>=? AND {time_col}<=?")
            params.extend([lo, hi])
        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        sql = f"SELECT {', '.join(actual_cols)} FROM {table} {where};"
        df = pd.read_sql_query(sql, self.con, params=params)
        self._cache[key] = df
        return df

    # ------------------------------------------------------------------ #
    # Table accessors — each loads once and caches.
    # ------------------------------------------------------------------ #
    def pkt_txrx(self) -> Optional[pd.DataFrame]:
        """Per-packet TX/RX events (with sequence numbers, srcIp, dstPort).

        Columns: timeSec, txRx, nodeId, imsi, pktSizeBytes, srcIp, srcPort,
                 dstIp, dstPort, pktSeqNum, SEED, RUN
        """
        return self._load(
            "pkt_txrx", "pktTxRx",
            "timeSec, txRx, nodeId, imsi, pktSizeBytes, srcIp, srcPort, "
            "dstIp, dstPort, pktSeqNum",
            time_col="timeSec",
        )

    def node_info(self) -> Optional[pd.DataFrame]:
        """Per-node identity + position snapshot.

        Columns: nodeId, imsi, ip, x, y, z
        """
        return self._load(
            "node_info", "nodeInfo",
            "nodeId, imsi, ip, x, y, z",
        )

    def imsi_to_node(self) -> Dict[int, int]:
        """Cached map from imsi -> nodeId (rnti == imsi in our sims).
        Returns empty dict if nodeInfo unavailable."""
        ni = self.node_info()
        if ni is None or len(ni) == 0:
            return {}
        return {int(r.imsi): int(r.nodeId) for r in ni.itertuples(index=False)}

    def app_pkt_seq(self) -> Optional[pd.DataFrame]:
        """App-layer per-packet TX/RX with sequence numbers (preferred for
        relay-aware metrics). Columns: timeSec, txRx, nodeId, dstPort, seq.

        May be None for older sims that don't write this table.
        """
        return self._load(
            "app_pkt_seq", "appPktSeq",
            "timeSec, txRx, nodeId, dstPort, seq",
            time_col="timeSec",
        )

    def relay_timing(self) -> Optional[pd.DataFrame]:
        """Per-forwarded-packet relay timing.

        Columns actually present: seq, origPort, relayNodeId, forwarded,
        leaderGenSec, relayDecodeSec, forwardSendSec (some legacy sims may
        differ). We only request columns that exist.
        """
        if not self._has_table("relayTiming"):
            self._cache["relay_timing"] = None
            return None
        existing = {row[1] for row in self.con.execute(
            "PRAGMA table_info(relayTiming);"
        ).fetchall()}
        # Pick only the columns we know about, in a stable order.
        desired = ("seq", "origPort", "relayNodeId", "forwarded",
                   "leaderGenSec", "relayDecodeSec", "forwardSendSec")
        cols = [c for c in desired if c in existing]
        if not cols:
            self._cache["relay_timing"] = None
            return None
        # Don't apply time-window filter to relayTiming (no time column here).
        df = pd.read_sql_query(
            f"SELECT {', '.join(cols)} FROM relayTiming;", self.con
        )
        self._cache["relay_timing"] = df
        return df

    def harq_feedback(self) -> Optional[pd.DataFrame]:
        """HARQ ACK/NACK events. Columns: timeSec, rxNodeId, rxRnti, txRnti,
        dstL2Id, harqId, status."""
        return self._load(
            "harq_feedback", "slHarqFeedback",
            "timeSec, rxNodeId, rxRnti, txRnti, dstL2Id, harqId, status",
            time_col="timeSec",
        )

    def pssch_tx(self) -> Optional[pd.DataFrame]:
        """PSSCH TX grants (UE MAC layer). Columns: timeMs, rnti, frame,
        subFrame, slot, srcL2Id, dstL2Id, rbStart, rbLen, sbChSize, harqId,
        ndi, rv."""
        return self._load(
            "pssch_tx", "psschTxUeMac",
            "timeMs, rnti, imsi, frame, subFrame, slot, srcL2Id, dstL2Id, "
            "rbStart, rbLen, sbChSize, harqId, ndi, rv",
            time_col="timeMs", time_scale=1000.0,
        )

    def pscch_tx(self) -> Optional[pd.DataFrame]:
        """PSCCH TX grants. Columns: timeMs, rnti, frame, subFrame, slot,
        rbStart, rbLen."""
        return self._load(
            "pscch_tx", "pscchTxUeMac",
            "timeMs, rnti, frame, subFrame, slot, rbStart, rbLen",
            time_col="timeMs", time_scale=1000.0,
        )

    def pssch_rx(self) -> Optional[pd.DataFrame]:
        """PSSCH RX events (UE PHY). Columns: timeMs, rnti, txRnti,
        srcL2Id, dstL2Id, bwpId, frame, subFrame, slot, psschRbStart,
        psschRbLen, tbSizeBytes, avrgSinr, minSinr, psschTbler,
        psschCorrupt, sci2Tbler, sci2Corrupt."""
        return self._load(
            "pssch_rx", "psschRxUePhy",
            "timeMs, rnti, txRnti, srcL2Id, dstL2Id, bwpId, frame, subFrame, "
            "slot, psschRbStart, psschRbLen, tbSizeBytes, avrgSinr, minSinr, "
            "psschTbler, psschCorrupt, sci2Tbler, sci2Corrupt",
            time_col="timeMs", time_scale=1000.0,
        )

    def rlc_rx(self) -> Optional[pd.DataFrame]:
        """RLC RX events. Columns: timeMs, imsi, rnti, txRnti, lcid,
        rxPduSize, delayMicroSec."""
        return self._load(
            "rlc_rx", "rlcRx",
            "timeMs, imsi, rnti, txRnti, lcid, rxPduSize, delayMicroSec",
            time_col="timeMs", time_scale=1000.0,
        )


if __name__ == "__main__":
    # Smoke test against the real fixed4_s100_seed1.db
    import sys
    db = Path("/Users/hadih/Documents/groupcast-adaptation-ns3/results_c4/"
              "fixed4_s100_seed1/fixed4_s100_seed1.db")
    if not db.exists():
        print(f"  (skipped: {db} not found)")
        sys.exit(0)
    with DataCache.open(db, seed=1, run=1, t_start=1.0, t_end=7.0) as cache:
        for name in ["pkt_txrx", "node_info", "app_pkt_seq", "relay_timing",
                     "harq_feedback", "pssch_tx", "pssch_rx"]:
            df = getattr(cache, name)()
            n = len(df) if df is not None else 0
            print(f"  {name:<20} n={n}")
    print("data.py smoke test passed.")